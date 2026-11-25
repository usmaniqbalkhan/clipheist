"""An app update must not cut a live recording. This runs the real thing in three processes, with no internet:

  the recorder service  (python -m studio.recorder), which runs ffmpeg and stays up throughout
  app no. 1             starts a live recording through it, transcribes, queues a clip, and is then KILLED
  app no. 2             a fresh process, as after a deploy: it must find the recording still going and carry on

The "stream" is a test video whose sound is a tone rising 10 Hz every second, so the pitch of any piece of audio
says which second of the stream it is. The speech model is replaced by one that reports that second, which lets
the test see whether the transcript carries on across the restart with nothing lost and nothing heard twice."""
import json, os, subprocess, sys, tempfile, time
APP = sys.argv[1:2] == ["app"]
if not APP:
    os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-restart-")
os.environ.update(DATABASE_URL="", PEAKCUT_RECORDER="1", PEAKCUT_VERIFY_EMAIL="0",
                  PYTHONIOENCODING="utf-8")
sys.path.insert(0, ".")
from pathlib import Path   # noqa: E402
from studio import capture, config   # noqa: E402

STREAM = config.DATA_DIR / "stream.ts"
URL = "https://kick.com/teststream"


def wait_for(fn, secs, every=0.3):
    end = time.time() + secs
    while time.time() < end:
        if fn():
            return True
        time.sleep(every)
    return False


def app(phase: str):
    """One life of the app. Phase 1 starts the recording and waits to be killed; phase 2 picks it up."""
    import threading
    import numpy as np
    from studio import alerts, auth, brain, creators, ctx, db, jobs, live, transcribe
    db.init()

    def hear(audio, on_progress=None, lang=None):
        a = np.asarray(audio)
        dur = len(a) / 16000
        if dur < 0.3:
            return []
        neg = a < 0
        pitch = int(np.count_nonzero(neg[1:] != neg[:-1])) / (2 * dur)
        return [{"w": f"{(pitch - 300) / 10:.2f}", "s": 0.0, "e": round(dur, 2)}]   # the word is the second it was heard at

    real_start = capture.start

    def start(jid, cmd, cwd):       # the test stream is a file: played at real speed, and no web reconnect options
        cmd = [x for x in cmd]
        for opt in ("-reconnect", "-reconnect_streamed", "-reconnect_delay_max"):
            i = cmd.index(opt)
            del cmd[i:i + 2]
        return real_start(jid, cmd[:3] + ["-re"] + cmd[3:], cwd)

    transcribe.transcribe = hear
    transcribe.model_ready = lambda: True      # the stand-in above is the speech model here
    capture.start = start
    live.resolve = lambda url, height=720: {"urls": [str(STREAM)], "title": "Test stream", "uploader": "Tester"}
    live.Session._ai_check = lambda self, window=None, why=None: None          # no AI in this test
    brain.describe = lambda text, title="", uploader="": {"score": 0, "category": "other", "hook": "A CLIP", "title": "A clip",
                                                          "description": "", "hashtags": [], "highlight_words": [], "reason": "By hand."}
    alerts.notify = lambda *a, **k: None

    def render_one(job, clip, words):                                          # the clip's footage is real; the render is not
        clip.update(status="done")
        jobs.save(job)
    jobs.render_one = render_one
    state = lambda jid: capture._read(capture.folder() / f"{jid}.state.json")   # noqa: E731
    out = {}
    try:
        if phase == "1":
            u, _ = auth._create("live@example.com", "live", password_hash="x")
            with ctx.as_user(u.id):
                c = creators.create({"name": "Tester", "channel": URL})
                job = live.start_session(c, URL)
            s = live.SESSIONS[job["id"]]
            out["recording"] = wait_for(lambda: s.samples >= 16 * 16000 and len(s.words) >= 2, 60)
            s.clip_last(8)
            on_disk = lambda: json.loads((Path(job["dir"]) / "job.json").read_text(encoding="utf-8"))   # noqa: E731
            out["clip_queued"] = wait_for(lambda: any(k.get("status") == "queued" for k in on_disk().get("clips", [])), 30)
            time.sleep(2.5)                                                    # one more save, as in normal running
            fid = job["id"]
            out.update(jid=job["id"], fid=fid, cap=type(s.cap).__name__, samples=s.samples, t_done=s.t_done, words=len(s.words),
                       pid=state(fid).get("pid"), running=state(fid).get("running"))
        else:
            first = json.loads((config.DATA_DIR / "phase1.json").read_text(encoding="utf-8"))
            jid, fid = first["jid"], first["fid"]
            jobs.load_all()
            threading.Thread(target=jobs._live_worker, daemon=True).start()
            live.start()
            s = live.SESSIONS.get(jid)
            out["attached"] = bool(s)
            out["jobs"] = len(jobs.all_jobs())
            if s:
                job = s.job
                out["picked_up_at"] = s.pending_base / 16000
                out["carried_on"] = wait_for(lambda: s.t_done >= first["t_done"] + 14, 60)
                out["clip_done"] = wait_for(lambda: all(k.get("status") == "done" for k in job["clips"]), 60)
                audio = sorted(s.buf.glob("p*_a*.pcm"))
                out.update(stage=job.get("stage"), status=job.get("status"), words=[[w["w"], w["s"], w["e"]] for w in s.words],
                           t_done=s.t_done, pid=state(fid).get("pid"), running=state(fid).get("running"),
                           clips=[[k["id"], k.get("status"), bool(k.get("src") and Path(k["src"]).is_file())] for k in job["clips"]],
                           audio_files=len(audio), audio_bytes=sum(f.stat().st_size for f in audio),
                           video_pieces=len(list(s.buf.glob("p*_*.ts"))), loudness=len(s.db), seconds=s.samples / 16000)
                s.stop()
                out["finished"] = wait_for(lambda: s.finished, 40)
                out["stopped"] = wait_for(lambda: capture.running(fid) is None, 20)
                left = lambda: sorted(f.name for f in capture.folder().glob(f"{fid}*"))   # noqa: E731
                wait_for(lambda: not left(), 8)                    # the recorder tidies its side within a second or two
                wait_for(lambda: not s.buf.exists() or not any(s.buf.glob("*")), 15)   # the buffer goes once nobody needs it
                time.sleep(2.5)                                    # ...and nothing turns up afterwards
                out.update(end_status=job.get("status"), end_stage=job.get("stage"),
                           buffer_left=len(list(s.buf.glob("*"))) if s.buf.exists() else 0, recorder_left=left())
    except Exception as e:  # noqa: BLE001
        import traceback
        out["error"] = f"{e}\n{traceback.format_exc()[-1500:]}"
    capture._write(config.DATA_DIR / f"phase{phase}.json", out)
    if phase == "1":
        time.sleep(600)             # recording away, until the test kills this process


if APP:
    app(sys.argv[2])
    sys.exit(0)

# ============================================================== the test itself
fails = []


def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)


subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-f", "lavfi",
                "-i", "aevalsrc=0.5*sin(2*PI*(300*t+5*t*t)):s=44100", "-t", "170", "-c:v", "libx264", "-preset", "ultrafast",
                "-g", "10", "-c:a", "aac", str(STREAM)], check=True)
env = {**os.environ}
procs = []
rec = subprocess.Popen([sys.executable, "-m", "studio.recorder"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
try:
    check("the recorder is up", wait_for(capture.recorder_up, 20))
    p1 = subprocess.Popen([sys.executable, __file__, "app", "1"], env=env); procs.append(p1)
    f1 = config.DATA_DIR / "phase1.json"
    wait_for(f1.exists, 120)
    a = capture._read(f1)
    if a.get("error"):
        print(a["error"])
    jid, fid = a.get("jid", ""), a.get("fid", "")
    check("app 1: the recording runs in the recorder, not in the app", (a.get("cap"), a.get("running")), ("Remote", True))
    check("app 1: it recorded and transcribed", (a.get("recording"), (a.get("words") or 0) >= 2), (True, True))
    check("app 1: a clip is waiting to be made", a.get("clip_queued"))

    p1.kill(); p1.wait()                                          # the app goes down, as in a deploy
    buf = next(config.DATA_DIR.glob(f"users/*/creators/*/live/{jid}/buffer"), None) or Path("missing")
    heard = lambda: sum(f.stat().st_size for f in buf.glob("p*_a*.pcm"))   # noqa: E731
    before = heard()
    time.sleep(4)
    st = capture._read(capture.folder() / f"{fid}.state.json")
    check("with the app gone, the same ffmpeg is still recording", (st.get("running"), st.get("pid"), time.time() - st.get("beat", 0) < 10),
          (True, a.get("pid"), True))
    check("...and sound is still coming in", heard() > before + 2 * 32000, True)

    p2 = subprocess.Popen([sys.executable, __file__, "app", "2"], env=env); procs.append(p2)
    f2 = config.DATA_DIR / "phase2.json"
    wait_for(f2.exists, 200)
    b = capture._read(f2)
    if b.get("error"):
        print(b["error"])
    check("app 2: it picked the recording up, as the same recording", (b.get("attached"), b.get("jobs"), b.get("stage")), (True, 1, "Recording"))
    check("app 2: the same ffmpeg, never restarted", (b.get("pid"), b.get("running")), (a.get("pid"), True))
    check("app 2: it started from where the transcript had got to", b.get("picked_up_at"), lambda t: t is not None and abs(t - a["t_done"]) < 8)
    check("app 2: the transcript carried on", b.get("carried_on"))
    words = b.get("words") or []
    check("the words from before the restart are still there", len(words) > (a.get("words") or 0) >= 2)
    joins = [round(words[i + 1][1] - words[i][2], 2) for i in range(len(words) - 1)]
    check("no gap and no repeat in the transcript: each piece starts where the last ended", [j for j in joins if abs(j) > 0.02], [])
    off = [round(float(w) - (s + e) / 2, 2) for w, s, e in words]
    check("every piece of sound is from the moment the clock says, before and after the restart", [o for o in off if abs(o) > 0.5], [])
    check("the transcript reaches well past the restart", b.get("t_done", 0) >= a.get("t_done", 0) + 14)
    check("the clip that was waiting got its footage and was made", b.get("clips"), lambda c: bool(c) and all(k[1] == "done" and k[2] for k in c))
    check("transcribed sound is deleted as it goes", (b.get("audio_files"), b.get("seconds", 0) > 30),
          lambda v: v[0] is not None and v[0] <= 5 and v[1])
    check("loudness is still one value a second", (b.get("loudness"), b.get("seconds")), lambda v: v[0] is not None and abs(v[0] - v[1]) < 2)
    check("Stop works on a picked-up recording", (b.get("finished"), b.get("stopped"), b.get("end_status"), b.get("end_stage")),
          (True, True, "done", "Stopped by you"))
    check("nothing is left behind: the buffer and the recorder's files are gone", (b.get("buffer_left"), b.get("recorder_left")), (0, []))
finally:
    for p in procs:
        if p.poll() is None:
            p.kill()
    for f in capture.folder().glob("*.json"):                      # any recording still going stops with its request
        if not f.name.endswith(".state.json"):
            f.unlink(missing_ok=True)
    time.sleep(3)
    rec.terminate()
    try:
        rec.wait(15)
    except subprocess.TimeoutExpired:
        rec.kill()

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
