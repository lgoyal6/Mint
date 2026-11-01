"use client";

import { LockKeyhole } from "lucide-react";
import { FormEvent, useState } from "react";

import { Button } from "@/components/ui/Button";
import { login } from "@/lib/api";


export function LoginGate({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!password.trim() || isSubmitting) return;
    setError(null);
    setIsSubmitting(true);
    try {
      await login(password);
      onAuthenticated();
    } catch {
      setError("That password did not work.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center bg-canvas px-6 text-ink">
      <section className="w-full max-w-sm rounded-xl border border-stone-200 bg-stone-50/80 p-6 shadow-[0_18px_50px_rgba(28,25,23,0.05)]">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-stone-200 bg-white text-accent">
            <LockKeyhole size={17} />
          </div>
          <div>
            <h1 className="text-base font-semibold text-stone-950">mint</h1>
            <p className="text-sm text-stone-500">Enter the shared workspace password.</p>
          </div>
        </div>
        <form className="space-y-3" onSubmit={handleSubmit}>
          <input
            className="h-11 w-full rounded-lg border border-stone-200 bg-white px-3 text-sm outline-none transition focus:border-accent/60 focus:ring-2 focus:ring-accent/10"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Password"
            autoComplete="current-password"
            autoFocus
          />
          {error ? <p className="text-sm text-red-700">{error}</p> : null}
          <Button className="h-10 w-full justify-center" type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Signing in..." : "Sign in"}
          </Button>
        </form>
      </section>
    </main>
  );
}
