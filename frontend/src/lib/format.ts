import { useEffect, useState } from "react";

/** "Just now", "5 min ago", "2h ago", "Yesterday", "3d ago", else a date. */
export function relativeTime(iso: string, now: number): string {
  const minutes = Math.floor((now - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** "Today, 10:42 AM", "Yesterday, 4:05 PM", or "Oct 6, 10:42 AM". */
export function dayLabel(iso: string, now: number): string {
  const date = new Date(iso);
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const daysAgo = Math.round((startOfDay(new Date(now)) - startOfDay(date)) / 86_400_000);
  const day =
    daysAgo === 0 ? "Today" : daysAgo === 1 ? "Yesterday" : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return `${day}, ${clockTime(iso)}`;
}

export function sameDay(a: string, b: string): boolean {
  return new Date(a).toDateString() === new Date(b).toDateString();
}

/** The current time, refreshed every `everyMs`, for relative timestamps. */
export function useNow(everyMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), everyMs);
    return () => window.clearInterval(id);
  }, [everyMs]);
  return now;
}
