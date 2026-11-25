# ClipHeist — Product Requirements (locked 2026-11-15)

**One line:** Paste a GTA 6 stream link, get ready-to-post vertical clips with captions, hooks, titles and
hashtags, and let them auto-post to YouTube Shorts on a schedule.

**Domain:** clipheist.site (backup redirect: sixstars.site)
**Base code:** fork of [PeakCut](https://github.com/haziqahmedqasmi/PeakCut) (MIT), remote `upstream`.
**Window:** Mon 16 Nov 2026 15:00 → Wed 16 Dec 2026 15:00. GTA 6 launches Thu 19 Nov.

---

## 1. Goal

Make as much verified profit as possible in 30 days by riding the GTA 6 launch. This is a seasonal product:
speed and low running cost beat polish and long-term architecture.

**Success:** 5+ paying customers by Day 7 (23 Nov), then maximum profit by 16 Dec.

## 2. Constraints that shape the product

- Payments only through **Polar** (connected to TrustMRR). One-time / upfront products, since they count in full.
- Profit = revenue − refunds − fees − ads. Hosting, AI and API costs do **not** reduce profit, but cash is tight,
  so running cost must stay near zero.
- No paid ads. Growth is outreach, our own clip accounts and communities.
- Do not register the domain, Polar product or any listing before the start time.
- No Rockstar/GTA logos, art or branded text. Say "made for GTA 6 streamers", never imply it is official.
- Keep the MIT licence notice from PeakCut in the repo.

## 3. Who it is for

One product, two kinds of buyer. Only the outreach message differs.

| Buyer | Need | Likely pass |
|---|---|---|
| Small and mid-size GTA 6 streamers | Grow on Shorts/TikTok without editing | Starter / Pro |
| Clippers (Whop Content Rewards, streamer clipping programs) | Many clips from many streams, fast | Premium |

## 4. Positioning

Competitors (Eklipse ~$20/mo, Opus Clip ~$15/mo, ClipSpeedAI has a GTA 6 hub) are monthly and generic.
ClipHeist wins on the combination:

1. **GTA-specific moment picking**: chases, crashes, cops, heists, NPC chaos, stunts, fails, rage, funny.
2. **Hands-free posting**: clips auto-post to Shorts at good times all week.
3. **One-time pass, no subscription.**
4. **Batch mode for clippers**: many links at once.
5. Cheap.

Pitch lines: "Steal the best moments from your stream." / "Clip it before Twitch deletes your VOD." (Twitch
keeps VODs only 7 days on default accounts.)

## 5. User flow

1. Land on clipheist.site → **Sign in with Google** (basic profile only; no review needed for this).
2. Paste a Twitch / Kick / YouTube link (or several, on Premium), or upload a file.
3. Optional: choose part of the stream, layout and caption style.
4. Watch progress live on the job page. First clips appear as soon as they render.
5. For each clip: preview, edit caption/title, download MP4 + cover image, copy the SEO kit.
6. If YouTube is connected: approve clips into a schedule, or turn on "auto-post N per day at peak hours".

Content permission is covered by the Terms of Service. No checkbox in the flow.

## 6. Features

Legend: ✅ already in PeakCut · 🔧 we change it · 🆕 we build it

### 6.1 MVP (Days 1–3, live by GTA launch on 19 Nov)

| Feature | Status | Notes |
|---|---|---|
| Link input: Twitch, Kick, YouTube + file upload | ✅ | yt-dlp; chunked uploads already fit Cloudflare's 100 MB request limit |
| Audio-only download → pick moments → download only clip sections | ✅ | Main cost saver |
| CPU speech-to-text (Parakeet via sherpa-onnx) | ✅ | No GPU needed. YouTube's own transcript used when available |
| Loudness peaks + AI moment picking | ✅ | |
| Hook, title, description, hashtags per clip | ✅ | This is the SEO kit |
| Word-by-word captions, 3 styles, 9:16 render | ✅ | |
| Google sign-in, accounts, admin, fair queue, abuse tests | ✅ | `PEAKCUT_ACCOUNTS=1` |
| **Rebrand to ClipHeist** | 🔧 | Name, colours, landing copy, legal pages, emails. No GTA marks |
| **GTA 6 prompt pack** | 🔧 | `studio/brain.py`: GTA categories, GTA slang in hooks/titles, action clips start 3–5 s earlier |
| **Default layout for console streams** | 🔧 | GTA 6 is console-only at launch, so many streams have no or a small facecam. Default = gameplay on blurred 9:16 background |
| **Polar checkout + clip credits** | 🆕 | Polar webhook `order.paid` → add clips to the account (matched by email). Deduct one credit per finished clip |
| **Free tier** | 🆕 | 1 stream, max 2 h, 3 clips, watermark. Turnstile on sign-up |
| Cover image per clip | ✅ | PeakCut already makes one per clip (found in the 15 Nov test) |
| Bigger gameplay in the vertical frame | ✅ done | "Zoom" layout (gameplay ~52% of frame height instead of ~31%) is now the default for videos and creators |
| Scan the whole stream before rendering | ✅ done | Every part is read; candidates are ranked, then the top N non-overlapping ones are rendered |
| **Faster transcription** | 🔧 | Full-stream CPU transcription is ~5x real time. Loud-window skipping was built (`PEAKCUT_SCAN_SHARE`) but is **off by default**: on roleplay it kept only 5/12 known good moments and dropped the best one. Real fix: PeakCut's GPU speech worker on RunPod, and Twitch chat spikes to guide windows |
| **YouTube download hardening** | 🆕 | yt-dlp + bgutil PO-token plugin. Residential proxy for YouTube only if still blocked |
| **Files auto-deleted after 72 h** | 🆕 | Keeps disk small |

### 6.2 Week 1 (19–23 Nov)

| Feature | Status | Notes |
|---|---|---|
| **YouTube auto-posting (Track A)** | ✅🔧 | PeakCut already has connect + queue + spacing + daily caps + view tracking. We wire it to passes and add "N per day at peak hours" |
| **Twitch past-VOD chat replay** | 🆕 | PeakCut reads chat only on live streams. Add chat-spike signal for past VODs (strong, free signal) |
| **Batch links** | 🆕 | Premium: paste up to 10 links, one queue |
| GTA 6 screen-cue detector | 🆕 | Wasted/busted screen, wanted level. Only possible after we see the real UI on 19 Nov |

### 6.3 Later (only if Day 7 says go)

- Track B posting via Upload-Post (adds TikTok + Instagram auto-post); see §7.
- Live clipping (✅ exists in PeakCut) as a capped premium feature: it uses CPU for the whole stream.
- "Radio music detected" warning (in-game radio causes copyright claims).
- AI narrator hook intro using ai33.pro voice credits.
- Expand to other games after the hackathon.

## 7. Auto-posting plan

**Track A — our own Google app (default, free).**
- Day 1, as soon as the domain, homepage and privacy policy are live: submit Google OAuth verification for the
  YouTube upload scope (needs verified domain, privacy policy, demo video of consent + upload) and the YouTube API
  compliance audit.
- Until verified: users see Google's "unverified app" warning and we are capped at 100 users. Enough for week 1.
- Day 1 test with our own channel: are API uploads public or locked private? This decides how urgent Track B is.
- Limit: about 100 uploads per day per app under the 2026 quota rules.

**Track B — Upload-Post API (fallback).** Switch on if uploads are locked private or verification stalls.
- Already approved by YouTube, TikTok and Instagram. Free plan (10 uploads/month) for testing; Basic ~$24/mo
  adds TikTok; roughly $1/mo per extra connected profile. API cost does not reduce hackathon profit.

Auto-posting is included in Premium only. It is the upsell.

## 8. Pricing (Polar, one-time) — locked 2026-11-16

Sold by clips, not stream-hours: buyers understand "50 clips" at a glance.

| Plan | Normal price | Launch price | Includes |
|---|---|---|---|
| Free trial | $0 | $0 | 3 clips from one stream, watermark |
| Starter | $25 | **$15** | 50 clips, captions + hook + title, download and post yourself |
| Pro | $59 | **$35** | 150 clips, everything in Starter, HD 1080p |
| Premium | $119 | **$69** | 500 clips, everything in Pro, **auto-post to YouTube Shorts**, paste many links at once |

- **Launch deal (40% off):** ends Sun 22 Nov 2026, 11:59 PM PT (72 h after GTA 6 launch). After that the normal
  price really applies. The discount must be real, so no extending it quietly; a later sale is a new, named sale.
- Cost guard: one stream is at most 4 hours; about 6–10 clips are made per stream.
- Refund: within 48 h and only if fewer than 10 clips used.
- Buy buttons open Polar checkout links with the launch discount pre-applied (IDs in docs/POLAR.md).
- Prices can change at the Day 7 checkpoint.
- Lives on its own page: `frontend/src/pages/Pricing.jsx` (`/pricing`), with the deal countdown at the top.

## 9. Moment detection (GTA pack)

Signals, cheapest first:
1. Twitch chat spikes (VOD chat replay; free).
2. Loudness peaks (shouting, crashes, gunfire, laughter).
3. Transcript of the candidate windows only.
4. AI ranks candidates with the GTA prompt and writes hook/title/description/hashtags.
5. (Week 1) GTA 6 screen cues.

Categories: `chase, crash, cops, heist, npc, stunt, fail, funny, rage, clutch, other`.
Rules: action categories start 3–5 s earlier (the reaction comes after the moment); 20–60 s clips; skip intros,
menus, loading screens, sponsor reads.

## 10. Architecture and running cost

```
Browser ── Cloudflare (DNS, Tunnel, SSL, Turnstile) ── ClipHeist server (FastAPI, PeakCut fork)
                                                         ├─ yt-dlp (+ PO-token plugin)
                                                         ├─ ffmpeg render, Parakeet speech-to-text (CPU)
                                                         ├─ SQLite or Postgres
                                                         ├─ AI: Gemini Flash (Groq as fallback)
                                                         └─ Polar webhooks, YouTube API
```

- **Server:** one CPU machine, ~4 vCPU / 8 GB. First choice: RunPod CPU pod paid from the $100 credits
  (~$86/month estimated; check the console price). RunPod pods are containers, so run PeakCut directly with
  Python + SQLite on a volume instead of docker-compose. Fallback: a cheap VPS with the stock docker-compose.
- **Cloudflare:** free plan. Tunnel to the server, so no open ports.
- **AI:** about 4 requests per hour of video on a cheap model, so cents per stream.
- **Target cost:** under $0.10 per processed stream-hour, excluding the fixed server.

## 11. Go-to-market (no paid ads)

- From 19 Nov: find GTA 6 streamers on Twitch/Kick/YouTube, clip 2–3 moments for free, send them with a
  free-tier or discount link. 30–50 per day.
- Clippers: post in Whop clipping communities and GTA clipping Discords.
- Our own TikTok/Shorts account posting the best clips, with permission.
- GTA and streamer subreddits / Discords, following each community's rules.
- Never ask friends, family or followers to buy "to help win".

## 12. Timeline

| When | Deliverable |
|---|---|
| Mon 16 Nov (from 15:00) | Buy domain → Cloudflare. Deploy fork as-is. Google OAuth client. Submit Google verification. Test: yt-dlp from server, YouTube upload public/private |
| Tue 17 Nov | Polar products + webhook + hour credits. Free tier + watermark + Turnstile. Rebrand + legal pages |
| Wed 18 Nov | GTA prompt pack, console layout default, cover images, 72 h cleanup. End-to-end test on real streams |
| Thu 19 Nov | GTA 6 out. Launch. Outreach starts |
| 19–22 Nov | Auto-posting on passes, VOD chat replay, batch links, GTA screen cues |
| Mon 23 Nov | **Day 7 checkpoint**: 5+ paying → all in. Interest, no pay → change price/buyer. Nothing → pivot |
| 23 Nov → 16 Dec | Quality from feedback, daily outreach, Track B if needed |

## 13. Risks

| Risk | Plan |
|---|---|
| YouTube blocks downloads from the server's IP | PO-token plugin → YouTube's own transcript → YouTube-only residential proxy → upload fallback. Twitch/Kick unaffected |
| Google verification slow, or uploads locked private | First 100 users on unverified app; Track B (Upload-Post) |
| Competitors already target GTA 6 | Compete on GTA-specific clips + auto-post + one-time price + batch |
| Bad clips on day one (no GTA 6 footage until 19 Nov) | Tune the prompt on GTA V / similar streams, retune on day 19 with real footage |
| Server overload at launch | PeakCut's load-aware queue; per-user job caps; add a second machine if paying users need it |
| In-game radio → copyright claims on posted clips | Warning feature later; mention in FAQ |
| Upstream PeakCut changes | We own our fork. Pull upstream only on purpose |

## 14. Metrics

- Paying customers, verified profit (TrustMRR).
- Free → paid conversion, outreach reply rate.
- Cost per processed stream-hour.
- Clips kept or posted per stream (quality).

## 15. Local test (2026-11-15)

82-min GTA V RP Twitch VOD (NoPixel), stock PeakCut on a 4-core laptop, AI answers written by Claude by hand
through a local adapter (stands in for Gemini Flash).

- Total: ~18.5 min to 6 finished 1080×1920 clips (includes a few minutes waiting on the hand-written AI answers).
- Transcription was the bottleneck (~5x real time on busy CPU). A 3 h stream would take ~35–40 min as is.
- Download: audio first, then only clip sections (~140 MB of 1080p sections for 6 clips).
- AI usage: ~13k tokens for the whole stream, a fraction of a cent on Gemini Flash.
- Quality: robbery/cop/argument moments came out well ("I AM NOT DEAF", score 80). One pick was an inventory-menu
  moment, because the AI reads only the transcript. Add a cheap frame check to drop menu/loading screens.
- Caption cleanup was skipped (words kept as spoken) during this test.

**Run 2 (same stream, after changes):** zoom layout + whole-stream ranking + loud-window transcription at 40%.
13.5 min instead of 18.5 min, but the best clip of run 1 was missed because it was not in a loud window. Offline
check against 12 known good moments: global 40% → 5/12 kept; per-part 50% → 7/12; even 65% never kept the best
one. Decision: transcribe everything by default; speed up with a GPU instead.

## 16. Open items

- RunPod CPU pod price confirmed in console, or switch to VPS.
- Result of the YouTube public/private upload test (Day 1).
- Final watermark and brand look.
