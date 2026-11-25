import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { supabase } from '../lib/supabase.js';
import { googleAvailable } from '../lib/google.js';
import JoinModal from './JoinModal.jsx';

// Who is signed in (Google via Supabase) and their ClipHeist profile: plan and clip credits.
const AuthCtx = createContext(null);
const AFTER_LOGIN = 'ch-after-login';
const TO_APP = 'ch-to-app';
// Where the clipping app lives. VITE_APP_URL points a local run at a local backend (http://127.0.0.1:8765).
export const APP_HOME = (import.meta.env.VITE_APP_URL || 'https://app.clipheist.site').replace(/\/+$/, '');

// Into the clipping app, signed in: the app checks this Supabase session and opens its own, so nobody signs in twice.
// The token travels in the #fragment, which browsers never send to any server or log.
export async function goToApp(next = '/app') {
  let token = '';
  try { token = (await supabase?.auth.getSession())?.data?.session?.access_token || ''; } catch { /* no session */ }
  window.location.href = token ? `${APP_HOME}/sso#t=${encodeURIComponent(token)}&next=${encodeURIComponent(next)}` : APP_HOME;
}

export function AuthProvider({ children }) {
  const [session, setSession] = useState(null);
  const [profile, setProfile] = useState(null);
  const [ready, setReady] = useState(!supabase);
  const [joinOpen, setJoinOpen] = useState(false);
  const [toApp, setToApp] = useState(false);

  const loadProfile = useCallback(async (s) => {
    if (!supabase || !s?.user) { setProfile(null); return; }
    // The profile row is created by a database trigger on first sign-in; retry briefly in case it lags.
    for (let i = 0; i < 3; i++) {
      const { data } = await supabase.from('profiles').select('plan, clips_total, clips_used, full_name, email').eq('id', s.user.id).maybeSingle();
      if (data) { setProfile(data); return; }
      await new Promise((r) => setTimeout(r, 600));
    }
    setProfile(null);
  }, []);

  useEffect(() => {
    if (!supabase) return undefined;
    let alive = true;
    // Signed out in the app: sign out here too, so the two never disagree.
    const params = new URLSearchParams(window.location.search);
    if (params.has('signout')) {
      params.delete('signout');
      window.history.replaceState(null, '', window.location.pathname + (params.toString() ? `?${params}` : '') + window.location.hash);
      supabase.auth.signOut().finally(() => { if (alive) { setSession(null); setProfile(null); setReady(true); } });
      return () => { alive = false; };
    }
    supabase.auth.getSession().then(({ data }) => {
      if (!alive) return;
      setSession(data.session); setReady(true); loadProfile(data.session);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, s) => {
      setSession(s); setReady(true); loadProfile(s);
    });
    return () => { alive = false; sub.subscription.unsubscribe(); };
  }, [loadProfile]);

  // After coming back from Google: tidy the address bar and jump to where the user started.
  useEffect(() => {
    if (!session) return;
    const url = new URL(window.location.href);
    if (url.searchParams.has('code')) {
      url.searchParams.delete('code');
      window.history.replaceState(null, '', url.pathname + url.search + url.hash);
    }
    let y = null;
    let go = false;
    try {
      y = sessionStorage.getItem(AFTER_LOGIN); sessionStorage.removeItem(AFTER_LOGIN);
      go = sessionStorage.getItem(TO_APP) === '1'; sessionStorage.removeItem(TO_APP);
    } catch { /* storage blocked */ }
    if (go) { goToApp(); return; }
    if (y !== null && Number(y) > 0) setTimeout(() => window.scrollTo({ top: Number(y), behavior: 'instant' }), 500);
  }, [session]);

  // Fallback only (Google's button failed to load): full-page Google sign-in through Supabase,
  // coming back to the same page and scroll position.
  const signInRedirect = useCallback(async (opts = {}) => {
    if (!supabase) return;
    try {
      sessionStorage.setItem(AFTER_LOGIN, String(Math.round(window.scrollY)));
      if (opts.toApp) sessionStorage.setItem(TO_APP, '1');
    } catch { /* storage blocked */ }
    await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: window.location.origin + window.location.pathname },
    });
  }, []);

  // Normal path: open the join window (plan choice + Google button, no page redirect).
  // toApp: "Start clipping" was pressed, so go on to the clipping app once signed in.
  const signIn = useCallback((opts = {}) => {
    setToApp(!!opts.toApp);
    if (googleAvailable()) setJoinOpen(true);
    else signInRedirect(opts);
  }, [signInRedirect]);

  const savePlanChoice = useCallback(async (plan) => {
    if (!supabase || !plan) return;
    await supabase.rpc('set_interested_plan', { p_plan: plan });
  }, []);

  const signOut = useCallback(async () => {
    if (supabase) await supabase.auth.signOut();
    setProfile(null);
  }, []);

  const value = { ready, session, user: session?.user ?? null, profile, signIn, signInRedirect, signOut, savePlanChoice, enabled: !!supabase };
  return (
    <AuthCtx.Provider value={value}>
      {children}
      {joinOpen && <JoinModal toApp={toApp} onClose={() => setJoinOpen(false)} />}
    </AuthCtx.Provider>
  );
}

export const useAuth = () => useContext(AuthCtx);

export const PLAN_NAMES = { free: 'Free trial', starter: 'Starter', pro: 'Pro', premium: 'Premium' };
export const clipsLeft = (p) => (p ? Math.max(0, p.clips_total - p.clips_used) : 0);
