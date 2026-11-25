import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { renderGoogleButton } from '../lib/google.js';
import { goToApp, useAuth } from './AuthContext.jsx';
import useSpots from './useSpots.js';

// Sign in with Google (popup, no redirect). Signing in creates the profile with 3 free clips.
// toApp ("Start clipping"): then straight to the clipping app. Otherwise (buying a plan): on to the pricing step.
export default function JoinModal({ onClose, toApp = false }) {
  const auth = useAuth();
  const navigate = useNavigate();
  const spots = useSpots();
  const [error, setError] = useState('');
  const btn = useRef(null);

  useEffect(() => {
    const esc = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', esc);
    document.body.style.overflow = 'hidden';
    return () => { document.removeEventListener('keydown', esc); document.body.style.overflow = ''; };
  }, [onClose]);

  useEffect(() => {
    if (!btn.current) return;
    renderGoogleButton(btn.current, {
      width: Math.min(340, btn.current.clientWidth || 340),
      onSignedIn: () => {
        onClose();
        if (toApp) goToApp();
        else navigate('/pricing?step=plan');
      },
      onError: () => setError('Google sign-in did not finish. Please try again.'),
    }).catch(() => setError('Google sign-in could not load. Check your connection, or use the button below.'));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const left = spots?.left;
  const pct = spots ? Math.round((spots.taken / spots.total) * 100) : 0;

  return (
    <div className="jm-backdrop" onPointerDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="jm" role="dialog" aria-modal="true" aria-labelledby="jm-title">
        <button type="button" className="jm-close" aria-label="Close" onClick={onClose}>×</button>
        <div className="jm-steps"><span className="on">1 · Sign in</span><span>{toApp ? '2 · Paste a stream link' : '2 · Pick your plan'}</span></div>
        <h2 id="jm-title">{toApp ? 'Sign in to start clipping.' : 'Get in before GTA 6 drops.'}</h2>
        <p className="jm-lead">{toApp
          ? 'Your first 3 clips are free, no card needed. One sign-in and you go straight to your clips.'
          : 'Sign in with Google, then pay once at the 40% launch price. No subscription.'}</p>

        <div className="jm-spots" aria-live="polite">
          <div className="jm-spots-text">
            <b className="jm-left">{left === undefined ? '…' : left === 0 ? 'Full' : `${left} spots left`}</b>
          </div>
          <div className="jm-bar"><i style={{ width: `${pct}%` }} /></div>
        </div>

        <div className="jm-google" ref={btn} />
        {error && (
          <div className="jm-error" role="alert">
            {error}{' '}
            <button type="button" className="link-button" onClick={() => auth.signInRedirect({ toApp })}>Sign in another way</button>
          </div>
        )}
        <p className="jm-small center">We only get your name and email from Google. No spam.</p>
      </div>
    </div>
  );
}
