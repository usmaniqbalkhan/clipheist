"""YouTube's limits never fail a post: a channel's own daily limit waits for tomorrow, the app's daily allowance from
Google waits until it resets (midnight Pacific) and holds every YouTube post until then. A posted video that YouTube
no longer has stops showing as posted. Uploads are not for kids and embeddable."""
import os, sys, tempfile, time
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-ytl-"); os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from studio import accounts, posting   # noqa: E402
fails = []


def check(n, ok):
    print(("PASS " if ok else "FAIL ") + n); ok or fails.append(n)


def post(**k):
    return {"id": "p", "user": "u", "job": "j", "clip": "c", "creator": None, "account": "a", "account_name": "Ch",
            "platform": "youtube", "status": "scheduled", "due_at": time.time(), "title": "t", "tries": 0, **k}


class Job(dict):
    pass


posting.jobs.get = lambda jid: {"id": "j", "dir": tempfile.gettempdir(), "creator": None, "clips": [{"id": "c", "status": "done", "file": "c.mp4"}]}
posting.accounts.get = lambda aid: {"id": "a", "name": "Ch", "platform": "youtube"}
posting.creators.get = lambda cid: None
posting._mirror = lambda p: None
posting._save = lambda: None
err = {"e": ""}


def up(*a, **k):
    raise RuntimeError(err["e"])


accounts.youtube_upload = up
err["e"] = '{"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}}'
p = post()
posting._post(p)
check("the app's allowance used up: waits for the reset, not failed, no try used", p["status"] == "scheduled" and p["tries"] == 0
      and p["due_at"] > time.time() and abs(p["due_at"] - posting._pacific_midnight() - 60) < 5)
q = post(id="q")
posting._post(q)
check("...and every other YouTube post waits too, without asking YouTube", q["due_at"] >= posting._yt_pause[0] and q["tries"] == 0)
posting._yt_pause[0] = 0
err["e"] = "The user has exceeded the number of videos they may upload."
r = post(id="r")
posting._post(r)
check("a channel's own daily limit: waits for tomorrow, not failed", r["status"] == "scheduled" and r["tries"] == 0 and r["waited"] == 1
      and r["due_at"] > time.time() + 3600)

# a posted video YouTube no longer has
posting._posts[:] = [post(id="g", status="posted", remote_id="v1", posted_at=time.time() - 3600),
                     post(id="h", status="posted", remote_id="v2", posted_at=time.time() - 3600)]
accounts.youtube_states = lambda acc, ids: {"v1": ("deleted", ""), "v2": ("processed", "")}
posting.check_gone()
check("deleted on YouTube: no longer shown as posted, and says why", posting._posts[0]["status"] == "failed" and posting._posts[0].get("removed")
      and "no longer on YouTube" in posting._posts[0]["error"])
check("...one still there stays posted", posting._posts[1]["status"] == "posted")

code = open("studio/accounts.py", encoding="utf-8").read()
check("uploads say: not made for kids, embeddable, public stats", '"selfDeclaredMadeForKids": False' in code and '"embeddable": True' in code)
print(f"\n{len(fails)} FAILED: {fails}" if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
