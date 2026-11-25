"""The Campaigns page: campaigns are read out of Content Rewards' public page, tidied, and shown; a page that has
changed shape or can't be reached gives a plain message. Their site is replaced by a stand-in page here."""
import json, os, sys, tempfile
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-camps-")
os.environ.update(DATABASE_URL="", PEAKCUT_VERIFY_EMAIL="0")
sys.path.insert(0, ".")
from fastapi.testclient import TestClient   # noqa: E402
from studio import auth, campaigns, creators, ctx, db, discover, server   # noqa: E402
db.init()
fails = []


def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)


A, B = "33c3d5a9-84a9-40c8-b6e4-9904e4816339", "0b178e3c-fbd3-4294-8ded-394e3da9c62f"
data = {"initialResults": [{"campaigns": [
    {"id": A, "title": "TJR $$50,000 Clipping Campaign", "brand": "Reach", "category": "Streamer", "description": "Get paid to clip TJR.\nKick: kick.com/tjr",
     "payoutSortRaw": 0.75, "type": "cpm", "budgetTotalRaw": 50000, "budgetSpentRaw": 38754.34, "availableBudgetRaw": 11245.66, "creatorCountRaw": 516,
     "platforms": ["instagram", "tiktok", "youtube", "<script>"], "isVerified": True, "requiresApplication": False, "launchedAtMs": 1793890164000,
     "thumbnail": "https://crusercontent.com/x/campaigns/thumbnails/a.webp", "avatar": "https://evil.example.com/logo.png"},
    {"id": B, "title": "$$9,000 Jason Pool Campaign", "brand": "Reach", "type": "pool", "budgetTotalRaw": 9000, "platforms": ["x"]},
    {"id": "not-an-id", "title": "bad"}, {"id": A, "title": "the same one again"}]}]}
piece = "1a:" + json.dumps(data, separators=(",", ":"))      # compact, as their page sends it
html = "<html><script>self.__next_f.push([1," + json.dumps(piece[:200]) + "])</script><script>self.__next_f.push([1," + json.dumps(piece[200:]) + "])</script></html>"

rows = campaigns.parse(html)
check("campaigns are read out of the page, once each, bad ones left out", [r["id"] for r in rows], [A, B])
a, b = rows
check("the doubled dollar sign in their titles is tidied", (a["title"], b["title"]), ("TJR $50,000 Clipping Campaign", "$9,000 Jason Pool Campaign"))
check("pay per 1,000 views, budget and what is left", (a["rate"], a["budget"], a["left"], a["creators"]), (0.75, 50000.0, 11245.66, 516))
check("when it started is kept, so the newest can be shown first", (a["launched"], b["launched"]), (1793890164.0, 0.0))
check("only real platforms are kept", a["platforms"], ["Instagram", "TikTok", "YouTube"])
check("a prize pool campaign is marked as one", (b["pool"], b["rate"]), (True, 0.0))
check("its page on their site", a["url"], "https://contentrewards.com/c/campaigns/" + A)
check("campaign art comes through PeakCut's own address", a["thumb"].startswith("/api/discover/img?u=https%3A%2F%2Fcrusercontent.com"))
check("pictures from anywhere else are not passed on", a["logo"], "")
check("a page with nothing in it gives no campaigns", campaigns.parse("<html>hello</html>"), [])

u, _ = auth._create("c@example.com", "c", password_hash="x")
cl = TestClient(server.app, base_url="https://clips.example.com", follow_redirects=False)
cl.cookies.set(auth.COOKIE, auth.start_session(u.id))
campaigns._fetch = lambda: rows
discover._cache.pop("campaigns", None)
r = cl.get("/api/campaigns")
check("the app gets the list", (r.status_code, len(r.json()["campaigns"]), r.json()["source"]), (200, 2, "Content Rewards"))


def down():
    raise OSError("their site is down")


campaigns._fetch = down
discover._cache.pop("campaigns", None)
r = cl.get("/api/campaigns")
check("their site down: a plain message, not a crash", (r.status_code, "Couldn't load the campaigns" in r.text), (503, True))

# ---- one campaign's own page: the whole brief, pay on each platform, and how much more there is after joining
one = {"queries": [
    {"queryKey": ["user", "me"], "state": {"data": None}},
    {"queryKey": ["campaigns", "detail", A], "state": {"data": {
        "id": A, "name": "TJR $50,000 Clipping Campaign", "description": "Clip TJR.\r\n\r\n\r\n\r\nUse #TJR and #ad, tag @tjr.\x07 Kick: kick.com/@notatag",
        "configuration": {"requirement": {"preApproval": True}, "payoutModel": [
            {"platform": "tiktok", "rate": 75, "minPayout": 375, "maxPayout": 75000}, {"platform": "myspace", "rate": 900}]},
        "briefCounts": {"contentRules": 1, "creatorRequirements": 4, "references": 2}}}},
    {"queryKey": ["campaigns", "discover-card", A], "state": {"data": {"name": "the card's own name", "description": "short", "requiresApplication": False}}},
    {"queryKey": ["campaigns", "detail", B], "state": {"data": {"name": "another campaign", "description": "not this one"}}}]}
piece = "18:" + json.dumps(one, separators=(",", ":"))
page = "<html><script>self.__next_f.push([1," + json.dumps(piece) + "])</script></html>"
d = campaigns.parse_detail(page, A)
check("the campaign's own page gives its name and whole brief, tidied", (d["title"], d["brief"]),
      ("TJR $50,000 Clipping Campaign", "Clip TJR.\n\nUse #TJR and #ad, tag @tjr.  Kick: kick.com/@notatag"))
check("pay on each platform, in dollars, real platforms only", d["payouts"], [{"platform": "TikTok", "rate": 0.75, "min": 3.75, "max": 750.0}])
check("how much brief there is after joining, and whether you must be accepted", (d["rules"], d["requirements"], d["files"], d["approval"]), (1, 4, 2, True))
check("a page for some other campaign gives nothing", campaigns.parse_detail(page, "ffffffff-ffff-4fff-8fff-ffffffffffff"), {})
check("hashtags and @names are read out of a brief; names inside links are not", campaigns.read_brief(d["brief"] + " mail me@x.com #1 &#39; #TJR"),
      {"tags": ["TJR", "ad"], "names": ["tjr"]})

# ---- a creator can be clipped for several campaigns: add with one click, change, remove
campaigns._fetch = lambda: rows
campaigns._fetch_detail = lambda cid: campaigns.parse_detail(page, cid) if cid == A else {} if cid != B else {"id": cid, "title": "$9,000 Jason Pool Campaign", "brief": "Clip Jason. Use #jason and #ad", "payouts": []}
for k in [k for k in discover._cache if str(k).startswith("campaign")]:
    discover._cache.pop(k, None)
H = {"Origin": "https://clips.example.com"}
c = cl.post("/api/creators", json={"name": "TJR"}, headers=H).json()
URL = f"/api/creators/{c['id']}/campaigns"


class _Ready:   # a campaign is only used once it has been set up and saved: these tests add and save in one go
    def __init__(self, r, put):
        self.status_code, self._r, self._put = r.status_code, r, put
        self.text = r.text
    def json(self):
        out = dict(self._r.json())
        if self._put is not None:
            out["creator"] = self._put.json()["creator"]
        return out


def add_ready(url, **kw):
    r = cl.post(url, **kw)
    put = cl.put(f"{url}/{r.json()['campaign']['key']}", json={"ready": True}, headers=kw.get("headers")) if r.status_code == 200 else None
    return _Ready(r, put)
def names(cr):     # the names of the campaigns a streamer uses, from My campaigns
    lib = {e["key"]: e["name"] for e in cl.get("/api/my-campaigns").json()["campaigns"]}
    return [lib[k] for k in cr["campaign_keys"]]


check("a new creator has no campaigns", c["campaign_keys"], [])
check("the brief of a listed campaign can be read", (cl.get(f"/api/campaigns/{A}").status_code, cl.get(f"/api/campaigns/{A}").json()["files"]), (200, 2))
check("an address that isn't a campaign is refused", cl.get("/api/campaigns/../../etc").status_code in (404, 405), True)
raw = cl.post(URL, json={"name": "Not set up yet"}, headers=H).json()
check("a new campaign waits to be set up: not ready, not in use", (raw["campaign"]["ready"], raw["creator"]["campaign_use"]), (False, ""))
cl.delete(f"/api/my-campaigns/{raw['campaign']['key']}", headers=H)
r = add_ready(URL, json={"id": A}, headers=H)
got, e1 = r.json()["creator"], r.json()["campaign"]
check("one click adds a listed campaign with its name, link, pay and brief", (r.status_code, len(got["campaign_keys"]), e1["id"], e1["name"], e1["url"], e1["rate"], e1["brief"][:9]),
      (200, 1, A, "TJR $50,000 Clipping Campaign", "https://contentrewards.com/c/campaigns/" + A, 0.75, "Clip TJR."))
check("...its hashtags, its cover and a colour of its own", (r.json()["tags"], e1["hashtags"], e1["thumb"].startswith("/api/discover/img?u="), e1["color"]), (["TJR", "ad"], ["TJR", "ad"], True, 0))
check("the first campaign added is the one in use; earnings use its pay", (got["campaign_use"], got["rate_per_1k"], got["campaign_name"]), (e1["key"], 0.75, "TJR $50,000 Clipping Campaign"))
r = add_ready(URL, json={"id": B}, headers=H)
got = r.json()["creator"]
e2 = r.json()["campaign"]
check("a second campaign joins the first, with its own hashtags and colour", (names(got), e2["hashtags"], e2["color"], got["campaign_use"]),
      (["TJR $50,000 Clipping Campaign", "$9,000 Jason Pool Campaign"], ["jason", "ad"], 1, e1["key"]))
r = add_ready(URL, json={"id": A}, headers=H)
check("adding the same campaign again doesn't double it", (len(r.json()["creator"]["campaign_keys"]), r.json()["campaign"]["key"]), (2, e1["key"]))
r = add_ready(URL, json={"name": "  My brand deal ", "url": "javascript:alert(1)", "rate": "2.5", "brief": "Post daily.\r\nUse #brand #AD", "thumb": "https://evil.example.com/x.png", "id": ""}, headers=H)
hand = r.json()["campaign"]
check("a campaign can be set up by hand (Clipster, a brand deal)", (r.status_code, hand["name"], hand["rate"], hand["brief"], hand["id"]), (200, "My brand deal", 2.5, "Post daily.\nUse #brand #AD", ""))
check("...only a real link and PeakCut's own picture address are kept", (hand["url"], hand["thumb"]), ("", ""))
check("...with the hashtags of its own brief", (r.json()["tags"], hand["hashtags"]), (["brand", "AD"], ["brand", "AD"]))
check("a campaign needs a name", cl.post(URL, json={"name": " ", "brief": "x"}, headers=H).status_code, 400)
check("a campaign that isn't listed is refused", cl.post(URL, json={"id": "ffffffff-ffff-4fff-8fff-ffffffffffff"}, headers=H).status_code, 404)
check("someone else's creator can't be given a campaign", cl.post("/api/creators/nobody/campaigns", json={"id": A}, headers=H).status_code, 404)
cl.put(f"/api/creators/{c['id']}", json={"campaigns": [{"name": "sneaked in"}], "campaign_name": "sneaked", "campaign_use": "nokey123"}, headers=H)
with ctx.as_user(u.id):
    mine = creators.get(c["id"])
check("the settings form can't change the campaign list, or use a campaign that isn't there", (names(mine)[-1], mine["campaign_name"], mine["campaign_use"]),
      ("My brand deal", "", ""))
r = cl.put(f"/api/creators/{c['id']}", json={"campaign_use": e2["key"]}, headers=H)
check("another campaign can be put in use; earnings follow it", (r.json()["campaign_use"], r.json()["campaign_name"], r.json()["rate_per_1k"]), (e2["key"], "$9,000 Jason Pool Campaign", 0.0))
r = cl.put(f"{URL}/{hand['key']}", json={"name": "My brand deal 2", "brief": "Use #brandnew #brand"}, headers=H)
check("editing a campaign's brief adds its new hashtags to the ones it has", (r.json()["campaign"]["name"], r.json()["campaign"]["rate"], r.json()["tags"], r.json()["campaign"]["hashtags"]),
      ("My brand deal 2", 2.5, ["brandnew"], ["brand", "AD", "brandnew"]))
r = cl.put(f"{URL}/{hand['key']}", json={"color": 4, "hashtags": "#only #these"}, headers=H)
check("its colour and hashtags can be set by hand", (r.json()["campaign"]["color"], r.json()["campaign"]["hashtags"], r.json()["campaign"]["name"]), (4, ["only", "these"], "My brand deal 2"))
check("a colour that doesn't exist is not taken", cl.put(f"{URL}/{hand['key']}", json={"color": 99}, headers=H).json()["campaign"]["color"], 4)

# every new video or recording belongs to the campaign in use
with ctx.as_user(u.id):
    from studio import jobs   # noqa: E402
    j1 = jobs.create({"type": "url", "url": "https://kick.com/x", "name": "x"}, {}, enqueue=False, creator=c["id"])
    j2 = jobs.create({"type": "url", "url": "https://kick.com/x", "name": "x"}, {}, enqueue=False)
check("a new video belongs to the creator's campaign in use; one without a creator to none", (j1["campaign"], j2["campaign"], jobs.public(j1)["campaign"]), (e2["key"], "", e2["key"]))
check("a campaign that isn't there can't be edited", cl.put(f"{URL}/nokey123", json={"name": "x"}, headers=H).status_code, 404)

from PIL import Image   # noqa: E402
import io   # noqa: E402
webp = io.BytesIO(); Image.new("RGBA", (64, 64), (255, 139, 51, 255)).save(webp, "WEBP")
campaigns.brand_logo = lambda cid: (webp.getvalue(), "image/webp") if cid == A else None
r = cl.post(f"{URL}/{e1['key']}/logo/brand", headers=H)
with ctx.as_user(u.id):
    check("a listed campaign's brand picture can be used as its clip logo", (r.status_code, creators.campaign(r.json(), e1["key"])["logo"]), (200, f"logo-{e1['key']}.webp"))
check("a campaign set up by hand has no brand picture", cl.post(f"{URL}/{hand['key']}/logo/brand", headers=H).status_code, 404)
r = cl.request("DELETE", f"{URL}/{e2['key']}", headers=H)
off = r.json()["creator"]
check("a streamer can stop using the campaign in use: it then uses none, and the campaign stays in My campaigns",
      (names(off), off["campaign_use"], e2["key"] in [x["key"] for x in cl.get("/api/my-campaigns").json()["campaigns"]]),
      (["TJR $50,000 Clipping Campaign", "My brand deal 2"], "", True))
r = cl.request("DELETE", f"/api/my-campaigns/{e1['key']}", headers=H)
with ctx.as_user(u.id):
    gone = not (creators._lib_dir() / f"logo-{e1['key']}.webp").exists()
    left = creators.get(c["id"])["campaign_keys"]
check("removing a campaign from My campaigns removes its logo file and takes it off every streamer", (gone, e1["key"] in left), (True, False))
check("removing it twice is refused", cl.request("DELETE", f"/api/my-campaigns/{e1['key']}", headers=H).status_code, 404)
for i in range(creators.MAX_CAMPAIGNS - 1):
    add_ready(URL, json={"name": f"extra {i}"}, headers=H)
check("there is a limit to how many campaigns one streamer uses", cl.post(URL, json={"name": "one too many"}, headers=H).status_code, 400)

# a creator from when there was room for one campaign only keeps it, with the logo, text and hashtags it had
with ctx.as_user(u.id):
    old = creators._fix({**creators.DEFAULTS, "id": "old-one", "name": "Old", "campaign_name": "FaZe Flank", "campaign_url": "https://contentrewards.com/c/campaigns/x", "rate_per_1k": 1.5,
                         "logo": "logo.png", "logo_pos": "tr", "overlay_text": "@faze", "campaign_tags": "#a #b"})
    e = creators.campaign(None, old["campaign_use"]) or {}
check("an older creator's single campaign moves to My campaigns, in use, with its look",
      (len(old["campaign_keys"]), e.get("name"), e.get("rate"), e.get("logo"), e.get("logo_pos"), e.get("overlay_text"), e.get("hashtags"), old["campaign_use"] == e.get("key")),
      (1, "FaZe Flank", 1.5, "logo.png", "tr", "@faze", ["a", "b"], True))
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
