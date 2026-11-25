import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import LaunchBar from './LaunchBar.jsx';
import AccountMenu from './AccountMenu.jsx';

// Top navigation. `base` is '' on the landing page (links scroll in place) and '/' on other pages
// (links go back to the landing section). `solid` gives the bar a white background straight away.
export default function Navbar({ base = '', solid = false }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    document.body.classList.toggle('menu-open', open);
    return () => document.body.classList.remove('menu-open');
  }, [open]);

  const close = () => setOpen(false);
  const href = (hash) => `${base}${hash}`;

  return (
    <div className="fixed">
      <LaunchBar />
      <header role="banner" className="navbar" style={solid ? { background: '#fff' } : undefined}>
        <div className="container">
          <div className="navbar-holder">
            {base ? (
              <Link to="/" className="brand" aria-label="ClipHeist home">
                <span className="brand-mark"><svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 1.5v9l7-4.5z" fill="#fffa8d"/></svg></span>ClipHeist
              </Link>
            ) : (
              <a href="#top" className="brand" aria-label="ClipHeist home">
                <span className="brand-mark"><svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 1.5v9l7-4.5z" fill="#fffa8d"/></svg></span>ClipHeist
              </a>
            )}
            <nav role="navigation" className="nav-menu" aria-label="Main" onClick={(e) => { if (e.target.closest('a')) close(); }}>
              <div className="nav-links">
                <div className="nav-link-wrapper"><a href={href('#about')} className="nav-link">What it does</a><div className="nav-line"></div></div>
                <div className="nav-link-wrapper"><a href={href('#clips')} className="nav-link">Clips</a><div className="nav-line"></div></div>
                <div className="nav-link-wrapper"><a href={href('#how')} className="nav-link">How it works</a><div className="nav-line"></div></div>
                <div className="nav-link-wrapper"><Link to="/pricing" className="nav-link">Pricing</Link><div className="nav-line"></div></div>
                <div className="nav-link-wrapper"><a href={href('#faq')} className="nav-link">FAQ</a><div className="nav-line"></div></div>
                <AccountMenu />
              </div>
            </nav>
            <button
              className="menu-button"
              aria-label={open ? 'Close menu' : 'Open menu'}
              aria-expanded={open}
              onClick={() => setOpen((o) => !o)}
            ><span></span><span></span><span></span></button>
          </div>
        </div>
      </header>
    </div>
  );
}
