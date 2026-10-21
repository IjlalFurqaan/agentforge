// Minimal unified-diff parser for the code review view.

export type DiffLineKind = "add" | "del" | "ctx" | "hunk";

export interface DiffLine {
  kind: DiffLineKind;
  text: string;
  oldNo: number | null;
  newNo: number | null;
}

export interface DiffFile {
  path: string;
  oldPath: string | null;
  status: "added" | "deleted" | "modified" | "renamed";
  additions: number;
  deletions: number;
  lines: DiffLine[];
}

export function parseUnifiedDiff(diff: string): DiffFile[] {
  const files: DiffFile[] = [];
  let current: DiffFile | null = null;
  let oldNo = 0;
  let newNo = 0;
  for (const line of diff.split("\n")) {
    if (line.startsWith("diff --git ")) {
      const match = /^diff --git a\/(.+) b\/(.+)$/.exec(line);
      current = { path: match?.[2] ?? line, oldPath: match?.[1] ?? null, status: "modified", additions: 0, deletions: 0, lines: [] };
      files.push(current);
      continue;
    }
    if (!current) continue;
    if (line.startsWith("new file mode")) current.status = "added";
    else if (line.startsWith("deleted file mode")) current.status = "deleted";
    else if (line.startsWith("rename from")) current.status = "renamed";
    else if (line.startsWith("--- ") || line.startsWith("+++ ") || line.startsWith("index ") || line.startsWith("similarity")) continue;
    else if (line.startsWith("@@")) {
      const m = /@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)/.exec(line);
      oldNo = m ? Number(m[1]) : 0;
      newNo = m ? Number(m[2]) : 0;
      current.lines.push({ kind: "hunk", text: line, oldNo: null, newNo: null });
    } else if (line.startsWith("+")) {
      current.additions += 1;
      current.lines.push({ kind: "add", text: line.slice(1), oldNo: null, newNo: newNo++ });
    } else if (line.startsWith("-")) {
      current.deletions += 1;
      current.lines.push({ kind: "del", text: line.slice(1), oldNo: oldNo++, newNo: null });
    } else if (line.startsWith(" ")) {
      current.lines.push({ kind: "ctx", text: line.slice(1), oldNo: oldNo++, newNo: newNo++ });
    }
  }
  return files;
}
