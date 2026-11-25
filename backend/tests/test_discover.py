"""Live now: the top live list keeps games only, famous streamers who are live come first, search finds the real
person before fan accounts, and "Clip 24/7" adds the streamer once with auto-record on. Twitch and Kick are replaced
by stand-ins, so nothing leaves this machine."""
import os, sys, tempfile
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-discover-")
os.environ.update(DATABASE_URL="", PEAKCUT_VERIFY_EMAIL="0")
sys.path.insert(0, ".")
from fastapi.testclient import TestClient   # noqa: E402
from studio import auth, creators, ctx, db, discover, live, profiles, server   # noqa: E402
db.init()
fails = []


def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)


def stream(login, game, viewers):
    return {"viewersCount": viewers, "title": f"{login} live", "language": "EN", "game": {"displayName": game},
            "previewImageURL": f"https://static-cdn.jtvnw.net/previews-ttv/live_user_{login}-440x248.jpg",
            "broadcaster": {"login": login, "displayName": login.title(), "profileImageURL": "https://static-cdn.jtvnw.net/x.png",
                            "followers": {"totalCount": 1000}}}


def fake_gql(q, v):
    if "streams(" in q:
        rows = [stream("chatter", "Just Chatting", 9000), stream("gamer", "Fortnite", 8000), stream("irl", "IRL", 7000), stream("dota", "Dota 2", 6000)]
        return {"streams": {"edges": [{"cursor": None, "node": n} for n in rows]}}
    if "users(logins" in q:
        return {"users": [{"login": "marlon", "displayName": "Marlon", "profileImageURL": "https://static-cdn.jtvnw.net/m.png",
                           "followers": {"totalCount": 2900000}, "stream": {"viewersCount": 40000, "title": "w/ kai", "game": {"displayName": "GTA V"},
                                                                         "previewImageURL": "https://static-cdn.jtvnw.net/p.jpg"}},
                          {"login": "kaicenat", "displayName": "KaiCenat", "profileImageURL": "", "followers": {"totalCount": 21000000}, "stream": None}]}
    if "searchFor" in q:
        return {"searchFor": {"users": {"items": [
            {"login": "marlonfan", "displayName": "marlonfan", "followers": {"totalCount": 12}, "stream": None},
            {"login": "marlonjlp", "displayName": "marlonjlp", "followers": {"totalCount": 54000}, "stream": None},
            {"login": "marlon", "displayName": "Marlon", "followers": {"totalCount": 2900000}, "stream": None}]}}}
    return {}


def offline(*a, **k):
    raise OSError("offline in tests")


discover._gql = fake_gql
profiles._get = offline
profiles.fetch = offline
profiles.live_shared = lambda *a, **k: {"live": False}
profiles.refresh = lambda *a, **k: None
started = []
live.check_now = lambda cid: started.append(cid)

top = discover.top_live("en", "gaming")
check("gaming leaves out Just Chatting and IRL", [r["name"] for r in top], ["Gamer", "Dota"])
check("everything keeps them, biggest first", [r["name"] for r in discover.top_live("en", "all")][:2], ["Chatter", "Gamer"])
check("thumbnails come through PeakCut's own address", top[0]["thumb"].startswith("/api/discover/img?u=https%3A%2F%2Fstatic-cdn.jtvnw.net"))

fam = discover.famous()
check("famous streamers who are live come first", (fam[0]["name"], fam[0]["live"]["viewers"]), ("Marlon", 40000))
check("then the biggest", fam[1]["name"], "Kai Cenat")
check("every famous streamer is listed, live or not", len(fam), len(discover.FAMOUS))

res = discover.search("marlon")
check("search finds the real Marlon first", res[0]["channels"], {"twitch": "https://www.twitch.tv/marlon"})
check("the real one isn't listed twice", sum(1 for r in res if "https://www.twitch.tv/marlon" in r["channels"].values()), 1)
check("fan accounts with a handful of followers are left out", any("marlonfan" in str(r["channels"]) for r in res), False)
check("one letter searches nothing", discover.search("m"), [])

# covers: big banners are shrunk here, even when Twitch labels them "binary/octet-stream"
import io   # noqa: E402
from PIL import Image   # noqa: E402
buf = io.BytesIO(); Image.new("RGB", (1200, 480), (200, 80, 20)).save(buf, "JPEG"); big = buf.getvalue()
profiles._get = lambda url, accept="", limit=0: ("binary/octet-stream", big)
got = discover.image("https://static-cdn.jtvnw.net/jtv_user_pictures/x-profile_banner-480.jpeg", 640)
check("a banner labelled as plain data still comes through", bool(got))
check("shrunk to 640 wide, as WebP", got and (Image.open(io.BytesIO(got[0])).width, got[1]), (640, "image/webp"))
check("and much smaller than it came", got and len(got[0]) < len(big))
profiles._get = lambda url, accept="", limit=0: ("text/html", b"<html>not a picture</html>")
check("a page that isn't a picture is refused", discover.image("https://static-cdn.jtvnw.net/jtv_user_pictures/y.png", 160), None)
check("covers are offered at the cover size", fam[0]["live"]["thumb"].endswith("&w=640"))
profiles._get = offline

BASE = "https://clips.example.com"
u, _ = auth._create("fan@example.com", "fan", password_hash="x")
cl = TestClient(server.app, base_url=BASE, follow_redirects=False)
cl.cookies.set(auth.COOKIE, auth.start_session(u.id))
H = {"Origin": BASE}

r = cl.post("/api/discover/follow", json={"name": "Marlon", "channels": {"twitch": "https://www.twitch.tv/marlon"}, "live_on": "twitch"}, headers=H)
check("Clip 24/7 adds the streamer", r.status_code, 200)
out = r.json()
with ctx.as_user(u.id):
    cs = creators.all_creators()
check("as one creator", len(cs), 1)
check("with auto-record on", cs[0]["auto_watch"], True)
check("recording from their channel", cs[0]["channel"], "https://www.twitch.tv/marlon")
check("and PeakCut looks right away whether they are live", started, [cs[0]["id"]])
r = cl.post("/api/discover/follow", json={"name": "Marlon again", "channels": {"twitch": "https://www.twitch.tv/Marlon/"}}, headers=H)
check("pressing it again uses the same creator", (r.json()["existed"], r.json()["creator"]["id"]), (True, out["creator"]["id"]))
with ctx.as_user(u.id):
    check("...and adds no second one", len(creators.all_creators()), 1)
    c2 = creators.create({"name": "Kai", "channel": "https://www.twitch.tv/kaicenat"})
check("a creator added by hand starts with auto-record off", c2["auto_watch"], False)
cl.post("/api/discover/follow", json={"name": "Kai Cenat", "channels": {"twitch": "https://www.twitch.tv/kaicenat"}}, headers=H)
with ctx.as_user(u.id):
    check("Clip 24/7 on someone already added turns auto-record on", creators.get(c2["id"])["auto_watch"], True)
    check("without adding them twice", len(creators.all_creators()), 2)
# + in the sidebar only adds: nothing is recorded and auto-record stays off until the person says so
started.clear()
r = cl.post("/api/discover/follow", json={"name": "Lacy", "channels": {"twitch": "https://www.twitch.tv/lacy"}, "live_on": "twitch", "record": False}, headers=H)
check("adding only: the creator is there with auto-record off", (r.status_code, r.json()["creator"]["auto_watch"], r.json()["existed"]), (200, False, False))
check("...and no recording is started", started, [])
r = cl.post("/api/discover/follow", json={"name": "Kai Cenat", "channels": {"twitch": "https://www.twitch.tv/kaicenat"}, "record": False}, headers=H)
check("adding someone already there leaves their auto-record as it was", (r.json()["existed"], r.json()["creator"]["auto_watch"]), (True, True))
check("links from other websites are refused", cl.post("/api/discover/follow", json={"name": "x", "channels": {"twitch": "https://evil.example.com/x"}}, headers=H).status_code, 400)
check("pictures from other websites are not passed through", cl.get("/api/discover/img?u=https://evil.example.com/a.png").status_code, 404)
profiles._get = lambda url, accept="", limit=0: ("image/jpeg", big)
pic = cl.get("/api/discover/img?u=https%3A%2F%2Fstatic-cdn.jtvnw.net%2Fjtv_user_pictures%2Fz-profile_image-70x70.png&w=160")
check("a streamer's picture is served through PeakCut's own address", (pic.status_code, pic.headers.get("content-type")), (200, "image/webp"))
check("the famous list loads in the app", len(cl.get("/api/discover/famous").json()["streamers"]), len(discover.FAMOUS))

other, _ = auth._create("other@example.com", "other", password_hash="x")
oc = TestClient(server.app, base_url=BASE, follow_redirects=False)
oc.cookies.set(auth.COOKIE, auth.start_session(other.id))
check("anyone can press Clip 24/7: nothing to buy first",
      oc.post("/api/discover/follow", json={"name": "x", "channels": {"twitch": "https://www.twitch.tv/x"}}, headers=H).status_code, 200)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
