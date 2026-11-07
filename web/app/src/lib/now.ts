// The present as Home sees it: the server's clock (`/api/pipeline`'s `now`, which a fixture may fix to a
// day), carried forward by the browser's clock between fetches. Its local text keeps the server's
// offset, so "today" is the owner's day, as the model's own times are.
import { useEffect, useMemo, useState } from "react";
import { usePipeline } from "../api/client";

export interface Now {
  ms: number;          // epoch milliseconds
  local: string;       // "2026-11-04T23:00:00-05:00": the wall clock at the server's offset
  today: string;       // "2026-11-04"
}

/** The present (undefined until the server has answered); ticks every 30 seconds. */
export function useNow(): Now | undefined {
  const { data, dataUpdatedAt } = usePipeline();
  const [tick, setTick] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setTick(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);
  return useMemo(() => {
    if (!data) return undefined;
    const ms = Date.parse(data.now) + Math.max(0, tick - dataUpdatedAt);
    const local = atOffset(ms, data.now.match(/[+-]\d\d:\d\d$|Z$/)?.[0] ?? "Z");
    return { ms, local, today: local.slice(0, 10) };
  }, [data, dataUpdatedAt, tick]);
}

/** `ms` as local ISO text at `offset` ("-05:00" or "Z"). */
export function atOffset(ms: number, offset: string): string {
  const minutes = offset === "Z" ? 0 : (offset[0] === "-" ? -1 : 1) * (Number(offset.slice(1, 3)) * 60 + Number(offset.slice(4, 6)));
  return `${new Date(ms + minutes * 60_000).toISOString().slice(0, 19)}${offset === "Z" ? "+00:00" : offset}`;
}

/** "just now", "12 min ago", "3 h ago", "2 days ago": how long before `now` the time `ms` was. */
export function ago(ms: number, now: number): string {
  const minutes = Math.max(0, Math.round((now - ms) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return `${days} days ago`;
}
