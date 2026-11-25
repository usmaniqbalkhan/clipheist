"""Clip maker that runs on Modal (deploy/modal_render.py): downloads just one clip's stretch of the video with its
own fast connection, finds where it really starts, renders the vertical clip with captions, and sends back a zip
with the clip, its cover, its captions file and the downloaded stretch (kept so later edits render locally).

POST /cut   (Authorization: Bearer <PEAKCUT_RENDER_TOKEN>)   JSON:
    url, want, until, quality, name, hay (base64 int16 mono 16 kHz audio), hay_at (seconds where hay starts),
    clip {start, end, hook, style, highlight_words, layout, watermark}, words [{w, s, e}] with times in the video
The zip holds <name>.mp4, <name>.jpg, <name>.ass, <name>.src.mp4 and meta.json."""
import base64
import hmac
import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from . import config, media, render

TOKEN = os.environ.get("PEAKCUT_RENDER_TOKEN") or os.environ.get("PEAKCUT_ASR_TOKEN", "")
app = FastAPI()


@app.get("/healthz")
def healthz():
    return {"ok": True}


def cut(p: dict) -> bytes:
    work = Path(tempfile.mkdtemp(prefix="cut"))
    try:
        name = "".join(ch for ch in str(p["name"]) if ch.isalnum() or ch in "-_")[:60] or "clip"
        piece = media.download_section(p["url"], work, f"{name}.src", float(p["want"]), float(p["until"]),
                                       int(p.get("quality") or 720))
        meta = media.probe(piece)
        wav = work / "part.wav"
        media.extract_audio(piece, wav)
        part = media.load_audio(wav)
        hay = np.frombuffer(base64.b64decode(p.get("hay") or b""), dtype=np.int16)
        at = float(p.get("hay_at") or 0)
        want = float(p["want"])
        off = media.find_offset(hay, part, want - at) + at if len(hay) else want
        c = dict(p["clip"])
        start, end = max(float(c["start"]), off), min(float(c["end"]), off + meta["duration"])
        if end - start < 1:
            raise RuntimeError("The downloaded part of the video does not cover this clip.")
        local = dict(c, start=start - off, end=end - off)
        near = [dict(w, s=w["s"] - off, e=w["e"] - off) for w in p.get("words") or []]
        out = render.render_clip(piece, work / "out", name, local, near, meta["width"], meta["height"])
        info = {"offset": round(off, 2), "src_end": round(off + meta["duration"], 2), "start": start, "end": end,
                "width": meta["width"], "height": meta["height"], "layout": out["layout"]}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for ext in ("mp4", "jpg", "ass"):
                z.write(work / "out" / f"{name}.{ext}", f"{name}.{ext}")
            z.write(piece, f"{name}.src.mp4")
            z.writestr("meta.json", json.dumps(info))
        return buf.getvalue()
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.post("/cut")
async def cut_route(request: Request):
    got = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not TOKEN or not hmac.compare_digest(got, TOKEN):
        raise HTTPException(401, "bad token")
    p = await request.json()
    try:
        data = await run_in_threadpool(cut, p)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)[-600:]) from None
    return Response(data, media_type="application/zip")


config.FONTS_DIR.mkdir(parents=True, exist_ok=True)
render.install_fonts()
