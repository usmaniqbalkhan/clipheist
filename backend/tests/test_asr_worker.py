"""The speech machine starts fresh when the card's memory fills: right away when a request fails for lack of card
memory, and when idle once the memory is nearly full. It holds no data, so a restart loses nothing."""
import os, sys
os.environ["PEAKCUT_ASR_TOKEN"] = "t"
sys.path.insert(0, ".")
import numpy as np                                   # noqa: E402
from fastapi.testclient import TestClient           # noqa: E402
from studio import asr_worker, transcribe            # noqa: E402

fails = []


def check(n, got, want=True):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)


restarts = []
asr_worker._restart = lambda why: restarts.append(why)
c = TestClient(asr_worker.app)
body = (np.zeros(16000, dtype="<i2")).tobytes()
H = {"authorization": "Bearer t", "x-lens": "16000"}

transcribe.decode_pieces = lambda pieces, lang=None: [(["hi"], [0.1]) for _ in pieces]
asr_worker._card_share = lambda: 0.40
r = c.post("/decode", content=body, headers=H)
check("a normal request is answered", (r.status_code, r.json()["pieces"][0]["tokens"]), (200, ["hi"]))
check("...and nothing restarts while the card has room", restarts, [])

asr_worker._card_share = lambda: 0.90
r = c.post("/decode", content=body, headers=H)
check("nearly full: the request is still answered", r.status_code, 200)
check("...then the machine starts fresh, while idle", len(restarts), 1)


def boom(pieces, lang=None):
    raise RuntimeError("CUBLAS failure 3: the resource allocation failed")


transcribe.decode_pieces = boom
asr_worker._card_share = lambda: 0.50
restarts.clear()
r = c.post("/decode", content=body, headers=H)
check("out of card memory: the caller is told to go elsewhere for now", r.status_code, 503)
check("...and the machine starts fresh", len(restarts), 1)

check("health shows how full the card is", "card_memory" in c.get("/healthz").json())

print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
