import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { APP_HOME, goToApp, useAuth } from './AuthContext.jsx';

// In-page links (#about, #how, ...) scroll smoothly.
// "Start clipping" links (.js-to-waitlist) open the clipping app, after Google sign-in if needed.
export const APP_URL = APP_HOME;

export default function useSmoothAnchors() {
  const auth = useAuth();
  const navigate = useNavigate();
  const ref = useRef({ auth, navigate });
  ref.current = { auth, navigate };

  useEffect(() => {
    const onClick = (e) => {
      const a = e.target.closest('a');
      if (!a) return;
      if (a.classList.contains('js-to-waitlist')) {
        e.preventDefault();
        const { auth: au } = ref.current;
        // Signed in: straight to the app. Not yet: Google sign-in first (creates the profile with 3 free clips).
        if (!au?.enabled || au.user) goToApp();
        else au.signIn({ toApp: true });
        return;
      }
      const id = a.getAttribute('href') || '';
      if (!id.startsWith('#')) return;
      if (id.length < 2) { e.preventDefault(); return; }
      const t = document.querySelector(id);
      if (!t) return;
      e.preventDefault();
      t.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
    document.addEventListener('click', onClick);
    return () => document.removeEventListener('click', onClick);
  }, []);
}

// Same behaviour as a component that renders nothing, so pages using it don't re-render when the login changes.
export function SmoothAnchors() {
  useSmoothAnchors();
  return null;
}
