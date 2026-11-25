"""Without the recorder service (your own computer, or the recorder being down) a live recording still works: ffmpeg
runs as the app's own child. A stream that drops and comes back is one recording on one clock, the audio files
are deleted as they are transcribed, and when the stream ends the recording wraps up by itself.

The "stream" is a tone rising 10 Hz every second, in two files (before and after the drop); the stand-in speech
model reports the second each piece of audio is from, as in test_recorder_restart.py."""
import os, subprocess, sys, tempfile, threading, time
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-reclocal-")
os.environ.update(DATABASE_URL="", PEAKCUT_RECORDER="0", PEAKCUT_VERIFY_EMAIL="0")
sys.path.insert(0, ".")
import numpy as np   # noqa: E402
from studio import alerts, auth, brain, capture, config, creators, ctx, db, jobs, live, transcribe   # noqa: E402
db.init()
fails = []


def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)


def wait_for(fn, secs):
    end = time.time() + secs
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.3)
    return False


FIRST, SECOND = 14, 14          # seconds of stream before the drop, and after it
parts = []
for i, (at, secs) in enumerate(((0, FIRST), (FIRST, SECOND))):
    f = config.DATA_DIR / f"stream{i}.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-f", "lavfi",
                    "-i", f"aevalsrc=0.5*sin(2*PI*(300*(t+{at})+5*(t+{at})*(t+{at}))):s=44100", "-t", str(secs),
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "10", "-c:a", "aac", str(f)], check=True)
    parts.append(f)


def hear(audio, on_progress=None, lang=None):
    a = np.asarray(audio)
    dur = len(a) / 16000
    if dur < 0.3:
        return []
    neg = a < 0
    pitch = int(np.count_nonzero(neg[1:] != neg[:-1])) / (2 * dur)
    return [{"w": f"{(pitch - 300) / 10:.2f}", "s": 0.0, "e": round(dur, 2)}]


asked = []


def resolve(url, height=720):
    asked.append(time.time())
    if len(asked) > len(parts):
        raise live.NotLive("This channel is not live right now.")
    return {"urls": [str(parts[len(asked) - 1])], "title": "Test stream", "uploader": "Tester"}


real_start, kinds, most = capture.start, [], [0]


def start(jid, cmd, cwd):
    cmd = [x for x in cmd]
    for opt in ("-reconnect", "-reconnect_streamed", "-reconnect_delay_max"):
        i = cmd.index(opt)
        del cmd[i:i + 2]
    cap = real_start(jid, cmd[:3] + ["-re"] + cmd[3:], cwd)
    kinds.append(type(cap).__name__)
    return cap


transcribe.transcribe = hear
transcribe.model_ready = lambda: True      # the stand-in above is the speech model here
capture.start = start
live.resolve = resolve
live.Session._ai_check = lambda self, window=None, why=None: None
alerts.notify = lambda *a, **k: None
threading.Thread(target=jobs._live_worker, daemon=True).start()

u, _ = auth._create("local@example.com", "local", password_hash="x")
with ctx.as_user(u.id):
    c = creators.create({"name": "Tester", "channel": "https://kick.com/teststream"})
    job = live.start_session(c, "https://kick.com/teststream")
s = live.SESSIONS[job["id"]]
check("the recording is its own", type(s).__name__, "Session")


def watch():                    # how many audio files are on disk at the worst moment
    while not s.finished:
        most[0] = max(most[0], len(list(s.buf.glob("p*_a*.pcm"))))
        time.sleep(0.5)


threading.Thread(target=watch, daemon=True).start()
check("the recording wraps up by itself when the stream ends", wait_for(lambda: s.finished, 120))
words = [[w["w"], w["s"], w["e"]] for w in s.words]
check("ffmpeg ran as the app's own child, once before the drop and once after", kinds, ["Local", "Local"])
check("the stream was asked for again after it dropped, then found to be over", len(asked), 3)
check("both halves were recorded, on one clock", s.samples / 16000, lambda t: abs(t - (FIRST + SECOND)) < 1.5)
joins = [round(words[i + 1][1] - words[i][2], 2) for i in range(len(words) - 1)]
check("the transcript has no gap and no repeat, across the drop too", [j for j in joins if abs(j) > 0.02], [])
off = [round(float(w) - (a + b) / 2, 2) for w, a, b in words]
check("every piece of sound is from the moment the clock says", [o for o in off if abs(o) > 0.6], [])
check("all of it was transcribed", (words[-1][2] if words else 0), lambda t: abs(t - s.samples / 16000) < 0.1)
check("loudness is one value a second", len(s.db), lambda n: abs(n - s.samples / 16000) < 2)
check("transcribed sound was deleted as it went (never more than a few files)", most[0], lambda n: 1 <= n <= 4)
check("it ended as a finished recording", (job["status"], job["stage"]), ("done", "Stream ended"))
check("the buffer is emptied at the end", wait_for(lambda: not s.buf.exists() or not any(s.buf.glob("*")), 15))
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
