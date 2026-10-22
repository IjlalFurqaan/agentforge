"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Icon } from "@/components/icons";
import { Button, ErrorNote, Field, Input } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function LoginPage() {
  const { user, ready, login } = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const nextPath = () => {
    const next = new URLSearchParams(window.location.search).get("next");
    return next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
  };

  useEffect(() => {
    if (ready && user) router.replace(nextPath());
  }, [ready, user, router]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "register") await api.register(email, password, name);
      await login(email, password);
      router.replace(nextPath());
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="hidden flex-col justify-between bg-gradient-to-br from-indigo-950 via-slate-950 to-slate-900 p-12 text-slate-200 lg:flex">
        <div className="flex items-center gap-2 text-white">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-indigo-500">
            <Icon name="layers" className="h-4 w-4" />
          </div>
          <span className="font-semibold">AgentForge</span>
        </div>
        <div className="max-w-md space-y-6">
          <h1 className="text-3xl font-semibold leading-tight text-white">An autonomous software engineering team, with a human in the loop.</h1>
          <ul className="space-y-3 text-sm text-slate-300">
            {[
              ["spark", "Seven specialised agents plan, build, test, secure and review every change"],
              ["terminal", "All code runs in an isolated sandbox. Test results come from real execution"],
              ["shield", "Security scanning, revision loops and an audit trail for every decision"],
              ["pr", "Draft pull requests, opened only after you approve them"],
            ].map(([icon, text]) => (
              <li key={text} className="flex gap-3">
                <Icon name={icon as "spark"} className="mt-0.5 h-4 w-4 shrink-0 text-indigo-400" />
                {text}
              </li>
            ))}
          </ul>
        </div>
        <p className="text-xs text-slate-500">LangGraph · FastAPI · PostgreSQL · Docker sandbox · OpenTelemetry</p>
      </div>
      <div className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="w-full max-w-sm space-y-5">
          <div>
            <h2 className="text-xl font-semibold">{mode === "login" ? "Sign in" : "Create your account"}</h2>
            <p className="mt-1 text-sm text-muted">
              {mode === "login" ? "Welcome back." : "The first account on a new installation becomes the administrator."}
            </p>
          </div>
          {mode === "register" && (
            <Field label="Full name">
              <Input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
            </Field>
          )}
          <Field label="Email">
            <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          </Field>
          <Field label="Password" hint={mode === "register" ? "At least 12 characters, mixing three of: lowercase, uppercase, digits, symbols." : undefined}>
            <Input
              type="password"
              required
              minLength={mode === "register" ? 12 : 1}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
            />
          </Field>
          <ErrorNote error={error} />
          <Button type="submit" className="w-full" loading={busy}>
            {mode === "login" ? "Sign in" : "Create account"}
          </Button>
          <p className="text-center text-xs text-muted">
            {mode === "login" ? "No account yet?" : "Already registered?"}{" "}
            <button type="button" className="font-medium text-accent hover:underline" onClick={() => setMode(mode === "login" ? "register" : "login")}>
              {mode === "login" ? "Create one" : "Sign in"}
            </button>
          </p>
        </form>
      </div>
    </div>
  );
}
