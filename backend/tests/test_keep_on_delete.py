"""Deleting never takes more than was asked for: a video can go while its clips stay, a streamer can go while their
videos move to All videos, and stopping a recording can skip the rest of that stream instead of starting it again.
Runs against the code directly, with its own empty data folder."""
import os, sys, tempfile
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-keep-"); os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from pathlib import Path   # noqa: E402
from studio import account, auth, config, creators, ctx, db, jobs, live, profiles   # noqa: E402
db.init()
fails = []


def check(n, ok, x=""):
    print(("PASS " if ok else "FAIL ") + n + (f"  {x}" if x and not ok else "")); ok or fails.append(n)


def video(creator=None, n=2):
    j = jobs.create({"type": "file", "path": "", "name": "v.mp4"}, {}, enqueue=False, creator=creator)
    d = Path(j["dir"]); (d / "clips").mkdir(parents=True, exist_ok=True)
    (d / "source.mp4").write_bytes(b"v" * 5000)
    for i in range(n):
        cid = f"clip{i + 1:02d}"
        for ext in ("mp4", "jpg"):
            (d / "clips" / f"{cid}.{ext}").write_bytes(b"x" * 100)
        j["clips"].append({"id": cid, "status": "done", "file": f"{cid}.mp4", "cover": f"{cid}.jpg", "title": "t", "start": 0, "end": 10})
    j.update(status="done", source_path=str(d / "source.mp4")); jobs.save(j)
    return j


u, _ = auth._create("keep@example.com", "Keep", password_hash="x")
with ctx.as_user(u.id):
    # ---- a video goes, its clips stay
    j = video()
    d = Path(j["dir"])
    check("deleting a video but keeping its clips works", account.drop_video(j["id"]))
    kept = jobs.get(j["id"])
    check("...the whole video file is gone", not (d / "source.mp4").exists())
    check("...the clips, their covers and the video's entry stay", kept is not None and len(kept["clips"]) == 2
          and all((d / "clips" / f"clip0{i}.mp4").exists() for i in (1, 2)))
    check("...and it says the video was deleted", kept.get("video_deleted") is True)
    empty = video(n=0)
    check("a video with no clips is simply deleted", account.drop_video(empty["id"]) and jobs.get(empty["id"]) is None)
    full = video()
    check("deleting a video with its clips removes all of it", jobs.delete(full["id"]) and not Path(full["dir"]).exists())
    many = [video(), video()]
    r = account.delete_many([x["id"] for x in many], [], keep_clips=True)
    check("several videos at once can keep their clips", r["videos"] == 2 and all(jobs.get(x["id"]) and len(jobs.get(x["id"])["clips"]) == 2 for x in many))

    # ---- a streamer goes, their videos stay
    c = creators.create({"name": "Moves"})
    v = video(c["id"])
    old = Path(v["dir"])
    r = account.delete_creator(c["id"], keep_videos=True)
    moved = jobs.get(v["id"])
    check("deleting a streamer can keep their videos", r["ok"] and moved is not None)
    check("...filed under no streamer, in the account's own videos", moved["creator"] is None and Path(moved["dir"]).parent == config.user_dir(u.id) / "jobs")
    check("...with their clips and files moved along", all((Path(moved["dir"]) / "clips" / f"clip0{i}.mp4").exists() for i in (1, 2)) and not old.exists())
    check("...and the paths inside point at the new place", moved["source_path"].startswith(moved["dir"]))
    check("...and the streamer itself is gone", creators.get(c["id"]) is None)
    c2 = creators.create({"name": "Goes"})
    v2 = video(c2["id"])
    account.delete_creator(c2["id"])
    check("deleting a streamer with their videos removes those too", jobs.get(v2["id"]) is None)

    # ---- stopping a recording can skip the rest of that stream
    c3 = creators.create({"name": "Skip", "channel": "https://kick.com/skipper", "auto_watch": True})
    c3 = creators.get(c3["id"]); c3["skip_stream"] = True; creators._write(c3)
    started = []
    real_live, real_start = profiles.live_shared, live.start_session
    live.start_session = lambda cr, url: started.append(url)
    profiles.live_shared = lambda plat, url: {"live": True}
    live._watch_one(creators.get(c3["id"]))
    check("a stream that was stopped by hand isn't started again while it's still on", not started and creators.get(c3["id"]).get("skip_stream"))
    profiles.live_shared = lambda plat, url: {"live": False}
    live._watch_one(creators.get(c3["id"]))
    check("...once they're offline, auto-record is back to normal", not creators.get(c3["id"]).get("skip_stream"))
    profiles.live_shared, live.start_session = real_live, real_start

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
