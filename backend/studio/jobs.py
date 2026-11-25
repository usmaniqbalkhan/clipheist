"""Job pipeline: get video -> audio -> transcript -> AI picks -> rendered clips. One heavy task at a time."""
import collections
import json
import os
import math
import queue
import re
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import ai, brain, config, creators, ctx, facecam, fetch_model, games, hashtags, load, media, quality, remote_render, render, roman, safe, templates, transcribe, usage

class _FairQueue:
    """Whose video is next. Each person has their own line and the lines take turns, so someone who adds ten
    videos does not make everyone else wait behind all ten. Small tasks (re-making one clip) go first."""

    def __init__(self):
        self.cv = threading.Condition()
        self.quick = collections.deque()
        self.lines = {}                       # user -> their waiting videos, oldest first
        self.turn = collections.deque()       # users with something waiting, in the order they will be served
        self.epoch = 0                        # goes up whenever the line moves
        self._pos = (0.0, {})
        self.running = 0                      # videos being worked on right now
        self.last_start = 0.0
        self.held = False                     # videos are waiting only because the machine is near full

    def _room(self) -> bool:
        """May one more video start? One always may. More only while the machine has room, and a few seconds
        after the last one started, so its weight is already showing in the measurements."""
        if self.running == 0:
            return True
        return self.running < config.JOB_WORKERS and time.time() - self.last_start > 6 and not load.busy()

    def done(self, jid=None):
        with self.cv:
            self.running = max(0, self.running - 1)
            self.cv.notify_all()

    def put(self, item, user=None):
        with self.cv:
            if user is None:
                self.quick.append(item)
            else:
                if user not in self.lines:
                    self.lines[user] = collections.deque()
                    self.turn.append(user)
                self.lines[user].append(item)
            self.epoch += 1
            self.cv.notify()

    def get(self, quick_only=False):
        with self.cv:
            while True:
                if self.quick:
                    self.epoch += 1
                    return self.quick.popleft()
                if not quick_only and self.turn:
                    if self._room():
                        break
                    if not self.held:
                        self.held = True
                        self.epoch += 1
                elif self.held:
                    self.held = False
                    self.epoch += 1
                self.cv.wait(1.0)             # looked at again every second: the line moves the moment there is room
            self.epoch += 1
            self.held = False
            self.running += 1
            self.last_start = time.time()
            user = self.turn.popleft()
            item = self.lines[user].popleft()
            if self.lines[user]:
                self.turn.append(user)
            else:
                del self.lines[user]
            return item

    def ahead(self) -> dict:
        """job id -> how many videos will start before it. Worked out at most once a second."""
        with self.cv:
            if time.time() - self._pos[0] < 1:
                return self._pos[1]
            out, lines, order, n = {}, {u: list(q) for u, q in self.lines.items()}, list(self.turn), 0
            depth = 0
            while order:
                nxt = []
                for u in order:
                    if depth < len(lines[u]):
                        out[lines[u][depth][1]] = n
                        n += 1
                        if depth + 1 < len(lines[u]):
                            nxt.append(u)
                order, depth = nxt, depth + 1
            self._pos = (time.time(), out)
            return out


_q = _FairQueue()
_live_q: "queue.Queue" = queue.Queue()   # live clips never wait behind a long video
_jobs: dict = {}
_by_user: dict = {}                       # user -> {job id: job}: listing one person's videos never scans everyone's
_lock = threading.RLock()
on_clip_ready: list = []   # callbacks (job, clip), run once when a clip first finishes

DEFAULTS = {"max_clips": 6, "min_len": 15, "max_len": 60, "style": "bounce", "layout": "zoom", "quality": 720,
            "range_start": None, "range_end": None, "captions": True, "hook_text": "", "description_text": "",
            "lang": "en",        # lang: the language spoken
            "template": "", "cap_lang": ""}   # cap_lang: the captions' language ("" is the language spoken)


LIMITS = {"max_clips": (1, 20), "min_len": (5, 120), "max_len": (10, 180), "quality": (360, 2160)}


def clean_options(options) -> dict:
    """Only known settings, each inside its allowed range. Raises ValueError on nonsense."""
    out = {}
    for k, v in (options if isinstance(options, dict) else {}).items():
        if k == "captions":
            if not isinstance(v, bool):
                raise ValueError("Captions must be on or off.")
            out[k] = v
            continue
        if k in ("hook_text", "description_text"):     # kept even when empty: it clears the creator's own text for this video
            out[k] = creators.clean_hook(v) if k == "hook_text" else creators.clean_description(v)
            continue
        if k not in DEFAULTS or v in ("", None):
            continue
        if k in LIMITS:
            lo, hi = LIMITS[k]
            v = max(lo, min(hi, int(float(v))))
        elif k == "style":
            v = v if v in render.STYLES else DEFAULTS["style"]
        elif k == "layout":
            v = v if v in render.LAYOUTS else DEFAULTS["layout"]
        elif k == "lang":
            v = v if fetch_model.known(v) else DEFAULTS["lang"]
        elif k == "template":
            v = str(v)[:12]                      # checked when the video is created (templates.pick)
        elif k == "cap_lang":                    # the captions' language: "" is the language spoken
            v = v if v in brain.CAP_LANGS else ""
        else:                                    # range_start, range_end: seconds into the video
            v = float(v)
            if not math.isfinite(v) or v < 0:
                raise ValueError("That part of the video isn't valid.")
        out[k] = v
    if out.get("range_end") is not None and out["range_end"] <= out.get("range_start", 0):
        raise ValueError("The end of the chosen part must be after its start.")
    return out


def job_dir(jid) -> Path:
    return Path(_jobs[jid]["dir"])


HIDDEN = ("dir", "user", "source_path", "recover", "live_state")


def public(job) -> dict:
    """What the browser may see: no server paths, no owner id."""
    out = {k: v for k, v in job.items() if k not in HIDDEN}
    out["source"] = {k: v for k, v in job["source"].items() if k != "path"}
    out["clips"] = [{k: v for k, v in c.items() if k not in ("src", "fixed_words", "fixed_for", "heard", "trans_words", "trans_for")}
                    for c in job["clips"] if c.get("status") != "dropped"]
    ai = job.get("ai") or {}
    out["ai"] = {"tokens": ai.get("tokens", 0), "note": ai.get("note", ""), "used": bool(ai.get("model"))}   # never the model, never raw errors
    if job.get("status") == "queued":
        n = _q.ahead().get(job["id"])
        if n:
            out["stage"] = (f"High demand right now: {n} video{'s' if n != 1 else ''} ahead of yours" if _q.held else
                            f"Waiting in line: {n} video{'s' if n != 1 else ''} ahead of yours")
        elif n == 0 and _q.held:
            out["stage"] = "High demand right now: yours is next"
    return out


def save(job):
    ctx.bump(job.get("user"))
    with _lock:
        d = Path(job["dir"])
        for _ in range(5):
            try:
                text = json.dumps(job, indent=1)
                break
            except RuntimeError:  # changed mid-dump by another thread
                time.sleep(0.05)
        else:
            return
        tmp = d / "job.json.tmp"
        tmp.write_text(text, encoding="utf-8")
        _put_in_place(tmp, d / "job.json")


def _put_in_place(tmp: Path, target: Path):
    """Move the freshly written job.json over the old one.

    Windows refuses this ("Access is denied") for a moment whenever anything else has the old file open: a virus
    scanner, the search indexer, or another part of ClipHeist reading it. A download saves many times a second, so it
    happens, and it used to stop the whole video. So it is tried again a few times, and if the file is still busy
    it is written in place; as a last resort this save is skipped, because the next one carries the same news."""
    for i in range(8):
        try:
            tmp.replace(target)
            return
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    try:
        target.write_text(tmp.read_text(encoding="utf-8"), encoding="utf-8")
        tmp.unlink(missing_ok=True)
    except OSError:
        pass


class Cancelled(Exception):
    """The owner cancelled this video: its work stops at the next step."""


class HandedOver(Exception):
    """ClipHeist: the link turned out to be a stream that is on air. A recording was started for it (live.py), and
    this video, which only stood in for it, is taken away."""


def _set(job, **kw):
    if job.get("cancel_asked") and "status" not in kw and not kw.get("_final"):
        raise Cancelled()
    kw.pop("_final", None)
    kw["beat"] = time.time()          # last sign of life, so the page can tell working from stuck
    with _lock:
        job.update(kw)
    save(job)


CANCELLED = "Cancelled by you."


def cancel(job) -> dict:
    """Stop a video that is waiting or being made. Clips already finished are kept; the rest are dropped."""
    if job.get("kind") == "live" or job.get("status") not in ("queued", "running"):
        raise ValueError("Only a video that is still being made can be cancelled.")
    job["cancel_asked"] = True
    for c in job["clips"]:
        if c.get("status") in ("queued", "rendering"):
            c.update(status="dropped", error=None)        # not made: gone, not shown as a failed clip
    _set(job, status="error", error=CANCELLED, stage="Cancelled", _final=True)
    return job


def _remap(path, folder: Path):
    """Paths saved before data moved into per-user folders (or on another machine): find the file by name."""
    if path and not Path(path).exists():
        cand = folder / Path(str(path).replace("\\", "/")).name
        if cand.exists():
            return str(cand)
    return path


def _load_file(f: Path, uid: str):
    try:
        job = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    for c in job.get("clips") or []:          # cancelled before this fix: those unfinished clips were left as errors
        if c.get("status") == "error" and c.get("error") == "Cancelled.":
            c.update(status="dropped", error=None)
    job.update(dir=str(f.parent), user=uid)
    job.setdefault("kind", "video")
    job.setdefault("creator", None)
    job.setdefault("options", {}).setdefault("captions", True)
    if job.get("source_path"):
        job["source_path"] = _remap(job["source_path"], f.parent)
    if job.get("source", {}).get("path"):
        job["source"]["path"] = _remap(job["source"]["path"], f.parent)
    if job.get("status") in ("queued", "running") and job.get("kind") != "live":
        # Picked up again as soon as the server is back (jobs.start), from the last finished part of the transcript.
        job.update(status="queued", stage="Picking up where it stopped", error=None, resume=True)
    if job.get("status") == "live":
        from . import capture
        if capture.running(job["id"]):
            job["attach"] = True          # the recorder kept recording through the update: live.start picks it up
        else:
            job.update(status="done", stage="Recording paused by a server update", was_live=True)
    buffered = job.get("kind") == "live" and any((f.parent / "buffer").glob("*.ts"))
    for c in job.get("clips", []):
        c.setdefault("captions", job["options"]["captions"])
        if c.get("src"):
            c["src"] = _remap(c["src"], f.parent / "clips")
        if c.get("status") in ("rendering", "queued"):
            if job.get("attach"):
                c["status"] = "queued"  # the recording is still going: live.attach finishes these
            elif buffered and not c.get("src"):
                c["status"] = "queued"  # the stream is still on disk: live.recover() finishes these
                job["recover"] = True
            elif job.get("resume"):
                c["status"] = "queued"  # the video is being picked up again: this clip is made then
            elif job.get("kind") != "live" and (f.parent / "words.json").is_file():
                c["status"] = "queued"  # a single clip being re-made: jobs.start() makes it again
                job.setdefault("remake", []).append(c["id"])
            else:
                c.update(status="error", error="Interrupted by a restart. Open it and press Save and re-make.")
    _jobs[job["id"]] = job
    _by_user.setdefault(uid, {})[job["id"]] = job


def load_user(uid: str):
    d = config.USERS_DIR / uid
    for f in list((d / "jobs").glob("*/job.json")) + list((d / "creators").glob("*/*/*/job.json")):
        _load_file(f, uid)


def load_all():
    for d in config.USERS_DIR.iterdir():
        if d.is_dir():
            load_user(d.name)


def all_jobs() -> list:
    """Every user's jobs. Only for background work; never send this to a browser."""
    with _lock:
        return list(_jobs.values())


def list_jobs():
    u = ctx.uid()
    with _lock:
        return sorted((dict(j) for j in _by_user.get(u, {}).values()), key=lambda j: -j["created"])


def get(jid):
    """The job, only if it belongs to whoever this code is running for."""
    job = _jobs.get(jid)
    return job if job and job["user"] == ctx.uid() else None


def busy_count() -> int:
    u = ctx.uid()
    return sum(1 for j in list(_by_user.get(u, {}).values()) if j["kind"] == "video" and j["status"] in ("queued", "running"))


def waiting_total() -> int:
    return sum(1 for j in list(_jobs.values()) if j["kind"] == "video" and j["status"] == "queued")


def queue_epoch() -> int:
    return _q.epoch


def running_now() -> int:
    return _q.running


def create(source: dict, options: dict, enqueue=True, creator=None, kind="video") -> dict:
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:10]
    c = creators.get(creator) if creator else None
    asked = creators.campaign(c, (options or {}).get("campaign"))      # a video without a streamer can have one too
    camp = ((options or {}).get("campaign") if asked and asked.get("ready", True) else (c or {}).get("campaign_use", "")) or ""
    if c:
        d = creators.root(c["id"]) / ("live" if kind == "live" else "videos") / jid
        s = creators.settings_for(c, camp)          # a campaign's own result rules win over the creator's
        base = {**DEFAULTS, **{k: s[k] for k in ("max_clips", "min_len", "max_len", "style", "layout", "captions", "lang") if k in s},
                "hook_text": creators.clean_hook(s.get("hook_text")),
                "description_text": creators.clean_description(s.get("description_text"))}
        if s.get("focus"):
            base["focus"] = s["focus"]
    else:
        d, base, creator = config.user_dir(ctx.uid()) / "jobs" / jid, dict(DEFAULTS), None
        if camp and asked:                          # no streamer: the campaign's own result rules
            base.update({k: asked["rules"][k] for k in ("max_clips", "min_len", "max_len", "style", "layout") if k in asked["rules"]})
            if asked["rules"].get("focus"):
                base["focus"] = asked["rules"]["focus"]
    d.mkdir(parents=True)
    if c:                                           # the streamer's caption language, unless the video picks its own
        base.update(cap_lang=c.get("cap_lang") or "")
    picked = clean_options(options)
    opts = {**base, **picked}
    # the look of its clips: the template picked for this video, else the streamer's, else the account's default
    opts["template"], opts["look"] = templates.pick(opts.get("template"), c)
    if picked.get("layout") and not picked.get("template"):    # a layout chosen for this video (e.g. gameplay) wins
        opts["look"] = {**opts["look"], "layout": picked["layout"]}
    opts["layout"] = opts["look"]["layout"]
    if "quality" not in picked:                     # worked out from the style: as sharp as what it shows needs
        opts["quality"] = render.needed_height(opts["look"], (c or {}).get("cam"))
    if opts["min_len"] >= opts["max_len"]:
        opts["min_len"] = max(5, opts["max_len"] - 5)
    job = {"id": jid, "user": ctx.uid(), "kind": kind, "creator": creator, "dir": str(d), "created": time.time(), "source": source,
           "options": opts, "status": "queued", "stage": "Waiting", "progress": 0.0,
           "title": source.get("name", ""), "uploader": "", "duration": 0, "clips": [], "error": None, "ai": {},
           # the campaign this video or recording is clipped for: the creator's campaign in use, unless one is named
           "campaign": camp}
    with _lock:
        _jobs[jid] = job
        _by_user.setdefault(job["user"], {})[jid] = job
    save(job)
    if enqueue:
        _q.put(("job", jid, None), user=job["user"])
    return job


def enqueue(job):
    save(job)
    _q.put(("job", job["id"], None), user=job["user"])


def submit(fn):
    """Run any heavy task (a render, a cleanup) on the single worker, in order."""
    _q.put(("fn", ctx.bind(fn), None))


def submit_live(fn):
    """Live-stream renders and cleanups: their own workers (config.LIVE_RENDER_WORKERS), so a long video can never
    hold them up."""
    _live_q.put(ctx.bind(fn))


def _live_worker():
    while True:
        fn = _live_q.get()
        try:
            fn()
        except Exception:  # noqa: BLE001
            traceback.print_exc()


def delete(jid):
    with _lock:
        job = _jobs.pop(jid, None)
        if job:
            _by_user.get(job["user"], {}).pop(jid, None)
    if job:
        ctx.bump(job["user"])
        shutil.rmtree(job["dir"], ignore_errors=True)
    return bool(job)


def _tags(v) -> list:
    raw = v if isinstance(v, list) else str(v or "").replace(",", " ").split()
    out = []
    for t in raw:
        t = re.sub(r"[^\w]", "", str(t).lstrip("#"))[:40]
        if t and t.lower() not in (x.lower() for x in out):
            out.append(t)
    return out[:30]


def update_text(jid, cid, changes: dict):
    """The post text only (title, description, hashtags): saved straight away, the video is not made again."""
    job = _jobs[jid]
    clip = next(c for c in job["clips"] if c["id"] == cid)
    for k, n in (("title", 100), ("description", 2000)):
        if k in changes:
            clip[k] = str(changes[k] or "")[:n]
    if "hashtags" in changes:
        clip["hashtags"] = _tags(changes["hashtags"])
    if "draft" in changes:
        d = changes["draft"] if isinstance(changes["draft"], dict) else {}
        keep = {}
        for k in ("hook", "style", "layout", "template"):
            if k in d:
                keep[k] = str(d[k])[:60 if k == "hook" else 20]
        for k in ("start", "end"):
            if k in d:
                try:
                    v = round(float(d[k]), 2)
                    if math.isfinite(v) and v >= 0:
                        keep[k] = v
                except (TypeError, ValueError):
                    pass
        if keep:
            clip["draft"] = keep
        else:
            clip.pop("draft", None)
    save(job)
    f = Path(job["dir"]) / "clips" / f"{cid}.json"
    if f.parent.is_dir():
        f.write_text(json.dumps({k: clip.get(k) for k in ("title", "description", "hashtags", "hook", "start", "end", "score")}, indent=1), encoding="utf-8")
    return clip


def copy_clip(jid, cid, changes: dict):
    """A new clip from this one, with the changes: the original and where it was posted stay as they are."""
    job = _jobs[jid]
    clip = next(c for c in job["clips"] if c["id"] == cid)
    n = 2
    while any(c["id"] == f"{cid}-{n}" for c in job["clips"]):
        n += 1
    new = {k: v for k, v in clip.items() if k not in ("posts", "file", "cover", "made_at")}
    new.update(id=f"{cid}-{n}", status="queued", error=None, asked=True)   # its version carries on: no second listen, no auto-post
    job["clips"].insert(job["clips"].index(clip) + 1, new)
    save(job)
    try:
        request_render(jid, new["id"], changes)
    except Exception:
        job["clips"].remove(new)
        save(job)
        raise
    return new["id"]


def restyle():
    """A style was changed, deleted or made the default: videos and recordings still being worked on make their
    clips that haven't been made yet with it as it is now (the clip's own template, else the streamer's or the
    default). Clips already made keep their look."""
    for j in list_jobs():
        if j.get("status") not in ("queued", "running", "live"):
            continue
        job = _jobs.get(j["id"])
        if not job:
            continue
        o = job.get("options") or {}
        c = creators.get(job["creator"]) if job.get("creator") else None
        changed = False
        if o.get("look"):
            tid, look = templates.pick(o.get("template") if templates.get(o.get("template")) else None, c)
            if look != o["look"]:
                o.update(template=tid, look=look, layout=look["layout"])
                changed = True
        for clip in job.get("clips") or []:
            if clip.get("status") != "queued" or not clip.get("look"):
                continue
            tid, look = templates.pick(clip.get("template") if templates.get(clip.get("template")) else None, c)
            if look != clip["look"]:
                clip.update(template=tid, look=look, layout=look["layout"])
                changed = True
        if changed:
            save(job)


def request_render(jid, cid, changes: dict):
    job = _jobs[jid]
    clip = next(c for c in job["clips"] if c["id"] == cid)
    new = dict(clip)
    if "captions" in changes:
        new.update(clean_options({"captions": changes["captions"]}))
    for k in ("start", "end"):
        if k in changes:
            new[k] = round(float(changes[k]), 2)
    if "src_offset" in clip:  # live clip: can only move inside the saved piece of stream
        new["start"] = max(new["start"], clip["src_offset"])
        new["end"] = min(new["end"], clip["src_end"])
    if not (math.isfinite(new["start"]) and math.isfinite(new["end"])) or new["start"] < 0:
        raise ValueError("Those times aren't valid.")
    if "src_offset" in clip and new["end"] - new["start"] < 3 and "start" not in changes and "end" not in changes:
        raise ValueError("This moment was not in the saved part of the stream, so it can't be made again.")
    if not 3 <= new["end"] - new["start"] <= 180:
        raise ValueError("A clip can be 3 to 180 seconds long.")
    for k in ("hook", "title", "description", "style", "layout"):
        if k in changes:
            new[k] = str(changes[k])[:{"hook": 60, "title": 100, "description": 2000}.get(k, 20)]
    if changes.get("template"):                      # made again with another template
        t = templates.get(str(changes["template"])[:12])
        if not t:
            raise ValueError("That style isn't there any more. Pick another one.")
        new.update(template=t["id"], look=templates.clean_look(t["look"]), layout=t["look"]["layout"])
    elif "style" in changes or "layout" in changes:  # the old caption style and layout choices
        new.pop("look", None)
        new["template"] = ""
    if "hashtags" in changes:
        new["hashtags"] = _tags(changes["hashtags"])
    if new.get("style") not in render.STYLES:
        new["style"] = DEFAULTS["style"]
    if new.get("layout") not in render.LAYOUTS:
        new["layout"] = DEFAULTS["layout"]
    new.pop("draft", None)
    clip.update(new, status="queued")
    clip.pop("draft", None)
    save(job)
    _q.put(("clip", jid, cid))


def clean_hashtags(v) -> list:
    """Hashtags typed by a person ("#funny gaming") or sent by the page as a list: plain words, at most 8."""
    if isinstance(v, str):
        v = v.split()
    if not isinstance(v, list):
        raise ValueError("Hashtags must be words separated by spaces.")
    tags = [re.sub(r"\W", "", str(h))[:30] for h in v[:50] if isinstance(h, (str, int))]
    return [t for t in tags if t][:8]


def save_post_text(job, clip, changes: dict):
    """Change a clip's title, description and hashtags without making the clip again. Posting reads them
    from the clip when it uploads, so the next post (or one still waiting in the queue) uses the new text."""
    text = {k: str(changes[k] or "")[:{"title": 100, "description": 2000}[k]] for k in ("title", "description") if k in changes}
    if "hashtags" in changes:
        text["hashtags"] = clean_hashtags(changes["hashtags"])
    if clip.get("hashtags_in_description") and not clip.get("fixed_description") and text:
        # This clip's description ends with its hashtags, and posting does not add them again. So the
        # description is put back together from the Hashtags box: the words, then one line of tags.
        desc = hashtags._TAG_IN_TEXT.sub("", text.get("description", clip.get("description") or "")).strip()
        line = " ".join("#" + t for t in text.get("hashtags", clip.get("hashtags") or []))
        text["description"] = ((desc + "\n\n" + line).strip() if desc else line)[:2000]
    with _lock:
        clip.update(text)
    save(job)
    side = Path(job["dir"]) / "clips" / f"{clip['id']}.json"   # the copy that sits next to the video file
    try:
        if side.is_file():
            side.write_text(json.dumps({**json.loads(side.read_text(encoding="utf-8")), **text}, indent=1), encoding="utf-8")
    except (OSError, ValueError):
        pass


def spoken_in(job, clip) -> str:
    """What is said during a clip, from the video's transcript."""
    try:
        words = json.loads((Path(job["dir"]) / "words.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return " ".join(str(w.get("w", "")) for w in words
                    if isinstance(w, dict) and clip["start"] - 0.5 <= _f(w.get("s")) <= clip["end"] + 0.5)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return -1.0


# Seconds of sound the AI hears before and after a decided clip, to find where it really starts and ends. More after:
# what a moment builds up to (the lift, the reveal, the punchline) often comes just after where it was first cut.
HEAR_BEFORE, HEAR_AFTER = 8, 20


def _clip_sound(src: Path, a: float, b: float) -> tuple:
    """This stretch of a file's sound: small and compressed for the AI, and as 16 kHz samples to find quiet moments."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="pc-listen-") as tmp:
        wav, small = Path(tmp) / "a.wav", Path(tmp) / "a.ogg"
        media.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, a):.2f}", "-t", f"{b - a:.2f}", "-i", str(src), "-vn",
                   "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
        pcm = media.load_audio(wav)
        try:
            media.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "libopus", "-b:a", "24k", "-application", "voip", str(small)])
            return (small.read_bytes(), "audio/ogg"), pcm
        except Exception:  # noqa: BLE001 - no Opus in this ffmpeg: the plain sound, larger but the same to the AI
            return (wav.read_bytes(), "audio/wav"), pcm


def shows(job, clip) -> tuple:
    """(captions, heading): what this clip's style draws on it. Only these are asked of the AI, so a style without
    captions or without a heading costs nothing for them."""
    look = clip.get("look") if isinstance(clip.get("look"), dict) else (job.get("options") or {}).get("look")
    if not isinstance(look, dict):
        return True, True
    return (look.get("cap") or {}).get("show", True) is not False, (look.get("hook") or {}).get("show", True) is not False


def _listen(job, clip, src: Path, off: float, words: list):
    """A decided clip, heard by the AI once: it moves the start and end onto sentence boundaries and writes the
    captions from the sound. Sets clip["heard"] (its words on the video's clock) and the new start and end."""
    lo = clip.get("src_offset", 0.0) if clip.get("src") else 0.0
    hi = clip.get("src_end") or job.get("duration") or clip["end"]
    a, b = max(lo, clip["start"] - HEAR_BEFORE), min(hi, clip["end"] + HEAR_AFTER)
    if b - a < 3 or b - a > 240:
        return
    sound, pcm = _clip_sound(src, a - off, b - off)
    o = job.get("options") or {}
    caps, head = shows(job, clip)
    caps = caps and clip.get("captions", True)      # captions turned off for this clip: no words asked for
    # The words' timing comes from the local model run on this very sound, so it matches the video exactly: the
    # recording's transcript can sit seconds away from the video when its clocks have slipped.
    try:
        local = transcribe.transcribe(pcm, lang=_speech(job))
    except Exception:  # noqa: BLE001 - the transcript the clip was found with
        local = [dict(w, s=w["s"] - a, e=w["e"] - a) for w in words if a <= w["s"] <= b]
    got = brain.listen(sound, pcm, b - a, clip["start"] - a, clip["end"] - a, int(o.get("min_len", 15)), int(o.get("max_len", 60)),
                       local, near=bool(clip.get("asked")), title=job.get("title") or "", why=clip.get("reason") or "",
                       hook=clip.get("hook") or "", retime=(lambda lang: brain.word_times(sound, lang)) if caps else None,
                       spoken=job.get("lang") or "", heading=head)
    if caps:
        try:                             # the last pass: misheard words fixed from what is being talked about
            talk = " ".join(w["w"] for w in words if clip["start"] - 150 <= w["s"] < clip["start"])
            got["words"] = brain.fix_heard(got["words"], talk, job.get("title") or "")
        except Exception:  # noqa: BLE001 - the words as heard
            pass
    if got.get("hook") and head and not o.get("hook_text"):   # a heading written for the final cut (never over the person's own)
        clip.update(hook=got["hook"], first_hook=clip.get("hook") or "")
    clip.update(start=round(a + got["start"], 2), end=round(a + got["end"], 2),
                heard={"a": round(a + got["start"], 2), "b": round(a + got["end"], 2), "lang": got["lang"],
                       "words": [dict(w, s=round(w["s"] + a, 2), e=round(w["e"] + a, 2)) for w in got["words"]],
                **({} if caps else {"raw": True})})     # not proofread: done if the captions are turned on later


def can_listen() -> bool:
    """May a decided clip be heard by the AI (studio/brain.listen)? That sends the clip's sound, which only Gemini
    takes, so with any other provider the clip is made with the local speech-to-text words, as they are."""
    try:
        return config.LISTEN and ai.ready() and ai.provider() == "gemini"
    except Exception:  # noqa: BLE001
        return False


def _speech(job) -> str:
    """The language spoken in a video, for the local speech-to-text model: what YouTube says was spoken when its model
    is here, else the language chosen for the video (English unless another was chosen)."""
    said = str(job.get("lang") or "").lower().split("-")[0]
    if fetch_model.known(said) and (said == fetch_model.DEFAULT or fetch_model.ready(said)):
        return said
    lang = str((job.get("options") or {}).get("lang") or "").lower()
    return lang if fetch_model.known(lang) else fetch_model.DEFAULT


def _cam_box(job, who, src):
    """Where the streamer's cam is in this video: the streamer's own (found once, or set by hand), else found in this
    video and kept on it (and on the streamer). None when there's no separate cam to cut out."""
    from . import facecam
    if who and who.get("cam"):
        return who["cam"]
    if "cam_src" in job:
        return job["cam_src"]
    try:
        box = facecam.find([src], 12)
    except Exception:  # noqa: BLE001 - no cam: the clip is made with the style's other parts
        box = None
    job["cam_src"] = box
    if who and box:
        creators.update(who["id"], {"cam": box, "cam_auto": True})
        creators.log(who["id"], "Found the streamer's camera in the stream")
    save(job)
    return box


def sources_of(creator_id, limit=8) -> list:
    """Files of this streamer's newest videos and recordings, to look for their cam in."""
    out = []
    for j in list_jobs():
        if j.get("creator") != creator_id:
            continue
        cands = [j.get("source_path")] + [c.get("src") for c in reversed(j.get("clips") or [])]
        buf = Path(j["dir"]) / "buffer"
        if buf.is_dir():
            cands += [str(p) for p in sorted(buf.glob("*.ts"))[-limit:]]
        out += [p for p in cands if p and Path(p).is_file() and p not in out]
        if len(out) >= limit:
            break
    return out[:limit]


def render_one(job, clip, words, audio=None):
    d = Path(job["dir"])
    clip["status"] = "rendering"
    save(job)
    try:
        # ClipHeist: on a long video the clip's stretch is fetched and rendered on Modal (remote_render). Two things
        # need the stretch on this machine instead: an AI that listens to it, and a style that shows the streamer's cam.
        remote = bool(clip.pop("remote", None)) and not clip.get("src")
        if remote and (can_listen() or (isinstance(clip.get("look"), dict) and clip["look"].get("cam"))):
            remote = False
            _fetch_piece(job, clip, audio if audio is not None else [])
            if clip["status"] == "error":
                raise RuntimeError(clip["error"])
            clip["status"] = "rendering"
        if not remote and not clip.get("src") and not job.get("source_path"):
            _piece_again(job, clip)
        off = clip.get("src_offset", 0.0)
        src = Path(clip["src"]) if clip.get("src") else Path(job.get("source_path") or ".")
        if not remote and not src.is_file():
            raise RuntimeError("The original video is no longer kept, so this clip can't be made again. Add the video again to get new clips from it.")
        if not clip.get("src"):
            clip["end"] = min(clip["end"], job["duration"])
        if not remote and not clip.get("heard") and not clip.get("version") and not clip.get("listen_error") and can_listen():
            try:                         # the first time only: a clip someone has re-trimmed keeps their cut
                usage.set_context(job.get("creator"), job.get("title") or "")
                _listen(job, clip, src, off, words)
            except brain.NoSpeech as e:
                if not clip.get("asked"):        # a break screen or music: left out (a clip someone asked for is still made)
                    clip.update(status="dropped", error=None)
                    if clip.get("src"):
                        Path(clip["src"]).unlink(missing_ok=True)
                    if job.get("creator"):
                        creators.log(job["creator"], "A clip was left out: nobody talks in it")
                    save(job)
                    return
                clip["listen_error"] = safe.friendly(e, 200)
            except Exception as e:  # noqa: BLE001 - the clip is still made, with the local transcript
                clip["listen_error"] = safe.friendly(e, 200)
        if not clip.get("version") and not (job.get("options") or {}).get("hook_text") and render.split_hook(clip.get("hook") or "")[1]:
            # emoji on about one clip in three of a video or recording; the clips with the best scores keep theirs
            others = [c for c in job["clips"] if c is not clip and c.get("status") != "dropped"]
            allowed = max(1, -(-(len(others) + 1) // 3))
            ahead = [c for c in others if render.split_hook(c.get("hook") or "")[1] and c.get("score", 0) >= clip.get("score", 0)]
            if len(ahead) >= allowed:
                clip["hook"] = render.split_hook(clip["hook"])[0]
        heard = clip.get("heard")
        caps = shows(job, clip)[0] and clip.get("captions", True)     # the style shows captions, and they are not turned off
        local = dict(clip, start=clip["start"] - off, end=clip["end"] - off, hook=roman.to_roman(clip.get("hook") or ""))
        span = [round(clip["start"], 2), round(clip["end"], 2), "en2"]      # "en2": captions made always-English
        if not caps:                           # a style without captions: no words, and no AI call for them
            raw = []
        elif heard and heard["a"] - 0.5 <= clip["start"] and clip["end"] <= heard["b"] + 0.5:
            raw = [w for w in heard["words"] if clip["start"] - 0.3 <= w["s"] < clip["end"]]   # what the AI heard
            if heard.get("raw") and raw:       # heard while the captions were off: proofread now, once
                try:
                    usage.set_context(job.get("creator"), job.get("title") or "")
                    talk = " ".join(w["w"] for w in words if clip["start"] - 150 <= w["s"] < clip["start"])
                    fixed = brain.fix_heard(raw, talk, job.get("title") or "")
                    keep = [w for w in heard["words"] if not (clip["start"] - 0.3 <= w["s"] < clip["end"])]
                    heard.update(words=sorted(keep + fixed, key=lambda w: w["s"]))
                    heard.pop("raw", None)
                    raw = fixed
                except Exception:  # noqa: BLE001 - the words as heard
                    pass
        elif clip.get("fixed_for") == span and clip.get("fixed_words") and not any(
                roman.needs_roman(w.get("w", "")) for w in clip["fixed_words"]):
            raw = clip["fixed_words"]          # already tidied for this exact span: no second AI call
        else:                                  # the AI couldn't listen, or the clip was moved past what it heard
            raw = [w for w in words if clip["start"] - 1 <= w["s"] <= clip["end"] + 1]
            try:
                usage.set_context(job.get("creator"), job.get("title") or "")
                raw = brain.english_captions(raw, job.get("lang") or "", job.get("title") or "",
                                             youtube_english=lambda: _english_part(job, clip))
                try:                       # then read as one passage and proofread, each word on its own moment
                    talk = " ".join(w["w"] for w in words if clip["start"] - 150 <= w["s"] < clip["start"])
                    raw = brain.fix_heard(raw, talk, job.get("title") or "")
                except Exception:  # noqa: BLE001 - the tidied words
                    pass
                clip.update(fixed_words=raw, fixed_for=span)
            except Exception:  # noqa: BLE001 - captions still render with the words as heard
                pass
        # the language picked for the captions (a recording: the streamer's, as it is now): translated by meaning,
        # each line on the moment its original line is said. Done once per span and language.
        who0 = creators.get(job["creator"]) if job.get("creator") else None
        tl = ((who0 or {}).get("cap_lang") if job.get("kind") == "live" else (job.get("options") or {}).get("cap_lang")) or ""
        spoken = ((heard or {}).get("lang") or job.get("lang") or "").split("-")[0]
        # Hindi or Urdu speech already comes out in Roman letters (Hinglish): nothing to do for Hinglish, and the
        # words are written again in their own script for Urdu or Hindi
        if tl and raw and not (tl == "rom" and spoken in ("hi", "ur")) and (spoken != tl or tl in ("hi", "ur")):
            tkey = span + [tl]
            if clip.get("trans_for") == tkey and clip.get("trans_words"):
                raw = clip["trans_words"]
            else:
                try:                              # the captions only: the heading is never translated
                    usage.set_context(job.get("creator"), job.get("title") or "")
                    raw, _hk = brain.translate_captions(raw, tl, job.get("title") or "", "")
                    clip.update(trans_words=raw, trans_for=tkey)
                except Exception:  # noqa: BLE001 - the captions in the language spoken
                    pass
        # the last word on it: whatever was said, nothing is drawn in Hindi or Urdu script
        # (Urdu or Hindi asked for in their own script: drawn in it; any other language: never Hindi or Urdu script)
        script = tl if tl in ("hi", "ur") and raw and clip.get("trans_for") == span + [tl] else ""
        local.update(script=script, cap_lang=tl)
        shown = raw if script or not raw else roman.words(raw)
        near = [dict(w, s=w["s"] - off, e=w["e"] - off) for w in shown]
        who = creators.get(job["creator"]) if job.get("creator") else None
        if who or job.get("campaign"):   # its campaign's hashtags first, the campaign's logo and small text on the video
            clip["hashtags"] = creators.with_tags(who, clip.get("hashtags"), job.get("campaign"))
        extras = creators.clip_extras(who, job.get("campaign")) if (who or job.get("campaign")) else None
        if not remote and isinstance(clip.get("look"), dict) and clip["look"].get("cam"):   # a style with the streamer's cam
            box = clip["look"].get("cam_src") or _cam_box(job, who, src)          # drawn on the style, else found
            if box:
                extras = {**(extras or {}), "cam": box}
        need, got_h = int((job.get("options") or {}).get("quality") or 1080), int(job.get("height") or 0)
        clip["soft"] = bool(got_h and need > 1080 and got_h < need / 2)     # e.g. a 720p video with a zoomed-in cam
        local["watermark"] = bool(job.get("watermark"))       # ClipHeist free plan
        out = None
        if remote:
            try:      # Modal downloads this stretch and renders it; on any trouble the clip is made here instead
                if extras and extras.get("logo"):
                    raise RuntimeError("this clip's logo is kept on this machine")
                m = remote_render.cut(job, clip, shown, audio, local, extras)
                clip.update(src=m["src"], src_offset=m["offset"], src_end=m["src_end"], start=m["start"], end=m["end"])
                job.setdefault("width", m["width"])
                job.setdefault("height", m["height"])
                out = {"file": f"{clip['id']}.mp4", "cover": f"{clip['id']}.jpg", "layout": m["layout"]}
            except Exception as e:  # noqa: BLE001
                print(f"remote render failed for {clip['id']}, making it here: {safe.friendly(e, 300)}", flush=True)
                _fetch_piece(job, clip, audio if audio is not None else [])
                if clip["status"] == "error":
                    raise RuntimeError(clip["error"])
                off = clip["src_offset"]
                src = Path(clip["src"])
                local.update(start=clip["start"] - off, end=clip["end"] - off)
                near = [dict(w, s=w["s"] - off, e=w["e"] - off) for w in shown]
        if out is None:
            out = render.render_clip(src, d / "clips", clip["id"], local, near, job["width"], job["height"], extras=extras)
        clip.update(out, status="done", version=clip.get("version", 0) + 1, error=None)
        post = {k: clip[k] for k in ("title", "description", "hashtags", "hook", "start", "end", "score")}
        (d / "clips" / f"{clip['id']}.json").write_text(json.dumps(post, indent=1), encoding="utf-8")
        if clip["version"] == 1:
            clip.setdefault("made_at", time.time())
            creators.log(job["creator"], f"Clip ready (score {clip['score']}): {clip['title'] or clip['hook']}")
            save(job)
            for fn in on_clip_ready:
                try:
                    fn(job, clip)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
    except Exception as e:  # noqa: BLE001
        clip.update(status="error", error=safe.friendly(e, 400))
        creators.log(job["creator"], f"Clip failed: {safe.friendly(e, 200)}")
    save(job)


CLIP_MINUTES = float(config.env("PEAKCUT_CLIP_MINUTES", "15"))     # the longest one clip may take to make


def _wait_for_clips(job, futures, began, workers):
    """Wait for a video's clips, but not for ever. A clip that hangs (a download that stalls, an encoder that
    freezes) used to keep the whole video "in progress", and no other clip could be re-made. Now it is marked as
    failed after CLIP_MINUTES and the video finishes with the clips that did work."""
    left, given_up = list(futures), 0
    late = "This clip took too long and was stopped. Open it and press Save and re-make to try again."
    while left:
        still = []
        for f, clip in left:
            if f.done():
                f.result()
                continue
            t = began.get(clip["id"])
            if (t and time.time() - t > CLIP_MINUTES * 60) or (not t and given_up >= workers):
                given_up += 1
                clip.update(status="error", error=late)
                creators.log(job["creator"], f"Clip stopped after {CLIP_MINUTES:g} minutes: {clip.get('title') or clip.get('hook') or clip['id']}")
                save(job)
                continue
            still.append((f, clip))
        left = still
        if left:
            time.sleep(1)


def _english_part(job, clip):
    """YouTube's English captions for just this clip, for a video in another language. Fetched once per video."""
    if (job.get("source") or {}).get("type") != "url":
        return None
    f = Path(job["dir"]) / "words_en.json"
    if f.is_file():
        en = json.loads(f.read_text(encoding="utf-8"))
    else:
        try:
            en = media.youtube_english_words(job["source"]["url"]) or []
        except Exception:  # noqa: BLE001 - YouTube often refuses its translations: the AI translates instead
            en = []
        f.write_text(json.dumps(en), encoding="utf-8")       # asked once per video, refused or not
    return [w for w in en if clip["start"] - 0.5 <= w["s"] <= clip["end"] + 0.5]


# ---------------------------------------------------------------- only the clips are kept
def _source_files(job) -> list:
    """The whole video (downloaded or uploaded) in this job's folder, if it is still there."""
    d = Path(job["dir"])
    found = {p for p in d.glob("source.*") if p.is_file()}
    for key in (job.get("source_path"), (job.get("source") or {}).get("path")):
        if key and Path(key).is_file() and Path(key).parent == d:
            found.add(Path(key))
    return [p for p in found if p.suffix.lower() not in (".json", ".txt")]


def tidies_sources() -> bool:
    """May ClipHeist tidy people's files by itself? Off unless PEAKCUT_TIDY=1: then whole videos are deleted once their
    clips are made (each clip keeps its own stretch), clip stretches sharper than 1080p are made 1080p after a few
    days, and leftover stream pieces of finished recordings are deleted. Off, nothing is ever deleted or re-encoded."""
    return bool(getattr(config, "TIDY", False))


def slim(job, audio=None) -> int:
    """Delete a finished video's source, keeping what each clip needs: its own stretch of the video with a few
    seconds either side, so it can still be re-trimmed and re-made. Returns the bytes freed.

    A clip's stretch is cut without re-encoding, which lands on a keyframe a little before the asked time; where
    it really starts is found by matching its sound against the whole video's, as for long videos. If any clip's
    stretch cannot be made, the whole video is left alone. Only with PEAKCUT_TIDY=1."""
    if not tidies_sources() or job.get("kind") == "live":
        return 0
    if not job.get("clips") and time.time() - float(job.get("empty_since") or 0) < KEEP_EMPTY_DAYS * 86400:
        return 0                         # nothing was clipped: kept a while, in case they press "Clip anyway"
    if audio is None and job.get("status") in ("queued", "running", "live"):
        return 0                         # still being worked on (the pipeline itself calls with the audio, at its end)
    files = _source_files(job)
    if not files:
        return 0
    d = Path(job["dir"])
    src = Path(job["source_path"]) if job.get("source_path") and Path(job["source_path"]).is_file() else files[0]
    todo = [c for c in job.get("clips", []) if c.get("status") == "done" and not c.get("src")]
    if todo:
        if audio is None:
            wav = d / "slim.wav"
            try:
                media.extract_audio(src, wav)
                audio = media.load_audio(wav)
            finally:
                wav.unlink(missing_ok=True)
        made = []
        try:
            for clip in todo:
                want = max(0.0, clip["start"] - PAD)
                until = min(job.get("duration") or clip["end"] + PAD, clip["end"] + PAD)
                piece = d / "clips" / f"{clip['id']}.src{src.suffix.lower() or '.mp4'}"
                media.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{want:.2f}", "-t", f"{until - want:.2f}", "-i", str(src),
                           "-c", "copy", "-avoid_negative_ts", "make_zero", str(piece)], timeout=900)
                wav = d / "clips" / f"{clip['id']}.slim.wav"
                try:
                    media.extract_audio(piece, wav)
                    part = media.load_audio(wav)
                finally:
                    wav.unlink(missing_ok=True)
                meta = media.probe(piece)
                if meta["duration"] < (clip["end"] - clip["start"]) * 0.9:
                    raise RuntimeError("the kept stretch came out too short")
                off = media.find_offset(audio, part, want)
                made.append((clip, piece, round(off, 2), round(off + meta["duration"], 2)))
        except Exception:
            for _c, piece, _a, _b in made:
                piece.unlink(missing_ok=True)
            raise
        for clip, piece, a, b in made:
            clip.update(src=str(piece), src_offset=a, src_end=b)
        save(job)
    freed = 0
    for f in files:
        try:
            freed += f.stat().st_size
            f.unlink()
        except OSError:
            pass
    for f in list(d.glob("*.part")) + list(d.glob("*.ytdl")):
        f.unlink(missing_ok=True)
    return freed


SHRINK_DAYS = 3


def shrink_stretches(job) -> int:
    """A finished video's clips keep their stretch of the source so they can be re-made. Fetched sharper than 1080p
    for a style that zooms in, after SHRINK_DAYS each is made 1080p (same moments, same timing), so high quality
    rarely stays on the disk for long. Bytes freed. Only with PEAKCUT_TIDY=1."""
    if not tidies_sources():
        return 0
    freed = 0
    for c in job.get("clips") or []:
        src = Path(c.get("src") or "")
        if not src.is_file() or c.get("shrunk"):
            continue
        try:
            if media.probe(src)["height"] <= 1080:
                c["shrunk"] = True
                continue
            tmp = src.with_name(src.stem + ".1080" + src.suffix)
            media.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-map", "0", "-vf", "scale=-2:1080", "-c:v", "libx264",
                       "-preset", "veryfast", "-crf", "20", "-c:a", "copy", str(tmp)], timeout=1800)
            before = src.stat().st_size
            tmp.replace(src)
            freed += max(0, before - src.stat().st_size)
            c["shrunk"] = True
        except Exception:  # noqa: BLE001 - left as it is
            pass
    if freed:
        save(job)
    return freed


def housekeep() -> dict:
    """Free the space that finished work no longer needs, for every account: whole videos whose clips are made,
    and the stream pieces left behind by recordings that a restart interrupted. Clips are never touched."""
    from . import live
    out = {"videos": 0, "recordings": 0, "bytes": 0}
    if not tidies_sources():             # off unless PEAKCUT_TIDY=1: nobody's files are touched
        return out
    for job in all_jobs():
        try:
            if job.get("status") in ("queued", "running", "live") or job["id"] in live.SESSIONS or job.get("recover") \
                    or job.get("resume") or job.get("remake"):
                continue
            if any(c.get("status") in ("queued", "rendering") for c in job.get("clips", [])):
                continue
            if job.get("kind") == "live":
                got = 0
                for f in (Path(job["dir"]) / "buffer").glob("*"):
                    try:
                        got += f.stat().st_size
                        f.unlink()
                    except OSError:
                        pass
                if got:
                    out["recordings"] += 1
                    out["bytes"] += got
            if time.time() - float(job.get("created") or 0) > SHRINK_DAYS * 86400:
                out["bytes"] += shrink_stretches(job)
            if job.get("kind") == "live":
                continue
            elif tidies_sources() and _source_files(job):
                token = ctx._uid.set(job["user"])
                try:
                    got = slim(job)
                finally:
                    ctx._uid.reset(token)
                if got:
                    out["videos"] += 1
                    out["bytes"] += got
        except Exception:  # noqa: BLE001 - one video that cannot be tidied is left as it is
            pass
    return out


def _housekeeping_loop():
    time.sleep(120)                      # after start-up has picked up interrupted work
    while True:
        try:
            got = housekeep()
            if got["bytes"]:
                print(f"[ClipHeist] housekeeping freed {got['bytes'] / 1e9:.1f} GB: {got['videos']} finished videos, "
                      f"{got['recordings']} old recordings", flush=True)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(6 * 3600)


LONG_VIDEO = 40 * 60   # above this, fetch only the pieces that become clips
KEEP_EMPTY_DAYS = 3    # a video with no clips keeps its source this long, for "Clip anyway"
PAD = 8                # seconds kept either side of a fetched piece, so clips can be re-trimmed


def _piece_again(job, clip):
    """Remaking a clip whose own piece of the video was never fetched (its first try failed) and whose whole video
    is not here: fetch the piece now, cut exactly at the asked time, so no sound matching is needed."""
    src = job.get("source") or {}
    if src.get("type") != "url" or not src.get("url"):
        raise RuntimeError("The original video is no longer here, so this clip can't be made again. Add the video again.")
    d = Path(job["dir"])
    want = max(0.0, clip["start"] - PAD)
    until = clip["end"] + PAD if not job.get("duration") else min(job["duration"], clip["end"] + PAD)
    try:
        piece = media.download_section(src["url"], d / "clips", f"{clip['id']}.src", want, until,
                                       int(job["options"]["quality"]), exact=True)
        meta = media.probe(piece)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Could not fetch this part of the video: {safe.friendly(e, 250)}") from e
    clip.update(src=str(piece), src_offset=round(want, 2), src_end=round(want + meta["duration"], 2))
    clip["end"] = min(clip["end"], clip["src_end"])
    job.setdefault("width", meta["width"])
    job.setdefault("height", meta["height"])


PIECE_TRIES = (0, 20, 60)   # seconds waited before each try at a clip's stretch


def _fetch_piece(job, clip, full_audio):
    """Long videos: download just this clip's stretch and pin down exactly where it starts. Tried a few times, a
    little apart (YouTube now and then refuses one stretch, or every download for a few minutes)."""
    for i, wait in enumerate(PIECE_TRIES):
        if wait:
            time.sleep(wait)
            if job.get("cancel_asked"):
                break
        clip.update(status="rendering" if clip.get("status") == "error" else clip.get("status"), error=None)
        _fetch_piece_once(job, clip, full_audio)
        if clip.get("status") != "error":
            return
        if i < len(PIECE_TRIES) - 1:
            media._yt_refused_until = 0.0          # the next try asks YouTube again, every way
            media._ROUTE_REFUSED.clear()


def _fetch_piece_once(job, clip, full_audio):
    d = Path(job["dir"])
    o = job["options"]
    try:
        # enough either side for the AI to hear where the moment really starts and what it builds up to after
        want = max(0.0, clip["start"] - max(PAD, HEAR_BEFORE))
        until = min(job["duration"], clip["end"] + max(PAD, HEAR_AFTER + 2))
        stem = f"{clip['id']}.src"
        for old in (d / "clips").glob(f"{clip['id']}.src*.*"):   # an earlier try's file (often the kept copy itself)
            try:
                old.unlink(missing_ok=True)
            except OSError:                        # still open (being played): this try writes under a new name
                stem = f"{clip['id']}.src{int(time.time())}"
        # with no sound of the whole video to match against (a clip made again later), it is cut at exactly `want`
        exact = len(full_audio) == 0
        piece = media.download_section(job["source"]["url"], d / "clips", stem, want, until, int(o["quality"]), exact=exact)
        part = None
        if not exact:
            wav = d / "clips" / f"{clip['id']}.wav"
            media.extract_audio(piece, wav)
            part = media.load_audio(wav)
            wav.unlink(missing_ok=True)
        meta = media.probe(piece)
        off = want if exact else media.find_offset(full_audio, part, want)
        clip.update(src=str(piece), src_offset=round(off, 2), src_end=round(off + meta["duration"], 2))
        clip["start"], clip["end"] = max(clip["start"], clip["src_offset"]), min(clip["end"], clip["src_end"])
        job.setdefault("width", meta["width"])
        job.setdefault("height", meta["height"])
    except media.YouTubeBlocked:
        clip.update(status="error", error="YouTube didn't send this part of the video just now. Press Try again in a few minutes.")
    except Exception as e:  # noqa: BLE001
        clip.update(status="error", error=f"Could not fetch this part of the video: {safe.friendly(e, 250)}")


BLOCK = 10 * 60         # long videos are transcribed and clipped one block at a time
PROGRESSIVE_FROM = 20 * 60


def _covers(words: list, start: float, end: float) -> bool:
    """Is a borrowed transcript good enough to find clips with? It has to reach the end of the part being clipped
    and have a normal amount of talking in it; otherwise our own model writes the transcript."""
    inside = [w for w in words if start <= w["s"] <= end]
    minutes = max(0.5, (end - start) / 60)
    return len(inside) >= 25 * minutes and inside[-1]["s"] >= start + 0.85 * (end - start)


def _hear_parts(audio, lo, a_, b_, windows, on_progress=None, lang=fetch_model.DEFAULT) -> list:
    """Transcribe only the chosen stretches (seconds from `lo`) that fall inside samples a_..b_. Word times come back
    relative to a_, the same as transcribing the whole part would give."""
    a_rel, b_rel = (a_ - lo) / 16000, (b_ - lo) / 16000
    segs = [(max(s, a_rel), min(e, b_rel)) for s, e in windows if e > a_rel and s < b_rel]
    total = sum(e - s for s, e in segs) or 1.0
    out, done = [], 0.0
    for s, e in segs:
        def part(f, done=done, ln=e - s):
            if on_progress:
                on_progress((done + f * ln) / total)
        ws = transcribe.transcribe(audio[lo + int(s * 16000): lo + int(e * 16000)], part, lang)
        out += [dict(w, s=round(w["s"] + s - a_rel, 2), e=round(w["e"] + s - a_rel, 2)) for w in ws]
        done += e - s
    if on_progress:
        on_progress(1.0)
    return out


def _best(candidates: list, n: int, taken=()) -> list:
    """The n highest-scoring candidates that do not overlap each other or clips already made, in time order."""
    picked = []
    for c in sorted(candidates, key=lambda c: -brain._num(c.get("score"))):
        if len(picked) >= n:
            break
        if any(not (c["end"] <= p["start"] or c["start"] >= p["end"]) for p in [*taken, *picked]):
            continue
        picked.append(c)
    return sorted(picked, key=lambda c: c["start"])


def _facecam(job) -> list | None:
    """Where the webcam is in the source: once for the whole video, for every clip of it.

    Only a sure answer is used. A box that is a scoreboard drawn over the game would
    put a picture of a scoreboard in the corner of the clip. One frame is also shown to
    the AI, which tells a person from a game character better than any measurement can;
    what it says is measured again on the picture, so it cannot do any harm."""
    path = job.get("source_path")
    if not path or not Path(path).is_file():
        return None
    try:
        box = facecam.detect(path, ask=brain.look_for_facecam)
    except Exception:  # noqa: BLE001 - a missing facecam is not a failure
        return None
    if not box or box.get("conf") != "high":
        return None
    job["facecam"] = [box["x"], box["y"], box["w"], box["h"]]
    return job["facecam"]


def _run_job(job):
    from concurrent.futures import ThreadPoolExecutor
    d = Path(job["dir"])
    o = job["options"]
    src = job["source"]
    # Which game this is: what the creator set, or what the title says. It decides the extra rules the AI
    # reads and the tags every post carries.
    game = job.get("game") or ""
    if not game and job.get("creator"):
        try:
            game = (creators.get(job["creator"]) or {}).get("game") or ""
        except Exception:  # noqa: BLE001
            game = ""
    if not game:
        game = games.detect(job["title"], src.get("name"))[0] or ""
    if game and job.get("game") != game:
        job["game"] = game
    t0 = time.time()
    job.setdefault("started_at", t0)
    notes = []
    face = False       # where the webcam is in the source, found once; False means not looked for yet
    usage.set_context(job["creator"], job["title"] or src.get("name"))
    resuming = bool(job.pop("resume", False)) and (d / "words.json").is_file()
    # "Clip anyway": the transcript is already written; only the moments are picked again, and the AI must return
    # the best there are instead of none
    forcing = bool(o.get("force")) and not resuming and (d / "words.json").is_file()
    if resuming:   # after a restart: keep the transcript parts, clips and AI answers already made, and carry on
        _set(job, status="running", stage="Picking up where it stopped", error=None)
    elif forcing:
        _set(job, status="running", stage="Getting the video again", progress=0.05, error=None, clips=[], done_blocks=0)
    else:
        _set(job, status="running", stage="Opening the link", step=1, started=time.time(), progress=0.02, error=None, clips=[], ai={}, done_blocks=0,
             candidates=[])
    speech = o.get("lang") if fetch_model.known(o.get("lang")) else fetch_model.DEFAULT   # the language spoken
    if not forcing:
        if speech == fetch_model.DEFAULT and not transcribe.model_ready() and not transcribe.REMOTE:
            _set(job, stage="Getting the speech model (one time, about 480 MB)")
            fetch_model.ensure()
            _set(job, stage="Getting the audio")
        could_borrow = src["type"] == "url" and config.YT_TRANSCRIPT and media.is_youtube(src["url"])
        if speech != fetch_model.DEFAULT and not transcribe.REMOTE and not could_borrow and fetch_model.status(speech) == "missing":
            fetch_model.ensure(speech, start=False)      # says what to do, before any time is spent on the download
    fetch = ThreadPoolExecutor(1)
    video = None
    vprog = {}                        # how far the background whole-video download is
    part_only = False                 # only the chosen part of the audio was fetched
    if src["type"] == "url":
        meta = media.info(src["url"])
        if meta["kind"] != "video":
            raise RuntimeError("That link is a channel or playlist, not a single video. Paste the link of one video.")
        if ((o["range_end"] or meta["duration"]) - (o["range_start"] or 0)) > config.MAX_VIDEO_HOURS * 3600:
            raise RuntimeError(f"That is longer than {config.MAX_VIDEO_HOURS:g} hours. Choose a shorter part of it under Options.")
        if meta["is_live"]:
            # ClipHeist: a stream that is on air has no end, so it can't be downloaded like a video. It is recorded
            # and clipped as it happens instead, and this card gives way to the recording. (When that can't start,
            # the reason is shown here: no clips left, too many recordings at once, and so on.)
            from . import live
            rec = live.start_from_link(src["url"], meta, watermark=bool(job.get("watermark")))
            raise HandedOver(rec["id"])
        _set(job, title=meta["title"], uploader=meta["uploader"], duration=meta["duration"], stage="Downloading the stream", step=2, progress=0.03)
        if not job.get("thumb") and media.save_thumb(d, url=src["url"]):
            _set(job, thumb=True)
        usage.set_context(job["creator"], meta["title"])
        if meta["duration"] > 0:
            if (o["range_start"] or 0) >= meta["duration"] - 5:
                raise RuntimeError(f"The start time is past the end of this video (it is {int(meta['duration'] // 60)} min long). "
                                   "Choose an earlier part under Options.")
            if o["range_end"] and o["range_end"] > meta["duration"]:
                o["range_end"] = meta["duration"]
        span = (o["range_end"] or meta["duration"]) - (o["range_start"] or 0)
        # long videos, and styles that zoom in (they need more than 1080p): only each clip's stretch is fetched,
        # at that quality, never the whole video. With Modal rendering, every clip's stretch is fetched there.
        pieces = (span > LONG_VIDEO or meta["duration"] > LONG_VIDEO or meta["duration"] <= 0 or int(o["quality"]) > 1080
                  or remote_render.enabled())
        if not pieces:  # shorter video: fetch all of it in the background while the audio is worked on
            video = fetch.submit(media.download, src["url"], d, int(o["quality"]), lambda p: vprog.update(p=p))
        part_only = bool(o["range_start"] or o["range_end"]) and not resuming and not forcing
        seen = [-1.0]

        def got(p):                       # the bar moves in whole percents: no need to save the file a hundred times a second
            if p - seen[0] >= 0.01 or p >= 1:
                seen[0] = p
                _set(job, progress=0.03 + 0.27 * p, stage=f"Downloading the stream · {int(p * 100)}%")

        audio_file = media.download_audio(src["url"], d, got, start=o["range_start"], end=o["range_end"]) if part_only else \
            media.download_audio(src["url"], d, got)
    else:
        pieces, audio_file = False, Path(src["path"])
        info = media.probe(audio_file)
        if info["duration"] > config.MAX_VIDEO_HOURS * 3600:
            raise RuntimeError(f"That video is longer than {config.MAX_VIDEO_HOURS:g} hours, which is the limit.")
        _set(job, source_path=str(audio_file), width=info["width"], height=info["height"], duration=info["duration"])
        if not job.get("thumb") and media.save_thumb(d, video=audio_file):
            _set(job, thumb=True)
    wav = d / "audio.wav"
    _set(job, stage="Preparing the audio", step=2, progress=max(job.get("progress", 0), 0.30))
    media.extract_audio(audio_file, wav)
    audio = media.load_audio(wav)
    wav.unlink(missing_ok=True)
    if part_only and o["range_start"]:
        import numpy as np
        # only the chosen part was fetched: put silence in front so every time still counts from the video's start
        audio = np.concatenate([np.zeros(int(o["range_start"] * 16000), dtype=audio.dtype), audio])
    if src["type"] == "url":
        audio_file.unlink(missing_ok=True)
    if not job["duration"]:
        job["duration"] = len(audio) / 16000
    lo = int((o["range_start"] or 0) * 16000)
    hi = min(len(audio), int(o["range_end"] * 16000)) if o["range_end"] else len(audio)
    span = (hi - lo) / 16000
    db = media.loudness_per_second(audio[lo:hi])

    vlock, vstate = threading.Lock(), {}

    def whole_video():
        """Wait for the background download once; retry it, or give up and use pieces."""
        with vlock:
            if "path" not in vstate:
                path = None
                try:
                    while not video.done():            # say what we're waiting for, never sit silent
                        p = vprog.get("p")
                        _set(job, stage="Downloading the video for your clips" + (f" · {int(p * 100)}%" if p else ""))
                        time.sleep(2)
                    path = video.result()
                except Exception as e:  # noqa: BLE001 - sites sometimes refuse a download
                    notes.append(f"Video download refused once ({str(e)[-80:]}), retrying")
                    try:
                        path = media.download(src["url"], d, int(o["quality"]))
                    except Exception as e2:  # noqa: BLE001
                        notes.append(f"Whole-video download failed ({str(e2)[-80:]}), fetching clip parts instead")
                if path:
                    info = media.probe(path)
                    job.update(source_path=str(path), width=info["width"], height=info["height"])
                vstate["path"] = path
            return vstate["path"]

    exact = {}      # clip id -> (from, to, words): our own model's words for just that clip

    began = {}      # clip id -> when work on it started

    def make(clip, words_now):
        began[clip["id"]] = time.time()
        if src["type"] == "url" and (pieces or not whole_video()):
            if remote_render.enabled():
                clip["remote"] = True        # fetched and rendered on Modal in render_one
            else:
                _fetch_piece(job, clip, audio)
        if clip["status"] != "error" and clip.get("captions", True) and (yt_model or windows):
            # The clip was found with YouTube's transcript, or with only parts of the audio transcribed. What is
            # shown on screen comes from our own model, run on just this clip: full punctuation, nothing blanked
            # out, timings to the word, and no gaps at the edges of a transcribed stretch.
            a0, b0 = max(lo, int((clip["start"] - 1.5) * 16000)), min(hi, int((clip["end"] + 1.5) * 16000))
            try:
                tw = [dict(w, s=round(w["s"] + a0 / 16000, 2), e=round(w["e"] + a0 / 16000, 2))
                      for w in transcribe.transcribe(audio[a0:b0], lang=yt_model or speech)]
            except Exception:  # noqa: BLE001 - the clip is still made, with YouTube's words
                tw = []
            if len(tw) >= 3:
                exact[clip["id"]] = (a0 / 16000, b0 / 16000, tw)
                words_now = tw
        if clip["status"] != "error":
            render_one(job, clip, words_now, audio)

    cpus = os.cpu_count() or 4
    # clips are made side by side: most of a clip's time is waiting (its piece downloading, the AI listening), not CPU.
    # With Modal doing the rendering this machine only waits, so a few more at once.
    renders = ThreadPoolExecutor((4 if cpus < 8 else min(8, cpus // 3)) if remote_render.enabled() else max(2, min(6, cpus // 3)))
    futures = []
    step = BLOCK * 16000 if span > PROGRESSIVE_FROM else hi - lo
    blocks = [(x, min(hi, x + step)) for x in range(lo, hi, step)] or [(lo, hi)]
    words, tokens, model = [], 0, None
    done_blocks = 0
    yt = None
    if src["type"] == "url" and config.YT_TRANSCRIPT and media.is_youtube(src["url"]) and not forcing:
        try:
            yt = media.youtube_words(src["url"])
        except Exception:  # noqa: BLE001 - no transcript to borrow: ours is written as usual
            yt = None
        if yt and not _covers(yt[0], lo / 16000, hi / 16000):
            yt = None
        job["transcript_from"] = "youtube" if yt else "own"
    # The language spoken decides the captions: YouTube's word for it when its transcript is used, else the choice
    # made. ("Clip anyway" keeps what the first run found.)
    if not forcing:
        job["lang"] = yt[1] if yt else speech
    # A clip found with YouTube's transcript gets its on-screen words from our own model, when there is one here
    # for the language YouTube says was spoken (English always; others once they are downloaded).
    yt_code = (yt[1].lower().split("-")[0] if yt else "")
    yt_model = yt_code if fetch_model.known(yt_code) and (yt_code == fetch_model.DEFAULT or fetch_model.ready(yt_code)) else ""
    if not yt and not forcing and speech != fetch_model.DEFAULT and not transcribe.REMOTE and not fetch_model.ready(speech):
        if fetch_model.state[speech]["doing"]:
            _set(job, stage=f"Waiting for the {fetch_model.label(speech)} speech model to finish downloading")
        fetch_model.ensure(speech, start=False)      # waits for a download already running, else says what to do
    # Long video with our own transcript: only the stretches around the loudest moments are transcribed
    # (PEAKCUT_SCAN_SHARE; 1, the default, transcribes everything).
    windows = brain.candidate_windows(db, config.SCAN_SHARE) if (not yt and not forcing and span > PROGRESSIVE_FROM) else None
    if windows == [(0, len(db))]:
        windows = None
    if windows:
        job["scanned_share"] = round(sum(b_ - a_ for a_, b_ in windows) / max(1, len(db)), 2)
    if forcing:
        try:
            words = json.loads((d / "words.json").read_text(encoding="utf-8"))
        except ValueError:
            words = []
    if resuming:
        try:
            words = json.loads((d / "words.json").read_text(encoding="utf-8"))
        except ValueError:
            words = []
        done_blocks = min(int(job.get("done_blocks", 0)), len(blocks)) if words else 0
        tokens, model = int((job.get("ai") or {}).get("tokens", 0)), (job.get("ai") or {}).get("model")
        if done_blocks < len(blocks):     # words past the last finished part are transcribed again
            cut = blocks[done_blocks][0] / 16000
            words = [w for w in words if w["s"] < cut]
        for c in job["clips"]:            # clips found before the restart that were not finished yet
            if c.get("status") not in ("done", "error", "dropped"):
                c["status"] = "queued"
                futures.append((renders.submit(ctx.bind(make), c, list(words)), c))
        save(job)
    # The transcript of the next part is written while the AI is still reading this one (the AI is a wait on the
    # network, the transcript is work on this machine), so neither waits for the other.
    ahead = ThreadPoolExecutor(2, thread_name_prefix="ahead")
    heard, now_on = {}, [done_blocks]

    def hear(i):
        if i < len(blocks) and i >= done_blocks and i not in heard:
            a_, b_ = blocks[i]
            last = [0.0]

            def tp(p, i=i, a_=a_, b_=b_):
                if now_on[0] == i and p - last[0] > 0.05:      # only the part being shown moves the bar
                    last[0] = p
                    _set(job, progress=max(job["progress"], 0.31 + 0.59 * ((a_ - lo) + p * (b_ - a_)) / max(1, hi - lo)))
            if windows:
                heard[i] = ahead.submit(ctx.bind(_hear_parts), audio, lo, a_, b_, windows, tp, speech)
            else:
                heard[i] = ahead.submit(ctx.bind(transcribe.transcribe), audio[a_:b_], tp, speech)

    # The ad breaks and static screens of the whole video, found once. It reads the video end to end,
    # so it is done here rather than for each clip, and only when asked for.
    statics = quality.static_ranges(job.get("source_path") or "") if config.SKIP_STATIC else []
    for bi, (a, b) in enumerate(blocks):
        if bi < done_blocks:
            continue                      # transcribed and checked by the AI before the restart
        shift = a / 16000
        where = f" (minute {round((a - lo) / 16000 / 60)} of {round(span / 60)})" if len(blocks) > 1 else ""
        if forcing:
            _set(job, stage="Looking closely for the best moments" + where, step=4)
            bw = [dict(w, s=round(w["s"] - shift, 2), e=round(w["e"] - shift, 2)) for w in words if a / 16000 <= w["s"] < b / 16000]
        elif yt:
            _set(job, stage="Reading the transcript" + where, step=3)
            bw = [dict(w, s=round(w["s"] - shift, 2), e=round(w["e"] - shift, 2)) for w in yt[0] if a / 16000 <= w["s"] < b / 16000]
        else:
            _set(job, stage="Writing the transcript" + where, step=3)
            now_on[0] = bi
            hear(bi)
            hear(bi + 1)
            bw = heard.pop(bi).result()
        _set(job, progress=max(job["progress"], 0.31 + 0.59 * (b - lo) / max(1, hi - lo)))
        if not forcing:
            words += [dict(w, s=round(w["s"] + shift, 2), e=round(w["e"] + shift, 2)) for w in bw]
            (d / "words.json").write_text(json.dumps(words), encoding="utf-8")
        _set(job, stage=("Looking closely for the best moments" if forcing else "Finding the best moments") + where, step=4, word_count=len(words))
        if len(bw) < 8:
            job["done_blocks"] = bi + 1
            save(job)
            continue
        # Every part of the video is read, and its best moments are kept as candidates. Only once the whole video
        # has been read are the strongest candidates made into clips, so a great moment near the end is not lost
        # to weaker ones found earlier.
        per_part = max(2, min(5, math.ceil(int(o["max_clips"]) * 1.5 / len(blocks)) + 1))
        s0 = int((a - lo) / 16000)
        found, ai = brain.find_clips(bw, db[s0: s0 + int((b - a) / 16000) + 1], (b - a) / 16000, job["title"],
                                     job["uploader"], per_part, int(o["min_len"]), int(o["max_len"]), notes.append,
                                     game=game, force=bool(o.get("force")), focus=o.get("focus", ""))
        tokens += ai.get("tokens", 0)
        model = ai.get("model") or model
        if ai.get("note"):
            notes.append(ai["note"])
        if found and statics:
            # A commercial or a "be right back" slate is not a clip. Any clip that sits mostly inside one
            # is dropped.
            keep = []
            for c in found:
                a_s, b_s = c["start"] + shift, c["end"] + shift
                inside = quality.static_overlap(a_s, b_s, statics)
                if inside > 0.5 * max(1.0, b_s - a_s):
                    notes.append(f"A clip from {round(a_s)}s was left out: it sat in an ad break or a static screen.")
                else:
                    keep.append(c)
            found = keep
        for c in found:
            c.update(start=round(c["start"] + shift, 2), end=round(c["end"] + shift, 2))
            job.setdefault("candidates", []).append(c)
        job["ai"] = {"model": model, "tokens": tokens, "note": "", "log": notes[-5:]}
        job["done_blocks"] = bi + 1       # a restart from here on carries on with the next part
        save(job)
    best = _best(job.get("candidates") or [], int(o["max_clips"]) - len(job["clips"]), job["clips"])
    if best:
        _set(job, stage=f"Rendering {len(best)} clips", step=5)
    for c in best:
        c.update(id=f"clip{len(job['clips']) + 1:02d}", style=o["style"], layout=o["layout"],
                 captions=o.get("captions", True), status="queued", version=0)
        if o.get("look"):
            c.update(look=o["look"], template=o.get("template", ""))
        if o.get("hook_text"):        # the person's own big text, on every clip, in place of the AI's
            c["hook"] = o["hook_text"]
        if o.get("description_text"):  # ...and their own description, exactly as written
            c["description"], c["fixed_description"] = o["description_text"], True
        else:                          # no description of their own: 3 to 5 fitting hashtags go at the end of the AI's
            hashtags.apply(c, words, job["title"], job["uploader"], game)
        # Only the gameplay layout uses a webcam, so it is only looked for then, and only once: a
        # stream with no webcam is not looked for again on every clip of it.
        if o["layout"] == "gameplay" and face is False:
            face = _facecam(job)
        if face:
            c["facecam"] = face
        job["clips"].append(c)
        futures.append((renders.submit(ctx.bind(make), c, list(words)), c))
    job["candidates"] = []
    save(job)
    if futures:
        _set(job, stage=f"Rendering {len(futures)} clip{'s' if len(futures) != 1 else ''}", step=5, progress=max(job["progress"], 0.90))
    _wait_for_clips(job, futures, began, renders._max_workers)
    if futures:
        _set(job, stage="Saving your clips", progress=max(job["progress"], 0.99))
    if exact:           # keep our own words for the clips in the saved transcript, so editing a clip later uses them
        for a_s, b_s, tw in exact.values():
            words = [w for w in words if not a_s <= w["s"] < b_s] + tw
        words.sort(key=lambda w: w["s"])
        (d / "words.json").write_text(json.dumps(words), encoding="utf-8")
    renders.shutdown(wait=False)
    ahead.shutdown(wait=False)
    fetch.shutdown(wait=False)
    o.pop("force", None)
    clips = [c for c in job["clips"] if c.get("status") != "dropped"]
    if not clips:
        job["empty_since"] = time.time()     # its source is kept a few days, for "Clip anyway"
    job["ai"] = {"model": model, "tokens": tokens, "log": notes[-5:],
                 "note": "The AI was unavailable, so clips were picked by loudness only." if clips and not model else ""}
    stage = "Done" if clips else "Done, but nothing clip-worthy was found"
    if forcing and not clips:
        stage = "Done, but there was no speech to clip"
    _set(job, status="done", stage=stage, progress=1.0, took=round(time.time() - t0))
    if not job.get("charged"):
        made = sum(1 for c in clips if c.get("status") == "done")
        try:
            from . import credits
            credits.spend(job["user"], made)
            job["charged"] = made
            save(job)
        except Exception:  # noqa: BLE001 - never lose the clips over a billing hiccup
            import traceback; traceback.print_exc()
    try:
        from . import notices
        name = job.get("title") or job["source"].get("name") or "your video"
        notices.add(job.get("user"), "clips", f"{len(clips)} clip{'s' if len(clips) != 1 else ''} ready" if clips else "Nothing clip-worthy found",
                    name, f"/app/creators/{job['creator']}" if job.get("creator") else "/app/videos")
    except Exception:  # noqa: BLE001
        pass
    creators.log(job["creator"], f"Video finished with {len(clips)} clips in {round(time.time() - t0)}s: {job['title']}")
    # Tidying up comes after "ready": the clips are done and announced the moment the last one is made, and only
    # then is the whole video cut down to each clip's own stretch (which can take a while on a long video).
    try:
        slim(job, audio)                 # only the clips are kept from here on (only with PEAKCUT_TIDY=1)
    except Exception:  # noqa: BLE001 - the video is done either way; housekeeping tries again later
        traceback.print_exc()
    del audio


def _worker(quick_only=False):
    while True:
        kind, a, cid = _q.get(quick_only)
        if kind == "fn":
            try:
                a()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            continue
        job = _jobs.get(a)
        if not job:
            if kind == "job":
                _q.done(a)
            continue
        token = ctx._uid.set(job["user"])  # everything this job does runs as its owner
        try:
            if kind == "job":
                _run_job(job)
            else:
                clip = next(c for c in job["clips"] if c["id"] == cid)
                try:
                    words = json.loads((Path(job["dir"]) / "words.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    words = []          # no transcript kept: the clip is made without one
                srcf = clip.get("src") or job.get("source_path") or ""
                if (job.get("source") or {}).get("type") == "url" and not (srcf and Path(srcf).is_file()):
                    # its stretch of the video is not here (its first try failed, or the video was tidied away):
                    # fetched again, cut at exactly the asked time
                    import numpy as np
                    clip.pop("src", None)
                    _fetch_piece(job, clip, np.zeros(0, dtype=np.int16))
                    if clip.get("status") == "error":
                        save(job)
                        continue
                render_one(job, clip, words)
        except HandedOver:
            delete(job["id"])                 # the recording has its own card
        except Cancelled:
            for c in job["clips"]:
                if c.get("status") in ("queued", "rendering"):
                    c.update(status="dropped", error=None)        # not made: gone, not shown as a failed clip
            _set(job, status="error", error=CANCELLED, stage="Cancelled", _final=True)
        except Exception as e:  # noqa: BLE001
            (Path(job["dir"]) / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
            if kind == "job":
                for c in job["clips"]:
                    if c.get("status") in ("queued", "rendering"):
                        c.update(status="error", error="The video stopped before this clip was made.")
                _set(job, status="error", error=safe.friendly(e), stage="Stopped")
                creators.log(job["creator"], f"Video failed: {safe.friendly(e)[:200]}")
        finally:
            ctx._uid.reset(token)
            if kind == "job":
                _q.done(a)


def _pick_up_after_restart():
    """Videos and single clips that were being made when the server stopped: queued again, oldest first."""
    waiting = sorted((j for j in all_jobs() if j.get("status") == "queued" and j.get("resume")), key=lambda j: j["created"])
    for j in waiting:
        _q.put(("job", j["id"], None), user=j["user"])
    for j in all_jobs():
        for cid in j.pop("remake", []):
            _q.put(("clip", j["id"], cid))
        if j.get("status") == "done":
            save(j)


def start():
    load.start()
    load_all()
    _pick_up_after_restart()
    for _ in range(max(1, config.JOB_WORKERS)):
        threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=_worker, args=(True,), daemon=True).start()   # re-making a single clip never waits behind whole videos
    for _ in range(max(1, config.LIVE_RENDER_WORKERS)):
        threading.Thread(target=_live_worker, daemon=True).start()
    threading.Thread(target=_housekeeping_loop, daemon=True).start()


# ---------------------------------------------------------------- same link, made once
# When anyone pastes a link that was already clipped with the same look (style, layout, quality, lengths, part of
# the video), the finished clips are copied into the new person's library at once instead of being made again.
SAME_LOOK = ("style", "layout", "quality", "min_len", "max_len", "range_start", "range_end")


def canonical_url(url: str) -> str:
    from urllib.parse import urlparse, parse_qs
    u = urlparse(url.strip())
    host = (u.netloc or "").lower().removeprefix("www.").removeprefix("m.")
    path = u.path.rstrip("/")
    if host == "youtu.be":
        return "yt:" + path.strip("/")
    if host.endswith("youtube.com"):
        v = parse_qs(u.query).get("v", [""])[0]
        if v:
            return "yt:" + v
        m = re.match(r"/(?:shorts|live|embed)/([\w-]+)", path)
        if m:
            return "yt:" + m.group(1)
    return f"{host}{path}".lower()


def find_ready(url: str, options: dict, watermark=False):
    """A finished video from the same link with the same look and enough clips, from any user."""
    key = canonical_url(url)
    want = {**DEFAULTS, **clean_options(options)}
    best = None
    with _lock:
        pool = list(_jobs.values())
    for j in pool:
        if j.get("status") != "done" or j.get("kind") != "video" or j.get("source", {}).get("type") != "url":
            continue
        if canonical_url(j["source"].get("url", "")) != key:
            continue
        o = j.get("options") or {}
        if any(o.get(k) != want.get(k) for k in SAME_LOOK) or bool(j.get("watermark")) != bool(watermark):
            continue
        done = [c for c in j.get("clips", []) if c.get("status") == "done" and c.get("src") and Path(c["src"]).is_file()]
        if not done or (len(done) < int(want["max_clips"]) and int(o.get("max_clips", 0)) < int(want["max_clips"])):
            continue
        if not best or len(done) > len(best[1]):
            best = (j, done)
    return best


def clone_ready(found, options: dict, creator=None, watermark=False) -> dict:
    """Copy finished clips into a new job for the current user. Returns the new job (already done)."""
    import shutil as _sh
    src_job, done = found
    want = {**DEFAULTS, **clean_options(options)}
    job = create(dict(src_job["source"]), options, enqueue=False, creator=creator)
    d = Path(job["dir"])
    (d / "clips").mkdir(exist_ok=True)
    sd = Path(src_job["dir"])
    for name in ("words.json", "clips/thumb.jpg"):
        if (sd / name).is_file():
            _sh.copyfile(sd / name, d / name)
    picked = sorted(done, key=lambda c: -float(c.get("score") or 0))[: int(want["max_clips"])]
    picked.sort(key=lambda c: c.get("start", 0))
    clips = []
    for c in picked:
        for f in (sd / "clips").glob(f"{c['id']}.*"):
            _sh.copyfile(f, d / "clips" / f.name)
        nc = {k: v for k, v in c.items()}
        if nc.get("src"):
            nc["src"] = str(d / "clips" / Path(nc["src"]).name)
        clips.append(nc)
    for k in ("title", "uploader", "duration", "width", "height", "lang", "transcript_from", "word_count"):
        if k in src_job:
            job[k] = src_job[k]
    job.update(clips=clips, thumb=(d / "clips" / "thumb.jpg").is_file(), status="done", progress=1.0, step=5,
               stage="Done · ready instantly (this link was already clipped)", took=0, reused=True, watermark=bool(watermark),
               ai={"model": (src_job.get("ai") or {}).get("model"), "tokens": 0, "note": ""})
    save(job)
    return job
