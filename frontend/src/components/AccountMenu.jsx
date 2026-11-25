import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { goToApp, useAuth, PLAN_NAMES, clipsLeft } from './AuthContext.jsx';

// Navbar account control. Signed out: "Sign in" (Google) + the yellow "Start clipping" button.
// Signed in: a chip with plan and clips left; clicking it opens a small menu with the email and "Sign out".
export default function AccountMenu() {
  const auth = useAuth();
  const [open, setOpen] = useState(false);
  const box = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', esc); };
  }, [open]);

  if (!auth?.user) {
    return (
      <>
        <div className="nav-link-wrapper">
          <button type="button" className="nav-link nav-signin" onClick={() => auth?.signIn()} disabled={!auth?.ready}>Sign in</button>
          <div className="nav-line"></div>
        </div>
        <div className="nav-link-wrapper"><a href="#waitlist" className="nav-cta js-to-waitlist">Start clipping</a></div>
      </>
    );
  }

  const p = auth.profile;
  const meta = auth.user.user_metadata || {};
  const name = meta.full_name || meta.name || auth.user.email;
  const initials = (name.includes('@') ? name.slice(0, 2) : name.split(/\s+/).map((w) => w[0]).join('').slice(0, 2)).toUpperCase();
  const photo = meta.avatar_url || meta.picture;
  const Avatar = ({ size }) => (photo
    ? <img className={`account-avatar ${size}`} src={photo} alt="" referrerPolicy="no-referrer" />
    : <span className={`account-avatar ${size}`} aria-hidden="true">{initials.charAt(0)}{size === 'big' ? initials.charAt(1) : ''}</span>);

  return (
    <>
    <div className="nav-link-wrapper">
      <button type="button" className="nav-link nav-signin nav-app" onClick={() => goToApp()}>Open app ↗</button>
      <div className="nav-line"></div>
    </div>
    <div className="nav-link-wrapper account" ref={box}>
      <button type="button" className="nav-cta nav-cta-in account-chip" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Avatar size="small" />
        <span>{p ? `${PLAN_NAMES[p.plan]} · ${clipsLeft(p)} clips` : 'Account'}</span>
      </button>
      {open && (
        <div className="account-card" role="menu">
          <Avatar size="big" />
          <div className="account-name">{name}</div>
          {name !== auth.user.email && <div className="account-email">{auth.user.email}</div>}
          <div className="account-plan-line">
            <span className="account-plan-pill">{p ? PLAN_NAMES[p.plan] : '…'}</span>
            <span>{p ? `${clipsLeft(p)} clips left` : ''}</span>
          </div>
          <div className="account-divider" />
          <Link to="/pricing" role="menuitem" className="account-item" onClick={() => setOpen(false)}>
            {p && p.plan !== 'free' ? 'Upgrade or add clips' : 'Get a plan'}
          </Link>
          <button type="button" role="menuitem" className="account-item" onClick={() => { setOpen(false); goToApp(); }}>Open the clipping app ↗</button>
          <div className="account-divider" />
          <button type="button" role="menuitem" className="account-item" onClick={() => { setOpen(false); auth.signOut(); }}>Sign out</button>
          <div className="account-foot">© 2026 ClipHeist · <Link to="/pricing" onClick={() => setOpen(false)}>Pricing</Link></div>
        </div>
      )}
    </div>
    </>
  );
}
