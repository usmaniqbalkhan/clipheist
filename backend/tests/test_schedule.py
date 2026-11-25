"""The content schedule: posts listed by day, moved to a new time, posted now, queued at a chosen time, and only
ever by their owner. Runs against the code directly, with its own empty data folder: nothing is posted."""
import os, sys, tempfile, time
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-sched-"); os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from pathlib import Path
from studio import accounts, auth, creators, ctx, db, jobs, posting
db.init()
fails = []
def check(n, ok, x=""):
    print(("PASS " if ok else "FAIL ") + n + (f"  {x}" if x else "")); ok or fails.append(n)
def refused(f, *a):
    try:
        f(*a)
    except ValueError:
        return True
    return False

a, _ = auth._create("a@example.com", "A", password_hash="x"); b, _ = auth._create("b@example.com", "B", password_hash="x")
DAY = 86400
with ctx.as_user(a.id):
    c = creators.create({"name": "Streamer"})
    j = jobs.create({"type": "file", "path": "", "name": "v.mp4"}, {}, enqueue=False, creator=c["id"])
    (Path(j["dir"]) / "clips").mkdir(parents=True, exist_ok=True)
    for i in range(3):
        j["clips"].append({"id": f"clip0{i+1}", "status": "done", "file": f"clip0{i+1}.mp4", "cover": f"clip0{i+1}.jpg", "title": f"Clip {i+1}", "start": 0, "end": 10})
    j["status"] = "done"; jobs.save(j)
    for aid, plat in (("acc_yt", "youtube"), ("acc_ig", "instagram"), ("acc_tt", "tiktok")):
        accounts._put({"id": aid, "platform": plat, "name": plat.title() + " channel", "added": time.time()})

    at = time.time() + 2 * DAY
    made = posting.enqueue(j, j["clips"][0], ["acc_yt", "acc_ig", "acc_tt"], at=at)
    check("a clip is queued for all three platforms at the time picked", len(made) == 3 and all(abs(p["due_at"] - at) < 1 for p in made))
    later = posting.enqueue(j, j["clips"][1], ["acc_yt"])
    check("without a time it still takes the next free slot", len(later) == 1 and later[0]["due_at"] >= time.time() - 1)
    check("a time in the past is refused", refused(posting.enqueue, j, j["clips"][2], ["acc_yt"], False, time.time() - 3600))
    check("a time more than a year ahead is refused", refused(posting.enqueue, j, j["clips"][2], ["acc_yt"], False, time.time() + 400 * DAY))
    check("a time that is not a number is refused", refused(posting.enqueue, j, j["clips"][2], ["acc_yt"], False, "tomorrow"))

    s = posting.schedule(time.time() - DAY, time.time() + 7 * DAY)
    rows = s["posts"]
    check("the week shows every post, by platform", sorted(r["platform"] for r in rows) == ["instagram", "tiktok", "youtube", "youtube"], str([r["platform"] for r in rows]))
    check("...in time order", [r["at"] for r in rows] == sorted(r["at"] for r in rows))
    check("...with creator name and clip cover", all(r["creator_name"] == "Streamer" and r["cover"].startswith(f"/media/{j['id']}/") for r in rows))
    check("...and never the owner's id", all("user" not in r for r in rows))
    check("the schedule lists the connected accounts", sorted(x["platform"] for x in s["accounts"]) == ["instagram", "tiktok", "youtube"])
    check("a week with nothing in it is empty", posting.schedule(time.time() + 30 * DAY, time.time() + 37 * DAY)["posts"] == [])
    check("more than two months at once is refused", refused(posting.schedule, time.time(), time.time() + 90 * DAY))

    yt = next(p for p in made if p["platform"] == "youtube")
    to = time.time() + 5 * DAY
    r = posting.move(yt["id"], to)
    check("a waiting post moves to a new day", abs(r["due_at"] - to) < 1 and abs(r["at"] - to) < 1)
    clip = jobs.get(j["id"])["clips"][0]
    check("...and the clip card shows the new time", abs(clip["posts"][yt["id"]]["due_at"] - to) < 1)
    check("moving into the past is refused", refused(posting.move, yt["id"], time.time() - 600))
    ig = next(p for p in made if p["platform"] == "instagram")
    posting.post_now(ig["id"])
    check("Post now makes a waiting post due straight away, even if paused", ig["due_at"] <= time.time() and ig["now"])
    tt = next(p for p in made if p["platform"] == "tiktok")
    posting.cancel(tt["id"])
    check("a cancelled post cannot be moved", refused(posting.move, tt["id"], time.time() + DAY))
    check("...or posted now", refused(posting.post_now, tt["id"]))
    yt["status"] = "posted"; yt["posted_at"] = time.time() - 3600
    s = posting.schedule(time.time() - DAY, time.time() + 7 * DAY)
    got = next(r for r in s["posts"] if r["id"] == yt["id"])
    check("a posted post is shown on the day it went out", abs(got["at"] - yt["posted_at"]) < 1)
    check("a posted post cannot be moved", refused(posting.move, yt["id"], time.time() + DAY))
    pid = later[0]["id"]

with ctx.as_user(b.id):
    check("someone else sees none of my posts", posting.schedule(time.time() - 30 * DAY, time.time() + 30 * DAY)["posts"] == [])
    check("someone else cannot move my post", refused(posting.move, pid, time.time() + DAY))
    check("...or post it now", refused(posting.post_now, pid))
    check("...or cancel it", refused(posting.cancel, pid))

print("\nall passed" if not fails else f"\n{len(fails)} failed: {fails}")
sys.exit(1 if fails else 0)
