"""The admin account list: every account can be found, however many there are, 50 to a page. Runs against the
code directly, with its own empty data folder."""
import os, sys, tempfile, time
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-adm-"); os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from studio import admin, db
db.init()
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

now = time.time()
with db.engine.begin() as c:
    c.execute(db.users.insert(), [{"id": f"u_{i:016d}", "email": f"person{i}@example.com", "name": f"Person {i}",
                                   "created_at": now - (3000 - i) * 60, "is_admin": i == 5, "disabled": i == 9} for i in range(2600)])
    c.execute(db.usage.insert(), [{"user_id": "u_0000000000000007", "t": now, "tokens_in": 900, "tokens_out": 100, "purpose": "Video: finding clips"},
                                  {"user_id": "u_0000000000002599", "t": now, "tokens_in": 10, "tokens_out": 5, "purpose": "Video: finding clips"}])

o = admin.overview()
check("the dashboard counts every account", o["totals"]["users"], 2600)
check("...and every disabled one, however old", o["totals"]["disabled"], 1)
check("the dashboard no longer sends the whole list", "users" in o, False)
l = admin.user_list()
check("50 accounts a page, newest first", (len(l["users"]), l["users"][0]["email"], l["pages"], l["total"]), (50, "person2599@example.com", 52, 2600))
check("the last page has the very first accounts", admin.user_list(page=52)["users"][-1]["email"], "person0@example.com")
check("a page past the end shows the last one", admin.user_list(page=999)["page"], 52)
check("Disabled finds an old account too", [u["email"] for u in admin.user_list(show="disabled")["users"]], ["person9@example.com"])
check("search finds an account from the very start", [u["email"] for u in admin.user_list(q="person7@")["users"]], ["person7@example.com"])
check("search by name", admin.user_list(q="Person 12")["total"], 111)
check("Admins", [u["email"] for u in admin.user_list(show="admins")["users"]], ["person5@example.com"])
check("sorted by AI use, an old account first", [(u["email"], u["tokens"]) for u in admin.user_list(sort="tokens")["users"][:2]],
      [("person7@example.com", 1000), ("person2599@example.com", 15)])
check("a made-up sort falls back to newest", admin.user_list(sort="password_hash")["users"][0]["email"], "person2599@example.com")

print("ALL PASSED" if not fails else f"{len(fails)} failed")
import shutil; db.engine.dispose(); shutil.rmtree(os.environ["PEAKCUT_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
