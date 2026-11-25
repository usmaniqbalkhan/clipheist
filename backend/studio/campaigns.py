"""Clipping campaigns people can earn from, shown on the Campaigns page.

Read from Content Rewards' public "discover" page (no account, nothing paid): the campaign list is part of that
page's own data. It is asked for at most every 30 minutes for everyone together, and an older list is shown while a
new one is fetched. If their page changes shape or can't be reached, the page here says so and nothing else breaks.
Joining a campaign happens on their site; here a campaign can be set on a creator so clips come out ready for it.

A campaign's own page adds its full description and how much brief there is (how many rules, requirements and
reference files). The rules and files themselves are only shown to people who joined, so they are not read here;
the creator's Campaign tab has room to paste them."""
import json
import re

from . import creators, discover, profiles

SOURCE = "Content Rewards"
URL = "https://contentrewards.com/c/discover/clipping"
PAGE = "https://contentrewards.com/c/campaigns/"
KEEP_FOR = 1800
PLATFORMS = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube", "x": "X", "facebook": "Facebook", "snapchat": "Snapchat"}
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CTRL = re.compile(r"[\x00-\x1f\x7f]+")


def _payload(html: str) -> str:
    """The page's data: it arrives as quoted pieces pushed one after another; joined, the lists inside are plain JSON."""
    out = []
    for piece in re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', html):
        try:
            out.append(json.loads('"' + piece + '"'))
        except ValueError:
            continue
    return "".join(out)


def _money(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return round(f, 2) if 0 <= f < 1e9 else 0.0


def _when(v) -> float:
    """When it started, in seconds (their page gives milliseconds)."""
    try:
        f = float(v) / 1000
    except (TypeError, ValueError):
        return 0.0
    return f if 0 < f < 1e11 else 0.0


def _text(v, limit) -> str:
    return _CTRL.sub(" ", str(v or "")).replace("$$", "$").strip()[:limit]      # their data doubles every dollar sign


def _long(v, limit) -> str:
    """Several lines of their text, kept as lines."""
    s = re.sub(r"[\x00-\x09\x0b-\x1f\x7f]+", " ", str(v or "").replace("\r\n", "\n").replace("\r", "\n"))
    s = re.sub(r"\n{3,}", "\n\n", "\n".join(x.strip() for x in s.split("\n")))
    return (s[1:] if s.startswith("$$") else s).strip()[:limit]


def _row(c: dict):
    cid = str(c.get("id") or "")
    title = _text(c.get("title"), 120)
    if not _UUID.fullmatch(cid) or not title:
        return None
    plats = [p for p in (c.get("platforms") or []) if p in PLATFORMS][:6]
    return {"id": cid, "title": title, "brand": _text(c.get("brand"), 60), "category": _text(c.get("category"), 40),
            "about": _text(c.get("description"), 500), "rate": _money(c.get("payoutSortRaw")),
            "pool": c.get("type") != "cpm",                       # a shared prize pool instead of pay per view
            "budget": _money(c.get("budgetTotalRaw")), "spent": _money(c.get("budgetSpentRaw")),
            "left": _money(c.get("availableBudgetRaw")), "creators": int(_money(c.get("creatorCountRaw"))),
            "platforms": [PLATFORMS[p] for p in plats], "verified": bool(c.get("isVerified")),
            "apply": bool(c.get("requiresApplication")), "launched": _when(c.get("launchedAtMs")),
            "thumb": discover.pic(str(c.get("thumbnail") or ""), 640), "logo": discover.pic(str(c.get("avatar") or ""), 160),
            "url": PAGE + cid}


def parse(html: str) -> list:
    """Every campaign in the page, once each, in the page's own order (most popular first)."""
    text = _payload(html)
    dec, seen, out = json.JSONDecoder(), set(), []
    for m in re.finditer(r'"campaigns":\[', text):
        try:
            arr, _ = dec.raw_decode(text, m.end() - 1)
        except ValueError:
            continue
        for c in arr if isinstance(arr, list) else []:
            row = _row(c) if isinstance(c, dict) else None
            if row and row["id"] not in seen:
                seen.add(row["id"])
                out.append(row)
    return out


def _queries(text: str):
    """The answers the page was sent with, one per question it asked their server."""
    dec = json.JSONDecoder()
    for m in re.finditer(r'"queries":\[', text):
        try:
            arr, _ = dec.raw_decode(text, m.end() - 1)
        except ValueError:
            continue
        for q in arr if isinstance(arr, list) else []:
            if isinstance(q, dict) and isinstance(q.get("state"), dict):
                yield q.get("queryKey"), q["state"].get("data")


def _count(v) -> int:
    return int(v) if isinstance(v, int) and 0 <= v < 1000 else 0


def parse_detail(html: str, cid: str) -> dict:
    """One campaign from its own page: the whole description, pay on each platform, and how much brief it has."""
    d = {}
    for key, data in _queries(_payload(html)):
        if isinstance(key, list) and len(key) == 3 and key[0] == "campaigns" and key[2] == cid and isinstance(data, dict):
            d = {**d, **data} if key[1] == "detail" else {**data, **d}
    title = _text(d.get("name") or d.get("title"), 120)
    if not title:
        return {}
    conf = d.get("configuration") if isinstance(d.get("configuration"), dict) else {}
    pays = []
    for p in (conf.get("payoutModel") or d.get("payouts") or []):
        if isinstance(p, dict) and p.get("platform") in PLATFORMS:
            pays.append({"platform": PLATFORMS[p["platform"]], "rate": _money(p.get("rate", p.get("rateCents"))) / 100,
                         "min": _money(p.get("minPayout", p.get("minPayoutCents"))) / 100,
                         "max": _money(p.get("maxPayout", p.get("maxPayoutCents"))) / 100})
    n = d.get("briefCounts") if isinstance(d.get("briefCounts"), dict) else {}
    need = conf.get("requirement") if isinstance(conf.get("requirement"), dict) else {}
    return {"id": cid, "title": title, "brief": _long(d.get("description"), 4000), "payouts": pays[:6],
            "rules": _count(n.get("contentRules")), "requirements": _count(n.get("creatorRequirements")), "files": _count(n.get("references")),
            "approval": bool(need.get("preApproval")) or bool(d.get("requiresApplication")), "url": PAGE + cid, "source": SOURCE}


def _fetch_detail(cid: str) -> dict:
    import httpx
    r = httpx.get(PAGE + cid, headers={**profiles.UA, "Accept": "text/html"}, timeout=25, follow_redirects=True)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    return parse_detail(r.text, cid)


def detail(cid: str) -> dict:
    """LookupError when there is no such campaign (or the id isn't one)."""
    if not _UUID.fullmatch(cid or ""):
        raise LookupError(cid)
    d = discover._shared("campaign:" + cid, KEEP_FOR, lambda: _fetch_detail(cid), stale_ok=True)
    if not d:
        raise LookupError(cid)
    return d


def read_brief(text: str) -> dict:
    """The hashtags and @names a brief asks for, in the order it gives them. Names inside links are not asks."""
    text = str(text or "")
    tags = creators.brief_tags(text)
    names = []
    for h in re.findall(r"(?<![\w/.@])@([A-Za-z0-9_][A-Za-z0-9_.]{1,29})", text):
        h = h.rstrip(".")
        if len(h) > 1 and h.lower() not in [x.lower() for x in names]:
            names.append(h)
    return {"tags": tags[:12], "names": names[:5]}


def for_creator(cid: str) -> dict:
    """This listed campaign as it is put on a creator (creators.add_campaign): name, link, pay, brief and cover."""
    if not _UUID.fullmatch(cid or ""):
        raise LookupError(cid)
    try:
        row = next((r for r in listing() if r["id"] == cid), None)
    except Exception:  # noqa: BLE001 - the list is down: the campaign's own page may still answer
        row = None
    try:
        d = detail(cid)
    except LookupError:
        d = {}
    except Exception:  # noqa: BLE001
        if not row:
            raise
        d = {}
    if not row and not d:
        raise LookupError(cid)
    rate = row["rate"] if row and not row["pool"] else max([p["rate"] for p in d.get("payouts", [])] or [0])
    return {"id": cid, "name": (d or row)["title"], "url": PAGE + cid, "rate": rate,
            "brief": d.get("brief") or (row or {}).get("about", ""), "thumb": (row or {}).get("thumb", "")}


def brand_logo(cid: str):
    """(bytes, type) of the picture the campaign's brand shows on their page, or None."""
    from urllib.parse import parse_qs, urlparse
    row = next((r for r in listing() if r["id"] == cid), None)
    src = (parse_qs(urlparse((row or {}).get("logo", "")).query).get("u") or [""])[0]
    return discover.image(src, 640) if src else None


def _fetch() -> list:
    import httpx
    r = httpx.get(URL, headers={**profiles.UA, "Accept": "text/html"}, timeout=25, follow_redirects=True)
    r.raise_for_status()
    rows = parse(r.text)
    if not rows:
        raise ValueError("no campaigns found in the page")
    discover._warm([x["thumb"] for x in rows[:24]] + [x["logo"] for x in rows[:24]])
    return rows


def listing() -> list:
    return discover._shared("campaigns", KEEP_FOR, _fetch, stale_ok=True)
