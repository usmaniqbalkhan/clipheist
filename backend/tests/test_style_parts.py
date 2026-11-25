"""A style can turn off the captions or the heading. What a clip's style doesn't show is never asked of the AI, so
nobody pays for it: no proofreading, word timing or translation of captions that aren't drawn, and no heading
written for a clip without one. Only that clip: a clip whose style shows them gets them as before."""
import os, sys, tempfile
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-parts-")
os.environ.update(DATABASE_URL="", PEAKCUT_VERIFY_EMAIL="0")
sys.path.insert(0, ".")
from pathlib import Path   # noqa: E402
import numpy as np   # noqa: E402
from studio import brain, config, jobs, templates, transcribe   # noqa: E402
fails = []


def check(n, got, want=True):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)


def look(caps=True, head=True):
    return templates.clean_look({"cap": {"show": caps}, "hook": {"show": head}})


check("a style keeps a captions switch, on unless turned off", (look()["cap"]["show"], look(caps=False)["cap"]["show"],
                                                              templates.clean_look({})["cap"]["show"]), (True, False, True))
check("shows(): what the clip's own style draws", jobs.shows({}, {"look": look(caps=False)}), (False, True))
check("...else the video's style", jobs.shows({"options": {"look": look(head=False)}}, {}), (True, False))
check("...and a clip from before styles shows both", jobs.shows({}, {}), (True, True))

# ---- the AI calls, as stand-ins: which ones a clip makes
calls = []
heard_words = [{"w": t, "s": 1.0 + i, "e": 1.5 + i} for i, t in enumerate("so what happened was crazy".split())]


def fake_listen(*a, **k):
    calls.append("listen")
    calls.append(("heading asked", k.get("heading")))
    if k.get("retime"):
        calls.append("timing")
    return {"start": 0.5, "end": 20.0, "lang": "en", "words": list(heard_words), "hook": "" if k.get("heading") is False else "New heading here"}


brain.listen = fake_listen
brain.fix_heard = lambda w, *a, **k: calls.append("proofread") or [dict(x, w=x["w"].upper()) for x in w]
brain.translate_captions = lambda w, *a, **k: calls.append("translate") or (w, "")
brain.english_captions = lambda w, *a, **k: calls.append("tidy") or w
transcribe.transcribe = lambda pcm: []
jobs._clip_sound = lambda src, a, b: ((b"x", "audio/ogg"), np.zeros(16000, dtype=np.int16))
drawn = {}
jobs.render.render_clip = lambda src, out_dir, cid, local, near, w, h, extras=None: drawn.update(words=near, hook=local.get("hook")) or {"file": "c.mp4", "cover": "c.jpg"}
config.LISTEN = True
jobs.ai.ready = lambda: True          # the AI in use is Gemini: only Gemini hears a clip's sound
jobs.ai.provider = lambda: "gemini"
d = Path(tempfile.mkdtemp())
(d / "clips").mkdir()
src = d / "v.mp4"
src.write_bytes(b"x")


def make(lk, tl=""):
    calls.clear()
    clip = {"id": "c1", "start": 1.0, "end": 19.0, "score": 80, "hook": "Old heading here", "title": "t", "description": "", "hashtags": [], "look": lk}
    job = {"id": "j", "dir": str(d), "source_path": str(src), "duration": 60, "clips": [clip], "creator": None,
           "options": {"cap_lang": tl}, "lang": "en", "title": "x", "width": 1920, "height": 1080}
    jobs.save = lambda j: None
    jobs.render_one(job, clip, [])
    return clip


c = make(look())
check("captions and heading on: heard, timed... proofread, heading written for the cut", (calls, c["hook"]),
      (["listen", ("heading asked", True), "timing", "proofread"], "New heading here"))
check("...and the captions are drawn", len(drawn["words"]) > 0)
c = make(look(caps=False))
check("captions off: no proofreading, no word timing", calls, ["listen", ("heading asked", True)])
check("...and no captions drawn", drawn["words"], [])
c = make(look(caps=False), tl="es")
check("captions off: no translation either", "translate" in calls, False)
c = make(look(), tl="es")
check("captions on in Spanish: translated", "translate" in calls, True)
c = make(look(head=False))
check("heading off: the AI is told to write none, and the earlier heading stays", (calls[:2], c["hook"]),
      (["listen", ("heading asked", False)], "Old heading here"))
p = brain.LISTEN.format(dur=40, a=8, b=30, why="", cut="", title="", transcript="",
                        hook="\nThis clip has no heading: skip step 5 and leave hook empty.")
check("...in its instructions", "skip step 5" in p, True)

# a clip heard while its captions were off, then made again with them on: proofread then, once
c = make(look(caps=False))
check("heard with captions off: marked not proofread", c["heard"].get("raw"), True)
calls.clear()
c["look"] = look()
c.pop("version", None)
job = {"id": "j", "dir": str(d), "source_path": str(src), "duration": 60, "clips": [c], "creator": None, "options": {},
       "lang": "en", "title": "x", "width": 1920, "height": 1080}
jobs.render_one(job, c, [])
check("turned on later: proofread then, without hearing it again", (calls, c["heard"].get("raw")), (["proofread"], None))
calls.clear()
jobs.render_one(job, c, [])
check("...and only once", calls, [])

# another AI service can't hear sound: the clip is made from the local transcript, and nothing is sent
jobs.ai.provider = lambda: "openai"
c = make(look())
check("with an AI that can't hear (not Gemini), the clip is never sent to be heard", "listen" in calls, False)
check("...and is still made", (c.get("status"), c.get("error")), ("done", None))

print(f"\n{len(fails)} failed" if fails else "\nall passed")
sys.exit(1 if fails else 0)
