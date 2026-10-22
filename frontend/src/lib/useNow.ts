import { useEffect, useState } from 'react';

/** The current time, re-read every `intervalMs`. `clock` is injected in tests. */
export function useNow(intervalMs: number, clock: () => Date = () => new Date()): Date {
  const [now, setNow] = useState<Date>(clock);
  useEffect(() => {
    const timer = setInterval(() => setNow(clock()), intervalMs);
    return () => clearInterval(timer);
  }, [clock, intervalMs]);
  return now;
}
