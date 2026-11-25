"""Creator workspaces. Each user has their own: users/<user>/creators/<id>/{videos,live,creator.json,log.jsonl}."""
import copy
import json
import shutil
import re
import secrets
import threading
import time
from pathlib import Path

from . import config, ctx, templates


def _root() -> Path:
    return config.user_dir(ctx.uid()) / "creators"

_lock = threading.RLock()

DEFAULTS = {"name": "", "channel": "", "style": "bounce", "layout": "zoom", "captions": True, "min_len": 15, "max_len": 60,
            "max_clips": 6, "min_score": 80, "auto_watch": False, "buffer_min": 20, "live_per_hour": 4, "ai_every_min": 3, "ai_only_on_spike": True, "auto_jump_pct": 60,
            "decide_mode": "both", "must_clip_min": 0, "min_gap_min": 2,
            "game": "", "hook_text": "", "description_text": "",
            "trigger_words": "clip that, clip it",
            "autopost": False, "accounts": [], "post_min_score": 75, "post_gap_min": 45, "daily_cap": 6,
            "privacy": "public", "post_footer": "", "rate_per_1k": 0.0, "channels": {},
            "lang": "en",    # the language this creator speaks: picks the speech model, for live recordings too
            # `campaigns` is every campaign this creator is clipped for (add_campaign and friends below). Each has
            # its own logo, small text, hashtags and colour. `campaign_use` is the one new videos and recordings
            # belong to ("" for none); its name, link and pay are also kept in campaign_name, campaign_url, rate_per_1k.
            "campaigns": [], "campaign_keys": [], "campaign_use": "", "campaign_name": "", "campaign_url": "",
            # the template new clips of this streamer are drawn with ("": the account's default template)
            "template": "",
            # where this streamer's facecam is in their stream (shares of the frame), found on the first clip with a
            # style that has a cam (cam_auto) or set by hand; cam_none: looked, there's no separate cam
            "cam": None, "cam_auto": False, "cam_none": False,
            # the captions' language for this streamer's clips ("": as spoken), and the quality videos are fetched in
            "cap_lang": "", "quality": 720}
LOGO_POS = ("tl", "tc", "tr", "bl", "bc", "br", "full")
LOGO_TYPES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm")
PLATFORMS = ("youtube", "twitch", "kick")


def clean_hook(v) -> str:
    """The big text a person typed for their clips: one line, as they wrote it (links and @names stay, a campaign
    may need them), and short enough to sit in the box on the video."""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in str(v or "")).split())[:60]


def clean_description(v) -> str:
    """The description a person typed for their clips: as they wrote it, line breaks included, so a campaign's
    set text (links, @names, hashtags) is not changed. Control characters go, and it is cut at 2000 characters."""
    nl, cr, tab = chr(10), chr(13), chr(9)
    lines = ("".join(ch if ch.isprintable() or ch == tab else " " for ch in line).rstrip()
             for line in str(v or "").replace(cr + nl, nl).replace(cr, nl).split(nl))
    return nl.join(lines).strip()[:2000]


def platform_key(url: str) -> str:
    from urllib.parse import urlparse
    h = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
    return "youtube" if "youtube" in h or h == "youtu.be" else "twitch" if "twitch" in h else "kick" if "kick" in h else ""


# Pages of a channel, not the channel. Recording from one of them asks for an old video instead of the live stream:
# kick.com/<name>/videos was read as a single Kick video, Kick refused it, and auto-record failed 83 times in a row.
CHANNEL_PAGES = {"videos", "about", "clips", "streams", "live", "featured", "shorts", "schedule", "home",
                 "playlists", "community", "posts", "vods", "followers", "following"}


def channel_link(url: str) -> str:
    """The bare channel address for a link to any page of a Kick, Twitch or YouTube channel; anything else as is."""
    from urllib.parse import urlparse
    raw = str(url or "").strip()
    if not raw:
        return raw
    p = urlparse(raw if "://" in raw else "https://" + raw)
    host, parts = (p.hostname or "").lower(), [x for x in p.path.split("/") if x]
    if not parts:
        return raw
    page_only = len(parts) == 1 or (len(parts) == 2 and parts[1].lower() in CHANNEL_PAGES)
    if (host == "kick.com" or host.endswith(".kick.com")) and page_only and re.fullmatch(r"[\w\-]{2,40}", parts[0]) \
            and parts[0].lower() not in ("video", "videos", "categories", "category", "search", "browse", "following"):
        return "https://kick.com/" + parts[0]
    if host in ("twitch.tv", "www.twitch.tv", "m.twitch.tv") and page_only and re.fullmatch(r"\w{2,30}", parts[0]) \
            and parts[0].lower() not in ("videos", "directory", "search", "downloads", "p", "settings"):
        return "https://www.twitch.tv/" + parts[0]
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        if parts[0].startswith("@") and page_only:
            return "https://www.youtube.com/" + parts[0]
        if parts[0] in ("channel", "c", "user") and len(parts) >= 2 and (len(parts) == 2 or (len(parts) == 3 and parts[2].lower() in CHANNEL_PAGES)):
            return f"https://www.youtube.com/{parts[0]}/{parts[1]}"
    return raw


def _fix(c: dict) -> dict:
    """One channel per platform in `channels`; `channel` is the one to record from and is always one of them."""
    ch = {k: channel_link(v) for k, v in (c.get("channels") or {}).items() if k in PLATFORMS and v}
    if c.get("channel"):
        c["channel"] = channel_link(c["channel"])
    if c.get("channel") and platform_key(c["channel"]) and c["channel"] not in ch.values():
        ch.setdefault(platform_key(c["channel"]), c["channel"])
    c["channels"] = ch
    if not c.get("ai_every_min"):
        c["ai_every_min"] = DEFAULTS["ai_every_min"]   # "Auto" (0) is gone: jumps in loudness and chat are always checked
    # Each AI check is paid for with the owner's own key, so how often it asks and whether a clip is forced every so
    # often stay exactly as each person set them; nothing here turns extra checks on.
    if c.get("channel") not in ch.values():
        c["channel"] = next((ch[k] for k in PLATFORMS if k in ch), "")
    camps = [x for x in (_campaign(e, c) for e in c.get("campaigns") or [] if isinstance(e, dict)) if x] if isinstance(c.get("campaigns"), list) else []
    if (c.get("campaigns_v") or 0) < 2:
        # Creators from before a campaign had its own look: the one campaign they had room for becomes the first of
        # the list, and the logo, small text and hashtags that sat on the creator move onto it.
        had = {"logo": c.get("logo") or "", "logo_pos": c.get("logo_pos"), "logo_size": c.get("logo_size"),
               "overlay_text": c.get("overlay_text") or "", "hashtags": tag_list(c.get("campaign_tags") or "")}
        if not camps and (c.get("campaign_name") or c.get("campaign_url") or had["logo"] or had["overlay_text"] or had["hashtags"]):
            camps = [_campaign({"key": "first001", "name": c.get("campaign_name") or "My campaign", "url": c.get("campaign_url"),
                                "rate": c.get("rate_per_1k"), "hashtags": []}, c)]
        if camps and (had["logo"] or had["overlay_text"] or had["hashtags"]) and not (camps[0]["logo"] or camps[0]["overlay_text"]):
            camps[0] = _campaign({**camps[0], **{k: v for k, v in had.items() if v}}, c)
        if camps and not c.get("campaign_use") and camps[0].get("ready", True):
            c["campaign_use"] = camps[0]["key"]
    for k in ("logo", "logo_pos", "logo_size", "overlay_text", "campaign_tags"):
        c.pop(k, None)
    if ctx.uid_or_none() is None:          # read outside anyone's session: the campaign list can't be reached
        return c
    if not c.get("template_v"):
        # Streamers from before templates: the caption style and layout they had become a template (a ready-made
        # one when it matches), so their clips look the same as before. Bounce with the blurred fill is the default.
        if not c.get("template") and (c.get("style"), c.get("layout")) not in (("bounce", "blur"), (None, None)):
            c["template"] = templates.for_legacy(c.get("style"), c.get("layout"))
        c["template_v"] = 1
    keys = [k for k in (c.get("campaign_keys") or []) if isinstance(k, str)]
    if camps and c.get("id"):
        # Campaigns used to be kept on each creator. They move to the account's own list (once; their keys stay, so
        # videos made for them still find them) and the creator keeps which ones it uses.
        _lib_import(camps, c["id"])
        keys += [e["key"] for e in camps if e["key"] not in keys]
    lib = {e["key"]: e for e in lib_all()}
    c["campaign_keys"] = [k for k in keys if k in lib][:MAX_CAMPAIGNS]
    c["campaigns"], c["campaigns_v"] = [], 3
    if c.get("campaign_use") not in lib or not lib[c["campaign_use"]].get("ready", True):
        c["campaign_use"] = ""             # only a campaign that has been set up can be the one in use
    if c["campaign_use"] and c["campaign_use"] not in c["campaign_keys"]:
        c["campaign_keys"].insert(0, c["campaign_use"])
    main = lib.get(c["campaign_use"])
    if main:
        c["campaign_name"], c["campaign_url"], c["rate_per_1k"] = main["name"], main["url"], main["rate"]
    else:
        c["campaign_name"] = c["campaign_url"] = ""
    return c


MAX_CAMPAIGNS = 10
COLORS = 6           # how many campaign colours the app has (web/index.html, .cc0 to .cc5)
_OWN_PIC = r"/api/discover/img\?u=[\w%.\-]{1,400}&w=\d{2,4}"      # campaign art is only ever ClipHeist's own picture address


def brief_tags(text: str) -> list:
    """The hashtags a brief asks for, in its own order ('#1' and the '#39' of a stray '&#39;' are not hashtags)."""
    return [t for t in tag_list(" ".join(re.findall(r"(?<![\w&/])#([A-Za-z0-9_]{2,30})", str(text or "")))) if not t.isdigit()]


RULE_NUM = {"min_len": (5, 120), "max_len": (10, 180), "max_clips": (1, 20), "live_per_hour": (1, 60), "min_score": (0, 100)}
RULE_KEYS = tuple(RULE_NUM) + ("style", "layout", "focus")


def _rules(r, base: dict) -> dict:
    """A campaign's result rules, tidied. Anything missing or broken comes from `base` (the creator's settings)."""
    from . import render
    r = r if isinstance(r, dict) else {}
    out = {}
    for k, (lo, hi) in RULE_NUM.items():
        try:
            out[k] = max(lo, min(hi, int(float(r[k]))))
        except (KeyError, TypeError, ValueError):
            out[k] = max(lo, min(hi, int(base.get(k, DEFAULTS[k]))))
    out["style"] = r.get("style") if r.get("style") in render.STYLES else base.get("style") if base.get("style") in render.STYLES else DEFAULTS["style"]
    out["layout"] = r.get("layout") if r.get("layout") in render.LAYOUTS else base.get("layout") if base.get("layout") in render.LAYOUTS else DEFAULTS["layout"]
    out["focus"] = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f<>{}]+", " ", str(r.get("focus") or ""))).strip()[:300]
    if out["min_len"] >= out["max_len"]:
        out["min_len"] = max(5, out["max_len"] - 5)
    return out


def settings_for(c: dict, key="") -> dict:
    """The creator's settings with the result rules of one of its campaigns on top (as they are without one)."""
    e = campaign(c, key) if key else None
    out = {**c, **e["rules"]} if e else dict(c)
    out["template"], out["look"] = templates.pick(None, c)       # the streamer's template, worked out now (as them)
    out["layout"] = out["look"]["layout"]
    return out


def _campaign(e: dict, base: dict = None):
    """One campaign on a creator, tidied. None without a name. `base` (the creator) fills rules it doesn't have."""
    name = re.sub(r"\s+", " ", str(e.get("name") or "")).strip()[:120]
    if not name:
        return None
    key, cid, url, thumb, logo = (str(e.get(k) or "").strip() for k in ("key", "id", "url", "thumb", "logo"))
    try:
        rate = max(0.0, min(1000.0, float(e.get("rate") or 0)))
    except (TypeError, ValueError):
        rate = 0.0
    try:
        color = int(e.get("color")) if e.get("color") is not None else -1
    except (TypeError, ValueError):
        color = -1
    brief = re.sub(r"[\x00-\x09\x0b-\x1f\x7f]+", " ", str(e.get("brief") or "").replace("\r\n", "\n").replace("\r", "\n")).strip()[:4000]
    tags = e.get("hashtags", e.get("tags"))            # "tags" is what it was called for a day
    tags = brief_tags(brief) if tags is None else tag_list(" ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags))
    return {"key": key if re.fullmatch(r"[a-z0-9]{8}", key) else secrets.token_hex(4),
            "id": cid if re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", cid) else "",      # a listed campaign's id; "" when set up by hand
            "name": name, "url": url[:300] if re.fullmatch(r"https://\S{4,296}", url) else "", "rate": round(rate, 2),
            "brief": brief, "thumb": thumb if re.fullmatch(_OWN_PIC, thumb) else "", "hashtags": tags,
            "color": color if 0 <= color < COLORS else -1,          # -1: not given one yet (_fix does)
            # what this campaign puts on its clips; the logo is a file in the creator's folder, set only by an upload
            "logo": logo if re.fullmatch(r"logo[\w\-]{0,20}\.\w{3,4}", logo) and Path(logo).suffix.lower() in LOGO_TYPES else "",
            "logo_pos": e.get("logo_pos") if e.get("logo_pos") in LOGO_POS else "tl",
            "logo_size": e.get("logo_size") if e.get("logo_size") in ("s", "m", "l") else "m",
            "overlay_text": re.sub(r"[\x00-\x1f{}\\\\]+", " ", str(e.get("overlay_text") or "")).strip()[:60],
            # what its clips should be like: length, how many, how good, captions, layout, and what to look for
            "rules": _rules(e.get("rules"), base or DEFAULTS),
            "autopost": _autopost(e.get("autopost")),
            "ready": bool(e.get("ready", True))}


def _autopost(a) -> dict:
    """A campaign's own auto-post: which accounts its clips go to, the lowest score that goes, how far apart posts
    go out (0: each as soon as it's made) and at most how many a day on each account."""
    a = a if isinstance(a, dict) else {}

    def num(k, lo, hi, d):
        try:
            return max(lo, min(hi, int(float(a.get(k, d)))))
        except (TypeError, ValueError):
            return d
    accs = [str(x)[:40] for x in a.get("accounts", [])][:20] if isinstance(a.get("accounts"), list) else []
    return {"on": bool(a.get("on")) and bool(accs), "accounts": accs, "min_score": num("min_score", 0, 100, 70),
            "gap_min": num("gap_min", 0, 10080, 300), "cap": num("cap", 1, 50, 6)}


# ---------------------------------------------------------------- the account's campaigns ("My campaigns")
# One list per account, in creators/_campaigns/campaigns.json, with their logos beside it. Creators, videos and
# recordings point at a campaign by its key, so a campaign is set up once and used everywhere.
MAX_LIB = 40
_lib_mem: dict = {}


def _lib_dir() -> Path:
    return _root() / "_campaigns"


def lib_all() -> list:
    u = ctx.uid()
    got = _lib_mem.get(u)
    if got is None:
        try:
            raw = json.loads((_lib_dir() / "campaigns.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            raw = []
        got = [x for x in (_campaign(e) for e in raw if isinstance(e, dict)) if x]
        _lib_mem[u] = got
    return copy.deepcopy(got)


def _lib_write(lst: list):
    for i, e in enumerate(lst):                    # every campaign its own colour while there are colours left
        if e["color"] < 0:
            used = {x["color"] for x in lst}
            e["color"] = next((n for n in range(COLORS) if n not in used), i % COLORS)
    with _lock:
        d = _lib_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "campaigns.json.tmp"
        tmp.write_text(json.dumps(lst, indent=1), encoding="utf-8")
        tmp.replace(d / "campaigns.json")
        _lib_mem[ctx.uid()] = copy.deepcopy(lst)
    ctx.bump()


def _lib_import(camps: list, cid: str):
    """Campaigns that sat on a creator join the account's list (by key; ones already there are left as they are)."""
    lst = lib_all()
    have = {e["key"] for e in lst}
    new = [e for e in camps if e["key"] not in have]
    if not new:
        return
    for e in new:
        if e.get("logo"):
            src, dst = root(cid) / e["logo"], _lib_dir() / e["logo"]
            try:
                if src.is_file() and not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dst)
            except OSError:
                pass
    _lib_write(lst + new)


def campaign(c: dict, key):
    """The account's campaign with this key, or None (`c`, the creator, is no longer needed: kept for callers)."""
    return next((e for e in lib_all() if key and e["key"] == key), None)


def lib_add(entry: dict, base: dict = None):
    """A campaign joins My campaigns, waiting to be set up. A listed campaign already there is brought up to date
    instead (its look is kept). Returns (the campaign, hashtags it brought)."""
    lst = lib_all()
    e = _campaign({**{k: v for k, v in entry.items() if k in ("id", "name", "url", "rate", "brief", "thumb")}, "ready": False}, base)
    if not e:
        raise ValueError("Give the campaign a name.")
    old = next((x for x in lst if e["id"] and x["id"] == e["id"]), None)
    if old:
        new = [t for t in e["hashtags"] if t.lower() not in [x.lower() for x in old["hashtags"]]]
        e = _campaign({**old, **{k: e[k] for k in ("name", "url", "rate", "brief", "thumb")}, "hashtags": (old["hashtags"] + new)[:12]})
        lst[lst.index(old)] = e
    elif len(lst) >= MAX_LIB:
        raise ValueError(f"You can have up to {MAX_LIB} campaigns. Remove one first.")
    else:
        new = e["hashtags"]
        lst.append(e)
    _lib_write(lst)
    return campaign(None, e["key"]), new


def lib_edit(key: str, data: dict):
    """Change a campaign: its name, link, pay, brief, hashtags, small text, where its logo goes, its colour, whether
    it is set up, and any of its result rules. Returns (the campaign, hashtags added from a changed brief)."""
    lst = lib_all()
    old = next((e for e in lst if e["key"] == key), None)
    if not old:
        raise KeyError(key)
    new = {k: data[k] for k in ("name", "url", "rate", "brief", "hashtags", "overlay_text", "logo_pos", "logo_size", "color", "ready") if data.get(k) is not None}
    if isinstance(data.get("autopost"), dict):
        new["autopost"] = {**old.get("autopost", {}), **data["autopost"]}
    if isinstance(data.get("rules"), dict):
        new["rules"] = _rules({**old["rules"], **{k: v for k, v in data["rules"].items() if k in RULE_KEYS}}, old["rules"])
    added = []
    if "brief" in new and "hashtags" not in new:
        have = [t.lower() for t in old["hashtags"]]
        added = [t for t in brief_tags(new["brief"]) if t.lower() not in have][:max(0, 12 - len(have))]
        new["hashtags"] = old["hashtags"] + added
    e = _campaign({**old, **new})
    if not e:
        raise ValueError("Give the campaign a name.")
    if e["color"] < 0:
        e["color"] = old["color"]
    lst[lst.index(old)] = e
    _lib_write(lst)
    if e["ready"] and not old.get("ready", True):      # set up just now: streamers that picked it and use none start using it
        for c in all_creators():
            if key in c.get("campaign_keys", []) and not c.get("campaign_use"):
                c["campaign_use"] = key
                _write(_fix(c))
    return e, added


def lib_drop(key: str):
    """Remove a campaign from My campaigns, from every streamer that used it, with its logo."""
    lst = lib_all()
    old = next((e for e in lst if e["key"] == key), None)
    if not old:
        raise KeyError(key)
    lst.remove(old)
    _lib_write(lst)
    if old["logo"] and not any(e["logo"] == old["logo"] for e in lst):
        (_lib_dir() / old["logo"]).unlink(missing_ok=True)
    for c in all_creators():
        if key in c.get("campaign_keys", []) or c.get("campaign_use") == key:
            c["campaign_keys"] = [k for k in c["campaign_keys"] if k != key]
            if c.get("campaign_use") == key:
                c["campaign_use"] = ""
            _write(_fix(c))
    return old


def lib_logo_dir() -> Path:
    d = _lib_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def lib_set_logo(key: str, filename: str) -> dict:
    """Record a campaign's uploaded logo file ('' removes it). The file itself is beside the campaign list."""
    lst = lib_all()
    e = next((x for x in lst if x["key"] == key), None)
    if not e:
        raise KeyError(key)
    if e["logo"] and e["logo"] != filename and not any(x["logo"] == e["logo"] for x in lst if x is not e):
        (_lib_dir() / e["logo"]).unlink(missing_ok=True)
    e["logo"] = filename
    _lib_write(lst)
    return campaign(None, key)


# ---- a streamer and its campaigns: which ones it uses, and the one new clips are for
def add_campaign(cid, entry: dict):
    """Add a campaign to My campaigns (or find it there) and let this streamer use it.
    Returns (creator, the campaign, hashtags it brought)."""
    c = get(cid)
    if not c:
        raise KeyError(cid)
    if len(c["campaign_keys"]) >= MAX_CAMPAIGNS:
        raise ValueError(f"A streamer can use up to {MAX_CAMPAIGNS} campaigns. Remove one first.")
    e, new = lib_add(entry, c)          # a campaign added from a streamer starts with that streamer's clip settings
    if e["key"] not in c["campaign_keys"]:
        c["campaign_keys"].append(e["key"])
    if not c.get("campaign_use") and e.get("ready"):
        c["campaign_use"] = e["key"]
    _write(_fix(c))
    return get(cid), e, new


def edit_campaign(cid, key: str, data: dict):
    e, added = lib_edit(key, data)
    return (get(cid) if cid else None), e, added


def drop_campaign(cid, key: str):
    """This streamer stops using a campaign (it stays in My campaigns). Returns (creator, the campaign)."""
    c = get(cid)
    e = campaign(c, key)
    if not c or not e:
        raise KeyError(key)
    c["campaign_keys"] = [k for k in c["campaign_keys"] if k != key]
    if c.get("campaign_use") == key:
        c["campaign_use"] = ""
    _write(_fix(c))
    return c, e


NUMERIC = {"min_len": (5, 120), "max_len": (10, 180), "max_clips": (1, 20), "min_score": (0, 100), "buffer_min": (5, 120), "live_per_hour": (1, 60), "ai_every_min": (1, 30), "auto_jump_pct": (20, 300), "must_clip_min": (0, 240), "min_gap_min": (0, 60),
           "post_min_score": (0, 100), "post_gap_min": (5, 1440), "daily_cap": (1, 50)}


def root(cid) -> Path:
    if not re.fullmatch(r"[\w\-]{1,60}", cid or ""):
        raise ValueError("bad creator id")
    return _root() / cid


def _clean(data: dict) -> dict:
    out = {}
    for k in DEFAULTS:
        if k == "captions":
            if k in data:
                if not isinstance(data[k], bool):
                    raise ValueError("Captions must be on or off.")
                out[k] = data[k]
            continue
        if k not in data or (data[k] is None and k != "cam"):     # a cam can be cleared
            continue
        v = data[k]
        if k in NUMERIC:
            lo, hi = NUMERIC[k]
            v = max(lo, min(hi, int(float(v))))
        elif k in ("auto_watch", "autopost", "ai_only_on_spike"):
            v = bool(v)
        elif k == "channels":
            v = {p: channel_link(str(u).strip()[:300]) for p, u in (v.items() if isinstance(v, dict) else []) if p in PLATFORMS and str(u).strip()}
        elif k == "channel":
            v = channel_link(str(v).strip()[:300])
        elif k == "accounts":
            v = [str(x) for x in v][:20] if isinstance(v, list) else []
        elif k == "hook_text":
            v = clean_hook(v)
        elif k == "description_text":
            v = clean_description(v)
        elif k == "rate_per_1k":
            v = max(0.0, min(1000.0, float(v or 0)))
        elif k == "decide_mode":
            v = v if v in ("both", "either") else "both"
        elif k == "privacy":
            v = v if v in ("public", "unlisted", "private") else "public"
        elif k == "lang":
            from . import fetch_model
            v = v if fetch_model.known(v) else "en"
        elif k in ("campaigns", "campaign_name", "campaign_url"):
            continue                    # only add_campaign and friends change the campaigns
        elif k == "campaign_use":
            v = str(v)[:8]              # checked against the account's campaigns in _fix
        elif k == "campaign_keys":
            v = [str(x)[:8] for x in v][:MAX_CAMPAIGNS] if isinstance(v, list) else []
        elif k == "cam":
            from . import facecam
            v = facecam.clean(v)
        elif k in ("cam_auto", "cam_none"):
            v = bool(v)
        elif k == "cap_lang":
            from . import brain
            v = v if v in brain.CAP_LANGS else ""
        elif k == "quality":
            v = 1080 if str(v) == "1080" else 720
        elif k == "template":
            v = str(v)[:12]
            if v and not templates.get(v):
                raise ValueError("That style isn't there any more. Pick another one.")
        else:
            v = str(v).strip()[:300]
        out[k] = v
    return out


_mem: dict = {}   # user -> {creator id: creator}. The files stay the truth; this saves re-reading them on every page check.


def _mine() -> dict:
    u = ctx.uid()
    got = _mem.get(u)
    if got is None:
        got = {}
        for f in sorted(_root().glob("*/creator.json")):
            try:
                c = _fix({**DEFAULTS, **json.loads(f.read_text(encoding="utf-8"))})
                got[c.get("id") or f.parent.name] = c
            except Exception:  # noqa: BLE001
                pass
        with _lock:
            got = _mem.setdefault(u, got)
    return got


def forget(uid: str):
    """Files were changed from outside this module (old data moved in): read them again next time."""
    _mem.pop(uid, None)
    ctx.bump(uid)


def all_creators() -> list:
    return [copy.deepcopy(c) for c in list(_mine().values())]


def get(cid):
    if not cid or not re.fullmatch(r"[\w\-]{1,60}", str(cid)):
        return None
    c = _mine().get(str(cid))
    return copy.deepcopy(c) if c else None


def _write(c):
    with _lock:
        d = root(c["id"])
        for sub in ("videos", "live"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        tmp = d / "creator.json.tmp"
        tmp.write_text(json.dumps(c, indent=1), encoding="utf-8")
        tmp.replace(d / "creator.json")
        _mine()[c["id"]] = copy.deepcopy(c)
    ctx.bump()


def create(data: dict) -> dict:
    c = _fix({**DEFAULTS, "must_clip_v": 1, **_clean(data)})     # new: whatever was picked, Off included, stands
    if not c["name"]:
        raise ValueError("Give the creator a name.")
    slug = re.sub(r"[^a-z0-9]+", "-", c["name"].lower()).strip("-")[:40] or "creator"
    cid, n = slug, 2
    while root(cid).exists():
        cid, n = f"{slug}-{n}", n + 1
    c.update(id=cid, created=time.time())
    _write(c)
    log(cid, f"Workspace created for {c['name']}")
    return c


def update(cid, data: dict) -> dict:
    c = get(cid)
    if not c:
        raise KeyError(cid)
    new = _clean(data)
    if "channels" in new and "channel" not in new and c["channel"] not in new["channels"].values():
        c["channel"] = ""  # the recording channel was removed: fall back to another one
    if "channel" in new and new["channel"] and "channels" not in new:
        new["channels"] = {**c["channels"], platform_key(new["channel"]): new["channel"]}
    c.update(new)
    _fix(c)
    if c["min_len"] >= c["max_len"]:
        raise ValueError("Shortest clip must be shorter than longest clip.")
    _write(c)
    return c


def tag_list(text: str) -> list:
    """'#TheWeeknd, fayewong #ad' -> ['TheWeeknd', 'fayewong', 'ad']: at most 12, no doubles."""
    out = []
    for t in re.split(r"[\s,]+", text or ""):
        t = re.sub(r"\W", "", t)[:30]
        if t and t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out[:12]


def with_tags(c: dict, tags, key="") -> list:
    """A clip's hashtags with its campaign's first (they are the ones the campaign checks for)."""
    mine = (campaign(c, key) or {}).get("hashtags", [])
    rest = [t for t in (tags or []) if str(t).lower() not in [m.lower() for m in mine]]
    return (mine + rest)[:12]


def logo_path(c: dict, key):
    e = campaign(c, key)
    if not e or not e["logo"]:
        return None
    f = _lib_dir() / e["logo"]
    return f if f.is_file() else None


def clip_extras(c: dict, key="") -> dict:
    """What a clip's campaign puts on the video, for the renderer: its logo and its small text."""
    e = campaign(c, key)
    out = {}
    f = logo_path(c, key)
    if f:
        out["logo"] = {"path": str(f), "pos": e["logo_pos"], "size": e["logo_size"]}
    if e and e["overlay_text"]:
        out["note"] = e["overlay_text"]
    return out


def set_logo(cid, key: str, filename: str) -> dict:
    lib_set_logo(key, filename)
    return get(cid) if cid else None


def set_profiles(cid, profiles: dict, extra: dict = None):
    """Each channel's public profile and live state, written by profiles.refresh. `profile` is the recording
    channel's one, used for the creator's picture."""
    with _lock:
        c = get(cid)
        if not c:
            return
        before = json.dumps([c.get("profiles"), {k: c.get(k) for k in (extra or {})}], sort_keys=True, default=str)
        c["profiles"] = {k: v for k, v in profiles.items() if k in c["channels"]}
        main = platform_key(c["channel"]) if c["channel"] else ""
        c["profile"] = c["profiles"].get(main) or next(iter(c["profiles"].values()), {})
        c.update(extra or {})
        same = re.sub(r'"live_at": [\d.]+', "", before) == re.sub(
            r'"live_at": [\d.]+', "", json.dumps([c["profiles"], {k: c.get(k) for k in (extra or {})}], sort_keys=True, default=str))
        if same:       # only "checked just now" moved on: remember it, but there is nothing new to save or show
            _mine()[c["id"]] = copy.deepcopy(c)
            return
        _write(c)


def log(cid, msg: str):
    if not cid or ctx.uid_or_none() is None or not root(cid).exists():
        return
    path = root(cid) / "log.jsonl"
    with _lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "msg": msg}) + "\n")
        try:
            if path.stat().st_size > LOG_MAX_BYTES:         # a stream that runs for weeks must not grow this for ever
                keep = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-LOG_KEEP_LINES:]
                path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except OSError:
            pass
    ctx.bump()


LOG_MAX_BYTES, LOG_KEEP_LINES, LOG_TAIL_BYTES = 1_000_000, 500, 64 * 1024


def read_log(cid, n=40) -> list:
    """The newest n lines, newest first. Only the end of the file is read: the page asks for this all the time."""
    f = root(cid) / "log.jsonl"
    try:
        size = f.stat().st_size
        with f.open("rb") as h:
            h.seek(max(0, size - LOG_TAIL_BYTES))
            raw = h.read()
    except OSError:
        return []
    lines = raw.decode("utf-8", errors="ignore").splitlines()
    if size > LOG_TAIL_BYTES:
        lines = lines[1:]                                   # the first one starts in the middle of a line
    out = []
    for x in reversed(lines[-n:]):
        try:
            out.append(json.loads(x))
        except ValueError:
            pass
    return out
