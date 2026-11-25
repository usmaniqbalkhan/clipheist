// Polar webhook -> Supabase orders. Deployed by Vercel at https://clipheist.site/api/polar-webhook
//
// Polar signs each event (Standard Webhooks). We verify the signature, then upsert the order into
// public.orders with the Supabase secret key. A database trigger recalculates the buyer's plan and clip
// credits (see supabase/schema.sql). Server-only env vars (set in Vercel, never VITE_):
//   POLAR_WEBHOOK_SECRET, SUPABASE_URL, SUPABASE_SECRET_KEY

// Fallback if a product's metadata is missing (IDs from docs/POLAR.md).
const PRODUCTS = {
  'ff997522-b4f2-4c33-a7d0-d98b238546fa': { plan: 'starter', clips: 50 },
  'a60e64fa-bc7f-4f4a-8be9-eee07a273737': { plan: 'pro', clips: 150 },
  'c83dfb79-4eaf-48e0-832e-c99a351f8d1e': { plan: 'premium', clips: 500 },
};
const TOLERANCE_SECONDS = 300;

const b64ToBytes = (s) => {
  try { return Uint8Array.from(atob(s), (c) => c.charCodeAt(0)); } catch { return null; }
};

// Standard Webhooks verification, same rules as Polar's SDK: HMAC-SHA256 over "id.timestamp.body",
// trying the secret as UTF-8 bytes and (if it decodes) as base64.
export async function verifySignature(body, headers, secret) {
  const id = headers.get('webhook-id'), ts = headers.get('webhook-timestamp'), sigs = headers.get('webhook-signature');
  if (!secret || !id || !ts || !sigs) return false;
  const t = Number(ts);
  if (!Number.isFinite(t) || Math.abs(Date.now() / 1000 - t) > TOLERANCE_SECONDS) return false;
  const content = new TextEncoder().encode(`${id}.${Math.floor(t)}.${body}`);
  const keys = [new TextEncoder().encode(secret)];
  const decoded = b64ToBytes(secret.startsWith('whsec_') ? secret.slice(6) : secret);
  if (decoded && decoded.length) keys.push(decoded);
  for (const raw of keys) {
    const key = await crypto.subtle.importKey('raw', raw, { name: 'HMAC', hash: 'SHA-256' }, false, ['verify']);
    for (const part of sigs.split(' ')) {
      const [version, sig] = part.split(',', 2);
      const bytes = version === 'v1' && sig ? b64ToBytes(sig) : null;
      if (bytes && await crypto.subtle.verify('HMAC', key, bytes, content)) return true;
    }
  }
  return false;
}

// Turn a Polar order into our orders row, or null if it isn't one of our plans.
export function toOrderRow(order, refunded) {
  const productId = order.product_id || order.product?.id;
  const meta = order.product?.metadata || {};
  const known = PRODUCTS[productId] || {};
  const plan = meta.plan || known.plan;
  const clips = Number(meta.clips || known.clips);
  const email = (order.customer?.email || order.customer_email || order.billing_email || '').toLowerCase();
  if (!plan || !clips || !email) return null;
  return {
    id: order.id,
    email,
    plan,
    clips,
    amount: order.total_amount ?? order.net_amount ?? order.amount ?? null,
    currency: order.currency || null,
    status: refunded || order.status === 'refunded' ? 'refunded' : 'paid',
    product_id: productId || null,
    polar_customer_id: order.customer_id || order.customer?.id || null,
    updated_at: new Date().toISOString(),
  };
}

async function upsertOrder(row) {
  const res = await fetch(`${process.env.SUPABASE_URL}/rest/v1/orders?on_conflict=id`, {
    method: 'POST',
    headers: {
      apikey: process.env.SUPABASE_SECRET_KEY,
      'Content-Type': 'application/json',
      Prefer: 'resolution=merge-duplicates,return=minimal',
    },
    body: JSON.stringify(row),
  });
  if (!res.ok) throw new Error(`Supabase ${res.status}: ${(await res.text()).slice(0, 300)}`);
}

export async function POST(request) {
  const body = await request.text();
  if (!(await verifySignature(body, request.headers, process.env.POLAR_WEBHOOK_SECRET))) {
    return new Response('invalid signature', { status: 403 });
  }
  let event;
  try { event = JSON.parse(body); } catch { return new Response('bad json', { status: 400 }); }

  const type = event?.type;
  const order = event?.data;
  const handled = type === 'order.paid' || type === 'order.refunded' || (type === 'order.updated' && order?.status === 'refunded');
  if (!handled) return new Response('ignored', { status: 202 });

  const row = toOrderRow(order, type === 'order.refunded');
  if (!row) {
    console.warn('polar-webhook: order without plan/clips/email', order?.id);
    return new Response('not a ClipHeist plan', { status: 202 });
  }
  try {
    await upsertOrder(row);
  } catch (err) {
    console.error('polar-webhook:', err.message);
    return new Response('storage error', { status: 500 });   // Polar retries on 5xx
  }
  return new Response('ok', { status: 202 });
}

export function GET() {
  return new Response('ClipHeist Polar webhook. POST only.', { status: 405 });
}
