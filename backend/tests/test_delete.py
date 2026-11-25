"""Deleting clips, videos, creators and accounts, bulk actions and the zip download. Runs against the code directly,
with its own empty data folder: nothing outside it is touched."""
import os, sys, tempfile, zipfile
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-del-"); os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from pathlib import Path
import sqlalchemy as sa
from studio import account, auth, config, creators, ctx, db, jobs
db.init()
fails = []
def check(n, ok, x=""):
    print(("PASS " if ok else "FAIL ") + n + (f"  {x}" if x else "")); ok or fails.append(n)
a, _ = auth._create("a@example.com", "A", password_hash="x"); b, _ = auth._create("b@example.com", "B", password_hash="x")
def video(creator=None, n=3, status="done"):
    j = jobs.create({"type": "file", "path": "", "name": "v.mp4"}, {}, enqueue=False, creator=creator)
    (Path(j["dir"]) / "clips").mkdir(parents=True, exist_ok=True)
    for i in range(n):
        cid = f"clip{i+1:02d}"
        for ext in ("mp4", "jpg", "ass", "json", "src.ts"): (Path(j["dir"]) / "clips" / f"{cid}.{ext}").write_bytes(b"x" * 1000)
        j["clips"].append({"id": cid, "status": "done", "file": f"{cid}.mp4", "cover": f"{cid}.jpg", "title": "Same title", "start": 0, "end": 10,
                           "src": str(Path(j["dir"]) / "clips" / f"{cid}.src.ts")})
    j["status"] = status; jobs.save(j); return j
with ctx.as_user(a.id):
    c = creators.create({"name": "Streamer"})
    j1, j2, j3 = video(), video(c["id"]), video(status="running")
    cl = Path(j1["dir"]) / "clips"
    whole = Path(j1["dir"]) / "source.mp4"; whole.write_bytes(b"v" * 1000)
    for cid in ("clip01", "clip02"):            # each clip's own source piece, captions and post text
        for ext in (".src.mp4", ".ass", ".json"):
            (cl / f"{cid}{ext}").write_bytes(b"x" * 100)
        next(k for k in j1["clips"] if k["id"] == cid)["src"] = str(cl / f"{cid}.src.mp4")
    j1["clips"][2]["src"] = str(whole)          # a clip made from the whole video
    jobs.save(j1)
    check("one clip is deleted with its files", account.delete_clip(j1["id"], "clip01") and not (Path(j1["dir"]) / "clips" / "clip01.mp4").exists() and len(jobs.get(j1["id"])["clips"]) == 2)
    left = sorted(f.name for f in cl.iterdir() if f.name.startswith("clip01"))
    check("...all of them: its source piece, captions and post text too", not left, str(left) if left else "")
    check("...and nothing of the other clips", all((cl / f"clip02{e}").exists() for e in (".mp4", ".jpg", ".src.mp4", ".ass", ".json")))
    check("deleting a clip made from the whole video never deletes the whole video",
          account.delete_clip(j1["id"], "clip03") and whole.exists() and (cl / "clip02.src.mp4").exists())
    j1["clips"].append({"id": "clip03", "status": "done", "file": "clip03.mp4", "cover": "clip03.jpg", "title": "Same title", "start": 0, "end": 10})
    for ext in ("mp4", "jpg"): (cl / f"clip03.{ext}").write_bytes(b"x" * 1000)
    jobs.save(j1)
    z = account.zip_clips([(j1["id"], "clip02"), (j1["id"], "clip03"), (j2["id"], "clip01"), ("nope", "x")])
    names = zipfile.ZipFile(z).namelist()
    check("several clips download as one zip with distinct names", len(names) == 3 and len(set(names)) == 3, str(names))
    check("a clip is saved under its title", account.clip_title_name({"title": "Kai's Epic Shootout – Got Him!", "hook": "X"}, "clip01") == "Kais Epic Shootout  Got Him", account.clip_title_name({"title": "Kai's Epic Shootout – Got Him!"}))
    check("...or its hook, or its id when it has neither", (account.clip_title_name({"hook": "WOW"}, "clip02"), account.clip_title_name({}, "clip03"), account.clip_title_name({"title": "???"}, "clip04")) == ("WOW", "clip03", "clip04"))
    r = account.delete_many([j1["id"], j3["id"]], [(j2["id"], "clip01"), (j1["id"], "clip02")])
    check("bulk delete removes videos and clips, leaves work in progress alone", r == {"videos": 1, "clips": 1, "skipped": 1}, str(r))
    check("the deleted video's folder is gone", not Path(j1["dir"]).exists())
with ctx.as_user(b.id):
    check("someone else cannot delete my clip", not account.delete_clip(j2["id"], "clip02"))
    try:
        account.zip_clips([(j2["id"], "clip02")]); ok = False
    except ValueError: ok = True
    check("someone else cannot download my clips", ok)
    check("someone else cannot delete my creator", account.delete_creator(c["id"]) == {"ok": False})
with ctx.as_user(a.id):
    r = account.delete_creator(c["id"])
    check("deleting a creator removes their videos and folder", r["ok"] and r["videos"] == 1 and not jobs.get(j2["id"]) and not creators.get(c["id"]) and not creators.root(c["id"]).exists(), str(r))
    try:
        account.delete_account(a.id); ok = False
    except account.Busy: ok = True
    check("an account with a video in progress is not deleted", ok)
    j = jobs.get(j3["id"]); j["status"] = "done"; jobs.save(j)
udir = config.user_dir(a.id)
from studio import posting
posting._posts.extend([{"id": "pa", "user": a.id, "status": "scheduled", "due_at": 0}, {"id": "pb", "user": b.id, "status": "scheduled", "due_at": 0}])
check("the account is deleted", account.delete_account(a.id)["ok"] and auth.get_user(a.id) is None and not udir.exists())
with db.engine.connect() as cn:
    left = {t.name: cn.execute(sa.select(sa.func.count()).select_from(t).where(t.c.user_id == a.id)).scalar() for t in db.meta.sorted_tables if "user_id" in t.c}
check("nothing of theirs is left in the database", not any(left.values()), str(left))
check("the other account is untouched", auth.get_user(b.id) is not None and config.user_dir(b.id).exists())
check("posts waiting for the deleted account are gone, the other account's stay",
      [p["id"] for p in posting._posts] == ["pb"] and "pa" not in posting._FILE.read_text(encoding="utf-8"))
a2, _ = auth._create("A@example.com", "A again", password_hash="x")
check("the same email can sign up again afterwards, as a new account", a2.id != a.id)
print("ALL PASSED" if not fails else f"{len(fails)} failed")
import shutil; shutil.rmtree(os.environ["PEAKCUT_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
