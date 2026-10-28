"use client";

import clsx from "clsx";
import { useMemo, useState } from "react";

import { parseUnifiedDiff } from "@/lib/diff";
import { Icon } from "./icons";
import { Badge } from "./ui";

export interface InlineComment {
  file: string;
  line: number;
  severity: string;
  body: string;
  author: string;
}

export function DiffViewer({ diff, comments = [] }: { diff: string; comments?: InlineComment[] }) {
  const files = useMemo(() => parseUnifiedDiff(diff), [diff]);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  if (!diff.trim()) return <p className="p-6 text-sm text-muted">No changes yet.</p>;

  return (
    <div className="space-y-4">
      {files.map((f) => {
        const fileComments = comments.filter((c) => c.file === f.path);
        const isCollapsed = collapsed[f.path];
        return (
          <div key={f.path} className="overflow-hidden rounded-lg border">
            <button
              onClick={() => setCollapsed({ ...collapsed, [f.path]: !isCollapsed })}
              className="flex w-full items-center gap-3 border-b bg-raised/60 px-4 py-2 text-left text-xs"
            >
              <Icon name="chevron" className={clsx("h-3.5 w-3.5 transition", !isCollapsed && "rotate-90")} />
              <span className="font-mono font-medium">{f.path}</span>
              <Badge tone={f.status === "added" ? "green" : f.status === "deleted" ? "red" : "slate"}>{f.status}</Badge>
              {fileComments.length > 0 && <Badge tone="amber">{fileComments.length} comments</Badge>}
              <span className="ml-auto tabular-nums">
                <span className="text-emerald-500">+{f.additions}</span> <span className="text-rose-500">−{f.deletions}</span>
              </span>
            </button>
            {!isCollapsed && (
              <div className="overflow-x-auto scrollbar-thin">
                <table className="w-full border-collapse font-mono text-[12px] leading-5">
                  <tbody>
                    {f.lines.map((l, i) => {
                      const lineComments = l.newNo != null ? fileComments.filter((c) => c.line === l.newNo) : [];
                      return [
                        <tr
                          key={i}
                          className={clsx(
                            l.kind === "add" && "bg-emerald-500/10",
                            l.kind === "del" && "bg-rose-500/10",
                            l.kind === "hunk" && "bg-indigo-500/5 text-muted",
                          )}
                        >
                          <td className="w-12 select-none border-r px-2 text-right text-muted/70">{l.oldNo ?? ""}</td>
                          <td className="w-12 select-none border-r px-2 text-right text-muted/70">{l.newNo ?? ""}</td>
                          <td className="w-5 select-none text-center text-muted">{l.kind === "add" ? "+" : l.kind === "del" ? "−" : ""}</td>
                          <td className="whitespace-pre pr-4">{l.text}</td>
                        </tr>,
                        ...lineComments.map((c, j) => (
                          <tr key={`${i}-c${j}`}>
                            <td colSpan={4} className="border-y bg-amber-500/5 px-4 py-2 font-sans">
                              <div className="flex items-center gap-2 text-xs">
                                <Icon name="message" className="h-3.5 w-3.5 text-amber-500" />
                                <span className="font-medium">{c.author}</span>
                                <Badge tone={c.severity === "blocker" || c.severity === "major" ? "red" : "slate"}>{c.severity}</Badge>
                              </div>
                              <p className="mt-1 text-xs leading-relaxed">{c.body}</p>
                            </td>
                          </tr>
                        )),
                      ];
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
