"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useAuth } from "@/lib/auth";
import { Icon, type IconName } from "./icons";
import { Spinner } from "./ui";

const NAV: { href: string; label: string; icon: IconName }[] = [
  { href: "/", label: "Dashboard", icon: "dashboard" },
  { href: "/tasks", label: "Engineering tasks", icon: "tasks" },
  { href: "/tasks/new", label: "New task", icon: "plus" },
  { href: "/repositories", label: "Repositories", icon: "repo" },
];

export function AppShell({ title, actions, children }: { title: ReactNode; actions?: ReactNode; children: ReactNode }) {
  const { user, ready, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (ready && !user) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [ready, user, router, pathname]);

  if (!ready || !user) {
    return (
      <div className="flex h-screen items-center justify-center text-muted">
        <Spinner className="h-5 w-5" />
      </div>
    );
  }

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r bg-panel md:flex">
        <div className="flex h-14 items-center gap-2 border-b px-5">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-accent text-white">
            <Icon name="layers" className="h-4 w-4" />
          </div>
          <div>
            <div className="text-sm font-semibold leading-none">AgentForge</div>
            <div className="mt-1 text-[10px] uppercase tracking-wider text-muted">Autonomous engineering</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 p-3">
          {NAV.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : item.href === "/tasks"
                  ? pathname === "/tasks" || (pathname.startsWith("/tasks/") && pathname !== "/tasks/new")
                  : pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={clsx(
                  "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition",
                  active ? "bg-raised font-medium text-ink" : "text-muted hover:bg-raised hover:text-ink",
                )}
              >
                <Icon name={item.icon} className="h-4 w-4" />
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="border-t p-3">
          <div className="flex items-center gap-2.5 rounded-md px-2 py-1.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-raised text-xs font-semibold uppercase">
              {(user.full_name || user.email).slice(0, 2)}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium">{user.full_name || user.email}</div>
              <div className="text-[11px] capitalize text-muted">{user.role}</div>
            </div>
            <button
              onClick={() => void logout().then(() => router.replace("/login"))}
              className="rounded p-1.5 text-muted hover:bg-raised hover:text-ink"
              title="Sign out"
            >
              <Icon name="logout" className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex h-14 items-center justify-between gap-4 border-b bg-canvas/85 px-4 backdrop-blur md:px-8">
          <div className="min-w-0 truncate text-sm font-semibold">{title}</div>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
        <main className="flex-1 px-4 py-6 md:px-8">{children}</main>
      </div>
    </div>
  );
}
