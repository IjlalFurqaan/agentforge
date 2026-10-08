"""Code search: symbol-aware chunking + BM25, with optional embedding re-ranking.

Lexical retrieval is the primary signal (identifiers matter in code); embeddings are a supporting
re-ranker when an embedding provider is configured.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.intelligence.python_ast import analyze_python_source
from app.intelligence.scanner import PROGRAMMING_LANGUAGES, ScanResult, read_text

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TEXT_LANGS = PROGRAMMING_LANGUAGES | {"Markdown", "YAML", "TOML", "JSON", "Dockerfile", "INI", "HTML", "CSS"}

Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        lower = raw.lower()
        tokens.append(lower)
        parts = [p for p in re.split(r"_+", _CAMEL_RE.sub("_", raw)) if p]
        if len(parts) > 1:
            tokens.extend(p.lower() for p in parts)
    return tokens


@dataclass
class Chunk:
    path: str
    start: int
    end: int
    symbol: str | None
    text: str
    tokens: Counter[str] = field(default_factory=Counter)


@dataclass
class SearchHit:
    path: str
    start: int
    end: int
    symbol: str | None
    score: float
    snippet: str


class CodeIndex:
    WINDOW = 60

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self.df: Counter[str] = Counter()
        for c in chunks:
            self.df.update(set(c.tokens))
        self.avg_len = (sum(sum(c.tokens.values()) for c in chunks) / len(chunks)) if chunks else 0.0
        self._embeddings: dict[int, list[float]] = {}

    @classmethod
    def build(cls, root: Path, scan: ScanResult, max_files: int = 4000) -> CodeIndex:
        chunks: list[Chunk] = []
        for entry in scan.files[:max_files]:
            if entry.is_binary or entry.language not in _TEXT_LANGS or entry.size > 400_000:
                continue
            text = read_text(root, entry.path)
            if text is None:
                continue
            lines = text.splitlines()
            covered: set[int] = set()
            if entry.language == "Python":
                for sym in analyze_python_source(entry.path, text).symbols:
                    if sym.kind == "method":
                        continue  # the enclosing class chunk already covers methods
                    body = "\n".join(lines[sym.lineno - 1: sym.end_lineno])
                    chunks.append(Chunk(entry.path, sym.lineno, sym.end_lineno, sym.name, body,
                                        Counter(tokenize(f"{sym.name} {entry.path} {body}"))))
                    covered.update(range(sym.lineno, sym.end_lineno + 1))
            for start in range(0, len(lines), cls.WINDOW):
                window = range(start + 1, min(start + cls.WINDOW, len(lines)) + 1)
                if all(n in covered for n in window):
                    continue
                body = "\n".join(lines[start: start + cls.WINDOW])
                chunks.append(Chunk(entry.path, start + 1, window[-1] if window else start + 1, None, body,
                                    Counter(tokenize(f"{entry.path} {body}"))))
        return cls(chunks)

    def _bm25(self, query_tokens: list[str], chunk: Chunk, k1: float = 1.4, b: float = 0.75) -> float:
        n = len(self.chunks)
        length = sum(chunk.tokens.values()) or 1
        score = 0.0
        for t in set(query_tokens):
            tf = chunk.tokens.get(t, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / (self.avg_len or 1)))
        if chunk.symbol and any(t in chunk.symbol.lower() for t in query_tokens):
            score *= 1.5  # symbol-name match boost
        return score

    def search(self, query: str, k: int = 8) -> list[SearchHit]:
        q = tokenize(query)
        scored = sorted(((self._bm25(q, c), i) for i, c in enumerate(self.chunks)), reverse=True)
        return [self._hit(i, s) for s, i in scored[:k] if s > 0]

    async def hybrid_search(self, query: str, embed: Embedder, k: int = 8, candidates: int = 40) -> list[SearchHit]:
        q = tokenize(query)
        scored = [(s, i) for s, i in sorted(((self._bm25(q, c), i) for i, c in enumerate(self.chunks)),
                                            reverse=True)[:candidates] if s > 0]
        if not scored:
            return []
        missing = [i for _, i in scored if i not in self._embeddings]
        if missing:
            vectors = await embed([self.chunks[i].text[:2000] for i in missing])
            self._embeddings.update(zip(missing, vectors, strict=True))
        (qvec,) = await embed([query])
        top = scored[0][0] or 1.0
        combined = [
            (0.5 * (s / top) + 0.5 * _cosine(qvec, self._embeddings[i]), i) for s, i in scored
        ]
        return [self._hit(i, s) for s, i in sorted(combined, reverse=True)[:k]]

    def _hit(self, i: int, score: float) -> SearchHit:
        c = self.chunks[i]
        snippet = "\n".join(c.text.splitlines()[:25])
        return SearchHit(c.path, c.start, c.end, c.symbol, round(score, 4), snippet)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
