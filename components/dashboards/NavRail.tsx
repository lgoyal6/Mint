"use client";

import { Activity, CalendarDays, ChartNoAxesCombined, CheckSquare, Gauge, LogOut, MessageCircle, Search } from "lucide-react";
import type { ComponentType } from "react";

import { cn } from "@/lib/utils";

export type AppScreen = "chat" | "events" | "pipeline" | "review" | "health" | "usage" | "source";

type NavRailProps = {
  activeScreen: AppScreen;
  reviewCount: number;
  onSelect: (screen: AppScreen) => void;
  onLogout: () => void;
};

const items: Array<{
  screen: AppScreen;
  label: string;
  icon: ComponentType<{ size?: number }>;
}> = [
  { screen: "chat", label: "Chat", icon: MessageCircle },
  { screen: "events", label: "Events", icon: CalendarDays },
  { screen: "pipeline", label: "Pipeline", icon: ChartNoAxesCombined },
  { screen: "review", label: "Review", icon: CheckSquare },
  { screen: "health", label: "Health", icon: Activity },
  { screen: "usage", label: "Usage", icon: Gauge },
  { screen: "source", label: "Source", icon: Search },
];

export function NavRail({ activeScreen, reviewCount, onSelect, onLogout }: NavRailProps) {
  return (
    <aside className="flex w-[88px] shrink-0 flex-col items-center border-r border-stone-200/80 bg-stone-100/90 py-3">
      <div className="mb-5 flex h-9 w-9 items-center justify-center rounded-xl border border-stone-200 bg-canvas text-sm font-semibold shadow-hairline">
        m
      </div>
      <nav className="flex flex-1 flex-col items-center gap-2" aria-label="Primary navigation">
        {items.map(({ screen, label, icon: Icon }) => {
          const active = activeScreen === screen;
          return (
            <button
              key={screen}
              className={cn(
                "group relative flex h-14 w-16 flex-col items-center justify-center gap-1 rounded-xl border text-stone-500 transition-colors",
                active
                  ? "border-accent-line bg-accent-soft text-accent"
                  : "border-transparent hover:bg-stone-200/70 hover:text-stone-950",
              )}
              onClick={() => onSelect(screen)}
              aria-label={label}
            >
              <Icon size={18} />
              <span className="text-[11px] font-medium leading-none">{label}</span>
              {screen === "review" && reviewCount > 0 ? (
                <span className="absolute right-1 top-1 min-w-5 rounded-full border border-canvas bg-accent px-1 text-center font-mono text-[10px] leading-5 text-stone-50">
                  {reviewCount > 99 ? "99+" : reviewCount}
                </span>
              ) : null}
            </button>
          );
        })}
      </nav>
      <button
        className="flex h-14 w-16 flex-col items-center justify-center gap-1 rounded-xl border border-transparent text-stone-500 transition-colors hover:bg-stone-200/70 hover:text-stone-950"
        onClick={onLogout}
        aria-label="Log out"
      >
        <LogOut size={18} />
        <span className="text-[11px] font-medium leading-none">Log out</span>
      </button>
    </aside>
  );
}
