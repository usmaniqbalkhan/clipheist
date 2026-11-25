import { useEffect, useRef } from 'react';
import Landing from './Landing.jsx';
import { goToApp, useAuth } from '../components/AuthContext.jsx';

// clipheist.site/start: the app sends signed-out people here. Signed in: straight back into the app.
// Not yet: the landing page with the Google sign-in window open, then on into the app.
export default function Start() {
  const auth = useAuth();
  const done = useRef(false);
  useEffect(() => {
    if (!auth?.ready || done.current) return;
    if (!auth.enabled) { done.current = true; goToApp(); return; }
    done.current = true;
    if (auth.user) goToApp();
    else auth.signIn({ toApp: true });
  }, [auth?.ready, auth?.user]); // eslint-disable-line react-hooks/exhaustive-deps
  return <Landing />;
}
