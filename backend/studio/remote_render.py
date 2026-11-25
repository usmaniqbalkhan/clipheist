"""Send one clip to the Modal clip maker (studio/render_worker.py) instead of downloading and rendering it here.

    PEAKCUT_RENDER_URL     the https://...modal.run address of deploy/modal_render.py
    PEAKCUT_RENDER_TOKEN   shared secret (defaults to PEAKCUT_ASR_TOKEN)
Without them, or when Modal fails, clips are made on this server as before."""
import base64
import io
import json
import os
import zipfile
from pathlib import Path

import httpx
import numpy as np

URL = os.environ.get("PEAKCUT_RENDER_URL", "").rstrip("/")
TOKEN = os.environ.get("PEAKCUT_RENDER_TOKEN") or os.environ.get("PEAKCUT_ASR_TOKEN", "")
PAD = 8


def enabled() -> bool:
    return bool(URL and TOKEN)


def cut(job: dict, clip: dict, words: list, audio, hook: str) -> dict:
    """Make the clip on Modal. Writes <id>.mp4/.jpg/.ass/.src.mp4 into the job's clips folder and returns
    {offset, src_end, start, end, width, height, layout, src}."""
    d = Path(job["dir"]) / "clips"
    d.mkdir(parents=True, exist_ok=True)
    want = max(0.0, clip["start"] - PAD)
    until = min(job["duration"] or clip["end"] + PAD, clip["end"] + PAD)
    hay_at = max(0.0, want - 20)
    hay = b""
    if audio is not None and len(audio):
        hay = np.asarray(audio[int(hay_at * 16000): int((want + 50) * 16000)], dtype=np.int16).tobytes()
    o = job["options"]
    body = {"url": job["source"]["url"], "want": want, "until": until, "quality": int(o.get("quality") or 720),
            "name": clip["id"], "hay": base64.b64encode(hay).decode(), "hay_at": hay_at,
            "clip": {"start": clip["start"], "end": clip["end"], "hook": hook, "style": clip.get("style", "bounce"),
                     "highlight_words": clip.get("highlight_words", []), "layout": clip.get("layout", "blur"),
                     "watermark": bool(job.get("watermark"))},
            "words": words}
    r = httpx.post(URL + "/cut", json=body, headers={"Authorization": f"Bearer {TOKEN}"},
                   timeout=httpx.Timeout(600, connect=20), follow_redirects=True)
    if r.status_code != 200:
        raise RuntimeError(f"Modal clip maker said {r.status_code}: {r.text[-300:]}")
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        meta = json.loads(z.read("meta.json"))
        for n in z.namelist():
            if n != "meta.json":
                (d / n).write_bytes(z.read(n))
    meta["src"] = str(d / f"{clip['id']}.src.mp4")
    return meta
