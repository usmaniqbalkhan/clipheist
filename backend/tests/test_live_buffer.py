"""A live clip waiting to be made keeps its footage. On 16 Nov 2026 live clips waited in a busy render queue longer
than the 20-minute buffer, the buffer trimmed their footage, and they failed with "That part of the stream is no
longer in the buffer". Footage a waiting clip needs is now kept until the clip has its own copy."""
import os, sys, tempfile, threading
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-livebuf-")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from pathlib import Path   # noqa: E402
from studio import config, live   # noqa: E402
fails = []


def check(n, got, want=True):
    print(("PASS " if got == want else "FAIL ") + n + ("" if got == want else f"   got {got!r}")); got == want or fails.append(n)


buf = Path(tempfile.mkdtemp(prefix="buf-"))
rows = []
for i in range(300):                        # 300 pieces of 10 s: 50 minutes of stream
    (buf / f"s{i:04d}.ts").write_bytes(b"x")
    rows.append(f"s{i:04d}.ts,{i * 10}.0,{i * 10 + 10}.0")
(buf / "p01.csv").write_text("\n".join(rows), encoding="utf-8")

s = live.Session.__new__(live.Session)
s.lock, s.buf, s.part, s.csv_seen, s.segments, s.seg_clock = threading.RLock(), buf, 1, {}, [], 0.0
s.csv_end, s.listed, s.swept, s.fixed = {}, set(), 0.0, {}
s.settings = {"buffer_min": 20}
s.job = {"clips": [{"id": "clip001", "start": 600.0, "end": 630.0, "status": "queued"}]}

s._scan_segments()
check("a clip at minute 10 still waiting: its footage is kept past the 20-minute buffer", (buf / "s0060.ts").exists())
check("footage from before it is trimmed as usual", (buf / "s0010.ts").exists(), False)
check("the last 20 minutes are kept", all((buf / f"s{i:04d}.ts").exists() for i in range(180, 300)))

s.job["clips"][0].update(status="rendering", src="clip001.src.ts")      # it has its own copy now
s._scan_segments()
check("once the clip has its own copy, its footage is trimmed", (buf / "s0060.ts").exists(), False)
check("the last 20 minutes are still kept", (buf / "s0200.ts").exists())

check("live clips are made by several workers", config.LIVE_RENDER_WORKERS >= 2)
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
