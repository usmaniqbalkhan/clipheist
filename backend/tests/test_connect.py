"""One-click Connect for YouTube, Instagram and TikTok, and posting to each. The platforms are replaced by
stand-ins answering the way their documentation says, so nothing leaves this machine."""
import json, os, sys, tempfile, time
from urllib.parse import parse_qs, urlparse
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-connect-")
os.environ.update(DATABASE_URL="", PEAKCUT_ACCOUNTS="1", PEAKCUT_PUBLIC_URL="https://clips.example.com", PEAKCUT_VERIFY_EMAIL="0",
                  GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="", GOOGLE_CLIENT_FILE="", PEAKCUT_IG_APP_ID="", PEAKCUT_IG_APP_SECRET="",
                  PEAKCUT_TIKTOK_CLIENT_KEY="", PEAKCUT_TIKTOK_CLIENT_SECRET="")
sys.path.insert(0, ".")
from pathlib import Path
import sqlalchemy as sa
from fastapi.testclient import TestClient
from studio import accounts, auth, config, ctx, db, server
db.init()
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

BASE = "https://clips.example.com"
ORIGIN = {"Origin": BASE}
def person(email, admin=False):
    u, _ = auth._create(email, email.split("@")[0], password_hash="x")
    if admin:
        with db.engine.begin() as c:
            c.execute(db.users.update().where(db.users.c.id == u.id).values(is_admin=True))
    cl = TestClient(server.app, base_url=BASE, follow_redirects=False)
    cl.cookies.set(auth.COOKIE, auth.start_session(u.id))
    return u, cl
boss, a = person("boss@example.com", admin=True)
user, b = person("user@example.com")

# ---- nothing is set up: the buttons say so, and only an admin may set it up
st = b.get("/api/status").json()
check("nothing can be connected before setup", st["connect"], {"youtube": False, "instagram": False, "tiktok": False})
check("a normal user cannot see the setup", b.get("/api/apps").status_code, 404)
check("...or change it", b.put("/api/apps", json={"platform": "tiktok", "client_id": "abcd", "client_secret": "x"}, headers=ORIGIN).status_code, 404)
r = b.get("/oauth/instagram/start")
check("Connect before setup comes back with a reason", (r.status_code, "connect_error" in r.headers.get("location", "")), (303, True))
check("setup from another website is refused", a.put("/api/apps", json={"platform": "tiktok", "client_id": "abcd1234", "client_secret": "s"},
                                                    headers={"Origin": "https://evil.example"}).status_code, 403)
check("a bad id is refused", a.put("/api/apps", json={"platform": "tiktok", "client_id": "a b<", "client_secret": "s"}, headers=ORIGIN).status_code, 400)
for pf, cid in (("youtube", "g-client-123.apps"), ("instagram", "1234567890"), ("tiktok", "awtiktokkey123")):
    r = a.put("/api/apps", json={"platform": pf, "client_id": cid, "client_secret": f"{pf}-secret-value"}, headers=ORIGIN)
    check(f"{pf} app saved", (r.status_code, r.json()[pf]["ready"]), (200, True))
apps = a.get("/api/apps").json()
check("secrets are never sent back", any("secret-value" in json.dumps(v) for v in apps.values()), False)
check("each platform shows the address to register", apps["tiktok"]["redirect"], BASE + "/oauth/tiktok/callback")
r = a.put("/api/apps", json={"platform": "tiktok", "client_id": "", "client_secret": "tiktok-secret-value2"}, headers=ORIGIN)
check("a new secret alone keeps the saved id", accounts.app_keys("tiktok"), ("awtiktokkey123", "tiktok-secret-value2"))
check("everyone can connect now", b.get("/api/status").json()["connect"], {"youtube": True, "instagram": True, "tiktok": True})
r = a.put("/api/apps", json={"platform": "tiktok", "client_id": "awtiktokkey123", "client_secret": "", "open": False}, headers=ORIGIN)
check("an admin can keep a platform to testers only", (r.status_code, r.json()["tiktok"]["open"]), (200, False))
check("...then ordinary users are not offered it", b.get("/api/status").json()["connect"]["tiktok"], False)
check("...but the admin still is, to test", a.get("/api/status").json()["connect"]["tiktok"], True)
check("...and Home's Connect pill follows what is open to everyone", a.get("/api/status").json()["open"]["tiktok"], False)
r = b.get("/oauth/tiktok/start")
check("...a user pressing Connect anyway is told why", (r.status_code, "connect_error" in r.headers["location"]), (303, True))
a.put("/api/apps", json={"platform": "tiktok", "client_id": "awtiktokkey123", "client_secret": "", "open": True}, headers=ORIGIN)
check("opened again: everyone can connect", b.get("/api/status").json()["connect"]["tiktok"], True)

# ---- YouTube cookies, for servers YouTube refuses
from studio import media
COOK = ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\tsecret-cookie-value\n"
check("a normal user cannot save YouTube cookies", b.put("/api/youtube-cookies", json={"text": COOK}, headers=ORIGIN).status_code, 404)
check("something that is not a cookies file is refused", a.put("/api/youtube-cookies", json={"text": "hello"}, headers=ORIGIN).status_code, 400)
r = a.put("/api/youtube-cookies", json={"text": COOK}, headers=ORIGIN)
check("cookies saved", (r.status_code, r.json()["saved"]), (200, True))
check("the cookies are never sent back", "secret-cookie-value" in a.get("/api/youtube-cookies").text, False)
check("downloads use them", media.cookie_opts(), {"cookiefile": str(config.YT_COOKIES)})
a.put("/api/youtube-cookies", json={"remove": True}, headers=ORIGIN)
check("...until they are removed", (config.YT_COOKIES.exists(), media.cookie_opts()), (False, {}))

# ---- the platforms, as stand-ins
class R:
    def __init__(self, code, data=None, headers=None, text=None):
        self.status_code, self._d, self.headers = code, data, headers or {}
        self.text = text if text is not None else json.dumps(data)
    def json(self):
        if self._d is None:
            raise ValueError
        return self._d
calls, uploads, tt = [], [], {"audited": False, "status": 0}
def post(url, data=None, json=None, headers=None, params=None, timeout=None, content=None):
    calls.append(("POST", url, data or json, headers or {}))
    if url == "https://oauth2.googleapis.com/token":
        return R(200, {"access_token": "yt-access", "refresh_token": "yt-refresh", "expires_in": 3600})
    if url == "https://api.instagram.com/oauth/access_token":
        if data["code"] != "igcode":
            return R(400, {"error_message": "bad code"})
        return R(200, {"data": [{"access_token": "ig-short", "user_id": "17841", "permissions": "instagram_business_basic,instagram_business_content_publish"}]})
    if url.endswith("/oauth/token/"):
        if data.get("grant_type") == "refresh_token":
            return R(200, {"access_token": "tt-access-2", "refresh_token": "tt-refresh-2", "expires_in": 7200, "open_id": "oid", "scope": "user.info.basic,video.publish"})
        return R(200, {"access_token": "tt-access", "refresh_token": "tt-refresh", "expires_in": 7200, "open_id": "oid", "scope": "user.info.basic,video.publish"})
    if url.endswith("/creator_info/query/"):
        return R(200, {"data": {"creator_username": "streamerclips", "creator_nickname": "Streamer Clips",
                                "privacy_level_options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]}, "error": {"code": "ok"}})
    if url.endswith("/post/publish/video/init/"):
        lv = json["post_info"]["privacy_level"]
        tt["init"] = json
        if lv != "SELF_ONLY" and not tt["audited"]:
            return R(403, {"error": {"code": "unaudited_client_can_only_post_to_private_accounts", "message": "private only"}})
        return R(200, {"data": {"publish_id": "v_pub_1", "upload_url": "https://open-upload.tiktokapis.com/video/?upload_id=1"}, "error": {"code": "ok"}})
    if url.endswith("/post/publish/status/fetch/"):
        tt["status"] += 1
        if tt["status"] < 2:
            return R(200, {"data": {"status": "PROCESSING_UPLOAD"}, "error": {"code": "ok"}})
        return R(200, {"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7300] if tt["audited"] else []}, "error": {"code": "ok"}})
    if url.endswith("/17841/media") or url.endswith("/9001/media"):
        return R(200, {"id": "container1"})
    if url.endswith("/17841/media_publish") or url.endswith("/9001/media_publish"):
        return R(200, {"id": "media1"})
    return R(404, {})
def get(url, params=None, headers=None, timeout=None):
    calls.append(("GET", url, params, headers or {}))
    if url.endswith("/youtube/v3/channels"):
        return R(200, {"items": [{"id": "UC123", "snippet": {"title": "My Channel"}}]})
    if url == "https://graph.instagram.com/access_token":
        return R(200, {"access_token": "ig-long", "token_type": "bearer", "expires_in": 5183944})
    if url == "https://graph.instagram.com/me":
        return R(200, {"user_id": "17841", "username": "clipper", "account_type": "MEDIA_CREATOR"})
    if url == "https://graph.facebook.com/v21.0/oauth/access_token":
        if params.get("grant_type") == "fb_exchange_token":
            return R(200, {"access_token": "fb-long-token", "expires_in": 5184000})
        return R(200, {"access_token": "fb-short-token"}) if params.get("code") == "fbcode" else R(400, {"error": {"message": "bad code"}})
    if url == "https://graph.facebook.com/v21.0/debug_token":
        return R(200, {"data": {"granular_scopes": [{"scope": "instagram_basic", "target_ids": ["9001"]},
                                                    {"scope": "instagram_content_publish", "target_ids": ["9001"]}]}})
    if url == "https://graph.facebook.com/v21.0/9001":
        return R(200, {"id": "9001", "username": "fbclipper", "account_type": "BUSINESS"})
    if url.endswith("/container1"):
        return R(200, {"status_code": "FINISHED"})
    if url.endswith("/media1"):
        return R(200, {"permalink": "https://www.instagram.com/reel/abc/"})
    return R(404, {})
def put(url, content=None, headers=None, timeout=None):
    uploads.append((url, len(content), headers))
    return R(201, None, text="")
accounts.httpx.post, accounts.httpx.get, accounts.httpx.put = post, get, put
accounts.time.sleep = lambda s: None

# ---- connecting: the button goes to the platform's own page, and back
def connect(cl, pf, code="code1"):
    r = cl.get(f"/oauth/{pf}/start")
    loc = urlparse(r.headers["location"])
    state = parse_qs(loc.query)["state"][0]
    back = cl.get(f"/oauth/{pf}/callback", params={"code": code, "state": state})
    return loc, back
loc, back = connect(b, "youtube")
check("YouTube: Google's page, asking to upload", (loc.netloc, "youtube.upload" in parse_qs(loc.query)["scope"][0]), ("accounts.google.com", True))
check("YouTube: back on Connected apps, connected", (back.status_code, back.headers["location"].startswith("/app/settings/apps?connected=youtube")), (303, True))
loc, back = connect(b, "instagram", "igcode#_")
q = parse_qs(loc.query)
check("Instagram: Instagram's own page, with publishing permission", (loc.netloc, q["client_id"][0], "instagram_business_content_publish" in q["scope"][0]),
      ("www.instagram.com", "1234567890", True))
check("Instagram: connected, with the #_ Instagram adds to the code removed", "connected=instagram" in back.headers["location"])
loc, back = connect(b, "tiktok")
q = parse_qs(loc.query)
check("TikTok: TikTok's own page, asking to post", (loc.netloc, q["client_key"][0], q["scope"][0]), ("www.tiktok.com", "awtiktokkey123", "user.info.basic,video.publish"))
check("TikTok: connected", "connected=tiktok" in back.headers["location"])
accs = {x["platform"]: x for x in b.get("/api/accounts").json()}
check("all three accounts are listed with their names", (accs["youtube"]["name"], accs["instagram"]["name"], accs["tiktok"]["name"]),
      ("My Channel", "@clipper", "@streamerclips"))
check("tokens never reach the page", any(t in b.get("/api/accounts").text for t in ("ig-long", "tt-access", "yt-refresh")), False)
check("someone else's accounts are not mine", a.get("/api/accounts").json(), [])
r = b.get("/oauth/tiktok/callback", params={"code": "x", "state": "made-up"})
check("a made-up return is refused", "connect_error" in r.headers["location"])
r = b.get("/oauth/tiktok/callback", params={"error": "access_denied"})
check("cancelling on the platform says so", "cancelled" in r.headers["location"])
loc = urlparse(a.get("/oauth/tiktok/start").headers["location"])
r = b.get("/oauth/tiktok/callback", params={"code": "c", "state": parse_qs(loc.query)["state"][0]})
check("one person's sign-in cannot be finished by another", "connect_error" in r.headers["location"])
check("an unknown platform does not exist", b.get("/oauth/myspace/start").status_code, 404)

# ---- the one-hour links Instagram fetches clips from
with ctx.as_user(user.id):
    jd = config.user_dir(user.id) / "jobs" / "job-12345678" / "clips"
jd.mkdir(parents=True)
(jd.parent / "job.json").write_text(json.dumps({"id": "job-12345678", "user": user.id, "dir": str(jd.parent), "clips": [], "status": "done",
                                                "creator": None, "created": time.time(), "options": {}, "source": {"type": "file"}}), encoding="utf-8")
(jd / "clip01.mp4").write_bytes(b"\x00" * 6_000)
from studio import jobs
jobs.load_user(user.id)
with ctx.as_user(user.id):
    link = accounts.clip_link("job-12345678", "clip01.mp4")
anon = TestClient(server.app, base_url=BASE)
path = urlparse(link).path
check("a signed link serves the clip to anyone, no sign-in", (anon.get(path).status_code, len(anon.get(path).content)), (200, 6000))
bad = path.replace(".mp4", "x.mp4")
check("a changed link does not", anon.get(bad).status_code, 404)
check("a made-up link does not", anon.get("/pub/abc.def.mp4").status_code, 404)
with ctx.as_user(user.id):
    old = accounts.clip_link("job-12345678", "clip01.mp4", seconds=-5)
check("an expired link does not", anon.get(urlparse(old).path).status_code, 404)

# ---- posting
with ctx.as_user(user.id):
    ig, ttacc = accounts.get(accs["instagram"]["id"]), accounts.get(accs["tiktok"]["id"])
    calls.clear()
    rid, url = accounts.instagram_upload(ig, jd / "clip01.mp4", "My caption", "job-12345678")
    made = next(c for c in calls if c[1].endswith("/17841/media"))
    check("Instagram: a Reel from the signed link", (made[2]["media_type"], made[2]["video_url"].startswith(BASE + "/pub/")), ("REELS", True))
    check("Instagram: published, with its link", (rid, url), ("media1", "https://www.instagram.com/reel/abc/"))

    uploads.clear(); tt.update(audited=False, status=0)
    rid, url = accounts.tiktok_upload(ttacc, jd / "clip01.mp4", "My caption #fyp", "public")
    check("TikTok: an app not yet reviewed posts privately instead of failing", (tt["init"]["post_info"]["privacy_level"], url), ("SELF_ONLY", ""))
    check("TikTok: the whole file in one piece, with the right range", (len(uploads), uploads[0][2]["Content-Range"]), (1, "bytes 0-5999/6000"))
    check("TikTok: the account says why posts are private", "private" in accounts.get(ttacc["id"]).get("detail", ""))
    tt.update(audited=True, status=0)
    rid, url = accounts.tiktok_upload(ttacc, jd / "clip01.mp4", "My caption", "public")
    check("TikTok: once reviewed, public, with the video's link", (tt["init"]["post_info"]["privacy_level"], url),
          ("PUBLIC_TO_EVERYONE", "https://www.tiktok.com/@streamerclips/video/7300"))
    check("TikTok: big files go in pieces, the last taking the rest", accounts._tt_chunks(150_000_123), (10_000_000, 15))
    acc = accounts.get(ttacc["id"]); acc["expires"] = 0; accounts._put(acc)
    accounts._tt_token(accounts.get(ttacc["id"]))
    check("TikTok: an old sign-in is renewed, and the new refresh token kept", accounts.get(ttacc["id"])["refresh_token"], "tt-refresh-2")

# ---- Instagram through Facebook login (newer Meta apps): a login configuration id switches it on
r = a.put("/api/apps", json={"platform": "instagram", "client_id": "", "client_secret": "", "config_id": "12ab"}, headers=ORIGIN)
check("a configuration id that is not digits is refused", r.status_code, 400)
r = a.put("/api/apps", json={"platform": "instagram", "client_id": "", "client_secret": "newer-app-secret", "config_id": "1122334455667788"}, headers=ORIGIN)
check("the configuration id is saved with the app", (r.status_code, r.json()["instagram"]["facebook"]), (200, True))
check("...and never sent back whole", "1122334455667788" in json.dumps(r.json()), False)
loc, back = connect(b, "instagram", "fbcode")
q = parse_qs(loc.query)
check("Instagram (Facebook login): Facebook's page, asking by configuration, as a code",
      (loc.netloc, q["config_id"][0], q["override_default_response_type"][0], q["response_type"][0]), ("www.facebook.com", "1122334455667788", "true", "code"))
check("Instagram (Facebook login): connected", "connected=instagram" in back.headers["location"])
check("...and the person is asked to sign in, so they can choose Instagram", q.get("prompt"), ["login"])
fbacc = next(x for x in b.get("/api/accounts").json() if x["name"] == "@fbclipper")
check("the shared Instagram account is listed with its type", fbacc["detail"], "Business account")
check("its token never reaches the page", any(t in b.get("/api/accounts").text for t in ("fb-short-token", "fb-long-token")), False)
with ctx.as_user(user.id):
    ig2 = accounts.get(fbacc["id"])
    check("the short-lived token is swapped for a long-lived one", ig2["token"], "fb-long-token")
    calls.clear()
    rid, url = accounts.instagram_upload(ig2, jd / "clip01.mp4", "From Facebook login", "job-12345678")
    made = next(c for c in calls if c[1].endswith("/9001/media"))
    check("posting goes through Facebook's address, as a Reel from the signed link",
          (made[1].startswith("https://graph.facebook.com/v21.0/"), made[2]["media_type"], made[2]["video_url"].startswith(BASE + "/pub/")), (True, "REELS", True))
    check("...and publishes", (rid, url), ("media1", "https://www.instagram.com/reel/abc/"))
    ig2["token_time"] = 0; accounts._put(ig2)
    check("an old token is renewed (the swap again) and the time reset", (accounts._ig_token(accounts.get(fbacc["id"])), time.time() - accounts.get(fbacc["id"])["token_time"] < 5),
          ("fb-long-token", True))
r = b.get("/oauth/instagram/callback", params={"code": "wrong", "state": parse_qs(urlparse(b.get("/oauth/instagram/start").headers["location"]).query)["state"][0]})
check("a wrong code says it was not connected", "connect_error" in r.headers["location"])

# ---- finding the Instagram account after Facebook login: through a Page shared by a business, and clear reasons when none
def fb_variant(debug, pages, page_lookup):
    def g(url, params=None, headers=None, timeout=None):
        if url == "https://graph.facebook.com/v21.0/debug_token":
            return R(200, {"data": debug})
        if url == "https://graph.facebook.com/v21.0/me/accounts":
            return R(200, {"data": pages})
        if url.startswith("https://graph.facebook.com/v21.0/page") :
            return R(200, page_lookup)
        if url == "https://graph.instagram.com/me":       # a Facebook sign-in: Instagram's own API doesn't know it
            return R(400, {"error": {"code": 190}})
        if url == "https://graph.facebook.com/v21.0/9002":
            return R(200, {"id": "9002", "username": "pageclipper", "account_type": "MEDIA_CREATOR"})
        return get(url, params, headers, timeout)
    return g
both = ["instagram_basic", "instagram_content_publish", "pages_show_list", "pages_read_engagement"]
accounts.httpx.get = fb_variant({"user_id": "fbp", "scopes": both, "granular_scopes": [{"scope": "pages_show_list", "target_ids": ["page77"]}]},
                                [], {"id": "page77", "connected_instagram_account": {"id": "9002"}})
loc, back = connect(b, "instagram", "fbcode")
check("an Instagram account linked to a Page shared through a business is found", "connected=instagram" in back.headers["location"])
accounts.httpx.get = fb_variant({"user_id": "fbp", "scopes": both, "granular_scopes": []}, [], {})
loc, back = connect(b, "instagram", "fbcode")
check("no Page shared: says to tick the Page", "Page" in back.headers["location"] and "connect_error" in back.headers["location"])
_g = fb_variant({"user_id": "fbp", "scopes": ["instagram_basic", "instagram_content_publish"], "granular_scopes": []}, [], {})
accounts.httpx.get = lambda url, params=None, headers=None, timeout=None: (
    R(200, {"data": [{"id": "9002"}]}) if url.endswith("/me/instagram_accounts") else _g(url, params, headers, timeout))
loc, back = connect(b, "instagram", "fbcode")
check("a business sign-in with no Page: its Instagram account is still found", "connected=instagram" in back.headers["location"])
accounts.httpx.get = fb_variant({"user_id": "fbp", "scopes": both, "granular_scopes": []}, [{"id": "page1"}], {})
loc, back = connect(b, "instagram", "fbcode")
check("a Page without Instagram: says to link Instagram to it", "linked" in back.headers["location"])
accounts.httpx.get = fb_variant({"user_id": "fbp", "scopes": ["pages_show_list"], "granular_scopes": []}, [], {})
loc, back = connect(b, "instagram", "fbcode")
check("Instagram permission left unticked: says to leave every permission ticked", "ticked" in back.headers["location"])
accounts.httpx.get = get

# ---- what Meta requires of a live app: the deletion page, and the callbacks that remove a person's account
import base64, hashlib, hmac as _hmac
def signed(uid, secret):
    payload = base64.urlsafe_b64encode(json.dumps({"user_id": uid, "algorithm": "HMAC-SHA256"}).encode()).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(_hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    return f"{sig}.{payload}"
pg = anon.get("/data-deletion")
check("a data-deletion page, public, says how to remove data", (pg.status_code, "Disconnect" in pg.text), (200, True))
secret_now = accounts.app_keys("instagram")[1]
with ctx.as_user(user.id):
    acc_fb = accounts.get(fbacc["id"])
    check("the Instagram account remembers which Facebook person shared it", acc_fb.get("fb_user"), "")   # the stand-in debug_token names no user
    acc_fb["fb_user"] = "fbperson42"; accounts._put(acc_fb)
r = anon.post("/webhooks/meta/data-deletion", data={"signed_request": signed("fbperson42", "wrong-secret")})
check("a request not signed by Meta is refused", r.status_code, 400)
r = anon.post("/webhooks/meta/data-deletion", data={"signed_request": signed("fbperson42", secret_now)})
check("Meta's deletion request: answered with a page and a confirmation code", (r.status_code, r.json()["url"].startswith(BASE + "/data-deletion?code="), len(r.json()["confirmation_code"])), (200, True, 16))
check("...and that person's Instagram account is gone", any(x["id"] == fbacc["id"] for x in b.get("/api/accounts").json()), False)
check("...while other accounts stay", any(x["platform"] == "tiktok" for x in b.get("/api/accounts").json()), True)

print("ALL PASSED" if not fails else f"{len(fails)} failed")
import shutil; db.engine.dispose(); shutil.rmtree(os.environ["PEAKCUT_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
