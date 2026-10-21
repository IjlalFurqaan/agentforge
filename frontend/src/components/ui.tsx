// Component system: small, composable primitives styled with Tailwind design tokens.
import clsx from "clsx";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

import { STATUS_STYLES, statusLabel } from "@/lib/format";
import type { TaskStatus } from "@/lib/types";
import { Icon, type IconName } from "./icons";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "success";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-white hover:bg-accent/90 shadow-sm",
  secondary: "bg-raised text-ink hover:bg-raised/70 ring-1 ring-inset ring-line",
  ghost: "text-muted hover:text-ink hover:bg-raised",
  danger: "bg-rose-600 text-white hover:bg-rose-500",
  success: "bg-emerald-600 text-white hover:bg-emerald-500",
};

export function Button({
  variant = "primary",
  size = "md",
  icon,
  loading,
  className,
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; icon?: IconName; loading?: boolean }) {
  return (
    <button
      className={clsx(
        "inline-flex items-center justify-center gap-2 rounded-md font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-8 px-3 text-xs" : "h-9 px-4 text-sm",
        VARIANTS[variant],
        className,
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Spinner className="h-3.5 w-3.5" /> : icon ? <Icon name={icon} className="h-4 w-4" /> : null}
      {children}
    </button>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={clsx("rounded-xl border bg-panel shadow-sm", className)}>{children}</div>;
}

export function CardHeader({ title, subtitle, action, icon }: { title: ReactNode; subtitle?: ReactNode; action?: ReactNode; icon?: IconName }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b px-5 py-4">
      <div className="flex min-w-0 items-center gap-2.5">
        {icon && <Icon name={icon} className="h-4 w-4 shrink-0 text-muted" />}
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold">{title}</h3>
          {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
        </div>
      </div>
      {action}
    </div>
  );
}

export function Badge({ tone = "slate", children, className }: { tone?: "slate" | "green" | "red" | "amber" | "indigo" | "sky"; children: ReactNode; className?: string }) {
  const tones = {
    slate: "bg-slate-500/10 text-muted ring-slate-500/25",
    green: "bg-emerald-500/10 text-emerald-600 ring-emerald-500/25 dark:text-emerald-400",
    red: "bg-rose-500/10 text-rose-600 ring-rose-500/25 dark:text-rose-400",
    amber: "bg-amber-500/10 text-amber-700 ring-amber-500/25 dark:text-amber-400",
    indigo: "bg-indigo-500/10 text-indigo-600 ring-indigo-500/25 dark:text-indigo-400",
    sky: "bg-sky-500/10 text-sky-600 ring-sky-500/25 dark:text-sky-400",
  };
  return (
    <span className={clsx("inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset", tones[tone], className)}>
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: TaskStatus }) {
  return (
    <span className={clsx("inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium capitalize ring-1 ring-inset", STATUS_STYLES[status])}>
      <span className={clsx("h-1.5 w-1.5 rounded-full bg-current", status === "running" && "animate-pulse-dot")} />
      {statusLabel(status)}
    </span>
  );
}

const fieldClass =
  "w-full rounded-md border bg-panel px-3 py-2 text-sm text-ink placeholder:text-muted/70 focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={clsx(fieldClass, "h-9", props.className)} />;
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={clsx(fieldClass, "min-h-[96px] leading-relaxed", props.className)} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={clsx(fieldClass, "h-9", props.className)} />;
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-ink">{label}</span>
      {children}
      {hint && <span className="block text-xs text-muted">{hint}</span>}
    </label>
  );
}

export function Spinner({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg className={clsx("animate-spin", className)} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function EmptyState({ icon = "spark", title, children }: { icon?: IconName; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <div className="rounded-full bg-raised p-3 text-muted">
        <Icon name={icon} className="h-5 w-5" />
      </div>
      <p className="text-sm font-medium">{title}</p>
      {children && <div className="max-w-md text-xs text-muted">{children}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, icon }: { label: string; value: ReactNode; hint?: ReactNode; icon: IconName }) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between text-xs text-muted">
        <span>{label}</span>
        <Icon name={icon} className="h-4 w-4" />
      </div>
      <div className="mt-2 text-2xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </Card>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string; icon?: IconName; count?: number }[]; value: T; onChange: (id: T) => void }) {
  return (
    <div className="flex gap-1 overflow-x-auto border-b" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={clsx(
            "-mb-px inline-flex items-center gap-2 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm transition",
            value === t.id ? "border-accent font-medium text-ink" : "border-transparent text-muted hover:text-ink",
          )}
        >
          {t.icon && <Icon name={t.icon} className="h-4 w-4" />}
          {t.label}
          {t.count !== undefined && <span className="rounded bg-raised px-1.5 text-[11px] tabular-nums text-muted">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex items-start gap-2 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-600 dark:text-rose-400">
      <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{message}</span>
    </div>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <code className={clsx("rounded bg-raised px-1.5 py-0.5 font-mono text-[12px]", className)}>{children}</code>;
}
