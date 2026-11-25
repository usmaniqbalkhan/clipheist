import Navbar from '../components/Navbar.jsx';
import Footer from '../components/Footer.jsx';
import { SmoothAnchors } from '../components/useSmoothAnchors.js';
import { APP_HOME } from '../components/AuthContext.jsx';
import '../styles/pricing.css';
import '../styles/legal.css';

// The privacy policy for the website and the clipping app. Keep it true: when a provider or a kind of data changes,
// change it here too (and the "Last updated" date).
export const CONTACT = 'contactusmaniqbal@gmail.com';
const UPDATED = '25 November 2026';

const mail = <a href={`mailto:${CONTACT}`}>{CONTACT}</a>;

const SECTIONS = [
  ['What we collect', (
    <ul>
      <li><b>Account.</b> When you sign in with Google we receive your name, email address, profile picture and Google account ID. There are no passwords: Google signs you in.</li>
      <li><b>Plan and clips.</b> Your plan, how many clips you have and how many you have used. If you buy clips, our payment provider tells us the order, the plan and the email address used. We never see or store your card details.</li>
      <li><b>What you give us to clip.</b> Links you paste, files you upload, the streamers and channel links you add, and your settings.</li>
      <li><b>What we make from it.</b> Downloaded video, transcripts, loudness and chat-activity figures, clips, titles, captions and scores.</li>
      <li><b>Public channel details.</b> For channels you add: the public name, picture, follower count and live status from YouTube, Twitch or Kick.</li>
      <li><b>Connected accounts.</b> An access token from YouTube, only if you connect your channel to post clips.</li>
      <li><b>AI usage.</b> A record of each AI check made for your account: when, what for and how many tokens it used.</li>
      <li><b>Technical data.</b> Session records with IP address and browser, and server logs, used for security and to keep the service running. The website counts page views without cookies and without identifying you.</li>
    </ul>
  )],
  ['Why we use it', (
    <>
      <ul>
        <li>To provide the service you asked for: downloading, recording, transcribing, judging, cutting, captioning and posting clips.</li>
        <li>To keep track of your plan and clips, and to apply a purchase to your account.</li>
        <li>To keep accounts secure, prevent fraud and abuse, and enforce our terms.</li>
      </ul>
      <p>We do not sell your information, do not use it for advertising, and do not use your videos or clips to train AI models.</p>
    </>
  )],
  ['Who else handles it', (
    <>
      <ul>
        <li><b>Google</b> signs you in.</li>
        <li><b>Supabase</b> stores accounts, plans and clip balances.</li>
        <li><b>Vercel</b> hosts this website; <b>Amazon Web Services</b> hosts the clipping app and stores your videos and clips.</li>
        <li><b>Modal</b> runs the machines that turn speech into text and render clips. They receive the audio and the stretch of video being worked on, and keep nothing afterwards.</li>
        <li><b>Our AI provider</b> (currently DeepSeek) receives transcript text and loudness and chat figures for the moments being judged, to choose clips and write titles and captions.</li>
        <li><b>Polar</b> takes payments, as the merchant of record.</li>
        <li><b>YouTube, Twitch and Kick</b> receive the requests needed to fetch content and public channel details and, when you choose, the clips you post.</li>
        <li><b>Authorities or others</b> where the law requires it, or to protect our rights, users or the public.</li>
      </ul>
      <p>Some of these providers process data outside your country. Where required, we rely on appropriate safeguards such as standard contractual clauses.</p>
    </>
  )],
  ['Google user data', (
    <>
      <p>ClipHeist's use and transfer of information received from Google APIs adheres to the{' '}
        <a href="https://developers.google.com/terms/api-services-user-data-policy" target="_blank" rel="noopener noreferrer">Google API Services User Data Policy</a>, including the Limited Use requirements.</p>
      <p>With Sign in with Google we receive only your name, email address, profile picture and Google account ID, to create and sign in to your account. If you connect YouTube, we use that access only to upload the clips you choose to your channel. We do not use Google user data for advertising, do not sell it, and do not let humans read it except with your permission, for security, or where the law requires. You can remove access in your Google account's security settings at any time.</p>
    </>
  )],
  ['Cookies and browser storage', (
    <p>This website keeps your sign-in in your browser's own storage, and the clipping app uses one essential cookie to keep you signed in. Neither is used for tracking. We use no advertising cookies, so there is no consent banner.</p>
  )],
  ['How long we keep it', (
    <ul>
      <li>Account, plan and order records: while your account is open, and afterwards for as long as the law requires for payment records.</li>
      <li>Videos, transcripts and clips: until you delete them or your account.</li>
      <li>Live-stream recordings: only in a short rolling buffer while recording, then deleted. The clips made from them are kept like any other clip.</li>
      <li>Security logs: for a limited period, then deleted.</li>
    </ul>
  )],
  ['Your rights', (
    <p>Depending on where you live, you can ask to access, correct, delete or receive a copy of your data, object to or restrict some uses, and complain to your data protection authority. You can delete videos and clips in the app at any time. To delete your account or for anything else, write to {mail}. We may need to confirm your identity first.</p>
  )],
  ['Security', (
    <p>Connections are encrypted, each account's data is kept separate, and access is limited. No system is perfectly secure; if a breach affects you, we will tell you as the law requires.</p>
  )],
  ['Children', (
    <p>ClipHeist is not meant for children under 13, or under 16 where local law sets that age. We do not knowingly collect their information and will delete it if we learn of it.</p>
  )],
  ['Changes', (
    <p>We will announce material changes on this page, in the app or by email before they take effect.</p>
  )],
  ['Contact', (
    <p>Privacy questions: {mail}. The terms of service are <a href={`${APP_HOME}/terms`}>here</a>.</p>
  )],
];

export default function Privacy() {
  return (
    <div className="pricing-page legal-page">
      <SmoothAnchors />
      <Navbar base="/" solid />
      <main id="top">
        <section className="section pr-hero">
          <div className="container">
            <article className="legal">
              <div className="paragraph-title">Legal</div>
              <h1>Privacy Policy</h1>
              <p className="legal-date">Last updated {UPDATED}</p>
              <p>This policy explains what ClipHeist ("we") collects when you use this website and the ClipHeist clipping app, why, who else handles it, how long we keep it and the choices you have. We act as the controller of this information.</p>
              {SECTIONS.map(([title, body]) => (
                <section key={title}>
                  <h2>{title}</h2>
                  {body}
                </section>
              ))}
            </article>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
