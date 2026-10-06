"""Secret detection on unified diffs (added lines only)."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("aws-access-key", "critical", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private-key", "critical", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("github-token", "critical", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b")),
    ("slack-token", "high", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe-live-key", "critical", re.compile(r"\b(sk|rk)_live_[A-Za-z0-9]{20,}\b")),
    ("google-api-key", "high", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    ("anthropic-key", "critical", re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b")),
    ("openai-key", "critical", re.compile(r"\bsk-(proj-)?[A-Za-z0-9]{32,}\b")),
    ("jwt", "medium", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
]
_ASSIGNMENT = re.compile(
    r"""(?i)\b([\w.-]*(secret|password|passwd|api[_-]?key|token|private[_-]?key|client[_-]?secret)[\w.-]*)\s*[:=]\s*['"]([^'"\s]{12,})['"]"""
)
_PLACEHOLDER = re.compile(r"(?i)(change[-_]?me|example|placeholder|dummy|test|fake|sample|xxx+|\*\*\*|<[^>]+>|\$\{|your[-_])")


@dataclass
class SecretFinding:
    rule: str
    severity: str
    file: str
    line: int
    excerpt: str


def shannon_entropy(value: str) -> float:
    counts = Counter(value)
    return -sum(c / len(value) * math.log2(c / len(value)) for c in counts.values()) if value else 0.0


def _mask(text: str) -> str:
    return re.sub(r"([A-Za-z0-9_\-]{4})[A-Za-z0-9_\-]{6,}", r"\1********", text)[:160]


def scan_diff(diff_text: str) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    file, line_no = "", 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            file = raw[6:] if raw.startswith("+++ b/") else raw[4:]
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line_no = int(m.group(1)) - 1 if m else 0
            continue
        if raw.startswith("-") or raw.startswith("\\"):
            continue
        line_no += 1
        if not raw.startswith("+"):
            continue
        content = raw[1:]
        test_file = bool(re.search(r"(^|/)(tests?|__tests__)/|test_[^/]*\.py$|\.(test|spec)\.", file))
        for rule, severity, rx in PATTERNS:
            if rx.search(content):
                findings.append(SecretFinding(rule, "medium" if test_file else severity, file, line_no, _mask(content.strip())))
        for m in _ASSIGNMENT.finditer(content):
            value = m.group(3)
            if _PLACEHOLDER.search(value) or shannon_entropy(value) < 3.5 or test_file:
                continue
            findings.append(SecretFinding("hardcoded-credential", "high", file, line_no, _mask(content.strip())))
    return findings
