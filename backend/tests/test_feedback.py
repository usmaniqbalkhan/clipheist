"""The admin's Feedback page: newest first, a message stops being new
once an admin has had it on screen, and messages nobody needs can be removed, one by one or the whole list."""
import os, sys, tempfile, time
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-fb-")
os.environ.update(DATABASE_URL="", PEAKCUT_VERIFY_EMAIL="0")
sys.path.insert(0, ".")
from fastapi.testclient import TestClient   # noqa: E402
import sqlalchemy as sa   # noqa: E402
from studio import auth, db, feedback, server   # noqa: E402
db.init()
fails = []


def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)


def person(email, admin=False):
    u, _ = auth._create(email, email.split("@")[0], password_hash="x")
    if admin:
        with db.engine.begin() as c:
            c.execute(db.users.update().where(db.users.c.id == u.id).values(is_admin=True))
    return u


BASE = "https://clips.example.com"
H = {"Origin": BASE}


def client(u):
    cl = TestClient(server.app, base_url=BASE, follow_redirects=False)
    cl.cookies.set(auth.COOKIE, auth.start_session(u.id))
    return cl


boss, free, payer, tester, refunded, gifted = (person("boss@example.com", True), person("free@example.com"), person("payer@example.com"),
                                               person("tester@example.com"), person("refunded@example.com"), person("gifted@example.com"))
# (the names are only labels here: there is no money in PeakCut, so nobody is put first)

t0 = time.time() - 1000
order = [(payer, "bug", "Paid: my oldest message"), (free, "idea", "Free: an idea"), (tester, "other", "Test money"),
         (refunded, "bug", "Refunded"), (gifted, "bug", "Gifted"), (payer, "idea", "Paid: my newer message"), (free, "bug", "Free: the newest")]
ids = {}
for i, (u, kind, msg) in enumerate(order):
    ids[msg] = feedback.submit(u.id, kind, msg)["id"]
    with db.engine.begin() as c:
        c.execute(db.feedback.update().where(db.feedback.c.id == ids[msg]).values(t=t0 + i))

adm, usr = client(boss), client(free)
got = adm.get("/api/admin/feedback?status=open").json()
check("newest first", [x["message"] for x in got["items"]],
      ["Free: the newest", "Paid: my newer message", "Gifted", "Refunded", "Test money", "Free: an idea", "Paid: my oldest message"])
check("each says who sent it", got["items"][0]["email"], "free@example.com")
check("every message starts as new", got["counts"], {"new": 7, "seen": 0, "done": 0})

# having them on screen is what makes them seen
shown = [ids["Paid: my newer message"], ids["Free: an idea"]]
check("the messages an admin has on screen stop being new", adm.post("/api/admin/feedback/seen", json={"ids": shown}, headers=H).json(), {"seen": 2})
check("...and are not counted twice", adm.post("/api/admin/feedback/seen", json={"ids": shown}, headers=H).json(), {"seen": 0})
got = adm.get("/api/admin/feedback?status=open").json()
check("seen messages stay in the open list until they are dealt with", (got["counts"], len(got["items"])), ({"new": 5, "seen": 2, "done": 0}, 7))
adm.post(f"/api/admin/feedback/{ids['Free: an idea']}", json={"status": "done"}, headers=H)
check("a message marked done is not marked seen again", adm.post("/api/admin/feedback/seen", json={"ids": [ids["Free: an idea"]]}, headers=H).json(), {"seen": 0})
check("done leaves the open list", ([x["message"] for x in adm.get("/api/admin/feedback?status=done").json()["items"]],
                                  len(adm.get("/api/admin/feedback?status=open").json()["items"])), (["Free: an idea"], 6))
check("the whole list has them all, newest first", [x["message"] for x in adm.get("/api/admin/feedback").json()["items"]][:2],
      ["Free: the newest", "Paid: my newer message"])

# removing
check("one message can be removed", adm.post("/api/admin/feedback/remove", json={"ids": [ids["Gifted"]]}, headers=H).json(), {"removed": 1})
check("...and is gone for good", adm.post("/api/admin/feedback/remove", json={"ids": [ids["Gifted"]]}, headers=H).json(), {"removed": 0})
late = feedback.submit(payer.id, "bug", "Arrived while the admin was reading")["id"]
on_screen = [x["id"] for x in got["items"]]
r = adm.post("/api/admin/feedback/remove", json={"ids": on_screen}, headers=H).json()
left = adm.get("/api/admin/feedback").json()
check("the whole list can be emptied in one go", r["removed"], 6)
check("...but a message that arrived meanwhile is never removed unread", [x["id"] for x in left["items"]], [late])
check("the counts follow", left["counts"], {"new": 1, "seen": 0, "done": 0})
with db.engine.connect() as c:
    log = [r.detail for r in c.execute(sa.select(db.admin_log).where(db.admin_log.c.action == "feedback").order_by(db.admin_log.c.id))]
check("what was removed is in the admin's activity", log, ["Removed 1 feedback message", "Removed 6 feedback messages"])
check("ids that aren't numbers are refused", adm.post("/api/admin/feedback/remove", json={"ids": ["all"]}, headers=H).status_code, 400)
check("nothing named, nothing removed", adm.post("/api/admin/feedback/remove", json={}, headers=H).json(), {"removed": 0})

# only admins
check("someone who isn't an admin can't see, mark or remove feedback",
      (usr.get("/api/admin/feedback").status_code, usr.post("/api/admin/feedback/seen", json={"ids": [late]}, headers=H).status_code,
       usr.post("/api/admin/feedback/remove", json={"ids": [late]}, headers=H).status_code), (404, 404, 404))
check("...and the message is still there", len(adm.get("/api/admin/feedback").json()["items"]), 1)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
import shutil; db.engine.dispose(); shutil.rmtree(os.environ["PEAKCUT_DATA"], ignore_errors=True)   # noqa: E702,E402
sys.exit(1 if fails else 0)
