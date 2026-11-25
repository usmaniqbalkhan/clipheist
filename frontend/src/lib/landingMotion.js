// Landing page motion, ported from the original inline script (GSAP 3.15 + ScrollTrigger + SplitText).
// initLandingMotion() runs once after the landing page mounts and returns a cleanup function.
// Call it inside useGSAP() so every tween, ScrollTrigger and SplitText it creates is reverted on unmount.
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { SplitText } from 'gsap/SplitText';

gsap.registerPlugin(ScrollTrigger, SplitText);

const INTRO_KEY = 'ch-intro-played';

export function initLandingMotion() {
  const cleanups = [];
  const on = (target, type, fn, opts) => { target.addEventListener(type, fn, opts); cleanups.push(() => target.removeEventListener(type, fn, opts)); };
  const every = (fn, ms) => { const t = setInterval(fn, ms); cleanups.push(() => clearInterval(t)); };
  const later = (fn, ms) => { const t = setTimeout(fn, ms); cleanups.push(() => clearTimeout(t)); return t; };

  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const navbar = document.querySelector('.navbar');
  const loader = document.querySelector('.loader');
  const logo = document.querySelector('h1.logo-hero-text');
  const logoW = document.querySelector('.logo-white');
  const wrap = document.querySelector('.image_wrap');
  const imgs = Array.from(document.querySelectorAll('.content_img-wrap'));

  // Fit the giant wordmarks to the container width
  function fit(el, box){ el.style.fontSize = '100px'; el.style.display = 'inline-block'; const w = el.getBoundingClientRect().width; el.style.display = ''; const cs = getComputedStyle(box); const inner = box.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight); el.style.fontSize = (100 * inner / w * .93) + 'px'; }
  function fitAll(){
    const hc = document.querySelector('.hero-container .hero-text-wrapper');
    fit(logo, hc); logoW.style.fontSize = logo.style.fontSize;
    document.querySelectorAll('.logo-text').forEach(el => fit(el, document.querySelector('.logo-animation-holder')));
  }
  // Centre the card on the wordmark and keep it clear of the small text below
  function cardCenter(){
    const hero = document.querySelector('.hero').getBoundingClientRect();
    const m = document.querySelector('.logo-mask').getBoundingClientRect();
    return m.top - hero.top + m.height / 2;
  }
  function placeCard(){ wrap.style.top = cardCenter() + 'px'; }
  function finalCard(){
    const mobile = window.innerWidth < 768;
    const hero = document.querySelector('.hero').getBoundingClientRect();
    const rails = document.querySelector('.hero-links-holder').getBoundingClientRect().top - hero.top;
    const room = 2 * (rails - cardCenter()) - 40;
    const h = Math.max(160, Math.min(window.innerHeight * (mobile ? .5 : .55), room));
    return { width: mobile ? '82vw' : '45vw', height: h + 'px' };
  }
  function syncClip(){
    const r = wrap.getBoundingClientRect(), m = logoW.getBoundingClientRect();
    const t = Math.max(0, r.top - m.top), rt = Math.max(0, m.right - r.right), b = Math.max(0, m.bottom - r.bottom), l = Math.max(0, r.left - m.left);
    if (r.bottom <= m.top || r.top >= m.bottom) { logoW.style.clipPath = 'inset(50% 50% 50% 50%)'; return; }
    logoW.style.clipPath = `inset(${t}px ${rt}px ${b}px ${l}px round 18px)`;
  }

  // Typing in services panel 1
  const typed = document.querySelector('.p-input .typed');
  const url = 'https://your-stream-link.tv/videos/gta6-session';
  let ti = 0;
  (function type(){ typed.textContent = url.slice(0, ti); ti = ti >= url.length + 18 ? 0 : ti + 1; later(type, ti === 0 ? 400 : 70); })();

  const finish = () => document.documentElement.classList.remove('is-loading');
  cleanups.push(finish);

  if (reduce){
    // No motion: show final states
    document.fonts && document.fonts.ready.then(fitAll); fitAll();
    loader.style.display = 'none';
    navbar.style.transform = 'none'; navbar.style.background = '#fff';
    imgs.forEach(w => w.style.clipPath = 'inset(0 0 0 0)');
    placeCard(); const f = finalCard(); wrap.style.width = f.width; wrap.style.height = f.height;
    document.querySelectorAll('.logo-animation-full').forEach(el => el.style.visibility = 'visible');
    document.querySelectorAll('.logo-animation-half').forEach(el => el.style.display = 'none');
    document.querySelectorAll('.clip-bar i').forEach(el => el.style.width = '40%');
    wrap.style.borderRadius = '18px';
    const sc = () => syncClip();
    on(window, 'scroll', sc, {passive:true}); on(window, 'resize', () => { fitAll(); sc(); }); later(sc, 50);
    setupStepsBasic(on);
    setupClipsBasic(on);
    return () => cleanups.forEach(fn => fn());
  }

  document.documentElement.classList.add('is-loading');
  window.scrollTo(0, 0);
  fitAll();
  placeCard();
  gsap.set(wrap, {xPercent:-50, yPercent:-50, x:0, y:0});

  // Split the hero word into chars
  const logoSplit = SplitText.create(logo, {type:'chars', charsClass:'char'});
  const logoSplitW = SplitText.create(logoW, {type:'chars', charsClass:'char'});
  const allChars = [...logoSplit.chars, ...logoSplitW.chars];
  // pair chars so both copies move together
  const pairs = logoSplit.chars.map((c, i) => [c, logoSplitW.chars[i]]);
  gsap.ticker.add(syncClip);
  cleanups.push(() => gsap.ticker.remove(syncClip));

  // ---------- Initial states ----------
  gsap.set(allChars, {yPercent:110, opacity:0});
  gsap.set('.hero-text-wrapper .animate-on-load > *, .hero-paragraph-holder .animate-on-load > *', {y:65});
  gsap.set('.navbar .nav-link-wrapper', {yPercent:-120, opacity:0});
  gsap.set(navbar, {yPercent:-100});

  // ---------- Intro timeline ----------
  const tl = gsap.timeline({onComplete(){ finish(); startReel(); ScrollTrigger.refresh(); }});
  const count = document.querySelector('.loader-count'), counter = {v:0};
  // width reveal 0 -> 100% from 0.3s, 1.21s
  tl.to('.loader-line', {width:'100%', duration:1.21, ease:'power2.inOut'}, .3)
    .to(counter, {v:100, duration:1.21, ease:'power2.inOut', onUpdate(){ count.textContent = String(Math.round(counter.v)).padStart(3, '0'); }}, .3)
  // one layer fades 100% -> 0% from 1.5s; height wipe 100% -> 0 from 1.5s
    .to('.loader-inner', {opacity:0, duration:.5}, 1.5)
    .to(loader, {height:0, duration:.5, ease:'power3.inOut'}, 1.5)
    .set(loader, {display:'none'})
  // the cover stack flashes in, one image after another
    .to(imgs, {clipPath:'inset(0% 0 0 0)', duration:.42, ease:'power3.out', stagger:.09}, 1.62)
  // paired tween from 2.4s: 104% -> full bleed
    .to(wrap, {width:'100vw', height:'100vh', duration:.6, ease:'power2.out'}, 2.4)
  // shrink the full-bleed cover into the centre card
    .to(wrap, {...finalCard(), borderRadius:18, duration:.9, ease:'expo.inOut'}, 3.06)
  // hero word rises
    .to(pairs, {yPercent:0, opacity:1, duration:.8, ease:'expo.out', stagger:.045}, 3.3)
  // load-ins rise y 65 -> 0
    .to('.hero-text-wrapper .animate-on-load > *, .hero-paragraph-holder .animate-on-load > *', {y:0, duration:.8, ease:'expo.out', stagger:.05}, 3.45)
  // navbar drops in and turns white
    .to(navbar, {yPercent:0, duration:.5, ease:'power3.out', backgroundColor:'rgba(255,255,255,1)'}, 3.55)
    .to('.navbar .nav-link-wrapper', {yPercent:0, opacity:1, duration:.3, ease:'power2.out', stagger:.08}, 3.75);

  // Coming back to the landing page in the same visit: skip straight to the end of the intro.
  let played = false;
  try { played = sessionStorage.getItem(INTRO_KEY) === '1'; sessionStorage.setItem(INTRO_KEY, '1'); } catch { /* storage blocked */ }
  if (played) tl.progress(1);

  // After the intro the centre card keeps cycling like a clip reel
  function startReel(){
    let z = imgs.length, i = 0;
    const top = imgs[imgs.length - 1].querySelector('img');
    gsap.fromTo(top, {scale:1}, {scale:1.06, duration:3.4, ease:'none'});
    every(() => {
      if (document.hidden) return;
      const w = imgs[i % imgs.length], img = w.querySelector('img'); i++;
      gsap.set(w, {zIndex:++z, clipPath:'inset(0% 0 0 0)', opacity:0});
      gsap.to(w, {opacity:1, duration:1.1, ease:'power2.inOut'});
      gsap.fromTo(img, {scale:1.12}, {scale:1.04, duration:3.6, ease:'power1.out'});
    }, 3200);
  }

  // Hero card drifts as you scroll away
  gsap.to(wrap, {y:() => -window.innerHeight * .2, ease:'none', scrollTrigger:{trigger:'.hero', start:'top top', end:'bottom top', scrub:true}});

  // ---------- Scroll reveals ----------
  gsap.utils.toArray('.fade-in-on-scroll').forEach(el => {
    gsap.from(el, {y:40, opacity:0, duration:.8, ease:'expo.out', scrollTrigger:{trigger:el, start:'top 88%'}});
  });

  // Scrubbed word fade on section titles: opacity 20% -> 100%
  gsap.utils.toArray('.split-words').forEach(el => {
    const s = SplitText.create(el, {type:'words'});
    gsap.fromTo(s.words, {opacity:.2}, {opacity:1, ease:'none', stagger:.5, duration:.05, scrollTrigger:{trigger:el, start:'top 85%', end:'bottom 45%', scrub:true}});
  });

  // Core promise: four cards rise in order, analysis bars grow
  gsap.from('.core-card', {y:60, opacity:0, duration:.8, ease:'expo.out', stagger:.12, clearProps:'transform', scrollTrigger:{trigger:'.core', start:'top 82%'}});
  gsap.from('.core-bars i', {scaleY:0, duration:.7, ease:'expo.out', stagger:.06, delay:.6, scrollTrigger:{trigger:'.core', start:'top 82%'}});

  // ---------- Clips: sticky sidebar, list scrolls, image wipes up ----------
  const cSlides = gsap.utils.toArray('.clips-slide');
  const cItems = gsap.utils.toArray('.clips-item');
  const cList = document.querySelector('.clips-list');
  const cCount = document.querySelector('.clips-count');
  const ci = () => cItems[0].offsetHeight;
  const N = cSlides.length, GAP = 1.6; // each step: 1 unit move + 0.6 hold
  gsap.set(cList, {y:() => -0.5 * ci()});
  let cActive = 0;
  const ctl = gsap.timeline({scrollTrigger:{trigger:'.clips-holder', start:'top top', end:'bottom bottom', scrub:.6, invalidateOnRefresh:true,
    onUpdate(self){
      const t = self.progress * ctl.duration();
      const k = Math.min(N - 1, Math.floor((t + .5) / GAP));
      if (k !== cActive){ cActive = k; cItems.forEach((el, i) => el.classList.toggle('on', i === k)); cCount.textContent = `0${k + 1} / 0${N}`; }
    }}});
  ctl.to({}, {duration:.3});
  for (let i = 1; i < N; i++){
    const at = .3 + (i - 1) * GAP;
    ctl.to(cList, {y:() => -(i + .5) * ci(), ease:'power2.inOut', duration:1}, at)
       .fromTo(cSlides[i], {clipPath:'inset(100% 0 0 0)'}, {clipPath:'inset(0% 0 0 0)', ease:'power2.inOut', duration:1}, at)
       .fromTo(cSlides[i].querySelector('.bg'), {scale:1.18}, {scale:1, ease:'power2.out', duration:1}, at)
       .fromTo(cSlides[i].querySelector('.clips-phone'), {yPercent:-42}, {yPercent:-50, ease:'power2.out', duration:1}, at)
       .to(cSlides[i - 1].querySelector('.bg'), {scale:1.08, ease:'none', duration:1}, at);
  }
  ctl.to({}, {duration:.6});

  // ---------- How it works: sticky stepper ----------
  const track = document.querySelector('.services-track');
  const panels = gsap.utils.toArray('.services-images');
  const descs = document.querySelectorAll('.services-desc');
  const bars = document.querySelectorAll('.step-progress i');
  const num = document.querySelector('.step-num');
  let cur = 0;
  function setStep(n){
    if (n === cur) return;
    const prev = cur; cur = n;
    gsap.to(track, {yPercent:-25 * n, duration:.7, ease:'expo.inOut'});
    descs.forEach((d, i) => d.classList.toggle('on', i === n));
    bars.forEach((b, i) => b.classList.toggle('on', i <= n));
    num.textContent = `0${n + 1} / 04`;
    panels.forEach((p, i) => gsap.set(p, {zIndex: i === n ? 3 : i === prev ? 2 : 1}));
    gsap.fromTo(panels[n], {clipPath: n > prev ? 'inset(100% 0 0 0)' : 'inset(0 0 100% 0)'}, {clipPath:'inset(0% 0 0% 0)', duration:.8, ease:'expo.inOut'});
  }
  ScrollTrigger.create({trigger:'.sticky-services-holder', start:'top top', end:'bottom bottom', onUpdate(self){ setStep(Math.min(3, Math.floor(self.progress * 4))); }});

  // ---------- FAQ wordmark assembles from three slices ----------
  const halves = gsap.utils.toArray('.logo-animation-half');
  const ltl = gsap.timeline({scrollTrigger:{trigger:'.logo-animation-holder', start:'top 90%', end:'top 35%', scrub:true}});
  ltl.from(halves[0], {xPercent:-28, ease:'none'}, 0)
     .from(halves[1], {xPercent:22, ease:'none'}, 0)
     .from(halves[2], {xPercent:-14, ease:'none'}, 0);
  gsap.from('.testimonial-item', {y:50, opacity:0, duration:.8, ease:'expo.out', stagger:.08, scrollTrigger:{trigger:'.testimonial-holder', start:'top 85%'}});

  // Footer wordmark parallax
  gsap.from('.footer-wordmark', {yPercent:40, opacity:0, ease:'none', scrollTrigger:{trigger:'.footer-wordmark', start:'top bottom', end:'top 60%', scrub:true}});

  // Refit wordmarks after fonts load and on resize
  let alive = true;
  cleanups.push(() => { alive = false; });
  const refit = () => { if (!alive) return; fitAll(); placeCard(); if (!document.documentElement.classList.contains('is-loading')) gsap.set(wrap, finalCard()); ScrollTrigger.refresh(); };
  if (document.fonts){ document.fonts.load('600 100px Oswald').then(refit); on(document.fonts, 'loadingdone', refit); document.fonts.ready.then(refit); }
  let rt;
  on(window, 'resize', () => { clearTimeout(rt); rt = setTimeout(refit, 150); });
  cleanups.push(() => clearTimeout(rt));

  return () => cleanups.forEach(fn => fn());
}

function setupClipsBasic(on){
  const holder = document.querySelector('.clips-holder');
  const slides = document.querySelectorAll('.clips-slide'), items = document.querySelectorAll('.clips-item');
  const list = document.querySelector('.clips-list'), count = document.querySelector('.clips-count');
  function upd(){
    const r = holder.getBoundingClientRect();
    const p = Math.min(1, Math.max(0, -r.top / (r.height - window.innerHeight)));
    const n = Math.min(slides.length - 1, Math.floor(p * slides.length));
    list.style.transform = `translateY(${-(n + .5) * items[0].offsetHeight}px)`;
    slides.forEach((el, i) => el.style.clipPath = i <= n ? 'inset(0 0 0 0)' : 'inset(100% 0 0 0)');
    items.forEach((el, i) => el.classList.toggle('on', i === n));
    count.textContent = `0${n + 1} / 0${slides.length}`;
  }
  on(window, 'scroll', upd, {passive:true}); upd();
}

function setupStepsBasic(on){
  const holder = document.querySelector('.sticky-services-holder');
  const track = document.querySelector('.services-track');
  const panels = document.querySelectorAll('.services-images');
  const descs = document.querySelectorAll('.services-desc');
  const bars = document.querySelectorAll('.step-progress i');
  const num = document.querySelector('.step-num');
  function upd(){
    const r = holder.getBoundingClientRect();
    const p = Math.min(1, Math.max(0, -r.top / (r.height - window.innerHeight)));
    const n = Math.min(3, Math.floor(p * 4));
    track.style.transform = `translateY(${-25 * n}%)`;
    panels.forEach((el, i) => el.style.clipPath = i === n ? 'inset(0 0 0 0)' : 'inset(100% 0 0 0)');
    descs.forEach((d, i) => d.classList.toggle('on', i === n));
    bars.forEach((b, i) => b.classList.toggle('on', i <= n));
    num.textContent = `0${n + 1} / 04`;
  }
  on(window, 'scroll', upd, {passive:true}); upd();
}
