"""A live recording keeps its video buffer when the stream's clock jumps (Twitch's opening ad), and never sits
silently when its sound can't be read: the card says why, and after a while the recording ends with the reason."""
import os, subprocess, sys, tempfile, time
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-liverec-")
os.environ.update(DATABASE_URL="", PEAKCUT_VERIFY_EMAIL="0")
sys.path.insert(0, ".")
from pathlib import Path   # noqa: E402
from studio import alerts, auth, creators, ctx, db, jobs, live, transcribe   # noqa: E402
db.init()
fails = []


def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)


d = Path(os.environ["PEAKCUT_DATA"]) / "rec"
(d / "buffer").mkdir(parents=True)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30", "-t", "2", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-f", "mpegts", str(d / "buffer" / "jump.ts")], check=True)
check("a piece's real length is read from its frames", abs(live.piece_seconds(d / "buffer" / "jump.ts") - 2.0) < 0.1)
check("a file that isn't video has no length", live.piece_seconds(d / "buffer" / "nothing.ts"), -1.0)
check("pieces are cut by the wall clock, so a jump in the stream's clock can't stop the cutting",
      '"-segment_atclocktime", "1"' in Path("studio/live.py").read_text(encoding="utf-8"))

saved, told = [], []
jobs.save = lambda j: saved.append(j.get("stage"))
alerts.notify = lambda m: told.append(m)
job = {"id": "j1", "dir": str(d), "ai": {}, "clips": [], "source": {"name": "Tfue live", "url": "https://www.twitch.tv/tfue"}, "status": "live", "stage": "Recording"}
s = live.Session(job, {"buffer_min": 20})
check("an ordinary piece keeps the length ffmpeg listed", s._piece_len("p01_00001.ts", "6.067000", "12.067333"), lambda v: abs(v - 6.0) < 0.01)
check("the piece the clock jumped in is measured instead (listed as 0.02 s)", abs(s._piece_len("jump.ts", "32357.233", "32357.253") - 2.0) < 0.1)
check("...and one listed as hours long too, from what was measured before", abs(s._piece_len("jump.ts", "30.3", "32400.0") - 2.0) < 0.1)
check("the measured length is kept with the recording", list(s.fixed), ["jump.ts"])
s._save_state()
check("...and saved, so a restart reads the same clock", list(job["live_state"]["fixed"]), ["jump.ts"])
check("the short tail of the ad stays short", s._piece_len("gone_tail.ts", "30.067000", "30.334333"), lambda v: abs(v - 0.267) < 0.01)
check("a jumped piece that is gone counts as one piece", s._piece_len("gone.ts", "100.0", "50000.0"), 6.0)

# ---- the sound can't be read
s.samples = 16000 * 42
s._trouble(FileNotFoundError("No speech model files in D:/nowhere"))
check("the card says what is wrong, and keeps counting", (job["stage"], job["live"]["elapsed"], job["status"]),
      ("Recording, but clips are held up: the speech model is missing on this server", 42, "live"))
s._trouble(None)
check("when it works again the card goes back to Recording", (job["stage"], s.trouble_since), ("Recording", None))
s._trouble(RuntimeError("decoder blew up"))
check("any other failure is named in plain words", job["stage"], "Recording, but clips are held up: the sound of the stream could not be read")
s.trouble_since = time.time() - live.Session.TROUBLE_STOP - 5
s._trouble(RuntimeError("decoder blew up"))
check("if it stays broken the recording ends with the reason", (job["status"], job["stage"], s.user_stopped),
      ("done", "Stopped: the sound of the stream could not be read", True))
check("...and the person is told", len(told) == 1 and "Tfue live" in told[0] and "stopped" in told[0])

# ---- nothing is recorded where it could never be transcribed
u, _ = auth._create("rec@example.com", "rec", password_hash="x")
transcribe.model_ready, transcribe.REMOTE = (lambda: False), ""
with ctx.as_user(u.id):
    c = creators.create({"name": "Tfue", "channel": "https://www.twitch.tv/tfue"})
    try:
        live.start_session(c)
        got = "started"
    except ValueError as e:
        got = str(e)
check("a server without its speech model refuses to start a recording, and says why", "speech model is missing" in got)
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
