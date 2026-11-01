import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type BadgeTone = "neutral" | "accent" | "p0" | "p1" | "p2" | "nr" | "np" | "warn";

const tones: Record<BadgeTone, string> = {
  neutral: "border-stone-300 bg-stone-100 text-stone-700",
  accent: "border-accent-line bg-accent-soft text-accent",
  p0: "border-emerald-300 bg-emerald-50 text-emerald-800",
  p1: "border-amber-300 bg-amber-50 text-amber-800",
  p2: "border-sky-300 bg-sky-50 text-sky-800",
  nr: "border-stone-300 bg-stone-100 text-stone-600",
  np: "border-stone-300 bg-stone-100 text-stone-500",
  warn: "border-orange-300 bg-orange-50 text-orange-800",
};

export function Badge({
  tone = "neutral",
  children,
  className,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium", tones[tone], className)}>
      {children}
    </span>
  );
}
