"""Post queue: decides when each finished clip goes to which account, posts it, and tracks views."""
import secrets
import threading
import time
from pathlib import Path

import json

from . import accounts, alerts, config, creators, ctx, jobs, live

_lock = threading.RLock()
_FILE = config.DATA_DIR / "posts.json"
try:
    _posts: list = json.loads(_FILE.read_text(encoding="utf-8")) if _FILE.exists() else []
except ValueError:
    _posts = []


def _mine() -> list:
    u = ctx.uid()
    return [p for p in _posts if p.get("user") == u]
OPEN = ("scheduled", "posting")


def _save():
    with _lock:
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_posts, indent=1), encoding="utf-8")
        tmp.replace(_FILE)


def forget(uid: str):
    """Drop everything queued or recorded for a deleted account, so nothing is posted or retried for it any more."""
    with _lock:
        for p in _posts:
            if p.get("user") == uid:
                p["status"] = "deleted"          # the posting loop may be holding this one already: it then skips it
        _posts[:] = [p for p in _posts if p.get("user") != uid]
        _save()


def _day(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


def _mirror(p):
    """Copy the post's state onto its clip so the clip card can show it."""
    job = jobs.get(p["job"])
    clip = next((c for c in job["clips"] if c["id"] == p["clip"]), None) if job else None
    if clip is not None:
        clip.setdefault("posts", {})[p["id"]] = {k: p.get(k) for k in ("account", "account_name", "platform", "status", "url", "due_at", "error", "processing")}
        jobs.save(job)


def _next_slot(account_id, gap_min, cap):
    """Earliest time that respects the gap between posts and the daily cap for this account."""
    mine = [p for p in _mine() if p["account"] == account_id and p["status"] in OPEN + ("posted",)]
    # no gap: as soon as it's made, whatever else is queued; with a gap: that long after the account's last post
    t = time.time() if not gap_min else max(time.time(), max((p.get("posted_at") or p["due_at"] for p in mine), default=0) + gap_min * 60)
    for _ in range(30):
        if sum(1 for p in mine if _day(p.get("posted_at") or p["due_at"]) == _day(t)) < cap:
            break
        lt = time.localtime(t + 86400)
        t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 9, 0, 0, 0, 0, -1))  # 9:00 next day
    return t


MAX_AHEAD = 366 * 86400      # a post can be planned up to a year ahead


def _when(at) -> float:
    """A time picked by hand on the schedule: a Unix time, now or later (a minute's slack for a slow click)."""
    try:
        t = float(at)
    except (TypeError, ValueError):
        raise ValueError("Pick a date and time for this post.")
    if t != t or t < time.time() - 60:
        raise ValueError("That time has already passed. Pick a time from now on.")
    if t > time.time() + MAX_AHEAD:
        raise ValueError("Posts can be planned up to a year ahead.")
    return max(t, time.time())


def enqueue(job, clip, account_ids, now=False, at=None, day=None, gap_min=None, cap=None) -> list:
    c = creators.get(job["creator"]) or creators.DEFAULTS
    gap_min = c["post_gap_min"] if gap_min is None else gap_min    # a campaign's auto-post brings its own spacing
    cap = c["daily_cap"] if cap is None else cap
    at = None if now or at is None else _when(at)
    made = []
    with _lock:
        if day is not None and at is not None:  # added from a day on the calendar: that day's limit counts
            d0 = float(day)   # the limit is per platform: three a day means three on YouTube, three on Instagram, three on TikTok
            dcap = int(accounts.settings().get("day_limit", 3))
            want = {}
            for aid in account_ids:
                a = accounts.get(aid)
                if a:
                    want[a["platform"]] = want.get(a["platform"], 0) + 1
            for plat, n in want.items():
                used = sum(1 for p in _mine() if p["status"] in OPEN + ("posted",) and p.get("platform") == plat
                           and d0 <= (p.get("posted_at") or p["due_at"]) < d0 + 86400)
                if used + n > dcap:
                    name = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}.get(plat, plat)
                    raise ValueError(f"{name} already has {used} of {dcap} posts on this day. Pick another day for {name}.")
        for aid in account_ids:
            acc = accounts.get(aid)
            if not acc:
                continue
            if any(p["job"] == job["id"] and p["clip"] == clip["id"] and p["account"] == aid
                   and p["status"] in OPEN + ("posted",) for p in _mine()):
                continue  # never post the same clip to the same account twice
            due = time.time() if now else at if at is not None else _next_slot(aid, gap_min, cap)
            p = {"id": secrets.token_hex(8), "user": ctx.uid(), "job": job["id"], "clip": clip["id"], "creator": job["creator"],
                 "account": aid, "account_name": acc["name"], "platform": acc["platform"], "status": "scheduled",
                 "due_at": due, "created": time.time(), "title": clip.get("title") or clip.get("hook") or "Clip",
                 "score": clip.get("score", 0), "tries": 0, "url": "", "remote_id": "", "error": None, "views": None,
                 "now": bool(now)}     # "Post now" was pressed: it goes out even while posting is paused
            _posts.append(p)
            made.append(p)
        _save()
    for p in made:
        _mirror(p)
    return made


def on_clip_ready(job, clip):
    """Called once when a clip first finishes rendering. A clip made for a campaign with auto-post on goes by the
    campaign's settings (from a video, an upload or a recording alike); otherwise by its streamer's auto-post."""
    e = creators.campaign(None, job.get("campaign")) if job.get("campaign") else None
    ap = (e or {}).get("autopost") or {}
    if e and e.get("ready", True) and ap.get("on") and ap.get("accounts"):
        if clip.get("score", 0) < ap["min_score"]:
            return  # includes hand-made clips (score 0): those wait for you to press Post
        mine = {a["id"] for a in accounts.list_accounts()}
        made = enqueue(job, clip, [a for a in ap["accounts"] if a in mine], gap_min=ap["gap_min"], cap=ap["cap"])
        if made and job.get("creator"):
            creators.log(job["creator"], f"{e['name']}: queued for {len(made)} account(s): {clip.get('title') or clip.get('hook')}")
        return
    c = creators.get(job["creator"])
    if not c or not c["autopost"] or not c["accounts"]:
        return
    if clip.get("score", 0) < c["post_min_score"]:
        return  # includes hand-made clips (score 0): those wait for you to press Post
    made = enqueue(job, clip, c["accounts"])
    if made:
        creators.log(c["id"], f"Queued for {len(made)} account(s): {clip.get('title') or clip.get('hook')}")


def _caption(clip, creator):
    # A description the person wrote themselves is posted as it is, without the AI's hashtags added to it, and
    # one that already ends with its hashtags does not get them twice.
    tags = "" if clip.get("fixed_description") or clip.get("hashtags_in_description") else " ".join("#" + h for h in clip.get("hashtags", []))
    return "\n\n".join(x for x in (clip.get("description", ""), tags, creator.get("post_footer", "")) if x)


_yt_pause = [0.0]          # YouTube posts wait until this time (the app's daily allowance from Google is used up)


def _pacific_midnight() -> float:
    """The next midnight in California, when Google's daily API allowance resets."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/Los_Angeles"))
    return (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()


def _post(p):
    if p.get("platform") == "youtube" and time.time() < _yt_pause[0]:
        p.update(due_at=_yt_pause[0] + 60)
        _save()
        return
    job = jobs.get(p["job"])
    clip = next((c for c in job["clips"] if c["id"] == p["clip"]), None) if job else None
    acc = accounts.get(p["account"])
    try:
        if clip and clip.get("status") in ("queued", "rendering"):
            # the clip is being made again with new changes: post the new version once it's ready
            p.update(status="scheduled", due_at=time.time() + 30)
            _save()
            return
        if not clip or clip.get("status") != "done":
            raise RuntimeError("The clip no longer exists or is not finished.")
        if not acc:
            raise RuntimeError("That account was disconnected.")
        path = Path(job["dir"]) / "clips" / clip["file"]
        creator = creators.get(job["creator"]) or dict(creators.DEFAULTS, name="clips")
        title, cap = clip.get("title") or clip.get("hook") or "Clip", _caption(clip, creator)
        if acc["platform"] == "youtube":
            rid, url = accounts.youtube_upload(acc, path, title, cap + "\n\n#Shorts", clip.get("hashtags", []), creator["privacy"])
        elif acc["platform"] == "instagram":
            rid, url = accounts.instagram_upload(acc, path, f"{title}\n\n{cap}", job["id"])
        elif acc["platform"] == "tiktok":
            rid, url = accounts.tiktok_upload(acc, path, f"{title}\n\n{cap}", creator["privacy"])
        else:
            raise RuntimeError("That kind of account is no longer supported.")
        if acc["platform"] == "youtube":
            # the file is there, but YouTube still processes it (its link shows "processing" until then): the post
            # stays "posting" and becomes "posted" once YouTube says it's ready to watch (_processing below)
            p.update(status="posting", processing=True, uploaded_at=time.time(), remote_id=rid, url=url, error=None)
        else:
            p.update(status="posted", posted_at=time.time(), remote_id=rid, url=url, error=None)
            creators.log(p["creator"], f"Posted to {acc['name']}: {title}")
    except Exception as e:  # noqa: BLE001
        if "quotaExceeded" in str(e) or "dailyLimitExceeded" in str(e):
            # the app's own daily allowance from Google (every channel together) is used up: every YouTube post waits
            # until it resets (midnight Pacific time), then goes out in order. Nothing fails.
            _yt_pause[0] = _pacific_midnight()
            p.update(status="scheduled", due_at=_yt_pause[0] + 60,
                     error="YouTube's daily upload allowance is used up. It will post after it resets.")
            _save()
            _mirror(p)
            return
        if ("uploadLimitExceeded" in str(e) or "exceeded the number of videos" in str(e)) and p.get("waited", 0) < 3:
            # YouTube's own limit on how many videos this channel may upload in a day: trying again in 10 minutes
            # only fails again, so this clip waits for tomorrow (up to three days), and the card says why.
            p["waited"] = p.get("waited", 0) + 1
            lt = time.localtime(time.time() + 86400)
            p.update(status="scheduled", due_at=time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 9, 0, 0, 0, 0, -1)),
                     error="YouTube's daily upload limit for this channel was reached. It will post tomorrow.")
            _save()
            _mirror(p)
            return
        p["tries"] += 1
        p["error"] = str(e)[-300:]
        if p["tries"] < 3 and clip and acc:
            p.update(status="scheduled", due_at=time.time() + 600)  # try again in 10 minutes
        else:
            p["status"] = "failed"
            creators.log(p["creator"], f"Post failed on {p['account_name']}: {p['error']}")
            alerts.notify(f"post failed on {p['account_name']} ({p['title']}): {p['error'][:200]}", kind="post")
    _save()
    _mirror(p)


def _go(p):
    try:
        with ctx.as_user(p["user"]):
            _post(p)
    except Exception:  # noqa: BLE001
        pass


def _run():
    # Posts go out on time: the queue is looked at every 3 seconds, and each due post uploads on its own thread,
    # so nine posts planned for 6 pm all start at 6 pm instead of waiting behind each other's uploads.
    while True:
        time.sleep(3)
        due = [p for p in list(_posts) if p["status"] == "scheduled" and p["due_at"] <= time.time() and p.get("user")]
        for p in due:
            try:
                with ctx.as_user(p["user"]):
                    if accounts.settings()["paused"] and not p.get("now"):
                        continue        # paused stops the queue; a clip you pressed "Post now" on still goes
                    with _lock:
                        if p["status"] != "scheduled":
                            continue
                        p["status"] = "posting"
                threading.Thread(target=_go, args=(p,), daemon=True).start()
            except Exception:  # noqa: BLE001
                pass


PROCESSING_MAX = 3 * 3600      # YouTube is still busy after this long: counted as posted (the video is there)
REASONS = {"duplicate": "YouTube found it's the same as a video already on the channel.",
           "length": "YouTube says the video is too long.", "copyright": "YouTube rejected it for copyright.",
           "uploadAborted": "The upload stopped before it finished.", "tooSmall": "YouTube says the file is too small.",
           "codec": "YouTube couldn't read the video."}


def _processing():
    """Every 20 seconds: YouTube uploads still being processed by YouTube become posted once it's done, or failed
    (with YouTube's reason) if it turned them down."""
    while True:
        time.sleep(20)
        check_processing()


GONE_CHECK, _gone_at = 6 * 3600, [0.0]


def check_gone(days=14):
    """Every few hours: YouTube posts from the last two weeks that YouTube no longer has (deleted on the channel, or
    taken down) stop showing as posted, and say what happened."""
    recent = [p for p in list(_posts) if p.get("platform") == "youtube" and p["status"] == "posted" and p.get("remote_id")
              and p.get("user") and time.time() - float(p.get("posted_at") or 0) < days * 86400]
    changed = False
    for uid in {p["user"] for p in recent}:
        try:
            with ctx.as_user(uid):
                for aid in {p["account"] for p in recent if p["user"] == uid}:
                    acc = accounts.get(aid)
                    ps = [p for p in recent if p["user"] == uid and p["account"] == aid]
                    if not acc:
                        continue
                    states = accounts.youtube_states(acc, [p["remote_id"] for p in ps])
                    for p in ps:
                        state, why = states.get(p["remote_id"], ("processed", ""))
                        if state in ("deleted", "rejected"):
                            p.update(status="failed", removed=True, error=REASONS.get(why) or "This video is no longer on YouTube: it was deleted on the channel or taken down by YouTube.")
                            _mirror(p)
                            changed = True
        except Exception:  # noqa: BLE001 - looked at again next time
            pass
    if changed:
        _save()


def check_processing():
    if time.time() - _gone_at[0] > GONE_CHECK:
        _gone_at[0] = time.time()
        try:
            check_gone()
        except Exception:  # noqa: BLE001
            pass
    """YouTube uploads still being processed: posted once YouTube is done, failed if it turned them down."""
    waiting = [p for p in list(_posts) if p["status"] == "posting" and p.get("processing") and p.get("user")]
    for uid in {p["user"] for p in waiting}:
        try:
            with ctx.as_user(uid):
                mine = [p for p in waiting if p["user"] == uid]
                for aid in {p["account"] for p in mine}:
                    acc = accounts.get(aid)
                    ps = [p for p in mine if p["account"] == aid]
                    if not acc:
                        for p in ps:
                            p.update(status="failed", processing=False, error="That account was disconnected.")
                            _mirror(p)
                        continue
                    states = accounts.youtube_states(acc, [p["remote_id"] for p in ps])
                    for p in ps:
                        state, why = states.get(p["remote_id"], ("uploaded", ""))
                        late = time.time() - p.get("uploaded_at", time.time()) > PROCESSING_MAX
                        if state == "processed" or (state == "uploaded" and late):
                            p.update(status="posted", processing=False, posted_at=time.time(), error=None)
                            creators.log(p["creator"], f"Posted to {p['account_name']}: {p.get('title', '')}")
                        elif state in ("failed", "rejected", "deleted"):
                            msg = REASONS.get(why) or ("The video was deleted on YouTube." if state == "deleted" else f"YouTube couldn't finish the video ({why or state}).")
                            p.update(status="failed", processing=False, error=msg)
                            creators.log(p["creator"], f"Post failed on {p['account_name']}: {msg}")
                            alerts.notify(f"post failed on {p['account_name']} ({p.get('title', '')}): {msg}", kind="post")
                        else:
                            continue
                        _mirror(p)
        except Exception:  # noqa: BLE001 - asked again in 20 seconds
            pass
    if waiting:
        _save()


def _stats_for(recent):
    for aid in {p["account"] for p in recent if p["platform"] == "youtube"}:
        acc = accounts.get(aid)
        if acc:
            views = accounts.youtube_views(acc, [p["remote_id"] for p in recent if p["account"] == aid])
            for p in recent:
                if p["account"] == aid and p["remote_id"] in views:
                    p["views"] = views[p["remote_id"]]
    for p in recent:
        acc = accounts.get(p["account"]) if p["platform"] == "instagram" else None
        if acc:
            v = accounts.instagram_views(acc, p["remote_id"])
            if v is not None:
                p["views"] = v


def _stats():
    while True:
        for uid in {p.get("user") for p in _posts if p.get("user")}:
            try:
                with ctx.as_user(uid):
                    _stats_for([p for p in _mine() if p["status"] == "posted" and time.time() - p["posted_at"] < 14 * 86400])
            except Exception:  # noqa: BLE001
                pass
        _save()
        time.sleep(1800)


def cancel(pid):
    with _lock:
        p = next((p for p in _mine() if p["id"] == pid), None)
        if not p or p["status"] != "scheduled":
            raise ValueError("Only posts that are still waiting can be cancelled.")
        p["status"] = "cancelled"
        _save()
    _mirror(p)


def retry(pid):
    with _lock:
        p = next((p for p in _mine() if p["id"] == pid), None)
        if not p or p["status"] not in ("failed", "cancelled"):
            raise ValueError("Only failed or cancelled posts can be retried.")
        p.update(status="scheduled", due_at=time.time(), tries=0, error=None)
        _save()
    _mirror(p)


def move(pid, at):
    """Give a waiting post a new time, picked on the schedule. The gap and daily cap do not apply: the time was chosen."""
    t = _when(at)
    with _lock:
        p = next((p for p in _mine() if p["id"] == pid), None)
        if not p or p["status"] != "scheduled":
            raise ValueError("Only posts that are still waiting can be moved.")
        p.update(due_at=t, waited=0)
        _save()
    _mirror(p)
    return _row(p, {})


def post_now(pid):
    """Send a waiting post straight away, even while posting is paused (the same as pressing Post now on a clip)."""
    with _lock:
        p = next((p for p in _mine() if p["id"] == pid), None)
        if not p or p["status"] != "scheduled":
            raise ValueError("Only posts that are still waiting can be posted now.")
        p.update(due_at=time.time(), now=True)
        _save()
    _mirror(p)


def waiting() -> int:
    """How many of the signed-in user's posts are still to go out: the count beside Schedule in the sidebar."""
    return sum(1 for p in _mine() if p["status"] in OPEN)


def posted() -> int:
    """How many of the signed-in user's posts have gone out: the sidebar turns its dot green when this grows."""
    return sum(1 for p in _mine() if p["status"] == "posted")


def _row(p, names) -> dict:
    """One post as the schedule shows it: never the owner's id, plus the clip's cover picture when it still exists."""
    r = {k: v for k, v in p.items() if k not in ("user", "remote_id", "tries", "waited")}
    r["creator_name"] = names.get(p.get("creator"), "")
    r["at"] = p.get("posted_at") if p["status"] == "posted" and p.get("posted_at") else p["due_at"]
    job = jobs.get(p["job"])
    clip = next((c for c in job["clips"] if c["id"] == p["clip"]), None) if job else None
    r["cover"] = f"/media/{job['id']}/{clip['cover']}?v={clip.get('version', 0)}" if clip and clip.get("cover") else ""
    r["clip_ok"] = bool(clip and clip.get("status") == "done")
    return r


def schedule(start, end) -> dict:
    """Every post of the signed-in user whose time falls in [start, end): waiting ones by when they go out, posted
    ones by when they went out. Cancelled ones are included so the page can show them when asked."""
    try:
        start, end = float(start), float(end)
    except (TypeError, ValueError):
        raise ValueError("Pick the days to show.")
    if not (start == start and end == end) or end <= start or end - start > 62 * 86400:
        raise ValueError("Show at most two months at a time.")
    names = {c["id"]: c["name"] for c in creators.all_creators()}
    rows = []
    for p in _mine():
        t = p.get("posted_at") if p["status"] == "posted" and p.get("posted_at") else p["due_at"]
        if start <= t < end:
            rows.append(_row(p, names))
    rows.sort(key=lambda r: r["at"])
    return {"posts": rows, "paused": accounts.settings()["paused"],
            "accounts": [{"id": a["id"], "platform": a["platform"], "name": a["name"]} for a in accounts.list_accounts()],
            "waiting": waiting(), "day_limit": int(accounts.settings().get("day_limit", 3))}


def overview() -> dict:
    today = _day(time.time())
    _posts = _mine()  # everything below is about the signed-in user only
    rows = []
    all_cs, all_js = creators.all_creators(), jobs.list_jobs()
    for c in all_cs:
        js = [j for j in all_js if j["creator"] == c["id"]]
        clips = [k for j in js for k in j["clips"] if k.get("status") == "done"]
        ps = [p for p in _posts if p["creator"] == c["id"]]
        views = sum(p["views"] or 0 for p in ps)
        rows.append({
            "id": c["id"], "name": c["name"],
            "state": "recording" if live.active_for(c["id"]) else "waiting" if c["auto_watch"] and c["channel"] else "idle",
            "autopost": c["autopost"] and bool(c["accounts"]),
            "clips_today": sum(1 for k in clips if _day(k.get("made_at", 0)) == today), "clips_total": len(clips),
            "posted_today": sum(1 for p in ps if p["status"] == "posted" and _day(p["posted_at"]) == today),
            "posted_total": sum(1 for p in ps if p["status"] == "posted"),
            "queued": sum(1 for p in ps if p["status"] in OPEN), "failed": sum(1 for p in ps if p["status"] == "failed"),
            "views": views, "earnings": round(views / 1000 * c["rate_per_1k"], 2)})
    names = {c["id"]: c["name"] for c in all_cs}
    queue = sorted((p for p in _posts if p["status"] in OPEN), key=lambda p: p["due_at"])
    done = sorted((p for p in _posts if p["status"] in ("posted", "failed")), key=lambda p: -(p.get("posted_at") or p["due_at"]))[:15]
    show = [{**{k: v for k, v in p.items() if k != "user"}, "creator_name": names.get(p["creator"], "")} for p in queue + done]
    # last 14 days, oldest first, for the home graph
    days = [_day(time.time() - 86400 * i) for i in range(13, -1, -1)]
    made, posted, views = {d: 0 for d in days}, {d: 0 for d in days}, {d: 0 for d in days}
    for j in all_js:
        for k in j["clips"]:
            d = _day(k.get("made_at") or j["created"])
            if k.get("status") == "done" and d in made:
                made[d] += 1
    for p in _posts:
        if p["status"] == "posted" and _day(p["posted_at"]) in posted:
            posted[_day(p["posted_at"])] += 1
            views[_day(p["posted_at"])] += p["views"] or 0
    daily = [{"day": d, "clips": made[d], "posted": posted[d], "views": views[d]} for d in days]
    return {"paused": accounts.settings()["paused"], "creators": rows, "posts": show, "daily": daily,
            "alerts": alerts.configured(), "recording": len(live.active_mine())}


def digest_text() -> str:
    o = overview()
    lines = ["ClipHeist daily summary"]
    for r in o["creators"]:
        lines.append(f"- {r['name']}: {r['state']}, {r['clips_today']} clips today, {r['posted_today']} posted today, "
                     f"{r['queued']} waiting, {r['failed']} failed, {r['views']} views")
    if not o["creators"]:
        lines.append("No creators set up yet.")
    if o["paused"]:
        lines.append("Posting is paused.")
    return "\n".join(lines)


def start():
    with _lock:
        for p in _posts:
            if p["status"] == "posting" and not p.get("processing"):  # interrupted mid-upload last time
                p.update(status="failed", error="The studio closed during this upload. Check the account, then retry.")
        _save()
    jobs.on_clip_ready.append(on_clip_ready)
    threading.Thread(target=_run, daemon=True).start()
    threading.Thread(target=_stats, daemon=True).start()
    threading.Thread(target=_processing, daemon=True).start()
    threading.Thread(target=alerts.digest_loop, args=(digest_text,), daemon=True).start()
    threading.Thread(target=alerts.keep_awake_loop,
                     args=(lambda: bool(live.active()) or any(p["status"] in OPEN for p in _posts),), daemon=True).start()
