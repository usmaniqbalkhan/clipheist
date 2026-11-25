"""The "Live now" page: who is live on Twitch right now, a list of famous streamers, and a search.

Everything comes from the platforms' public endpoints, free and without an account: the same Twitch web API that
already tells us whether a creator is live, and Kick's public channel API. Answers are shared by every user for a
minute or two, so a thousand people on this page still ask Twitch once. Pictures are passed through this server,
since the app only shows pictures from its own address."""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import profiles

GQL = "https://gql.twitch.tv/gql"
TOP_FOR = 120          # seconds the "live right now" list is reused
FAMOUS_FOR = 120       # seconds the famous list's live state is reused
SEARCH_FOR = 300

# Categories that are not a game. "Gaming" leaves these out.
NOT_GAMING = {"just chatting", "irl", "special events", "music", "art", "asmr", "talk shows & podcasts", "sports",
              "pools, hot tubs, and beaches", "travel & outdoors", "food & drink", "makers & crafting", "politics",
              "software and game development", "science & technology", "animals, aquariums, and zoos", "slots",
              "fitness & health", "beauty & body art", "dj", "djs", "co-working & studying", "virtual casino",
              "always on", "crypto", "watch parties"}
LANGS = {"en": "English", "es": "Spanish", "pt": "Portuguese", "de": "German", "fr": "French", "ru": "Russian",
         "ja": "Japanese", "ko": "Korean", "it": "Italian", "tr": "Turkish", "ar": "Arabic", "hi": "Hindi", "any": "Any language"}

# Famous streamers. Each channel was checked by hand to be the person's own (not a fan or clip account).
# Platforms are listed with the one they stream on most first; ClipHeist watches all of them.
FAMOUS = [
    ("Kai Cenat", "celebs", {"twitch": "kaicenat"}),
    ("IShowSpeed", "celebs", {"youtube": "@IShowSpeed", "twitch": "ishowspeed"}),
    ("Adin Ross", "celebs", {"kick": "adinross", "twitch": "adinross"}),
    ("Marlon", "celebs", {"twitch": "marlon"}),
    ("Fanum", "celebs", {"twitch": "fanum"}),
    ("Duke Dennis", "celebs", {"twitch": "duke"}),
    ("Agent 00", "celebs", {"twitch": "agent00"}),
    ("PlaqueBoyMax", "celebs", {"twitch": "plaqueboymax"}),
    ("Jynxzi", "celebs", {"twitch": "jynxzi"}),
    ("Lacy", "celebs", {"twitch": "lacy"}),
    ("Silky", "celebs", {"twitch": "silky"}),
    ("JasonTheWeen", "celebs", {"twitch": "jasontheween"}),
    ("RayAsianBoy", "celebs", {"twitch": "rayasianboy"}),
    ("BruceDropEmOff", "celebs", {"twitch": "brucedropemoff"}),
    ("DDG", "celebs", {"twitch": "ddg"}),
    ("StableRonaldo", "celebs", {"twitch": "stableronaldo"}),
    ("CaseOh", "celebs", {"twitch": "caseoh_"}),
    ("N3on", "celebs", {"kick": "n3on", "twitch": "n3on"}),
    ("xQc", "chatting", {"kick": "xqc", "twitch": "xqc"}),
    ("HasanAbi", "chatting", {"twitch": "hasanabi"}),
    ("Asmongold", "chatting", {"twitch": "zackrawrr"}),
    ("Pokimane", "chatting", {"twitch": "pokimane"}),
    ("Emiru", "chatting", {"twitch": "emiru"}),
    ("Mizkif", "chatting", {"twitch": "mizkif"}),
    ("Sykkuno", "chatting", {"twitch": "sykkuno"}),
    ("ExtraEmily", "chatting", {"twitch": "extraemily"}),
    ("Ninja", "gaming", {"twitch": "ninja"}),
    ("Tfue", "gaming", {"twitch": "tfue"}),
    ("shroud", "gaming", {"twitch": "shroud"}),
    ("summit1g", "gaming", {"twitch": "summit1g"}),
    ("TimTheTatman", "gaming", {"twitch": "timthetatman"}),
    ("NICKMERCS", "gaming", {"twitch": "nickmercs"}),
    ("Clix", "gaming", {"twitch": "clix"}),
    ("Bugha", "gaming", {"twitch": "bugha"}),
    ("TenZ", "gaming", {"twitch": "tenz"}),
    ("Tarik", "gaming", {"twitch": "tarik"}),
    ("Quackity", "gaming", {"twitch": "quackity"}),
    ("moistcr1tikal", "gaming", {"twitch": "moistcr1tikal"}),
]
GROUPS = {"celebs": "Streamer celebs", "chatting": "Just chatting", "gaming": "Gaming"}

_cache: dict = {}            # key -> (when, value)
_locks: dict = {}
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="discover")


def url_for(platform: str, handle: str) -> str:
    if platform == "twitch":
        return "https://www.twitch.tv/" + handle.lower()
    if platform == "kick":
        return "https://kick.com/" + handle.lower()
    return "https://www.youtube.com/" + handle


def _shared(key, ttl, fn, stale_ok=False):
    """fn(), worked out once for everyone and reused for `ttl` seconds. If the platform fails, the last answer
    (even an old one) is better than an error. With stale_ok, an out-of-date answer is handed back at once while a
    fresh one is worked out in the background, so nobody waits for the platforms (the famous list takes seconds)."""
    got = _cache.get(key)
    if got and time.time() - got[0] < ttl:
        return got[1]
    with _lock:
        lk = _locks.setdefault(key, threading.Lock())
    if got and stale_ok:
        def refresh():
            if lk.acquire(blocking=False):
                try:
                    _cache[key] = (time.time(), fn())
                except Exception:  # noqa: BLE001 - the old answer stays until the next try
                    pass
                finally:
                    lk.release()
        _pool.submit(refresh)
        return got[1]
    with lk:
        got = _cache.get(key)
        if got and time.time() - got[0] < ttl:
            return got[1]
        try:
            val = fn()
        except Exception:  # noqa: BLE001
            if got:
                return got[1]
            raise
        _cache[key] = (time.time(), val)
        if len(_cache) > 400:
            for k in [k for k, v in list(_cache.items()) if time.time() - v[0] > SEARCH_FOR]:
                _cache.pop(k, None)
                _locks.pop(k, None)
        return val


def _gql(query: str, variables: dict) -> dict:
    import httpx
    r = httpx.post(GQL, json={"query": query, "variables": variables},
                   headers={"Client-ID": profiles._TWITCH_WEB, **profiles.UA}, timeout=10)
    r.raise_for_status()
    d = r.json() or {}
    if d.get("errors") and not d.get("data"):
        raise ValueError(str(d["errors"])[:200])
    return d.get("data") or {}


SIZES = (160, 640)          # widths a picture is shrunk to: a logo, a cover
# picture servers this server will pass pictures on from: the streaming platforms, and Content Rewards' campaign art
IMG_HOSTS = profiles.PIC_HOSTS + ("crusercontent.com",)


def pic(url: str, w: int = 160) -> str:
    """The app's address for a picture on Twitch's or Kick's image servers ("" for anything else)."""
    from urllib.parse import quote, urlparse
    u = urlparse(url or "")
    if url.startswith("yt:"):
        return f"/api/discover/img?u={quote(url, safe='')}&w={w}"
    return f"/api/discover/img?u={quote(url, safe='')}&w={w}" if u.scheme == "https" and u.hostname in IMG_HOSTS else ""


_raw: dict = {}              # "yt:<handle>" -> (until, (bytes, type)): pictures read while looking up a YouTube channel
_imgs: dict = {}             # (address, width) -> (when, (bytes, type))
MAX_SOURCE = 4_000_000


def _shrink(data: bytes, w: int):
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.load()
    if im.width > w:
        im = im.resize((w, max(1, round(im.height * w / im.width))), Image.LANCZOS)
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB")
    out = io.BytesIO()
    im.save(out, "WEBP", quality=80, method=4)
    return out.getvalue(), "image/webp"


def image(url: str, w: int = 160):
    """(bytes, type) for a picture on the platforms' own image servers, shrunk to `w` wide and kept a while."""
    from urllib.parse import urlparse
    w = w if w in SIZES else SIZES[0]
    key = (url, w)
    got = _imgs.get(key)
    if got and time.time() - got[0] < (300 if "previews-ttv" in url else 3600):   # live pictures change every few minutes
        return got[1]
    if url.startswith("yt:"):
        src = (_raw.get(url) or (0, None))[1]
    else:
        u = urlparse(url)
        if u.scheme != "https" or (u.hostname or "") not in IMG_HOSTS:
            return None
        ctype, data = profiles._get(url, "image/*", MAX_SOURCE + 1)
        # Twitch labels some pictures "binary/octet-stream": Pillow decides whether it is a picture, not the label
        src = (data, ctype.split(";")[0].strip()) if len(data) <= MAX_SOURCE else None
    if not src:
        return None
    try:
        out = _shrink(src[0], w)
    except Exception:  # noqa: BLE001 - not a picture Pillow can read: send it as it came only if it says it's one
        out = src if len(src[0]) <= profiles.MAX_PIC and src[1] in ("image/jpeg", "image/png", "image/webp", "image/gif") else None
    if out:
        _imgs[key] = (time.time(), out)
        if len(_imgs) > 600:
            for k in sorted(_imgs, key=lambda k: _imgs[k][0])[:150]:
                _imgs.pop(k, None)
    return out


# ------------------------------------------------------------------------------------------- live on Twitch now
_STREAM = ("viewersCount title language game{displayName} previewImageURL(width:440,height:248) contentClassificationLabels{id} "
           "broadcaster{login displayName profileImageURL(width:70) followers{totalCount}}")


def _stream_row(n: dict) -> dict:
    b = n.get("broadcaster") or {}
    login = (b.get("login") or "").lower()
    return {"key": "twitch:" + login, "name": b.get("displayName") or login, "platform": "twitch",
            "channels": {"twitch": url_for("twitch", login)}, "viewers": n.get("viewersCount") or 0,
            "title": (n.get("title") or "").strip()[:140], "game": ((n.get("game") or {}).get("displayName") or ""),
            "lang": (n.get("language") or "").lower(), "thumb": pic(n.get("previewImageURL") or "", 640),
            "pic": pic(b.get("profileImageURL") or ""), "followers": ((b.get("followers") or {}).get("totalCount")),
            "gated": bool(n.get("contentClassificationLabels"))}


def top_live(lang: str = "en", kind: str = "gaming", n: int = 24) -> list:
    """The biggest live streams on Twitch right now."""
    lang = lang if lang in LANGS else "en"
    kind = "all" if kind == "all" else "gaming"

    def get():
        opts = {"sort": "VIEWER_COUNT"}
        if lang != "any":
            opts["languages"] = [lang.upper()]
        # Twitch hands out 30 at a time; two pages leave enough once the non-games are taken out
        q = "query($o:StreamOptions,$a:Cursor){streams(first:30,after:$a,options:$o){edges{cursor node{%s}}}}" % _STREAM
        rows, after = [], None
        for _ in range(2 if kind == "gaming" else 1):
            edges = ((_gql(q, {"o": opts, "a": after}).get("streams") or {}).get("edges")) or []
            rows += [_stream_row(e["node"]) for e in edges if e.get("node")]
            after = edges[-1].get("cursor") if edges else None
            if not after:
                break
        if kind == "gaming":
            rows = [r for r in rows if r["game"] and r["game"].lower() not in NOT_GAMING]
        rows = rows[:n]
        _warm([r["thumb"] for r in rows] + [r["pic"] for r in rows])
        return rows
    return _shared(f"top:{lang}:{kind}", TOP_FOR, get, stale_ok=True)


GTA_GAMES = ("Grand Theft Auto VI", "Grand Theft Auto V")


def game_live(games=GTA_GAMES, n: int = 30) -> list:
    """Everyone streaming these games on Twitch right now, biggest first (any language)."""
    def get():
        q = "query($n:String!){game(name:$n){streams(first:30){edges{node{%s}}}}}" % _STREAM
        rows = []
        for g in games:
            edges = ((((_gql(q, {"n": g}).get("game") or {}).get("streams")) or {}).get("edges")) or []
            rows += [_stream_row(e["node"]) for e in edges if e.get("node")]
        seen, out = set(), []
        for r in sorted(rows, key=lambda r: -(r["viewers"] or 0)):
            if r["key"] not in seen:
                seen.add(r["key"]); out.append(r)
        out = out[:n]
        _warm([r["thumb"] for r in out] + [r["pic"] for r in out])
        return out
    return _shared("game:" + "|".join(games), TOP_FOR, get, stale_ok=True)


# ------------------------------------------------------------------------------------------- famous streamers
def _kick(slug: str) -> dict:
    import json
    _, raw = profiles._get(f"https://kick.com/api/v2/channels/{slug}", "application/json")
    d = json.loads(raw)
    ls = d.get("livestream") or None
    n = str(d.get("followers_count") or "").strip()
    ob = d.get("offline_banner_image") or {}
    return {"followers": int(n) if n.isdigit() else None, "pic": (d.get("user") or {}).get("profile_pic") or "",
            "cover": ob.get("src") or (d.get("banner_image") or {}).get("url") or "",
            "live": {"platform": "kick", "viewers": ls.get("viewer_count") or 0, "title": (ls.get("session_title") or "")[:140],
                     "game": ((ls.get("categories") or [{}])[0] or {}).get("name") or "",
                     "thumb": (ls.get("thumbnail") or {}).get("src") or (ls.get("thumbnail") or {}).get("url") or ""} if ls else None}


def _youtube(handle: str) -> dict:
    url = url_for("youtube", handle)
    out = {"followers": None, "pic": "", "cover": "", "live": None}
    try:
        p = profiles.fetch(url)                   # kept an hour
        if p.get("pic"):
            _raw["yt:" + handle] = (time.time() + 3600, p["pic"])
            out["pic"] = "yt:" + handle
        out["followers_text"] = p.get("followers") or ""
    except Exception:  # noqa: BLE001
        pass
    lv = profiles.live_shared("YouTube", url)
    if lv.get("live"):
        vid = lv.get("embed") if re.fullmatch(r"[\w-]{11}", str(lv.get("embed") or "")) else ""
        out["live"] = {"platform": "youtube", "viewers": lv.get("viewers") or 0, "title": lv.get("title") or "", "game": "", "thumb": "", "embed": vid}
    return out


def _twitch_many(logins: list) -> dict:
    q = ("query($l:[String!]){users(logins:$l){login displayName profileImageURL(width:150) followers{totalCount} offlineImageURL bannerImageURL "
         "stream{viewersCount title game{displayName} previewImageURL(width:440,height:248) contentClassificationLabels{id}}}}")
    out = {}
    for u in (_gql(q, {"l": logins}).get("users") or []):
        if not u:
            continue
        st = u.get("stream")
        out[u["login"].lower()] = {"followers": (u.get("followers") or {}).get("totalCount"), "pic": u.get("profileImageURL") or "",
                                   "cover": u.get("bannerImageURL") or u.get("offlineImageURL") or "",
                                   "live": {"platform": "twitch", "viewers": st.get("viewersCount") or 0, "title": (st.get("title") or "")[:140],
                                            "game": (st.get("game") or {}).get("displayName") or "",
                                            "thumb": st.get("previewImageURL") or "",
                                            "gated": bool(st.get("contentClassificationLabels"))} if st else None}
    return out


def _famous_now() -> list:
    tw = [h for _, _, ch in FAMOUS for p, h in ch.items() if p == "twitch"]
    futs = {("kick", h): _pool.submit(_kick, h) for _, _, ch in FAMOUS for p, h in ch.items() if p == "kick"}
    futs.update({("youtube", h): _pool.submit(_youtube, h) for _, _, ch in FAMOUS for p, h in ch.items() if p == "youtube"})
    try:
        tinfo = _twitch_many(tw)
    except Exception:  # noqa: BLE001
        tinfo = {}
    info = {("twitch", h): v for h, v in tinfo.items()}
    for k, f in futs.items():
        try:
            info[k] = f.result(timeout=25)
        except Exception:  # noqa: BLE001
            pass
    rows = []
    for name, group, ch in FAMOUS:
        found = [(p, info.get((p, h.lower() if p != "youtube" else h))) for p, h in ch.items()]
        found = [(p, i) for p, i in found if i]
        lives = sorted([i["live"] for _, i in found if i.get("live")], key=lambda x: -(x.get("viewers") or 0))
        followers = max([i["followers"] for _, i in found if isinstance(i.get("followers"), int)] or [0])
        picture = next((i["pic"] for _, i in found if i.get("pic")), "")
        cover = next((i["cover"] for _, i in found if i.get("cover")), "")
        live = dict(lives[0]) if lives else None
        if live:
            live["thumb"] = pic(live.get("thumb") or "", 640)
        rows.append({"key": "famous:" + name.lower().replace(" ", ""), "name": name, "group": group,
                     "channels": {p: url_for(p, h) for p, h in ch.items()}, "followers": followers or None,
                     "pic": pic(picture), "cover": pic(cover, 640), "live": live})
    rows.sort(key=lambda r: (not r["live"], -((r["live"] or {}).get("viewers") or 0), -(r["followers"] or 0)))
    _warm([r["pic"] for r in rows] + [r["cover"] for r in rows] + [(r["live"] or {}).get("thumb") for r in rows])
    return rows


_warming = threading.Lock()


def _warm(addrs: list):
    """Fetch and shrink the pictures now, in the background, so the first person to open the page doesn't wait
    for 80 pictures one by one. Pictures already kept are skipped."""
    from urllib.parse import parse_qs, urlparse

    def run():
        if not _warming.acquire(blocking=False):
            return
        def one(a):
            q = parse_qs(urlparse(a or "").query)
            u, w = (q.get("u") or [""])[0], int((q.get("w") or ["160"])[0])
            if u and (u, w) not in _imgs:
                try:
                    image(u, w)
                except Exception:  # noqa: BLE001
                    pass
        try:
            with ThreadPoolExecutor(max_workers=6, thread_name_prefix="discover-warm") as ex:
                list(ex.map(one, addrs))
        finally:
            _warming.release()
    threading.Thread(target=run, daemon=True, name="discover-warm").start()


def famous() -> list:
    return _shared("famous", FAMOUS_FOR, _famous_now, stale_ok=True)


def warm_up():
    """At start-up, in the background: the famous list and the English gaming streams are worked out before
    anyone opens Home, so the first person doesn't wait for them."""
    def run():
        for fn in (famous, lambda: top_live("en", "gaming")):
            try:
                fn()
            except Exception:  # noqa: BLE001 - the page works it out on first open instead
                pass
    threading.Thread(target=run, daemon=True, name="discover-warm-up").start()


# ------------------------------------------------------------------------------------------- search
def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def search(q: str) -> list:
    """Streamers matching what was typed: the famous list first, then Twitch's own search, then a Kick channel
    with exactly that name. A channel link works too."""
    q = (q or "").strip()[:60]
    if len(_squash(q)) < 2:
        return []
    return _shared("s:" + q.lower(), SEARCH_FOR, lambda: _search(q))


def _search(q: str) -> list:
    sq = _squash(q)
    out, seen = [], set()

    def add(row):
        k = tuple(sorted(row["channels"].values()))
        if k not in seen and not (set(row["channels"].values()) & {u for r in out for u in r["channels"].values()}):
            seen.add(k)
            out.append(row)
    try:
        fam = famous()
    except Exception:  # noqa: BLE001
        fam = []
    for r in fam:
        if sq in _squash(r["name"]) or any(sq in _squash(u.rsplit("/", 1)[-1]) for u in r["channels"].values()):
            add(r)
    kick = _pool.submit(_kick, sq) if re.fullmatch(r"[a-z0-9_]{3,25}", sq) else None
    try:
        qq = ("query($s:String!){searchFor(userQuery:$s,platform:\"web\",target:{index:USER}){users{items{login displayName "
              "profileImageURL(width:150) offlineImageURL bannerImageURL followers{totalCount} stream{viewersCount title game{displayName} previewImageURL(width:440,height:248)}}}}}")
        items = ((((_gql(qq, {"s": q}).get("searchFor") or {}).get("users") or {}).get("items")) or [])
    except Exception:  # noqa: BLE001
        items = []
    rows = []
    for u in items[:20]:
        login = (u.get("login") or "").lower()
        st = u.get("stream")
        f = (u.get("followers") or {}).get("totalCount") or 0
        if f < 500 and not st:
            continue                               # an empty or fan account
        rows.append({"key": "twitch:" + login, "name": u.get("displayName") or login, "group": "",
                     "channels": {"twitch": url_for("twitch", login)}, "followers": f, "pic": pic(u.get("profileImageURL") or ""),
                     "cover": pic(u.get("bannerImageURL") or u.get("offlineImageURL") or "", 640),
                     "live": {"platform": "twitch", "viewers": st.get("viewersCount") or 0, "title": (st.get("title") or "")[:140],
                              "game": (st.get("game") or {}).get("displayName") or "", "thumb": pic(st.get("previewImageURL") or "", 640)} if st else None})
    rows.sort(key=lambda r: -(r["followers"] or 0))
    for r in rows[:6]:
        add(r)
    if kick:
        try:
            k = kick.result(timeout=3)              # Kick can be slow; never hold the whole search up for it
            if (k.get("followers") or 0) >= 1000 or k.get("live"):
                live = k["live"]
                if live:
                    live = {**live, "thumb": pic(live.get("thumb") or "", 640)}
                add({"key": "kick:" + sq, "name": sq, "group": "", "channels": {"kick": url_for("kick", sq)},
                     "followers": k.get("followers"), "pic": pic(k.get("pic") or ""), "cover": pic(k.get("cover") or "", 640), "live": live})
        except Exception:  # noqa: BLE001
            pass
    return out[:9]
