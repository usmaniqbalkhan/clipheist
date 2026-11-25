"""A YouTube post counts as posted only once YouTube has finished processing the video: it stays "posting" while
YouTube works on it, becomes "posted" when it's ready, "failed" with YouTube's reason if it's turned down, and a
restart in between doesn't mark it failed. Nothing is uploaded (YouTube is stood in for)."""
import os, sys, tempfile, time
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-ytp-"); os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from studio import accounts, auth, ctx, db, posting   # noqa: E402
db.init()
fails = []


def check(n, ok, x=""):
    print(("PASS " if ok else "FAIL ") + n + (f"  {x}" if x and not ok else "")); ok or fails.append(n)


u, _ = auth._create("ytp@example.com", "Y", password_hash="x")
state = {"v1": ("uploaded", ""), "v2": ("rejected", "duplicate")}
accounts.youtube_states = lambda acc, ids: {i: state[i] for i in ids}
accounts.get = lambda aid: {"id": aid, "platform": "youtube", "name": "My channel"}
posting._mirror = lambda p: None
posting._save = lambda: None


def post(rid):
    p = {"id": "p" + rid, "user": u.id, "account": "acc1", "account_name": "My channel", "platform": "youtube", "creator": "",
         "job": "j", "clip": "c", "title": "Clip", "status": "posting", "processing": True, "uploaded_at": time.time(),
         "remote_id": rid, "url": f"https://www.youtube.com/shorts/{rid}", "due_at": time.time(), "tries": 0}
    posting._posts.append(p)
    return p


with ctx.as_user(u.id):
    a, b = post("v1"), post("v2")
    posting.creators.log = lambda *x: None
    posting.alerts.notify = lambda *x, **k: None
    posting.check_processing()
    check("while YouTube processes it, the post is still posting", a["status"] == "posting" and a["processing"])
    check("a video YouTube turns down fails, with YouTube's reason", b["status"] == "failed" and "same as a video" in b["error"], str(b))
    state["v1"] = ("processed", "")
    posting.check_processing()
    check("once YouTube is done it's posted", a["status"] == "posted" and not a["processing"] and a.get("posted_at"))
    c = post("v3"); state["v3"] = ("uploaded", ""); c["uploaded_at"] = time.time() - posting.PROCESSING_MAX - 5
    posting.check_processing()
    check("a video YouTube is still busy with after hours counts as posted", c["status"] == "posted")

# a restart while YouTube processes: not counted as a broken upload
d = post("v4")
real = posting.threading.Thread
posting.threading.Thread = lambda *a, **k: type("T", (), {"start": lambda self: None})()
posting.jobs.on_clip_ready = []
try:
    posting.start()
except Exception:  # noqa: BLE001
    pass
posting.threading.Thread = real
check("a restart while YouTube processes keeps waiting for it", d["status"] == "posting" and d["processing"])

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
