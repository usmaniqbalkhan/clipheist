// Google Identity Services ("Sign in with Google" button) on our own domain.
// Google shows "clipheist.site" instead of the Supabase address, and there is no page redirect:
// the ID token goes straight to Supabase with signInWithIdToken.
import { supabase } from './supabase.js';

const CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID;
let loading = null;

export const googleAvailable = () => !!(CLIENT_ID && supabase);

function loadScript() {
  if (window.google?.accounts?.id) return Promise.resolve();
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'https://accounts.google.com/gsi/client';
      s.async = true;
      s.onload = () => resolve();
      s.onerror = () => { loading = null; reject(new Error('Google sign-in could not load.')); };
      document.head.appendChild(s);
    });
  }
  return loading;
}

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

// Draws Google's button inside `el`. Resolves the Supabase session through onSignedIn / onError.
export async function renderGoogleButton(el, { onSignedIn, onError, width = 320 }) {
  await loadScript();
  const raw = crypto.randomUUID() + crypto.randomUUID();
  const hashed = await sha256Hex(raw);
  window.google.accounts.id.initialize({
    client_id: CLIENT_ID,
    nonce: hashed,
    use_fedcm_for_prompt: true,
    callback: async ({ credential }) => {
      const { data, error } = await supabase.auth.signInWithIdToken({ provider: 'google', token: credential, nonce: raw });
      if (error) onError?.(error); else onSignedIn?.(data.session);
    },
  });
  el.replaceChildren();
  window.google.accounts.id.renderButton(el, {
    type: 'standard', theme: 'filled_black', size: 'large', shape: 'pill', text: 'continue_with', logo_alignment: 'left', width,
  });
}
