// Drawn fallback scenes for the landing page (ported 1:1 from the original inline script).
// Used only when a photo in /images is missing or fails to load.

let uid = 0;
const rng = s => () => (s = (s * 16807) % 2147483647) / 2147483647;
const SCENES = {
  sunset:  {seed:3,  hz:.62, sky:['#2b1055','#d6457a','#ffb36b'], sun:{x:.5,r:.17,c1:'#fffa8d',c2:'#ff7a59'}, city:{c:'#2a1338',h:260}, ground:['#3a1747','#120818'], water:true, palms:[[.07,.62],[.16,.46],[.91,.58]], palm:'#12061a'},
  night:   {seed:9,  hz:.66, sky:['#05030f','#1b0f3a','#5b2a86'], stars:true, moon:{x:.8,r:.04}, city:{c:'#0b0717',h:420,win:'#fffa8d'}, ground:['#0b0717','#000'], road:{c:'#0d0a16'}},
  chase:   {seed:21, hz:.6,  sky:['#1a1033','#7a2c5c','#f08a4b'], sun:{x:.28,r:.09,c1:'#fff1c2',c2:'#ff9a5a'}, city:{c:'#1c1026',h:200}, ground:['#20142a','#0a0610'], road:{c:'#151019'}, police:true, palms:[[.04,.55],[.96,.5]], palm:'#0a0510'},
  beach:   {seed:5,  hz:.55, sky:['#33a6d6','#8fd6ea','#ffe9b8'], sun:{x:.82,r:.07,c1:'#ffffff',c2:'#fffa8d'}, sea:'#2f8fb8', ground:['#f3d9a0','#e3b874'], palms:[[.1,.72],[.21,.55],[.86,.66]], palm:'#1d3a2a'},
  heli:    {seed:13, hz:.72, sky:['#020208','#0d1030','#24204a'], stars:true, city:{c:'#06060f',h:460,win:'#9fe8ff'}, ground:['#06060f','#000'], heli:true},
  vault:   {vault:true},
  desert:  {seed:17, hz:.6,  sky:['#ff9a3c','#ffc46b','#fff0c2'], sun:{x:.62,r:.12,c1:'#ffffff',c2:'#fffa8d'}, mountains:'#b5532f', ground:['#d9894a','#9c4a24'], road:{c:'#3b2a24'}},
  marina:  {seed:29, hz:.58, sky:['#141a4a','#6a3b8c','#ff8fa3'], sun:{x:.42,r:.1,c1:'#ffe3a3',c2:'#ff8fa3'}, city:{c:'#1b1438',h:340,win:'#fffa8d'}, ground:['#241a4d','#0a0820'], water:true},
  neon:    {seed:33, hz:.64, sky:['#0a0014','#2b0a3d','#ff2e88'], city:{c:'#0d0418',h:380,win:'#00e5ff'}, ground:['#14061f','#000'], road:{c:'#0b0412',neon:true}},
  dawn:    {seed:41, hz:.66, sky:['#fff7d1','#fffa8d','#ffd7a8'], sun:{x:.5,r:.14,c1:'#ffffff',c2:'#fff3b0'}, city:{c:'#2a2a2a',h:300}, ground:['#3a3a3a','#141414'], palms:[[.1,.5],[.88,.6]], palm:'#141414'},
  overpass:{seed:51, hz:.62, sky:['#3b1a5c','#e0507a','#ffc078'], sun:{x:.7,r:.13,c1:'#fff3c4',c2:'#ff9966'}, city:{c:'#2a1240',h:240}, ground:['#2a1240','#0d0618'], bridge:true, palms:[[.06,.5]], palm:'#0d0618'}
};

function palm(R, x, base, h, col){
  const tx = x + h * .14, ty = base - h;
  let s = `<path d="M${x} ${base} Q${x + h * .02} ${base - h * .55} ${tx} ${ty}" stroke="${col}" stroke-width="${h * .04}" fill="none" stroke-linecap="round"/>`;
  [-172,-145,-118,-88,-60,-32,-6].forEach(a => {
    const L = h * (.3 + R() * .12), r = a * Math.PI / 180;
    const ex = tx + Math.cos(r) * L, ey = ty + Math.sin(r) * L * .55 + L * .38;
    const cx = tx + Math.cos(r) * L * .55, cy = ty + Math.sin(r) * L * .6 - L * .1;
    s += `<path d="M${tx} ${ty} Q${cx} ${cy} ${ex} ${ey}" stroke="${col}" stroke-width="${h * .032}" fill="none" stroke-linecap="round"/>`;
  });
  return s;
}

function vaultScene(W, H){
  const id = 'v' + (uid++), cx = W / 2, cy = H * .46, r = Math.min(W, H) * .32;
  let s = `<defs><radialGradient id="${id}b" cx=".5" cy=".42" r=".75"><stop offset="0" stop-color="#3a3326"/><stop offset="1" stop-color="#07060a"/></radialGradient><linearGradient id="${id}g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff3a0"/><stop offset="1" stop-color="#c9a227"/></linearGradient><radialGradient id="${id}l" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#fffa8d" stop-opacity=".35"/><stop offset="1" stop-color="#fffa8d" stop-opacity="0"/></radialGradient></defs>`;
  s += `<rect width="${W}" height="${H}" fill="url(#${id}b)"/>`;
  s += `<circle cx="${cx}" cy="${cy}" r="${r * 1.6}" fill="url(#${id}l)"/>`;
  s += `<circle cx="${cx}" cy="${cy}" r="${r * 1.12}" fill="#15130f" stroke="#2c281f" stroke-width="${r * .05}"/>`;
  s += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#5d5648" stroke="#8a826f" stroke-width="${r * .04}"/>`;
  for (let i = 0; i < 12; i++){ const a = i / 12 * Math.PI * 2; s += `<circle cx="${cx + Math.cos(a) * r * .86}" cy="${cy + Math.sin(a) * r * .86}" r="${r * .045}" fill="#2c281f"/>`; }
  s += `<circle cx="${cx}" cy="${cy}" r="${r * .62}" fill="none" stroke="#3e392f" stroke-width="${r * .03}"/>`;
  s += `<circle cx="${cx}" cy="${cy}" r="${r * .26}" fill="#3e392f"/>`;
  for (let i = 0; i < 3; i++){ const a = i / 3 * Math.PI * 2 + .3; s += `<line x1="${cx}" y1="${cy}" x2="${cx + Math.cos(a) * r * .5}" y2="${cy + Math.sin(a) * r * .5}" stroke="#8a826f" stroke-width="${r * .07}" stroke-linecap="round"/>`; }
  s += `<circle cx="${cx}" cy="${cy}" r="${r * .1}" fill="#fffa8d"/>`;
  const by = H * .86, bw = Math.min(W, H) * .1, bh = bw * .38;
  for (let row = 0; row < 3; row++) for (let i = 0; i < 6 - row; i++){
    const x = cx - (6 - row) * bw * .55 + i * bw * 1.1, y = by - row * bh * 1.05;
    s += `<rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="${bh * .15}" fill="url(#${id}g)" stroke="#8a6d12" stroke-width="2"/>`;
  }
  s += `<rect y="${H * .9}" width="${W}" height="${H * .1}" fill="#050407"/>`;
  return s;
}

export function makeScene(name, W, H){
  const c = SCENES[name];
  let body;
  if (c.vault) body = vaultScene(W, H);
  else {
    const R = rng(c.seed), id = 'g' + (uid++), hz = H * c.hz, u = Math.max(W, H) / 1600, minS = Math.min(W, H);
    let d = `<defs><linearGradient id="${id}s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${c.sky[0]}"/><stop offset=".62" stop-color="${c.sky[1]}"/><stop offset="1" stop-color="${c.sky[2]}"/></linearGradient>`;
    d += `<linearGradient id="${id}g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${c.ground[0]}"/><stop offset="1" stop-color="${c.ground[1]}"/></linearGradient>`;
    d += `<radialGradient id="${id}v" cx=".5" cy=".5" r=".75"><stop offset=".55" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".55"/></radialGradient>`;
    let s = '';
    s += `<rect width="${W}" height="${hz + 2}" fill="url(#${id}s)"/>`;
    if (c.stars) for (let i = 0; i < 90; i++) s += `<circle cx="${R() * W}" cy="${R() * hz * .75}" r="${(.6 + R() * 1.4) * u}" fill="#fff" opacity="${.25 + R() * .7}"/>`;
    if (c.moon) s += `<circle cx="${W * c.moon.x}" cy="${hz * .22}" r="${minS * c.moon.r}" fill="#f4f1ff"/><circle cx="${W * c.moon.x + minS * c.moon.r * .45}" cy="${hz * .22 - minS * c.moon.r * .2}" r="${minS * c.moon.r}" fill="${c.sky[0]}"/>`;
    if (c.sun){
      const r = minS * c.sun.r, cx = W * c.sun.x, cy = hz - r * .3;
      d += `<linearGradient id="${id}u" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${c.sun.c1}"/><stop offset="1" stop-color="${c.sun.c2}"/></linearGradient>`;
      d += `<mask id="${id}m"><rect width="${W}" height="${H}" fill="#fff"/>`;
      for (let k = 0; k < 6; k++) d += `<rect x="0" y="${cy + r * .12 + k * r * .15}" width="${W}" height="${(2 + k * 2.4) * u}" fill="#000"/>`;
      d += `</mask>`;
      s += `<circle cx="${cx}" cy="${cy}" r="${r * 1.9}" fill="${c.sun.c2}" opacity=".18"/>`;
      s += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="url(#${id}u)" mask="url(#${id}m)"/>`;
    }
    if (c.mountains){
      let p = `0,${hz} `; for (let x = 0; x <= W; x += W / 10) p += `${x},${hz - (30 + R() * 150) * u} `; p += `${W},${hz}`;
      s += `<polygon points="${p}" fill="${c.mountains}" opacity=".85"/>`;
    }
    if (c.city){
      let x = -10 * u;
      while (x < W){
        const bw = (34 + R() * 90) * u, bh = (50 + R() * c.city.h) * u;
        s += `<rect x="${x}" y="${hz - bh}" width="${bw}" height="${bh + 2}" fill="${c.city.c}"/>`;
        if (R() > .8) s += `<rect x="${x + bw / 2 - u}" y="${hz - bh - 30 * u}" width="${2 * u}" height="${30 * u}" fill="${c.city.c}"/>`;
        if (c.city.win){ const n = Math.floor(R() * 10); for (let i = 0; i < n; i++) s += `<rect x="${x + 6 * u + R() * (bw - 14 * u)}" y="${hz - bh + 8 * u + R() * (bh - 16 * u)}" width="${4 * u}" height="${5 * u}" fill="${c.city.win}" opacity="${.4 + R() * .6}"/>`; }
        x += bw + R() * 6 * u;
      }
    }
    if (c.bridge){
      const by = hz - H * .1;
      s += `<rect x="0" y="${by}" width="${W}" height="${H * .028}" fill="#12081f"/>`;
      for (let x = W * .08; x < W; x += W * .22) s += `<rect x="${x}" y="${by}" width="${18 * u}" height="${hz - by}" fill="#12081f"/>`;
      for (let x = 0; x < W; x += 40 * u) s += `<circle cx="${x}" cy="${by - 4 * u}" r="${2 * u}" fill="#fffa8d" opacity=".8"/>`;
    }
    s += `<rect y="${hz}" width="${W}" height="${H - hz}" fill="url(#${id}g)"/>`;
    if (c.sea) s += `<rect y="${hz}" width="${W}" height="${(H - hz) * .32}" fill="${c.sea}"/><rect y="${hz + (H - hz) * .32 - 3 * u}" width="${W}" height="${6 * u}" fill="#fff" opacity=".7"/>`;
    if (c.water && c.sun){
      const cx = W * c.sun.x, r = minS * c.sun.r;
      for (let i = 0; i < 16; i++){ const y = hz + 6 * u + i * i * 1.5 * u * (H / 1000) * 1.1; const w = r * (1.7 - i * .05) * (1 + R() * .3); s += `<rect x="${cx - w / 2}" y="${y}" width="${w}" height="${(2 + i * .5) * u}" rx="${u}" fill="${c.sun.c2}" opacity="${Math.max(.08, .75 - i * .045)}"/>`; }
    }
    if (c.road){
      const vx = W * .5, bw = W * .62;
      s += `<polygon points="${vx - 4 * u},${hz} ${vx + 4 * u},${hz} ${vx + bw},${H} ${vx - bw},${H}" fill="${c.road.c}"/>`;
      const edge = c.road.neon ? '#ff2e88' : '#fffa8d';
      s += `<line x1="${vx - 3 * u}" y1="${hz}" x2="${vx - bw * .92}" y2="${H}" stroke="${edge}" stroke-width="${3 * u}" opacity=".7"/>`;
      s += `<line x1="${vx + 3 * u}" y1="${hz}" x2="${vx + bw * .92}" y2="${H}" stroke="${c.road.neon ? '#00e5ff' : edge}" stroke-width="${3 * u}" opacity=".7"/>`;
      for (let i = 0; i < 9; i++){ const t0 = Math.pow(i / 9, 2), t1 = t0 + .035 + i * .006; const y0 = hz + (H - hz) * t0, y1 = hz + (H - hz) * Math.min(1, t1); const w0 = (1 + t0 * 10) * u, w1 = (1 + t1 * 10) * u; s += `<polygon points="${vx - w0},${y0} ${vx + w0},${y0} ${vx + w1},${y1} ${vx - w1},${y1}" fill="#fff" opacity=".75"/>`; }
    }
    if (c.police){
      d += `<radialGradient id="${id}r" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#ff2b3a" stop-opacity=".75"/><stop offset="1" stop-color="#ff2b3a" stop-opacity="0"/></radialGradient><radialGradient id="${id}b" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#2b6bff" stop-opacity=".75"/><stop offset="1" stop-color="#2b6bff" stop-opacity="0"/></radialGradient>`;
      const gy = hz + (H - hz) * .55;
      s += `<ellipse cx="${W * .36}" cy="${gy}" rx="${W * .3}" ry="${H * .2}" fill="url(#${id}r)"/><ellipse cx="${W * .64}" cy="${gy}" rx="${W * .3}" ry="${H * .2}" fill="url(#${id}b)"/>`;
      const cw = minS * .42, ch = cw * .32, cx = W / 2 - cw / 2, cy = H - ch - H * .06;
      s += `<rect x="${cx}" y="${cy + ch * .35}" width="${cw}" height="${ch * .65}" rx="${ch * .15}" fill="#050308"/><path d="M${cx + cw * .2} ${cy + ch * .36} L${cx + cw * .3} ${cy} L${cx + cw * .7} ${cy} L${cx + cw * .8} ${cy + ch * .36} Z" fill="#050308"/>`;
      s += `<rect x="${cx + cw * .36}" y="${cy - ch * .1}" width="${cw * .13}" height="${ch * .1}" fill="#ff2b3a"/><rect x="${cx + cw * .51}" y="${cy - ch * .1}" width="${cw * .13}" height="${ch * .1}" fill="#2b6bff"/>`;
      s += `<rect x="${cx + cw * .04}" y="${cy + ch * .55}" width="${cw * .12}" height="${ch * .12}" fill="#ff5a4a"/><rect x="${cx + cw * .84}" y="${cy + ch * .55}" width="${cw * .12}" height="${ch * .12}" fill="#ff5a4a"/>`;
    }
    if (c.heli){
      const hx = W * .7, hy = H * .14;
      s += `<polygon points="${hx},${hy} ${W * .42},${H} ${W * .86},${H}" fill="#fff" opacity=".12"/><ellipse cx="${W * .64}" cy="${H * .93}" rx="${W * .22}" ry="${H * .05}" fill="#fff" opacity=".18"/>`;
      s += `<ellipse cx="${hx}" cy="${hy}" rx="${minS * .06}" ry="${minS * .028}" fill="#000"/><rect x="${hx}" y="${hy - minS * .008}" width="${minS * .1}" height="${minS * .012}" fill="#000"/><rect x="${hx - minS * .1}" y="${hy - minS * .04}" width="${minS * .2}" height="${minS * .006}" fill="#000"/><circle cx="${hx - minS * .04}" cy="${hy + minS * .01}" r="${minS * .006}" fill="#ff2b3a"/>`;
    }
    if (c.palms) c.palms.forEach(([px, ph]) => { s += palm(R, W * px, H + 4, H * ph, c.palm); });
    s += `<rect width="${W}" height="${H}" fill="url(#${id}v)"/>`;
    body = d + '</defs>' + s;
  }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid slice">${body}</svg>`;
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
}

// Real artwork in /images. Vertical clips, clip-card backgrounds and wide scenes each have their own set.
export const PHOTOS = {
  v:  { vault: 'clip-heist', chase: 'clip-chase', overpass: 'clip-fail', beach: 'clip-getaway' },
  bg: { marina: 'bg-heist', night: 'bg-chase', neon: 'bg-chaos', sunset: 'bg-getaway' },
  h:  { overpass: 'hero-01-overpass', dawn: 'hero-02-skyline', neon: 'hero-03-neon', marina: 'hero-04-marina',
        desert: 'hero-05-desert', vault: 'hero-06-vault', heli: 'hero-07-heli', beach: 'hero-08-beach',
        chase: 'hero-09-chase', night: 'hero-10-night', sunset: 'hero-11-sunset' }
};

const cache = {};
export function drawnScene(scene, vertical){
  const key = scene + (vertical ? 'v' : 'h');
  return cache[key] || (cache[key] = makeScene(scene, vertical ? 900 : 1600, vertical ? 1600 : 1000));
}
