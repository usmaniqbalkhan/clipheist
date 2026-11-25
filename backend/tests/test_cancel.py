"""Cancelling a video while it is being made: it stops at its next step, finished clips stay, live recordings are
not cancelled this way (they have Stop)."""
import os, sys, tempfile
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-cancel-")
os.environ.update(DATABASE_URL="", PEAKCUT_VERIFY_EMAIL="0")
sys.path.insert(0, ".")
from studio import auth, ctx, db, jobs   # noqa: E402
db.init()
fails = []


def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)


u, _ = auth._create("cancel@example.com", "C", password_hash="x")
with ctx.as_user(u.id):
    j = jobs.create({"type": "url", "url": "https://www.youtube.com/watch?v=abcdefghijk", "name": "x"}, {}, enqueue=False)
    j["status"] = "running"
    j["clips"] = [{"id": "c1", "status": "done"}, {"id": "c2", "status": "rendering"}, {"id": "c3", "status": "queued"}]
    jobs.cancel(j)
    check("a video being made can be cancelled: it says so", (j["status"], j["error"], j["stage"]), ("error", jobs.CANCELLED, "Cancelled"))
    check("finished clips stay; the rest are dropped", [c["status"] for c in j["clips"]], ["done", "dropped", "dropped"])
    try:
        jobs._set(j, stage="Transcribing", progress=0.5)
        got = "kept going"
    except jobs.Cancelled:
        got = "stopped"
    check("its work stops at the next step", got, "stopped")
    try:
        jobs.cancel(j)
        got = "cancelled again"
    except ValueError:
        got = "refused"
    check("a video that is no longer being made can't be cancelled", got, "refused")
    live = jobs.create({"type": "url", "url": "https://www.twitch.tv/x", "name": "x"}, {}, enqueue=False, kind="live")
    live["status"] = "running"
    try:
        jobs.cancel(live)
        got = "cancelled"
    except ValueError:
        got = "refused"
    check("a live recording is stopped with Stop, not cancelled", got, "refused")

print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
