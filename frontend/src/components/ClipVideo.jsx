import { useEffect, useRef } from 'react';

// A short muted looping clip inside a phone card. Shows the still image first, loads the video
// only when the card is near the screen, and pauses again when it scrolls away.
export default function ClipVideo({ name, label, className = 'clip-img' }) {
  const ref = useRef(null);
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;   // keep the still image
    const io = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) {
        if (!v.src) v.src = `/videos/${name}.mp4`;
        v.play().catch(() => {});
      } else {
        v.pause();
      }
    }, { rootMargin: '200px' });
    io.observe(v);
    return () => io.disconnect();
  }, [name]);
  return <video ref={ref} className={className} poster={`/images/${name}.webp`} aria-label={label} muted loop playsInline preload="none" />;
}
