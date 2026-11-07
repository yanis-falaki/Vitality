// `useNow()`: the current time in ms, updated every second (for countdowns).
import { useEffect, useState } from "react";

export function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}
