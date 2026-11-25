import { useEffect, useState } from 'react';

// GTA 6 launch: local midnight, 19 Nov 2026 (same as the original page).
export const GTA_LAUNCH = new Date(2026, 10, 19, 0, 0, 0).getTime();
// Launch deal ends Sun 22 Nov 2026, 11:59 PM Pacific (= 23 Nov 07:59 UTC).
export const DEAL_ENDS = Date.UTC(2026, 10, 23, 7, 59, 0);

const pad = (n) => String(n).padStart(2, '0');

export function useCountdown(target) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  let diff = Math.max(0, target - now);
  const d = Math.floor(diff / 864e5); diff -= d * 864e5;
  const h = Math.floor(diff / 36e5); diff -= h * 36e5;
  const m = Math.floor(diff / 6e4); diff -= m * 6e4;
  const s = Math.floor(diff / 1e3);
  return { d: pad(d), h: pad(h), m: pad(m), s: pad(s), over: target - now <= 0 };
}

// "02d 14h 22m 08s", or "Out now" once the moment has passed.
export function CountdownText({ target = GTA_LAUNCH, overText = 'Out now', className }) {
  const c = useCountdown(target);
  return <div className={className}>{c.over ? overText : `${c.d}d ${c.h}h ${c.m}m ${c.s}s`}</div>;
}

// Four boxes: Days / Hours / Mins / Secs.
export function CountdownBoxes({ target = GTA_LAUNCH, className, label }) {
  const c = useCountdown(target);
  return (
    <div className={className} aria-label={label}>
      <div><b>{c.d}</b><span>Days</span></div>
      <div><b>{c.h}</b><span>Hours</span></div>
      <div><b>{c.m}</b><span>Mins</span></div>
      <div><b>{c.s}</b><span>Secs</span></div>
    </div>
  );
}
