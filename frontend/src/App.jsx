import { useEffect } from 'react';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import Landing from './pages/Landing.jsx';
import Pricing from './pages/Pricing.jsx';
import Start from './pages/Start.jsx';

const TITLES = {
  '/': 'ClipHeist · GTA 6 streams to ready-to-post clips',
  '/pricing': 'Pricing · ClipHeist',
  '/start': 'Start clipping · ClipHeist',
};

// New page: start at the top (or at the #section in the link), and set the tab title.
function RouteEffects() {
  const { pathname, hash } = useLocation();
  useEffect(() => {
    document.title = TITLES[pathname] || TITLES['/'];
    if (hash) {
      const t = setTimeout(() => document.querySelector(hash)?.scrollIntoView({ block: 'start' }), 60);
      return () => clearTimeout(t);
    }
    window.scrollTo(0, 0);
  }, [pathname, hash]);
  return null;
}

export default function App() {
  return (
    <>
      <RouteEffects />
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/pricing" element={<Pricing />} />
        <Route path="/start" element={<Start />} />
        <Route path="/pricing.html" element={<Navigate to="/pricing" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
