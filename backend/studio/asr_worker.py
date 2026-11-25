"""The speech-to-text machine: a tiny service that turns pieces of audio into words, meant for a machine with a
graphics card. The main ClipHeist server sends it pieces and gets the words back (see studio/transcribe.py).

Run with:  python -m studio.asr_worker
Settings:  PEAKCUT_ASR_PROVIDER=cuda, PEAKCUT_ASR_TOKEN=<shared secret>, PEAKCUT_ASR_PORT (9000),
           PEAKCUT_ASR_BATCH (pieces decoded together on the card, default 16),
           PEAKCUT_ASR_LANGS (the spoken languages it has models for, e.g. "en,ar"; default "en")

It holds nothing: no accounts, no files, no database. It only answers on the private network, and only to callers
that know the shared secret."""
import hmac
import os
import threading
import time

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, Request

from . import fetch_model, transcribe

TOKEN = os.environ.get("PEAKCUT_ASR_TOKEN", "").strip()
PORT = int(os.environ.get("PEAKCUT_ASR_PORT", "9000"))
CARD_BATCH = int(os.environ.get("PEAKCUT_ASR_BATCH", "16"))
MAX_BODY = 64 * 1024 * 1024             # about 33 minutes of sound in one request: far more than is ever sent
MAX_PIECE = 16000 * 40                  # a piece is 12 to 24 seconds; 40 is the ceiling
# Languages this machine has models for. Asked for another one, it says so (409) and the main server does those
# pieces itself, so a video is never transcribed with the wrong language's model.
LANGS = [x for x in (s.strip() for s in os.environ.get("PEAKCUT_ASR_LANGS", "en").split(",")) if fetch_model.known(x)] or ["en"]

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_stats = {"pieces": 0, "audio_s": 0.0, "busy_s": 0.0, "since": time.time()}
_slock = threading.Lock()
_busy = [0]                             # requests being decoded right now

# The card's memory: the speech library keeps the scratch memory of every batch it has seen and never hands it back,
# so over days it fills the card and every request fails. The service then restarts itself (Docker starts it again,
# the model reloads in about 20 seconds, nothing is lost: it holds no data). Meanwhile ClipHeist decodes on its own
# processors, as it does whenever this machine is away.
MEM_FULL = float(os.environ.get("PEAKCUT_ASR_MEM_FULL", "0.85"))    # restart, when idle, once this share is in use
_CARD_ERRORS = ("cuda", "cublas", "cudnn", "out of memory", "allocation failed", "allocate")


def _restart(why: str):
    print(f"Speech machine restarting: {why}", flush=True)
    threading.Timer(0.3, lambda: os._exit(1)).start()     # after this answer has gone out


def _card_share():
    """Share of the card's memory in use (0 to 1), or None if it can't be read."""
    try:
        import ctypes
        nv = ctypes.CDLL("libnvidia-ml.so.1")
        if nv.nvmlInit_v2() != 0:
            return None
        h = ctypes.c_void_p()
        if nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(h)) != 0:
            return None

        class Mem(ctypes.Structure):
            _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]
        m = Mem()
        if nv.nvmlDeviceGetMemoryInfo(h, ctypes.byref(m)) != 0 or not m.total:
            return None
        return m.used / m.total
    except Exception:  # noqa: BLE001
        return None


@app.get("/healthz")
def healthz():
    with _slock:
        s = dict(_stats)
    share = _card_share()
    return {"ok": True, "provider": transcribe.PROVIDER, "langs": LANGS, "pieces": s["pieces"], "audio_hours": round(s["audio_s"] / 3600, 2),
            "speed": round(s["audio_s"] / s["busy_s"], 1) if s["busy_s"] else None,
            "card_memory": round(share, 2) if share is not None else None}


@app.post("/decode")
async def decode(request: Request):
    if not TOKEN or not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + TOKEN):
        raise HTTPException(401, "no")
    lang = request.headers.get("x-lang", "").strip() or fetch_model.DEFAULT
    if lang not in LANGS or not fetch_model.ready(lang):
        raise HTTPException(409, "no model for that language here")
    try:
        lens = [int(x) for x in request.headers.get("x-lens", "").split(",") if x]
    except ValueError:
        raise HTTPException(400, "bad lengths")
    if not lens or len(lens) > 256 or any(n <= 0 or n > MAX_PIECE for n in lens):
        raise HTTPException(400, "bad lengths")
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        raise HTTPException(413, "too much")
    body = await request.body()
    if len(body) != 2 * sum(lens) or len(body) > MAX_BODY:
        raise HTTPException(400, "lengths do not match the audio")
    audio = np.frombuffer(body, dtype="<i2").astype(np.float32) / 32768.0
    pieces, pos = [], 0
    for n in lens:
        pieces.append(audio[pos:pos + n])
        pos += n

    def work():
        t0 = time.time()
        out = []
        for i in range(0, len(pieces), max(1, CARD_BATCH)):
            out += transcribe.decode_pieces(pieces[i:i + max(1, CARD_BATCH)], lang=lang)
        with _slock:
            _stats["pieces"] += len(pieces)
            _stats["audio_s"] += sum(lens) / 16000
            _stats["busy_s"] += time.time() - t0
        return out

    import anyio
    with _slock:
        _busy[0] += 1
    try:
        got = await anyio.to_thread.run_sync(work)
    except Exception as e:  # noqa: BLE001
        if any(k in str(e).lower() for k in _CARD_ERRORS):     # the card is out of memory: start fresh
            _restart("the card ran out of memory")
            raise HTTPException(503, "restarting")
        raise
    finally:
        with _slock:
            _busy[0] -= 1
            idle = _busy[0] == 0
    if idle:
        share = _card_share()
        if share is not None and share >= MEM_FULL:            # nearly full: start fresh now, before requests fail
            _restart(f"card memory {share:.0%} in use")
    return {"lang": lang, "pieces": [{"tokens": tok, "timestamps": ts} for tok, ts in got]}


def main():
    if not TOKEN:
        raise SystemExit("Set PEAKCUT_ASR_TOKEN: the speech machine refuses to start without a shared secret.")
    for lang in LANGS:                  # fetched if missing, and loaded now, so the first request is not the slow one
        fetch_model.ensure(lang)
        transcribe._recognizer(lang)
    print(f"Speech machine ready on port {PORT} ({transcribe.PROVIDER}, {', '.join(LANGS)})", flush=True)
    uvicorn.run(app, host=os.environ.get("PEAKCUT_ASR_HOST", "0.0.0.0"), port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
