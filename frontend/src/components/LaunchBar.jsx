import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { DEAL_ENDS, useCountdown } from './Countdown.jsx';

// Thin launch-deal bar above the navbar on every page. Waits for the landing intro to finish
// (while <html> has .is-loading) and disappears by itself once the deal ends.
export default function LaunchBar() {
  const c = useCountdown(DEAL_ENDS);
  const [introDone, setIntroDone] = useState(() => !document.documentElement.classList.contains('is-loading'));

  useEffect(() => {
    const html = document.documentElement;
    const check = () => setIntroDone(!html.classList.contains('is-loading'));
    check();
    const mo = new MutationObserver(check);
    mo.observe(html, { attributes: true, attributeFilter: ['class'] });
    return () => mo.disconnect();
  }, []);

  const show = introDone && !c.over;
  useEffect(() => {
    document.documentElement.style.setProperty('--bar-h', show ? '36px' : '0px');
    return () => document.documentElement.style.setProperty('--bar-h', '0px');
  }, [show]);

  if (!show) return null;
  return (
    <Link to="/pricing" className="launch-bar" aria-label="Launch price, 40% off. See plans">
      <span className="lb-pill">Launch price · 40% off</span>
      <span className="lb-ends">Ends Sun 22 Nov, 11:59 PM PT</span>
      <span className="lb-clock">{c.d}d {c.h}h {c.m}m {c.s}s</span>
      <span className="lb-go">See plans →</span>
    </Link>
  );
}
