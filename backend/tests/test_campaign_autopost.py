"""A campaign's own auto-post: its clips post themselves to the accounts it names, only from the score it asks for,
the first as soon as it's made and the rest spaced by its gap, at most so many a day. It works for a plain upload
with no streamer and for a streamer's video alike, and nothing is posted (the queue is only filled)."""
import os, sys, tempfile, time
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-ap-"); os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from pathlib import Path   # noqa: E402
from studio import accounts, auth, creators, ctx, db, jobs, posting   # noqa: E402
db.init()
fails = []


def check(n, ok, x=""):
    print(("PASS " if ok else "FAIL ") + n + (f"  {x}" if x and not ok else "")); ok or fails.append(n)


def video(campaign="", creator=None, scores=(90, 85, 40, 95)):
    j = jobs.create({"type": "file", "path": "", "name": "v.mp4"}, {"campaign": campaign}, enqueue=False, creator=creator)
    (Path(j["dir"]) / "clips").mkdir(parents=True, exist_ok=True)
    for i, sc in enumerate(scores):
        j["clips"].append({"id": f"clip0{i + 1}", "status": "done", "file": f"clip0{i + 1}.mp4", "cover": "x.jpg", "title": f"Clip {i + 1}", "score": sc, "start": 0, "end": 10})
    j["status"] = "done"; jobs.save(j)
    return j


u, _ = auth._create("ap@example.com", "AP", password_hash="x")
with ctx.as_user(u.id):
    accounts._put({"id": "acc_ig", "platform": "instagram", "name": "@brandclips", "added": time.time()})
    e, _ = creators.lib_add({"name": "Brand deal"})
    creators.lib_edit(e["key"], {"ready": True, "autopost": {"on": True, "accounts": ["acc_ig"], "min_score": 80, "gap_min": 300, "cap": 6}})
    e = creators.campaign(None, e["key"])
    check("a campaign keeps its auto-post settings, tidied", (e["autopost"]["on"], e["autopost"]["min_score"], e["autopost"]["gap_min"], e["autopost"]["cap"]) == (True, 80, 300, 6))

    j = video(e["key"])
    check("an upload with no streamer still belongs to the campaign", j["campaign"] == e["key"])
    t0 = time.time()
    for c in j["clips"]:
        posting.on_clip_ready(j, c)
    q = sorted([p for p in posting._mine() if p["job"] == j["id"]], key=lambda p: p["due_at"])
    check("only clips scoring 80 or more are queued", sorted(p["clip"] for p in q) == ["clip01", "clip02", "clip04"], str([p["clip"] for p in q]))
    check("the first goes out as soon as it's made", q and q[0]["due_at"] - t0 < 5)
    gaps = [round((b["due_at"] - a["due_at"]) / 3600, 1) for a, b in zip(q, q[1:])]
    check("the rest are spaced five hours apart", all(abs(g - 5) < 0.05 for g in gaps), str(gaps))
    check("they go to the campaign's account", all(p["account"] == "acc_ig" for p in q))

    for c in j["clips"]:
        posting.on_clip_ready(j, c)
    check("the same clip is never queued twice", len([p for p in posting._mine() if p["job"] == j["id"]]) == 3)

    creators.lib_edit(e["key"], {"autopost": {"gap_min": 0}})
    st = creators.create({"name": "Streamer"})
    j2 = video(e["key"], st["id"], scores=(90, 92))
    t1 = time.time()
    for c in j2["clips"]:
        posting.on_clip_ready(j2, c)
    q2 = [p for p in posting._mine() if p["job"] == j2["id"]]
    check("with no gap, each clip goes out as soon as it's made (a recording's clips too)", len(q2) == 2 and all(p["due_at"] - t1 < 5 for p in q2))

    creators.lib_edit(e["key"], {"autopost": {"on": False}})
    j3 = video(e["key"])
    for c in j3["clips"]:
        posting.on_clip_ready(j3, c)
    check("with auto-post off, nothing is queued", not [p for p in posting._mine() if p["job"] == j3["id"]])

    creators.lib_edit(e["key"], {"autopost": {"on": True, "accounts": []}})
    check("auto-post can't be on without an account", creators.campaign(None, e["key"])["autopost"]["on"] is False)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
