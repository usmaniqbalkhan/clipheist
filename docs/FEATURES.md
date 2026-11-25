# What the PeakCut base has, and what ClipHeist keeps

From a tour of the running app on 2026-11-16. ✅ keep · 🙈 hide for now (code stays) · ❌ remove · 🆕 add

## Screens in the base app

| Screen | What's there |
|---|---|
| Home | Paste link / upload, options, today's stats, 14-day chart (clips, posted, views, est. earnings), AI usage card, setup steps, creators list, post queue |
| All videos | Every job with its clips: score, length, preview, download, delete, select, Post |
| Clip editor | Hook text, caption style, layout, start/end seconds, post title + description, "Save and re-make clip", post |
| Creators | One workspace per streamer: channel link, live auto-watch + recording, live clipping, auto-post rules, earnings rate |
| Settings | AI service (admin only), profile, security, connected apps (YouTube, Instagram, TikTok) |
| Accounts | Email + password, Google sign-in, email confirmation, admin dashboard at `/admin` |
| Public pages | Landing page, privacy / terms / acceptable use |

## Keep / hide / remove

### Clipping
| Feature | Verdict | Why |
|---|---|---|
| Paste Twitch / Kick / YouTube link, upload file | ✅ | Core |
| Options: how many clips, min/max length, part of the video | ✅ | Useful, cheap |
| Caption styles (Bounce, Hype, Clean) | ✅ | Looks good already |
| Layouts (Blurred fill, Zoomed in, Fill screen) | ✅ | Zoomed in is our default |
| Video quality 720p / 1080p | ✅ | 1080p as a paid perk (2x slower) |
| Clip editor + re-make | ✅ | Lets users fix a bad cut without support from us |
| Download clip / zip of all clips | ✅ | |
| Score badge on each clip | ✅ | Helps users pick |
| Cover image per clip | ✅ | Already made |
| Hindi / Urdu Roman captions, translate to English | 🙈 | Harmless, not our audience |
| Live recording + live clipping | ✅ (since 2026-11-25) | Paste a link that is live and it is recorded and clipped as it happens; also from Streamers → Start recording. Every live clip comes off the clip balance, and the recording stops when the balance is used up |

### Posting
| Feature | Verdict | Why |
|---|---|---|
| Connect YouTube | ✅ | Our differentiator (Track A in PRD) |
| Post queue, spacing, daily cap, min score, privacy, footer | ✅ | Exactly the auto-post feature we planned |
| Connect Instagram / TikTok | 🙈 | Need app reviews we won't get in time; return with Upload-Post (Track B) |
| View tracking + "est. earnings" (rate per 1k views) | ✅ | Strong pitch for Whop clippers |

### Accounts and admin
| Feature | Verdict | Why |
|---|---|---|
| Google sign-in | ✅ | Agreed login method |
| Email + password sign-in | 🙈 | Google only keeps it simple |
| Admin dashboard, per-user limits, abuse protection | ✅ | Needed for a public site |
| AI settings page | ✅ admin only | We hold the AI key; users never see it |
| Creators (workspaces per streamer) | ✅ | Clippers handle many streamers; rename to "Channels" |
| Discord / Telegram alerts | 🙈 | Fine later |

### PeakCut branding and extras
| Feature | Verdict | Why |
|---|---|---|
| "Star on GitHub", "Follow me" buttons | ❌ | Not ours |
| "New version of PeakCut is out" notice + GitHub update check | ❌ | Set `PEAKCUT_UPDATE_CHECK=0`, remove the banner |
| PeakCut landing page | ❌ | Replaced by our own frontend |
| Legal pages | ✅ rewrite | ClipHeist name, content-permission clause in Terms |

## What we add (from the PRD)

| Add | Note |
|---|---|
| 🆕 Polar checkout + clip credits | Free trial / Starter $15 / Pro $35 / Premium $69 (launch prices; see PRD §8) |
| 🆕 Free tier with watermark | 1 stream, 3 clips |
| 🆕 GTA 6 prompt pack | Chase, crash, cops, heist, NPC, stunt, fail categories; GTA-style hooks |
| 🆕 Swear-word filter for captions | Test clips showed "REAL SH*T B*TCH" on screen. Option to mask words so TikTok/Shorts don't hold clips back |
| 🆕 Twitch past-VOD chat spikes | Better moment signal than loudness on talk-heavy streams |
| 🆕 Batch links | Premium |
| 🆕 Frame check to drop menu / loading-screen clips | One test clip was an inventory menu |
| 🆕 ClipHeist branding | Logo, colours, emails, page titles |

## Upstream PeakCut merged on 2026-11-25

ClipHeist was forked from PeakCut at commit `44b7c62` (2026-11-15). On 2026-11-25 everything upstream had added
since (50 commits, up to `52591d9` "The big update") was merged in: Streamers (who is live now, record and clip
24/7), templates/styles, campaigns, the posting schedule, "Clip anyway", cancelling a video, YouTube cookies and
routes for servers YouTube refuses, Arabic and other speech models, facecam layouts, ad and static-screen skipping,
a recorder that survives restarts, storage settings, and many fixes. ClipHeist's own parts were kept on top:
one sign-in through the website, clip credits and the free-plan watermark, Modal rendering and speech, "same link,
made once", the step-by-step progress, DeepSeek, and the zoomed-in look as the default template (`b-zoom`).
Still hidden as before: Instagram/TikTok cards, the "new version" bar, the "add your AI key" bar.
Upstream's own guides are kept as `docs/PEAKCUT_README.md`, `docs/PEAKCUT_AGENTS.md` and `docs/GAMING.md`.
To merge upstream again later: `git remote add upstream https://github.com/haziqahmedqasmi/PeakCut.git`, fetch, and
merge by hand (the folders differ: `studio` is `backend/studio`, `web` is `peakcut-ui`, `tests` is `backend/tests`).
