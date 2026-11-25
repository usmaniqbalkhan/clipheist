"""ClipHeist: a pasted link that is a stream on air is recorded and clipped as it happens, instead of failing as
"a live stream never finishes downloading". Runs against the code directly, with its own empty data folder; the
network, the recorder and the clip balance are replaced, so nothing outside it is touched."""
import os
import sys
import tempfile

os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-livelink-")
os.environ.update(DATABASE_URL="", PEAKCUT_ACCOUNTS="0", PEAKCUT_AI_KEY="", PEAKCUT_AI_KEY_FILE="", PEAKCUT_AI_MODELS="",
                  PEAKCUT_PUBLIC_URL="http://127.0.0.1:8765", SUPABASE_URL="", SUPABASE_SECRET_KEY="", DEEPSEEK_API_KEY="",
                  PEAKCUT_ASR_URL="http://speech.invalid", PEAKCUT_ASR_TOKEN="x", PEAKCUT_RENDER_URL="")
sys.path.insert(0, ".")
from fastapi.testclient import TestClient  # noqa: E402

from studio import creators, credits, ctx, db, jobs, live, media, server  # noqa: E402

db.init()
fails = []
BASE = "http://127.0.0.1:8765"
ORIGIN = {"Origin": BASE}
c = TestClient(server.app, base_url=BASE, follow_redirects=False)


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    ok or fails.append(name)


# ---- nothing real: the recorder never starts ffmpeg, links are "looked up" from this table
started = []
live.Session.start = lambda self: (live.SESSIONS.__setitem__(self.job["id"], self), started.append(self.job["id"]))
LINKS = {
    "https://www.twitch.tv/somestreamer": {"title": "GTA 6 day one", "uploader": "SomeStreamer", "duration": 0.0, "is_live": True,
                                           "thumbnail": "", "kind": "video", "channel_url": ""},
    "https://www.youtube.com/watch?v=LIVE1234567": {"title": "Heist stream", "uploader": "Heist TV", "duration": 0.0, "is_live": True,
                                                    "thumbnail": "", "kind": "video", "channel_url": "https://www.youtube.com/@heisttv"},
    "https://www.youtube.com/watch?v=VOD12345678": {"title": "Old video", "uploader": "Heist TV", "duration": 600.0, "is_live": False,
                                                    "thumbnail": "", "kind": "video", "channel_url": "https://www.youtube.com/@heisttv"},
    "https://www.youtube.com/watch?v=NOCHANNEL01": {"title": "Mystery stream", "uploader": "", "duration": 0.0, "is_live": True,
                                                    "thumbnail": "", "kind": "video", "channel_url": ""},
}
media.info = lambda url: dict(LINKS[url])
media.check_youtube = lambda url: None
jobs.enqueue = lambda job: None                     # a queued video is not worked on here
me = c.get("/api/status").json()["user"]["id"]


def paste(url):
    return c.post("/api/jobs", json={"url": url}, headers=ORIGIN)


# ---- a Twitch link seen as live while it was typed: recorded at once
tw = "https://www.twitch.tv/somestreamer"
media._PREVIEWS[tw] = {"title": "GTA 6 day one", "uploader": "SomeStreamer", "duration": 0.0, "is_live": True, "thumb": ""}
r = paste(tw)
j = r.json()
check("a live Twitch link is accepted", r.status_code, 200)
check("...as a recording, not a video to download", (j.get("kind"), j.get("status")), ("live", "live"))
check("the recorder was started for it", started, [j.get("id")])
with ctx.as_user(me):
    mine = creators.all_creators()
check("the streamer got a workspace of their own, named after the channel",
      [(x["name"], x["channels"].get("twitch"), x["auto_watch"]) for x in mine], [("SomeStreamer", "https://www.twitch.tv/somestreamer", False)])
check("the recording is filed under that streamer", j.get("creator"), mine[0]["id"] if mine else None)
r2 = paste(tw)
check("pasting the same live link again shows the recording already running", (r2.status_code, r2.json().get("id"), len(started)), (200, j.get("id"), 1))
with ctx.as_user(me):
    check("...and makes no second workspace", len(creators.all_creators()), 1)

# ---- a link that is not live goes the usual way
vod = "https://www.youtube.com/watch?v=VOD12345678"
media._PREVIEWS[vod] = {"title": "Old video", "uploader": "Heist TV", "duration": 600.0, "is_live": False, "thumb": ""}
r = paste(vod)
check("a finished video is still queued as a video", (r.status_code, r.json().get("kind"), r.json().get("status"), len(started)), (200, "video", "queued", 1))

# ---- a live link nobody looked up first (YouTube's quick lookup can't tell): handed over when its turn comes
yt = "https://www.youtube.com/watch?v=LIVE1234567"
r = paste(yt)
stand_in = r.json()
check("a live YouTube link not seen as live yet is queued like any link", (r.status_code, stand_in.get("kind")), (200, "video"))
with ctx.as_user(me):
    job = jobs.get(stand_in["id"])
    try:
        jobs._run_job(job)
        handed = None
    except jobs.HandedOver as e:
        handed = str(e)
    rec = jobs.get(handed) if handed else None
    check("when its turn comes it is handed over to a recording", bool(rec) and (rec["kind"], rec["status"]), ("live", "live"))
    check("the recording is of that broadcast, not of the whole channel", rec and rec["source"]["url"], yt)
    check("...filed under the channel yt-dlp names", rec and (creators.get(rec["creator"]) or {}).get("channels", {}).get("youtube"), "https://www.youtube.com/@heisttv")
    check("the recorder was started once more", len(started), 2)

# ---- when a recording can't start, the person is told why
nc = "https://www.youtube.com/watch?v=NOCHANNEL01"
media._PREVIEWS[nc] = {"title": "Mystery stream", "uploader": "", "duration": 0.0, "is_live": True, "thumb": ""}
r = paste(nc)
check("a stream whose channel can't be worked out is refused in plain words", (r.status_code, "Streamers" in r.json().get("detail", "")), (400, True))

for s in list(live.SESSIONS.values()):
    s.user_stopped = True                      # the first two recordings are over
credits.profile = lambda uid=None, fresh=False: {"plan": "free", "left": 0, "watermark": True, "unlimited": False}
tw2 = "https://www.twitch.tv/another"
LINKS[tw2] = dict(LINKS[tw], uploader="Another")
media._PREVIEWS[tw2] = {"title": "x", "uploader": "Another", "duration": 0.0, "is_live": True, "thumb": ""}
r = paste(tw2)
check("with no clips left a recording does not start", (r.status_code, len(started)), (402, 2))

# ---- the free plan: watermark on the recording's clips, and each clip comes off the balance as it is made
credits.profile = lambda uid=None, fresh=False: {"plan": "free", "left": 2, "watermark": True, "unlimited": False}
r = paste(tw2)
fj = r.json()
with ctx.as_user(me):
    fjob = jobs.get(fj["id"]) if r.status_code == 200 else None
check("with clips left it starts", (r.status_code, len(started)), (200, 3))
check("free-plan recordings carry the watermark", bool(fjob and fjob.get("watermark")), True)
spent, stops = [], []
credits.enabled = lambda: True
credits.spend = lambda uid, n: spent.append(n)
ses = live.SESSIONS.get(fj.get("id"))
if ses:
    ses.user_stopped = False
    ses.stop = lambda note="": stops.append(note)
    credits.on_clip(fjob, {"id": "clip01"})
    check("a finished live clip takes one clip off the balance", (spent, fjob.get("charged")), ([1], 1))
    check("...and the recording carries on while clips are left", stops, [])
    credits.profile = lambda uid=None, fresh=False: {"plan": "free", "left": 0, "watermark": True, "unlimited": False}
    credits.on_clip(fjob, {"id": "clip02"})
    check("when the last clip is used the recording is stopped, with the reason", (spent, len(stops), "out of clips" in (stops or [""])[0]), ([1, 1], 1, True))
credits.on_clip({"kind": "video", "user": me, "id": "v1"}, {"id": "clip01"})
check("clips of ordinary videos are not charged one by one (they are charged when the video is done)", spent, [1, 1])

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
