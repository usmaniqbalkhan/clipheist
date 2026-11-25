import { useSearchParams } from 'react-router-dom';
import Navbar from '../components/Navbar.jsx';
import Footer from '../components/Footer.jsx';
import { DEAL_ENDS, useCountdown } from '../components/Countdown.jsx';
import { SmoothAnchors } from '../components/useSmoothAnchors.js';
import { useAuth, PLAN_NAMES, clipsLeft } from '../components/AuthContext.jsx';
import useSpots from '../components/useSpots.js';
import '../styles/pricing.css';

const PLANS = [
  {
    name: 'Starter',
    normal: 25,
    launch: 15,
    clips: 50,
    who: 'Try it on your streams',
    checkout: 'https://buy.polar.sh/polar_cl_skDmEUMEYsVHbUBnFFDmeIvml0sTtF6HHQXqY4KKx0V',
    features: ['50 clips', 'Captions, hook and title on every clip', 'Download and post yourself'],
  },
  {
    name: 'Pro',
    normal: 59,
    launch: 35,
    clips: 150,
    who: 'Post every day',
    checkout: 'https://buy.polar.sh/polar_cl_Qa5zkDY2XU18Dq8svVMbEaryRM1eCBQDguumf2EsgEd',
    features: ['150 clips', 'Everything in Starter', 'HD 1080p clips'],
  },
  {
    name: 'Premium',
    normal: 119,
    launch: 69,
    clips: 500,
    who: 'Streamers and clippers going all in',
    checkout: 'https://buy.polar.sh/polar_cl_h7ZpBiNtiWdV7o2rtsNi7TF0ad6PWmnHjlh3T2iVleY',
    badge: 'Best value',
    features: ['500 clips', 'Everything in Pro', 'Auto-post to YouTube Shorts', 'Paste many links at once'],
  },
];

const FAQ = [
  ['Is this a subscription?', 'No. You pay once and get your clips. Nothing renews, nothing to cancel.'],
  ['What happens after the launch deal?', 'The launch price ends Sunday 22 November, 11:59 PM PT. After that the plans go back to their normal price.'],
  ['What counts as a clip?', 'One finished vertical clip, ready to post. We make about 6 to 10 clips from each stream, and a stream can be up to 4 hours long.'],
  ['When can I start clipping?', 'Right now. Sign in at app.clipheist.site with the same Google account and paste a stream link.'],
  ['Can I get a refund?', 'Yes. Within 48 hours of buying, or if you have used fewer than 10 clips, just ask and we refund you.'],
];

const Arrow = () => (
  <span className="button-icon" aria-hidden="true">
    <svg viewBox="0 0 14 14"><path d="M3 11L11 3M11 3H4.5M11 3v6.5" stroke="#000" strokeWidth="1.6" fill="none" strokeLinecap="round" /></svg>
  </span>
);

const Check = () => (
  <svg className="pr-check" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 5.2l2 2 4-4.4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
);

function DealTimer() {
  const c = useCountdown(DEAL_ENDS);
  if (c.over) {
    return (
      <div className="pr-deal pr-deal-over">
        <div className="small-text">Launch deal ended</div>
        <p>Plans are back at their normal price.</p>
      </div>
    );
  }
  return (
    <div className="pr-deal" role="timer" aria-label="Time left on the launch price">
      <div className="pr-deal-head">
        <span className="pr-deal-pill">Launch price · 40% off</span>
        <span className="small-text">Ends Sun 22 Nov, 11:59 PM PT</span>
      </div>
      <div className="pr-deal-clock">
        {[['d', 'Days'], ['h', 'Hours'], ['m', 'Mins'], ['s', 'Secs']].map(([k, label]) => (
          <div key={k}><b>{c[k]}</b><span>{label}</span></div>
        ))}
      </div>
    </div>
  );
}

function ThanksBanner({ plan }) {
  return (
    <div className="pr-thanks" role="status">
      <strong>Payment received. Thank you!</strong>
      <p>Your {plan ? plan[0].toUpperCase() + plan.slice(1) : ''} clips are ready. Open app.clipheist.site and sign in with Google using the same email. Your receipt is in your inbox.</p>
    </div>
  );
}

// Signed in: pre-fill the checkout with the Google email, so the purchase lands on this account.
function checkoutUrl(plan, email) {
  if (!email) return plan.checkout;
  const u = new URL(plan.checkout);
  u.searchParams.set('customer_email', email);
  return u.toString();
}

// Pre-order button: you must be signed in first (so the order lands on your account).
// Signed in: remember which plan you picked, then open Polar checkout with your email filled in.
// Shown right after "Join the waitlist" + Google sign-in (step 2 of 2).
function StepBanner({ name }) {
  const spots = useSpots();
  return (
    <div className="pr-step" role="status">
      <div className="pr-step-top">
        <span className="pr-step-pill">Step 2 of 2</span>
        {spots && <span className="pr-step-spots"><b>{spots.left}</b> early-access spots left</span>}
      </div>
      <strong>{name ? `You're signed in, ${name.split(' ')[0]}.` : "You're signed in."} Pick a plan to lock the launch price.</strong>
      <p>Pay the 40% launch price today and start clipping right away. 48-hour refund if you change your mind.</p>
    </div>
  );
}

function PlanCard({ plan, dealOn, email, current, auth }) {
  const price = dealOn ? plan.launch : plan.normal;
  const go = async (e) => {
    e.preventDefault();
    if (!auth?.user) { auth?.signIn(); return; }
    const url = checkoutUrl(plan, email);
    try { await Promise.race([auth.savePlanChoice(plan.name.toLowerCase()), new Promise((r) => setTimeout(r, 600))]); } catch { /* checkout matters more */ }
    window.location.href = url;
  };
  return (
    <article className={`pr-card${plan.badge ? ' featured' : ''}`}>
      <div className="pr-card-top">
        <h2 className="pr-name">{plan.name}</h2>
        {current ? <span className="pr-badge">Your plan</span> : plan.badge && <span className="pr-badge">{plan.badge}</span>}
      </div>
      <p className="pr-who">{plan.who}</p>
      <div className="pr-price">
        {dealOn && <s className="pr-was" aria-label={`Normal price $${plan.normal}`}>${plan.normal}</s>}
        <span className="pr-now">${price}</span>
        <span className="pr-once">one-time</span>
      </div>
      <ul className="pr-features">
        {plan.features.map((f) => <li key={f}><Check />{f}</li>)}
      </ul>
      {/* Polar checkout. The launch discount is attached to the link and stops applying by itself after the deadline. */}
      <a href={checkoutUrl(plan, email)} onClick={go} className={`button pr-cta${plan.badge ? '' : ' secondary'}`}>
        <div className="button-text-holder">
          <div className="button-text">Get {plan.name} · ${price}</div>
          <div className="button-text">Get {plan.name} · ${price}</div>
        </div>
        <Arrow />
      </a>
    </article>
  );
}

export default function Pricing() {
  const dealOn = !useCountdown(DEAL_ENDS).over;
  const [params] = useSearchParams();
  const paid = params.get('checkout') === 'success';
  const auth = useAuth();
  const email = auth?.user?.email;
  const myPlan = auth?.profile?.plan;
  const step = params.get('step') === 'plan' && !!email;
  const firstName = auth?.user?.user_metadata?.full_name || auth?.user?.user_metadata?.name;

  return (
    <div className="pricing-page">
      <SmoothAnchors />
      <Navbar base="/" solid />

      <main id="top">
        <section className="section pr-hero">
          <div className="container">
            {paid && <ThanksBanner plan={params.get('plan')} />}
            {step && !paid && <StepBanner name={firstName} />}
            <DealTimer />
            <div className="pr-heading">
              <div className="paragraph-title">Pricing</div>
              <h1>Pay once. Get your clips.</h1>
              <p className="pr-lead">No subscription. Pick how many clips you want, and keep posting all launch season.</p>
              <p className="pr-note">Open now. Pay once and start clipping today.</p>
            </div>

            {email && (
              <p className="pr-signed">
                Signed in as <b>{email}</b>{auth.profile ? <> · {PLAN_NAMES[auth.profile.plan]} · {clipsLeft(auth.profile)} clips</> : null}. Checkout will use this email.
              </p>
            )}
            <div className="pr-grid">
              {PLANS.map((p) => <PlanCard key={p.name} plan={p} dealOn={dealOn} email={email} current={myPlan === p.name.toLowerCase()} auth={auth} />)}
            </div>

            <div className="pr-trial">
              <div>
                <div className="small-text">Free trial</div>
                {email
                  ? <p>You have 3 free clips (small watermark). Get a plan above to lock the launch price before it ends.</p>
                  : <p>3 clips from one stream, with a small watermark. See your own moments before you pay.</p>}
              </div>
              {(
                <a href="#waitlist" className="button secondary js-to-waitlist">
                  <div className="button-text-holder"><div className="button-text">Try 3 free clips</div><div className="button-text">Try 3 free clips</div></div>
                </a>
              )}
            </div>

            <ul className="pr-all">
              <li><Check />No watermark on paid clips</li>
              <li><Check />One-time payment</li>
              <li><Check />48-hour refund</li>
              <li><Check />Works with Twitch, Kick and YouTube</li>
            </ul>
          </div>
        </section>

        <section className="section pr-faq">
          <div className="container">
            <div className="line"></div>
            <h2 className="h2-style pr-faq-title">Questions about pricing</h2>
            <div className="pr-faq-grid">
              {FAQ.map(([q, a]) => (
                <div className="testimonial-item" key={q}><div><h3>{q}</h3><p>{a}</p></div></div>
              ))}
            </div>
          </div>
        </section>
      </main>

      <Footer base="/" />
    </div>
  );
}
