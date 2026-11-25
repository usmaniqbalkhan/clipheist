import { useMemo, useRef } from 'react';
import { Link } from 'react-router-dom';
import { useGSAP } from '@gsap/react';
import Navbar from '../components/Navbar.jsx';
import Footer from '../components/Footer.jsx';
import SceneImg from '../components/SceneImg.jsx';
import ClipVideo from '../components/ClipVideo.jsx';
import { CountdownText } from '../components/Countdown.jsx';
import { SmoothAnchors } from '../components/useSmoothAnchors.js';
import { initLandingMotion } from '../lib/landingMotion.js';
import { useAuth } from '../components/AuthContext.jsx';

// Text that changes once the visitor is signed in (kept tiny so the animated page itself never re-renders).
function AuthLabel({ out, in: signedIn }) {
  const auth = useAuth();
  return auth?.user ? signedIn : out;
}

// "How it works" panel 2: the waveform with four highlighted moments.
function Wave() {
  const bars = useMemo(() => {
    const hot = [[16,19],[32,35],[45,47],[56,59]];
    return Array.from({ length: 72 }, (_, i) => {
      const isHot = hot.some(([a, z]) => i >= a && i <= z);
      const h = isHot ? 70 + Math.random() * 30 : 12 + Math.abs(Math.sin(i * .7)) * 30 + Math.random() * 18;
      return { h, isHot };
    });
  }, []);
  return <div className="p-wave">{bars.map((b, i) => <b key={i} className={b.isHot ? 'hot' : undefined} style={{ height: b.h + '%' }} />)}</div>;
}

export default function Landing() {
  const root = useRef(null);
  useGSAP(() => initLandingMotion(), { scope: root });

  return (
    <div ref={root}>
      <SmoothAnchors />
      {/* ============ LOADER ============ */}
      <div className="loader" aria-hidden="true">
        <div className="loader-inner">
          <div className="loader-top"><div className="loader-brand">ClipHeist</div><div className="loader-count">000</div></div>
          <div className="loader-track"><div className="loader-line"></div></div>
          <div className="loader-sub">Scanning stream for the good parts</div>
        </div>
      </div>

      <Navbar />

      <main id="top">
      {/* ============ HERO ============ */}
      <section className="hero">
        <div className="image_wrap" aria-hidden="true">
          <div className="content_img-wrap"><SceneImg scene="overpass" className="content_img" alt="" /></div>
          <div className="content_img-wrap"><SceneImg scene="dawn" className="content_img" alt="" /></div>
          <div className="content_img-wrap"><SceneImg scene="neon" className="content_img" alt="" /></div>
          <div className="content_img-wrap"><SceneImg scene="marina" className="content_img" alt="" /></div>
          <div className="content_img-wrap"><SceneImg scene="desert" className="content_img" alt="" /></div>
          <div className="content_img-wrap"><SceneImg scene="vault" className="content_img" alt="" /></div>
          <div className="content_img-wrap"><SceneImg scene="heli" className="content_img" alt="" /></div>
          <div className="content_img-wrap"><SceneImg scene="beach" className="content_img" alt="" /></div>
          <div className="content_img-wrap"><SceneImg scene="chase" className="content_img" alt="" /></div>
          <div className="content_img-wrap"><SceneImg scene="night" className="content_img" alt="" /></div>
              </div>

        <div className="container hero-container">
          <div className="hero-text-wrapper">
            <div className="logo-holder">
              <div className="logo-mask"><h1 className="logo-hero-text" aria-label="ClipHeist">ClipHeist</h1><div className="logo-hero-text logo-white" aria-hidden="true">ClipHeist</div></div>
              <div className="hero-links-holder">
                <div className="hero-content">
                  <div className="hero-content-holder">
                    <div className="animate-on-load"><div className="small-text label">What you get</div></div>
                    <div className="animate-on-load"><div className="small-text">Best moments, found</div></div>
                    <div className="animate-on-load"><div className="small-text">Vertical 9:16 clips</div></div>
                    <div className="animate-on-load"><div className="small-text">Zero editing</div></div>
                  </div>
                  <div className="hero-content-holder">
                    <div className="animate-on-load"><div className="small-text label">Auto-posted to</div></div>
                    <div className="animate-on-load"><div className="small-text">TikTok</div></div>
                    <div className="animate-on-load"><div className="small-text">YouTube Shorts</div></div>
                    <div className="animate-on-load"><div className="small-text">Instagram Reels</div></div>
                  </div>
                </div>
                <div className="hero-content no-gird">
                  <div className="hero-content-holder right">
                    <div className="animate-on-load"><div className="small-text label">GTA 6 launch</div></div>
                    <div className="animate-on-load"><div className="small-text">19 Nov 2026</div></div>
                    <div className="animate-on-load"><CountdownText className="small-text countdown" /></div>
                    <div className="animate-on-load"><a href="#waitlist" className="small-link js-to-waitlist"><div className="small-text">Try 3 free clips ↗</div></a></div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="hero-paragraph-holder">
            <div className="hero-holder">
              <div className="animate-on-load"><h2 className="h5-like no-margins" style={{fontSize: 'var(--h5)'}}>Drop your GTA 6 stream link. ClipHeist clips it and posts it.</h2></div>
            </div>
            <div className="hero-paragraph-container">
              <div className="animate-on-load"><p>ClipHeist finds the best moments in your stream, cuts them into vertical clips and posts them to TikTok, YouTube Shorts and Instagram Reels for you. No editing. Built for GTA 6 streamers and clippers.</p></div>
              <div className="hero-button-holder">
                <div className="animate-on-load">
                  <a href="#waitlist" className="button js-to-waitlist">
                    <div className="button-text-holder"><div className="button-text">Start clipping free</div><div className="button-text">Start clipping free</div></div>
                    <span className="button-icon" aria-hidden="true"><svg viewBox="0 0 14 14"><path d="M3 11L11 3M11 3H4.5M11 3v6.5" stroke="#000" strokeWidth="1.6" fill="none" strokeLinecap="round"/></svg></span>
                  </a>
                </div>
                <div className="animate-on-load">
                  <a href="#how" className="button secondary">
                    <div className="button-text-holder"><div className="button-text">How it works</div><div className="button-text">How it works</div></div>
                    <div className="red-circle"></div>
                  </a>
                </div>
              </div>
              <div className="animate-on-load"><div className="hero-perks"><span><i></i>Early access</span><span><i></i>Launch discount</span><span><i></i>Free to join</span></div></div>
            </div>
          </div>
        </div>
      </section>

      {/* ============ ABOUT ============ */}
      <section className="section" id="about">
        <div className="section-spacing">
          <div className="line"></div>
          <div className="container">
            <div className="section-inside">
              <div className="grid-title">
                <div className="grid-title-container">
                  <div className="fade-in-on-scroll"><div className="paragraph-title">What ClipHeist does</div></div>
                  <div className="title-animation"><div className="fade-in-on-scroll"><h2 className="h4-style split-words">Drop your stream link. ClipHeist does the rest.</h2></div></div>
                </div>
                <div className="fade-in-on-scroll"><p>One link in. Clips made, posted and improved over time. You never open an editor.</p></div>
              </div>
              <div className="core">
                <div className="core-card you">
                  <div className="core-label"><span>01</span><span>Your part</span></div>
                  <h3 className="core-title">Drop your link</h3>
                  <p className="core-text">Paste your GTA 6 stream link. That's the whole job.</p>
                  <div className="core-foot"><div className="core-input"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.5 9.5l3-3M7 4.5l1-1a2.8 2.8 0 014 4l-1 1M9 11.5l-1 1a2.8 2.8 0 01-4-4l1-1" stroke="#000" strokeWidth="1.4" fill="none" strokeLinecap="round"/></svg><span>your-stream-link.tv/gta6</span></div></div>
                </div>
                <div className="core-card">
                  <div className="core-label"><span>02</span><span>AI clipping</span></div>
                  <h3 className="core-title">AI makes the clips</h3>
                  <p className="core-text">It watches the whole stream, picks the best moments and cuts them into vertical clips.</p>
                  <div className="core-foot"><span className="tag">Best moments</span><span className="tag">9:16</span></div>
                </div>
                <div className="core-card">
                  <div className="core-label"><span>03</span><span>Auto-upload</span></div>
                  <h3 className="core-title">Posted for you</h3>
                  <p className="core-text">Clips go straight to your channels. No downloading, no uploading.</p>
                  <div className="core-foot"><span className="tag">TikTok</span><span className="tag">Shorts</span><span className="tag">Reels</span></div>
                </div>
                <div className="core-card">
                  <div className="core-label"><span>04</span><span>Auto-analysis</span></div>
                  <h3 className="core-title">Learns your channel</h3>
                  <p className="core-text">It studies which clips work on your channel and which don't, then makes more of what works.</p>
                  <div className="core-foot"><div className="core-bars" aria-hidden="true"><i style={{height: '30%'}}></i><i style={{height: '45%'}}></i><i style={{height: '38%'}}></i><i className="up" style={{height: '70%'}}></i><i style={{height: '52%'}}></i><i className="up" style={{height: '86%'}}></i><i className="up" style={{height: '100%'}}></i></div></div>
                </div>
              </div>
              <div className="core-strip fade-in-on-scroll"><p>Just stream. Clipping, posting and learning what works is on us, so you can focus on growing and getting monetized.</p><a href="#waitlist" className="nav-cta js-to-waitlist">Start clipping</a></div>
            </div>
          </div>
        </div>
      </section>

      {/* ============ CLIPS (sticky sidebar) ============ */}
      <section className="section" id="clips">
        <div className="section-spacing" style={{paddingBottom: '0'}}>
          <div className="container">
            <div className="grid-title">
              <div className="grid-title-container">
                <div className="fade-in-on-scroll"><div className="paragraph-title">Clips</div></div>
                <div className="title-animation"><div className="heading-holder"><div className="fade-in-on-scroll"><h2 className="h4-style split-words">Four hours of stream. The thirty seconds people share.</h2></div></div></div>
              </div>
              <div className="fade-in-on-scroll"><p>The kind of moments ClipHeist pulls from a long GTA 6 session, cropped vertical and ready for the feed. Previews are illustrations.</p></div>
            </div>
          </div>
        </div>
        <div className="clips-holder">
          <div className="clips-sticky">
            <div className="container">
              <div className="clips-box">
                <div className="clips-side">
                  <div className="clips-side-top paragraph-title">Moments worth posting</div>
                  <div className="clips-list-mask">
                    <div className="clips-list">
                      <div className="clips-item on"><div className="clips-name">Bank Job</div><div className="clips-cat">( Heist )</div></div>
                      <div className="clips-item"><div className="clips-name">Hot Pursuit</div><div className="clips-cat">( Police Chase )</div></div>
                      <div className="clips-item"><div className="clips-name">Total Chaos</div><div className="clips-cat">( Fails )</div></div>
                      <div className="clips-item"><div className="clips-name">Clean Getaway</div><div className="clips-cat">( Highlights )</div></div>
                    </div>
                  </div>
                  <div className="clips-bar"><span className="paragraph-title">Clips</span><span className="paragraph-title clips-count">01 / 04</span></div>
                </div>
                <div className="clips-media">
                    <a href="#waitlist" className="clips-slide js-to-waitlist first" aria-label="Bank Job: start clipping to get clips like this">
                      <SceneImg scene="marina" className="bg" alt="" />
                      <div className="overlay"></div>
                      <div className="clips-phone"><div className="clip">
                        <ClipVideo name="clip-heist" label="Vertical clip: a masked robber during a heist" />
                        <div className="clip-ui">
                          <div className="clip-top"><span className="clip-pill accent">Auto-clipped</span><span className="clip-pill">0:28</span></div>
                          <div><div className="clip-caption">We're in. <mark>Don't touch anything.</mark></div><div className="clip-source">From stream · 01:12:40</div><div className="clip-bar"><i></i></div></div>
                        </div>
                      </div></div>
                      <div className="discover"><div className="paragraph-title">Get clips like this</div></div>
                      <div className="clips-tag">Pulled from a 4h stream · 01:12:40</div>
                    </a>
                    <a href="#waitlist" className="clips-slide js-to-waitlist" aria-label="Hot Pursuit: start clipping to get clips like this">
                      <SceneImg scene="night" className="bg" alt="" />
                      <div className="overlay"></div>
                      <div className="clips-phone"><div className="clip">
                        <ClipVideo name="clip-chase" label="Vertical clip: a high-speed chase on the highway" />
                        <div className="clip-ui">
                          <div className="clip-top"><span className="clip-pill accent">Auto-clipped</span><span className="clip-pill">0:34</span></div>
                          <div><div className="clip-caption">Five stars and <mark>zero</mark> plan</div><div className="clip-source">From stream · 02:03:11</div><div className="clip-bar"><i></i></div></div>
                        </div>
                      </div></div>
                      <div className="discover"><div className="paragraph-title">Get clips like this</div></div>
                      <div className="clips-tag">Pulled from a 4h stream · 02:03:11</div>
                    </a>
                    <a href="#waitlist" className="clips-slide js-to-waitlist" aria-label="Total Chaos: start clipping to get clips like this">
                      <SceneImg scene="neon" className="bg" alt="" />
                      <div className="overlay"></div>
                      <div className="clips-phone"><div className="clip">
                        <ClipVideo name="clip-fail" label="Vertical clip: chaos breaking out on the street" />
                        <div className="clip-ui">
                          <div className="clip-top"><span className="clip-pill accent">Auto-clipped</span><span className="clip-pill">0:19</span></div>
                          <div><div className="clip-caption">That was <mark>not</mark> the plan</div><div className="clip-source">From stream · 02:47:52</div><div className="clip-bar"><i></i></div></div>
                        </div>
                      </div></div>
                      <div className="discover"><div className="paragraph-title">Get clips like this</div></div>
                      <div className="clips-tag">Pulled from a 4h stream · 02:47:52</div>
                    </a>
                    <a href="#waitlist" className="clips-slide js-to-waitlist" aria-label="Clean Getaway: start clipping to get clips like this">
                      <SceneImg scene="sunset" className="bg" alt="" />
                      <div className="overlay"></div>
                      <div className="clips-phone"><div className="clip">
                        <ClipVideo name="clip-getaway" label="Vertical clip: the crew driving off after a job" />
                        <div className="clip-ui">
                          <div className="clip-top"><span className="clip-pill accent">Auto-clipped</span><span className="clip-pill">0:41</span></div>
                          <div><div className="clip-caption">Chat said impossible. <mark>Watch this.</mark></div><div className="clip-source">From stream · 03:21:05</div><div className="clip-bar"><i></i></div></div>
                        </div>
                      </div></div>
                      <div className="discover"><div className="paragraph-title">Get clips like this</div></div>
                      <div className="clips-tag">Pulled from a 4h stream · 03:21:05</div>
                    </a>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ============ HOW IT WORKS (services) ============ */}
      <section className="section" id="how">
        <div className="section-spacing" style={{paddingBottom: '0'}}>
          <div className="container">
            <div className="section-inside">
              <div className="grid-title">
                <div className="grid-title-container">
                  <div className="fade-in-on-scroll"><div className="paragraph-title">How it works</div></div>
                  <div className="title-animation"><div className="heading-holder l"><div className="fade-in-on-scroll"><h2 className="h4-style split-words">From stream link to posted clip without touching a timeline.</h2></div></div></div>
                </div>
                <div className="fade-in-on-scroll"><p>You bring the stream. ClipHeist does the watching, the cutting, the cropping and the posting.</p></div>
              </div>
            </div>
          </div>
        </div>
        <div className="sticky-services-holder">
          <div className="sticky-services">
            <div className="services">
              <div className="services-inner">
                <div className="services-text-holder">
                  <div className="services-title">
                    <div className="paragraph-title white-text">How it works</div>
                    <div className="step-progress" aria-hidden="true"><i className="on"></i><i></i><i></i><i></i></div>
                  </div>
                  <div>
                    <div className="services-text-container">
                      <div className="services-track">
                        <div className="services-text _1"><div className="h2-style">Paste your link</div></div>
                        <div className="services-text _2"><div className="h2-style">We find the moments</div></div>
                        <div className="services-text _3"><div className="h2-style">Cropped to vertical</div></div>
                        <div className="services-text _4"><div className="h2-style">Posted for you</div></div>
                      </div>
                    </div>
                    <div className="services-desc-holder">
                      <p className="services-desc on">Drop in the link to your GTA 6 stream. That's the only thing ClipHeist needs from you.</p>
                      <p className="services-desc">ClipHeist goes through the whole stream and pulls out the best moments, so you don't have to rewatch hours of footage.</p>
                      <p className="services-desc">Every moment comes back reframed as a vertical 9:16 clip, made for phones.</p>
                      <p className="services-desc">ClipHeist publishes your clips straight to TikTok, YouTube Shorts and Instagram Reels. No editing, no uploading.</p>
                    </div>
                  </div>
                  <div className="yellow-box"><span className="paragraph-title">Step</span><span className="paragraph-title step-num">01 / 04</span></div>
                </div>

                <div className="services-images-holder" aria-hidden="true">
                  {/* 1: paste */}
                  <div className="services-images first">
                    <div className="p-link">
                      <div className="p-label">Your stream link</div>
                      <div className="p-input"><span className="url"><span className="typed"></span><span className="caret"></span></span><span className="go">Find clips</span></div>
                      <div className="p-note"><i></i>No editor, no timeline, no uploading.</div>
                    </div>
                  </div>
                  {/* 2: moments */}
                  <div className="services-images">
                    <div className="p-time">
                      <div className="p-label">Stream · 4h 12m</div>
                      <Wave />
                      <div className="p-axis"><span>0:00</span><span>1:00:00</span><span>2:00:00</span><span>3:00:00</span><span>4:12:33</span></div>
                      <div className="p-tags">
                        <div className="p-tag"><span>Heist</span><span>01:12:40</span></div>
                        <div className="p-tag"><span>Police chase</span><span>02:03:11</span></div>
                        <div className="p-tag"><span>Fail</span><span>02:47:52</span></div>
                        <div className="p-tag"><span>Getaway</span><span>03:21:05</span></div>
                      </div>
                    </div>
                  </div>
                  {/* 3: crop */}
                  <div className="services-images">
                    <div className="p-crop">
                      <SceneImg scene="chase" alt="" />
                      <div className="dim l"></div><div className="dim r"></div><div className="box"></div>
                      <div className="src">16:9 stream</div>
                    </div>
                  </div>
                  {/* 4: export */}
                  <div className="services-images">
                    <div className="p-export">
                      <div className="p-phone"><SceneImg scene="vault" orient="v" alt="" /><div className="ready"><b>TikTok</b><i><svg viewBox="0 0 10 10"><path d="M2 5.2l2 2 4-4.4" stroke="#000" strokeWidth="1.6" fill="none"/></svg></i></div></div>
                      <div className="p-phone"><SceneImg scene="chase" orient="v" alt="" /><div className="ready"><b>Shorts</b><i><svg viewBox="0 0 10 10"><path d="M2 5.2l2 2 4-4.4" stroke="#000" strokeWidth="1.6" fill="none"/></svg></i></div></div>
                      <div className="p-phone"><SceneImg scene="beach" orient="v" alt="" /><div className="ready"><b>Reels</b><i><svg viewBox="0 0 10 10"><path d="M2 5.2l2 2 4-4.4" stroke="#000" strokeWidth="1.6" fill="none"/></svg></i></div></div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ============ FAQ (testimonials layout) ============ */}
      <section className="section" id="faq">
        <div className="section-spacing">
          <div className="container">
            <div className="section-inside">
              <div className="content">
                <div className="center-text-holder"><p className="paragraph-title">Questions, answered</p></div>
                <div className="logo-animation-holder" aria-hidden="true">
                  <div className="logo-animation-half _1"><div className="logo-text">ClipHeist</div></div>
                  <div className="logo-animation-half _2"><div className="logo-text">ClipHeist</div></div>
                  <div className="logo-animation-half _3"><div className="logo-text">ClipHeist</div></div>
                  <div className="logo-animation-full"><div className="logo-text">ClipHeist</div></div>
                </div>
              </div>
              <div className="testimonial-holder">
                <div className="testimonial-item"><div><h3>What is ClipHeist?</h3><p>A tool that turns your GTA 6 streams into short vertical clips that are ready to post. Drop a link and ClipHeist makes the clips and posts them for you.</p></div><div className="testimonial-user-holder"><div className="testimonial-user">01</div><div className="testimonial-content"><div className="paragraph-title">The basics</div><p className="no-margins">Product</p></div></div></div>
                <div className="testimonial-item"><div><h3>Who is it for?</h3><p>GTA 6 streamers who want short-form content without editing, and clippers who cut streams for other creators.</p></div><div className="testimonial-user-holder"><div className="testimonial-user">02</div><div className="testimonial-content"><div className="paragraph-title">Audience</div><p className="no-margins">Streamers + clippers</p></div></div></div>
                <div className="testimonial-item"><div><h3>Do I need to edit anything?</h3><p>No. ClipHeist finds the moments, crops them to vertical and posts them for you.</p></div><div className="testimonial-user-holder"><div className="testimonial-user">03</div><div className="testimonial-content"><div className="paragraph-title">Workflow</div><p className="no-margins">No editing</p></div></div></div>
                <div className="testimonial-item"><div><h3>Where do the clips get posted?</h3><p>ClipHeist posts them straight to TikTok, YouTube Shorts and Instagram Reels.</p></div><div className="testimonial-user-holder"><div className="testimonial-user">04</div><div className="testimonial-content"><div className="paragraph-title">Platforms</div><p className="no-margins">Shorts, Reels, TikTok</p></div></div></div>
                <div className="testimonial-item"><div><h3>Can I try it free?</h3><p>Yes. Sign in with Google and get 3 free clips (small watermark). No card needed.</p></div><div className="testimonial-user-holder"><div className="testimonial-user">05</div><div className="testimonial-content"><div className="paragraph-title">Free trial</div><p className="no-margins">3 clips, no card</p></div></div></div>
                <div className="testimonial-item"><div><h3>Is it open now?</h3><p>Yes. GTA 6 is out (19 November 2026) and ClipHeist is open. Sign in with Google, paste a stream link and get your clips in minutes.</p></div><div className="testimonial-user-holder"><div className="testimonial-user">06</div><div className="testimonial-content"><div className="paragraph-title">Timing</div><p className="no-margins">GTA 6 launch</p></div></div></div>
                <div className="testimonial-item"><div><h3>How much will it cost?</h3><p>There are three plans. See the <Link to="/pricing" style={{textDecoration: 'underline'}}>pricing page</Link>. Launch price is 40% off until Sunday 22 November.</p></div><div className="testimonial-user-holder"><div className="testimonial-user">07</div><div className="testimonial-content"><div className="paragraph-title">Pricing</div><p className="no-margins">Three plans</p></div></div></div>
                <div className="testimonial-item"><div><h3>Is this made by Rockstar?</h3><p>No. ClipHeist is an independent tool and is not affiliated with Rockstar Games or Take-Two Interactive.</p></div><div className="testimonial-user-holder"><div className="testimonial-user">08</div><div className="testimonial-content"><div className="paragraph-title">Legal</div><p className="no-margins">Independent</p></div></div></div>
              </div>
            </div>
          </div>
        </div>
      </section>
      </main>

      <Footer />
    </div>
  );
}
