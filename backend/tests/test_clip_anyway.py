"""Clip anyway: when the AI finds nothing clip-worthy in a video, the person can ask again and get the best moments
there are. The transcript is reused (not written again), the AI is told it must return moments, and if
it still returns none the loudest stretches with speech are taken. With PEAKCUT_TIDY=1 a video with no clips keeps
its source a few days so this works. The slow parts (video, audio, the AI) are replaced by stand-ins."""
import json, os, sys, tempfile, time
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-anyway-")
os.environ.update(DATABASE_URL="", PEAKCUT_VERIFY_EMAIL="0", PEAKCUT_KEEP_SOURCE="0", PEAKCUT_TIDY="1")
sys.path.insert(0, ".")
from pathlib import Path   # noqa: E402
import numpy as np   # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402
from studio import auth, brain, ctx, db, jobs, media, server, transcribe   # noqa: E402
db.init()
fails = []


def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)


WORDS = [{"w": f"word{i}", "s": float(i), "e": float(i) + 0.8} for i in range(60)]

# ---- the AI is told to return moments, and something always comes back
prompts = []
brain.ask_ai = lambda prompt, schema=None, purpose="", **k: (prompts.append(prompt) or [], "test-model", 10)
db_ = np.zeros(60); db_[30] = 30.0
none, _ = brain.find_clips(WORDS, db_, 60.0, "Title", "Someone", 2, 15, 30)
check("normally, nothing worth clipping means no clips", none, [])
check("...and the AI may say so", "Return [] if nothing is worth clipping" in prompts[-1])
forced, _ = brain.find_clips(WORDS, db_, 60.0, "Title", "Someone", 2, 15, 30, force=True)
check("Clip anyway tells the AI it must return moments", "Do NOT return []" in prompts[-1] and "Never return []" in prompts[-1])
check("if the AI still returns none, the loudest stretch with speech is taken", len(forced) >= 1 and forced[0]["start"] <= 30 <= forced[0]["end"])

# ---- a person and a finished video with nothing clipped
u, _ = auth._create("a@example.com", "a", password_hash="x")
BASE = "https://clips.example.com"
cl = TestClient(server.app, base_url=BASE, follow_redirects=False)
cl.cookies.set(auth.COOKIE, auth.start_session(u.id))
H = {"Origin": BASE}
with ctx.as_user(u.id):
    job = jobs.create({"type": "file", "path": "", "name": "talk.mp4"}, {}, enqueue=False)
d = Path(job["dir"])
src = d / "source.mp4"; src.write_bytes(b"x" * 1000)
(d / "words.json").write_text(json.dumps(WORDS), encoding="utf-8")
job.update(status="done", stage="Done, but nothing clip-worthy was found", duration=60, paid_blocks=1, done_blocks=1,
           source={"type": "file", "path": str(src), "name": "talk.mp4"}, source_path=str(src), empty_since=time.time())
jobs.save(job)

with ctx.as_user(u.id):
    check("a video with no clips keeps its source for a few days", (jobs.slim(job), src.is_file()), (0, True))
    job["empty_since"] = time.time() - 4 * 86400
    jobs.slim(job)
    check("...and lets it go after that", src.is_file(), False)
src.write_bytes(b"x" * 1000); job["empty_since"] = time.time(); jobs.save(job)

# ---- the button's route
queued = []
jobs.enqueue = lambda j: queued.append(j["id"])
r = cl.post(f"/api/jobs/{job['id']}/clip-anyway", headers=H)
check("Clip anyway is accepted", r.status_code, 200)
check("the video goes back in line to be looked at again", (job["status"], job["options"].get("force"), queued), ("queued", True, [job["id"]]))

# ---- the run: the transcript is reused, the AI is asked with force, the clips are made
heard, asked = [], []
transcribe.transcribe = lambda *a, **k: heard.append(1) or []
media.probe = lambda p: {"duration": 60.0, "width": 1920, "height": 1080}
media.save_thumb = lambda *a, **k: False
media.extract_audio = lambda *a, **k: None
media.load_audio = lambda p: np.zeros(60 * 16000, dtype=np.float32)
media.loudness_per_second = lambda a: np.zeros(60)


def fake_find(words, db, duration, title, uploader, n, mn, mx, log, force=False, focus="", **k):
    asked.append((force, len(words)))
    return [{"start": 10.0, "end": 30.0, "score": 55, "hook": "LOOK", "title": "A moment", "description": "", "hashtags": [],
             "highlight_words": [], "category": "other", "reason": "best there is"}], {"model": "test-model", "tokens": 5}


brain.find_clips = fake_find
jobs.render_one = lambda job_, clip, words, audio=None: clip.update(status="done")
jobs.slim = lambda *a, **k: 0
with ctx.as_user(u.id):
    jobs._run_job(job)
check("the AI was asked with force, on the saved transcript", asked, [(True, 60)])
check("the transcript was not written again", heard, [])
check("the clips were made", [(c["start"], c["end"], c["status"]) for c in job["clips"]], [(10.0, 30.0, "done")])
check("the video is done, with its clip", (job["status"], job["stage"]), ("done", "Done"))
check("asking with force doesn't stick for later", job["options"].get("force"), None)

# ---- when it isn't allowed
r = cl.post(f"/api/jobs/{job['id']}/clip-anyway", headers=H)
check("a video that already has clips can't ask again", r.status_code, 400)
job["clips"] = []; job["kind"] = "live"
check("live recordings are refused (they have Clip now)", cl.post(f"/api/jobs/{job['id']}/clip-anyway", headers=H).status_code, 400)
job["kind"] = "video"; src.unlink(missing_ok=True)
r = cl.post(f"/api/jobs/{job['id']}/clip-anyway", headers=H)
check("an upload that is gone from the server asks to be uploaded again", (r.status_code, "Upload the video again" in r.text), (400, True))
v, _ = auth._create("b@example.com", "b", password_hash="x")
other = TestClient(server.app, base_url=BASE, follow_redirects=False)
other.cookies.set(auth.COOKIE, auth.start_session(v.id))
check("someone else's video can't be touched", other.post(f"/api/jobs/{job['id']}/clip-anyway", headers=H).status_code, 404)
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
