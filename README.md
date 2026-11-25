# ClipHeist

Paste a GTA 6 stream link, get ready-to-post vertical clips with captions, hooks, titles and hashtags.
clipheist.site · built on [PeakCut](https://github.com/haziqahmedqasmi/PeakCut) (MIT, see `LICENSE`).

## Layout

```
clipheist/
├── backend/            Python server and clip pipeline (FastAPI, yt-dlp, ffmpeg, speech-to-text)
│   ├── studio/         the app code
│   ├── tests/          python tests/run.py
│   ├── deploy/         Caddy / nginx / GPU speech worker configs
│   ├── data/           local videos, clips, database, speech model (git-ignored)
│   ├── Dockerfile      build from the repo root (docker-compose.yml does this)
│   └── .env.example    every setting
├── frontend/           public site: Vite + React (landing `/` and `/pricing`), deployed on Vercel
├── peakcut-ui/         the signed-in app pages the backend serves (plain HTML/JS, from PeakCut)
├── assets/             originals: images/ and videos/ (web-sized copies live in frontend/public; videos/ is git-ignored)
└── docs/               PRD, project brief, deploy guide, code map
```

The backend serves the app pages in `peakcut-ui` (override with `PEAKCUT_WEB_DIR`). The public site in `frontend/` is a separate React app.

## Run locally

Needs Python 3.10+ and `ffmpeg`.

```bash
cd backend
uv venv -p 3.12 .venv && uv pip install -p .venv -r requirements.txt
.venv/bin/python -m studio.fetch_model      # speech model, once (~480 MB)
.venv/bin/python -m studio.server           # http://127.0.0.1:8765
```

Tests: `cd backend && .venv/bin/python tests/run.py`

## Public site (frontend)

```bash
cd frontend
npm install
npm run dev        # http://127.0.0.1:5500
npm run build      # output in frontend/dist
```

Vercel: import the repo, set **Root Directory** to `frontend`. `vercel.json` handles the build and page routes.
`frontend/reference/landing.html` is the original single-file landing page the React version was ported from.

## Docs

- [docs/PRD.md](docs/PRD.md) — what we are building and why
- [docs/PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md) — hackathon brief
- [docs/AGENTS.md](docs/AGENTS.md) — map of the code
- [docs/DEPLOY.md](docs/DEPLOY.md) — server deployment (PeakCut's guide)
- [docs/PEAKCUT_README.md](docs/PEAKCUT_README.md) — original PeakCut readme
- [docs/FEATURES.md](docs/FEATURES.md) — what the base has, what we keep, hide, remove and add
- [docs/SETUP.md](docs/SETUP.md) — where everything lives, Vercel variables, dashboard steps, server commands
- [docs/POLAR.md](docs/POLAR.md) — our Polar products, discounts and checkout links
- [docs/polar-api-reference.md](docs/polar-api-reference.md) — Polar API notes (auth, SDK, pagination, rate limits)
