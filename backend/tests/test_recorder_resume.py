"""A restart that stops the recorder too (no recorder service: ffmpeg runs inside the app) no longer starts a new
recording from 0:00. The same recording carries on: what was recorded before the stop is taken in, the stream is
recorded again into the same recording, and its clock, transcript and clips continue.

The "stream" is a tone rising 10 Hz every second; the stand-in speech model reports the second each piece of audio
is from, as in test_recorder_local.py."""
import copy, json, os, subprocess, sys, tempfile, threading, time
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-resume-")
os.environ.update(DATABASE_URL="", PEAKCUT_RECORDER="0", PEAKCUT_VERIFY_EMAIL="0")
sys.path.insert(0, ".")
import numpy as np   # noqa: E402
from studio import alerts, auth, capture, config, creators, ctx, db, jobs, live, transcribe   # noqa: E402
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


BEFORE, AFTER = 40, 12           # seconds of stream offered before the restart (only part is recorded), and after it
parts = []
for i, (at, secs) in enumerate(((0, BEFORE), (100, AFTER))):
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


offered = []


def resolve(url, height=720):
    offered.append(time.time())
    if len(offered) > len(parts):
        raise live.NotLive("This channel is not live right now.")
    return {"urls": [str(parts[len(offered) - 1])], "title": "Test stream", "uploader": "Tester"}


real_start = capture.start


def start(jid, cmd, cwd):
    cmd = [x for x in cmd]
    for opt in ("-reconnect", "-reconnect_streamed", "-reconnect_delay_max"):
        i = cmd.index(opt)
        del cmd[i:i + 2]
    return real_start(jid, cmd[:3] + ["-re"] + cmd[3:], cwd)


transcribe.transcribe = hear
transcribe.model_ready = lambda: True
capture.start = start
live.resolve = resolve
live.Session._ai_check = lambda self, window=None, why=None: None
alerts.notify = lambda *a, **k: None
threading.Thread(target=jobs._live_worker, daemon=True).start()

u, _ = auth._create("resume@example.com", "resume", password_hash="x")
with ctx.as_user(u.id):
    c = creators.create({"name": "Tester", "channel": "https://kick.com/teststream"})
    job = live.start_session(c, "https://kick.com/teststream")
s = live.SESSIONS[job["id"]]
f = s
check("the recording is under way", wait_for(lambda: s.t_done >= 12 and (f.job.get("live_state") or {}).get("afiles"), 90))

# ---- the app stops: everything as it was last saved, and ffmpeg ends with it. (A real restart ends the process,
# so nothing is tidied away: the stream's buffer stays on disk.)
saved = copy.deepcopy(job)
f._cleanup = lambda: None
f.cap.stop()
s.stop_flag.set()
live.SESSIONS.pop(job["id"], None)
wait_for(lambda: s.finished, 30)
before = saved["live_state"]["pending_base"] / 16000
saved.update(status="done", stage="Recording paused by a server update")

# ---- the app is back and the stream is still on: the same recording carries on
with ctx.as_user(u.id):
    ok = live.resume(saved)
s2 = live.SESSIONS.get(saved["id"])
check("the same recording is carried on, not a new one", (ok, s2 is not None and s2.job is saved, saved["status"]), (True, True, "live"))
check("its clock carries on from where the transcript had got to, not from 0:00", s2.samples / 16000, lambda t: t >= before - 0.1)
check("it records to the end of the stream", wait_for(lambda: s2.finished, 120))
recorded = s2.samples / 16000
check("everything recorded before the stop is in, and the stream after it", recorded, lambda t: t >= before + AFTER - 1.5)
words = [[w["w"], w["s"], w["e"]] for w in s2.words]
joins = [round(words[i + 1][1] - words[i][2], 2) for i in range(len(words) - 1)]
check("one transcript on one clock: no gap, no repeat, across the restart too", [j for j in joins if abs(j) > 0.02], [])
check("the sound after the restart is the stream after the restart", float(words[-1][0]), lambda p: p >= 100)
check("everything from before the restart is from the moment the clock says", [w for w in words if w[2] <= before and abs(float(w[0]) - (w[1] + w[2]) / 2) > 0.6], [])
check("the stream was recorded again as a new part of the same recording", sorted(f.name for f in s2.buf.glob("p*.csv")) or ["(emptied)"],
      lambda names: names == ["(emptied)"] or "p02.csv" in names)
check("it ends as one finished recording", (saved["status"], saved["stage"]), ("done", "Stream ended"))
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
