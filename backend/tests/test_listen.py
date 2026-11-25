"""A decided clip is heard by the AI once: it sets the exact cut on sentence boundaries (in a quiet moment, never
mid-word) and writes the captions from the sound. Only Gemini can hear sound: with any other AI service nothing is
sent and the local transcript is used. Chat speed counts on Kick too, and a jump is measured against the creator's
own setting. Checked without the AI (its answers are stand-ins)."""
import json, os, subprocess, sys, tempfile, time
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-listen-")
os.environ.update(DATABASE_URL="", PEAKCUT_VERIFY_EMAIL="0", PEAKCUT_AI_PROVIDER="gemini", PEAKCUT_AI_BASE_URL="",
                  PEAKCUT_AI_MODELS="text-model", PEAKCUT_AI_AUDIO_MODELS="ear-model", PEAKCUT_AI_KEY="k" * 40, PEAKCUT_AI_KEY_FILE="",
                  PEAKCUT_AI_FIX_MODELS="cheap-model", PEAKCUT_AI_FIX_THINKING="minimal",
                  PEAKCUT_AI_TIMING_MODEL="timing-model", PEAKCUT_AI_INTERACTIONS="https://ai.example.com/interactions")
sys.path.insert(0, ".")
from pathlib import Path   # noqa: E402
import numpy as np   # noqa: E402
from studio import ai, brain, config, jobs, live, profiles, usage   # noqa: E402
fails = []


def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)


# ---- words from the AI's phrases, timing leaned on the local transcript, the cut in a quiet moment
w = brain._phrase_words([{"text": "Bro what", "start": 1.0, "end": 1.6}, {"text": "is that?", "start": 1.6, "end": 2.2}], 30)
check("each phrase's words are spread over its time, in order", [(x["w"], x["s"]) for x in w], [("Bro", 1.0), ("what", 1.27), ("is", 1.6), ("that?", 1.8)])
local = [{"w": "bro", "s": 1.05, "e": 1.3}, {"w": "what", "s": 1.35, "e": 1.6}, {"w": "is", "s": 1.7, "e": 1.8}, {"w": "this", "s": 1.82, "e": 2.1}]
far = [dict(v, s=v["s"] + 7, e=v["e"] + 7) for v in w]                          # the AI's clock 7 seconds off
lean = brain._place(far, local, 0.5, 3.0)
check("the AI's words are put on the local transcript's clock, even when its own is seconds off",
      [(v["w"], v["s"]) for v in lean], [("Bro", 1.05), ("what", 1.35), ("is", 1.7), ("that?", 1.8)])
english = [{"w": t, "s": 0, "e": 0} for t in "Bro what is that my friend".split()]
spoken = [{"w": "क्या", "s": 1.0, "e": 1.5}, {"w": "है", "s": 1.5, "e": 2.0}, {"w": "यार", "s": 5.0, "e": 5.5}]
placed = brain._place(english, spoken, 0.5, 6.0)
check("with no words in common, the AI's own times stay (never spread out, which drifted)", placed, english)
pcm = np.full(16000 * 4, 3000, dtype=np.int16)
pcm[int(16000 * 2.4):int(16000 * 2.46)] = 0                       # a short silence at 2.4 s
check("the cut goes into the quietest moment nearby", abs(brain._quiet(pcm, 2.3, 2.2, 2.7) - 2.41) < 0.03)
check("no sound to look at: the cut stays where it was", brain._quiet(None, 2.3, 2.2, 2.7), 2.3)

answer = {}
brain.ask_ai = lambda prompt, schema=None, purpose="", audio=None, **k: (answer["now"], "m", {})
words = [{"text": t, "start": s, "end": s + 0.5} for t, s in (("So", 7.0), ("what happened", 7.5), ("was", 9.0), ("crazy.", 20.0), ("Later.", 31.0))]
answer["now"] = {"start": 6.8, "end": 31.6, "language": "en", "phrases": words}
got = brain.listen((b"x", "audio/ogg"), None, 40, 8.0, 30.0, 15, 60)
check("the AI's cut is used when it keeps the moment and the length", (got["start"], got["end"], got["lang"]), (6.8, 31.6, "en"))
check("...and the captions are its words inside the cut", " ".join(x["w"] for x in got["words"]), "So what happened was crazy. Later.")
local_lines = [{"w": t, "s": s_, "e": s_ + 0.5} for t, s_ in (("so", 7.0), ("what", 7.5), ("was", 9.0), ("crazy", 20.0), ("wow", 34.0), ("later", 38.0))]
answer["now"] = {"start": 7.0, "end": 21.0, "payoff_end": 34.2, "payoff": "the lift", "phrases": words}
g = brain.listen((b"x", "audio/ogg"), None, 40, 8.0, 30.0, 15, 60, local_lines)
check("an end before the payoff it named itself is moved to just after the payoff", (g["start"], g["end"]), (7.0, 34.5))
answer["now"] = {"start": 0.0, "end": 8.0, "phrases": words}
check("a cut that loses the picked moment is not taken", (lambda g: (g["start"], g["end"]))(brain.listen((b"x", "audio/ogg"), None, 40, 8.0, 30.0, 15, 60)), (8.0, 30.0))
answer["now"] = {"start": 7.0, "end": 39.5, "phrases": words}
check("one longer than allowed keeps its end (the payoff) and starts later", (lambda g: (g["start"], g["end"]))(brain.listen((b"x", "audio/ogg"), None, 40, 8.0, 30.0, 15, 25)), (14.5, 39.5))
answer["now"] = {"start": 7.0, "end": 31.6, "phrases": words, "hook": "speed tries 225 with no warm up"}
check("the heading is written again for the final cut", brain.listen((b"x", "audio/ogg"), None, 40, 8.0, 30.0, 15, 60)["hook"], "Speed tries 225 with no warm up")
answer["now"] = {"start": 1.0, "end": 38.0, "phrases": words}
check("a stretch someone asked for moves 4 seconds at most", (lambda g: (g["start"], g["end"]))(brain.listen((b"x", "audio/ogg"), None, 40, 8.0, 30.0, 5, 60, near=True)), (4.0, 34.0))
answer["now"] = {"start": 7.0, "end": 30.0, "phrases": []}
try:
    brain.listen((b"x", "audio/ogg"), None, 40, 8.0, 30.0, 15, 60)
    check("an answer with no words means nobody talks in the clip", False)
except brain.NoSpeech:
    check("an answer with no words means nobody talks in the clip", True)
p = brain.LISTEN.format(dur=40, a=8, b=30, why="", cut=brain.CUT_FREE.format(mn=15, mx=60), title="", hook="", transcript="")
check("the AI is told: speakers only, not the game; Spanish stays Spanish; Hindi and Urdu in Roman letters, never translated",
      all(x in p for x in ("Leave out game sounds", "Spanish stays Spanish", "Hindi and Urdu are written in Roman letters", "never translated",
                           "in the WHOLE audio")))
check("...and to hold the whole moment: listen past the end, never end before the payoff",
      all(x in p for x in ("Listen past the picked end", "never end before the payoff", "start later and drop setup")))
check("the picking rules say the same, and a forced pick must still be a real moment",
      ("Never end before the payoff" in brain.RULES, "return [] anyway" in brain.MUST_PICK), (True, True))
lines = [{"s": x, "text": "x"} for x in (0.0, 10.0, 25.0, 31.0, 50.0)]
wds = [{"w": "w", "s": x, "e": x + 0.4} for x in range(0, 90)]
check("a picked moment that runs too long keeps its end and loses setup",
      (lambda g: (g[0], 79.0 <= g[1] <= 81.0))(brain._snap({"start": 0.0, "end": 80.0}, lines, wds, 15, 60, 100)), (24.85, True))

# ---- sound is sent to the models that hear (Gemini only); the tokens are noted as AI use, never charged
import importlib   # noqa: E402
importlib.reload(brain)
sent, noted = [], []
ai.call = lambda model, prompt, schema, timeout=None, temperature=0.4, images=None, thinking="": (sent.append((model, None, thinking)) or ({"ok": 1}, 100, 20))
ai.hear = lambda model, prompt, audio, schema, timeout=None, temperature=0.4: (sent.append((model, audio, "")) or ({"ok": 1}, 1100, 100))
usage.record = lambda i, o, purpose: noted.append((i, o, purpose))
brain.ask_ai("hear this", brain.LISTEN_SCHEMA, purpose="Clip: listening for captions", audio=(b"ogg", "audio/ogg"))
check("sound goes to the models that hear", sent[-1], ("ear-model", (b"ogg", "audio/ogg"), ""))
check("...and its tokens are noted as AI use", noted[-1], (1100, 100, "Clip: listening for captions"))
brain.ask_ai("read this", purpose="x")
check("text still goes to the text models", sent[-1][0], "text-model")
brain.ask_ai("fix this", brain.FIX_SCHEMA, purpose="Clip: fixing misheard words", cheap=True)
check("the word-fixing pass goes to the cheapest model, with its lowest thinking", sent[-1][0::2], ("cheap-model", "minimal"))

# another AI service can't hear: nothing is sent, the caller carries on with the local transcript
real_provider = ai.provider
ai.provider = lambda: "openai"
sent.clear()
try:
    brain.ask_ai("hear this", brain.LISTEN_SCHEMA, purpose="Clip: listening for captions", audio=(b"ogg", "audio/ogg"))
    refused = False
except RuntimeError:
    refused = True
check("with another AI service, sound is refused at once and nothing is sent", (refused, sent, brain.can_listen(), jobs.can_listen()), (True, [], False, False))
try:
    brain.listen((b"x", "audio/ogg"), None, 40, 8.0, 30.0, 15, 60)
    refused = False
except RuntimeError:
    refused = True
check("...and listening to a clip is refused, so the clip keeps the local transcript", refused)
ai.provider = real_provider

# ---- a busy model is not waited on when there is another: the next one answers
real_models = ai.models
ai.models = lambda: ["text-model", "backup-model"]
tried = []


def busy_first(model, *a, **k):
    tried.append(model)
    if model == "text-model":
        raise RuntimeError("HTTP 503 busy")
    return {"ok": 4}, 1100, 100


ai.call = busy_first
t0 = __import__("time").time()
brain.ask_ai("x", purpose="p", audio=None)
check("when a model is busy the next one is asked straight away", (tried, __import__("time").time() - t0 < 1), (["text-model", "backup-model"], True))
check("...and the tokens of the one that answered are noted", noted[-1], (1100, 100, "p"))
tried[:] = []
gone = lambda model, *a, **k: (tried.append(model), (_ for _ in ()).throw(RuntimeError("HTTP 404 no longer available")))[1]   # noqa: E731
ai.call = gone
try:
    brain.ask_ai("x", purpose="p")
except RuntimeError:
    pass
check("a model that is gone for good is not asked again and again", tried, ["text-model", "backup-model"])
ai.models = real_models

# ---- every token the provider counts is noted
answers_ = iter([ai.Unreadable("cut off", 500, 20), ({"ok": 2}, 500, 60)])


def flaky(*a, **k):
    x = next(answers_)
    if isinstance(x, Exception):
        raise x
    return x


ai.call, noted[:] = flaky, []
got = brain.ask_ai("read this", purpose="Video: finding clips")
check("an answer that couldn't be read still counts as AI use, then the AI is asked again",
      (got[0], noted), ({"ok": 2}, [(500, 20, "Video: finding clips"), (500, 60, "Video: finding clips")]))
calls = []
ai.call = lambda *a, **k: (calls.append(1) or ({"ok": 3}, 10, 2))
usage.record = lambda *a: (_ for _ in ()).throw(RuntimeError("database hiccup"))
try:
    brain.ask_ai("read this", purpose="x")
except RuntimeError:
    pass
check("a hiccup noting the use never makes the AI be asked again", len(calls), 1)
usage.record = lambda i, o, purpose: noted.append((i, o, purpose))

# ---- a real file: the decided clip's sound is cut out and heard; the clip gets the new cut and the AI's words
d = Path(os.environ["PEAKCUT_DATA"]) / "j"
d.mkdir()
src = d / "src.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=60", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=60",
                "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
heard = {}


def fake_listen(sound, pcm, dur, a, b, mn, mx, local=(), near=False, title="", why="", hook="", **k):
    heard.update(type=sound[1], size=len(sound[0]), dur=dur, a=a, b=b, samples=len(pcm), local=len(local), near=near)
    return {"start": a - 1.2, "end": b + 0.8, "lang": "es", "words": [{"w": "¿Qué", "s": a - 1.0, "e": a - 0.6}, {"w": "pasó?", "s": a - 0.5, "e": a}]}


jobs.brain.listen = fake_listen
jobs.transcribe.transcribe = lambda pcm, lang=None: [{"w": "que", "s": 7.5, "e": 7.9}, {"w": "far", "s": 43.0, "e": 43.4}]   # the sound's own words
job = {"duration": 60.0, "options": {"min_len": 15, "max_len": 60}, "title": "x"}
clip = {"start": 20.0, "end": 40.0, "reason": "funny"}
jobs._listen(job, clip, src, 0.0, [{"w": "que", "s": 19.5, "e": 19.9}, {"w": "far", "s": 55.0, "e": 55.4}])
check("the AI hears the clip with 8 seconds before and 20 after, small and compressed", (heard["type"], round(heard["dur"], 1), heard["a"], heard["size"] < 48 * 16000 * 2 / 5), ("audio/ogg", 48.0, 8.0, True))
check("...the samples to find quiet moments come with it, and the words of that very sound for timing", (heard["samples"], heard["local"]), (48 * 16000, 2))
check("the clip takes the new cut", (clip["start"], clip["end"]), (18.8, 40.8))
check("...and keeps what was heard, on the video's clock", (clip["heard"]["lang"], [x["w"] for x in clip["heard"]["words"]], clip["heard"]["words"][0]["s"]), ("es", ["¿Qué", "pasó?"], 19.0))
near = {"start": 40.0, "end": 58.0, "asked": True}
jobs._listen(job, near, src, 0.0, [])
check("a clip someone asked for is heard as such, and never past the end of the video", (heard["near"], round(heard["dur"], 1)), (True, 28.0))

# ---- a clip nobody talks in (a break screen, music) is left out; one someone asked for is still made
def no_speech(*a, **k):
    raise brain.NoSpeech("nobody")


jobs.brain.listen = no_speech
jobs.config.LISTEN = True
job2 = {"id": "v", "dir": str(d), "duration": 60.0, "options": {"min_len": 15, "max_len": 60}, "title": "x", "source_path": str(src),
        "source": {"type": "url"}, "clips": [], "width": 320, "height": 180}
quiet = {"id": "c1", "start": 20.0, "end": 40.0, "status": "queued", "hook": "x"}
job2["clips"].append(quiet)
jobs.save = lambda j: None
jobs.render_one(job2, quiet, [])
check("a clip nobody talks in is left out, and not shown", (quiet["status"], jobs.public(job2)["clips"]), ("dropped", []))

# ---- chat: Kick's recent messages are counted (never kept), and a jump follows the creator's setting
pages = iter([{"data": {"messages": [{"id": f"m{i}"} for i in range(25)]}},          # the backlog: not counted
              {"data": {"messages": [{"id": f"m{i}"} for i in range(5, 30)]}},       # 5 new
              {"data": {"messages": [{"id": f"m{i}"} for i in range(12, 37)]}}])     # 7 new


def fake_get(url, accept="", limit=0):
    if url.endswith("/messages"):
        try:
            return "application/json", json.dumps(next(pages)).encode()
        except StopIteration:
            raise RuntimeError("no more")
    return "application/json", json.dumps({"id": 4242, "chatroom": {"id": 99}}).encode()


profiles._get = fake_get
live.Session.KICK_EVERY = 0.05
job = {"id": "k", "dir": str(d), "ai": {}, "clips": [], "source": {"name": "x live", "url": "https://kick.com/someone"}, "status": "live"}
s = live.Session(job, {"buffer_min": 20, "auto_jump_pct": 100})
s.start_chat()
time.sleep(1.2)
s.stop_flag.set()
check("Kick chat: new messages are counted, the backlog isn't", s.chat_total, 12)
check("...and the text of messages is never kept", all(isinstance(v, int) for v in s.chat.values()))

s2 = live.Session(dict(job, source={"url": "https://www.youtube.com/@x/live"}), {"buffer_min": 20, "auto_jump_pct": 100})
s2.db = [-30.0] * 200
s2.chat = __import__("collections").Counter({t: 2 for t in range(200)})
for t in range(192, 200):
    s2.chat[t] = 5                                                  # 150% busier than usual
s2.chat_total = sum(s2.chat.values())
s2._watch_spikes()
check("a jump uses the creator's setting: 150% busier is a jump at 100%", (s2.spike or {}).get("why", ""), lambda v: v.startswith("chat +"))
s3 = live.Session(dict(job, source={"url": "https://www.youtube.com/@x/live"}), {"buffer_min": 20, "auto_jump_pct": 200})
s3.db, s3.chat, s3.chat_total = s2.db, s2.chat, s2.chat_total
s3._watch_spikes()
check("...and is not one at 200%", s3.spike, None)

# ---- forced live clips (none for a while) still need a real moment: decent, with people talking
st = {"buffer_min": 20, "min_score": 80, "must_clip_min": 15, "live_per_hour": 4, "decide_mode": "both", "min_gap_min": 2,
      "ai_every_min": 1, "min_len": 15, "max_len": 60, "style": "bounce", "layout": "blur"}
sf = live.Session(dict(job, clips=[], source={"url": "https://www.youtube.com/@x/live", "name": "x"}, title="t", uploader="u", creator=None), st)
sf.started_at = time.time() - 3600
made = []
sf._add_clip = lambda c: made.append(c)
live.brain.judge_signals = lambda *a, **k: ([], 0)
sf.db = [-30.0] * 70
pick = {}
live.brain.judge_transcript = lambda *a, **k: ([dict(pick)], 0)
sparse = [{"w": "music", "s": float(t), "e": t + 0.3} for t in range(0, 60, 6)]               # 10 words in a minute
talky = [{"w": "word", "s": t / 2, "e": t / 2 + 0.3} for t in range(0, 120)]                 # a lot of talking
for words_, score, want, name in ((sparse, 45, 0, "a weak forced pick (45) is not made"),
                                  (sparse, 65, 0, "a decent forced pick where hardly anyone talks is not made"),
                                  (talky, 65, 1, "a decent forced pick with people talking is made")):
    sf.words, made[:] = words_, []
    pick.update(start=5.0, end=50.0, score=score, hook="h", title="t", description="", hashtags=[], reason="r", category="other")
    sf._ai_check(window=(0.0, 60.0))
    check(name, len(made), want)

# ---- the last pass: the captions proofread as one passage, every word kept on its own moment
said = [{"w": t, "s": i * 0.5, "e": i * 0.5 + 0.4} for i, t in enumerate("he pulled all those bobs over here".split())]
reply = {}
brain.ask_ai = lambda prompt, schema=None, purpose="", audio=None, temperature=0.4, cheap=False: (reply.update(prompt=prompt, cheap=cheap) or reply["out"], "m", {})
reply["out"] = ["He", "pulled", "all", "those", "mobs", "over", "here."]
got = brain.fix_heard(said, "we keep pulling mobs in this dungeon", "WoW dungeons")
check("a misheard word is fixed from what is being talked about; every word keeps its timing",
      ([w["w"] for w in got], [w["s"] for w in got]), (["He", "pulled", "all", "those", "mobs", "over", "here."], [w["s"] for w in said]))
check("...reading the whole caption as one passage, with the earlier talk and the title",
      ("he pulled all those bobs over here" in reply["prompt"], "pulling mobs" in reply["prompt"], "WoW dungeons" in reply["prompt"]), (True, True, True))
reply["out"] = ["he", "pulled", "all", "those", "enemies", "over", "here"]
check("a word that doesn't sound like what was heard is not taken", [w["w"] for w in brain.fix_heard(said, "", "")][4], "bobs")
reply["out"] = ["she", "pushed", "all", "these", "mobs", "over", "there"]
check("too many changes at once and the words stay as they were", [w["w"] for w in brain.fix_heard(said, "", "")], [w["w"] for w in said])
reply["out"] = ["he", "pulled"]
check("an answer of the wrong length changes nothing", brain.fix_heard(said, "", ""), said)
fast = [{"w": t, "s": i * 0.5, "e": i * 0.5 + 0.4} for i, t in enumerate("we going win this base ball game".split())]
reply["out"] = ["we", "going to", "win", "this", "baseball", "", "game"]
got = brain.fix_heard(fast, "", "")
check("a word said too fast is added beside it, sharing its moment", [w["w"] for w in got], ["we", "going", "to", "win", "this", "baseball", "game"])
check("...and a word split in two is joined, taking both moments; nothing moves out of order",
      (got[1]["s"] == fast[1]["s"] and got[2]["e"] <= fast[1]["e"] + 0.01, got[5]["s"] == fast[4]["s"] and got[5]["e"] == fast[5]["e"],
       all(got[i]["s"] <= got[i + 1]["s"] for i in range(len(got) - 1))), (True, True, True))
reply["out"] = ["we", "going", "lose", "this", "base", "ball", "game"]
check("one wrong swap is dropped, not the whole answer", [w["w"] for w in brain.fix_heard(fast, "", "")][2], "win")
nums = [{"w": t, "s": i * 0.5, "e": i * 0.5 + 0.4} for i, t in enumerate("he got eleven kills in round two".split())]
reply["out"] = ["He", "got", "11", "kills", "in", "round", "2."]
check("numbers become digits", [w["w"] for w in brain.fix_heard(nums, "", "")][2::4], ["11", "2."])

# ---- live: the clip's place on the recording's clock is found even when the clocks have slipped far apart
words_live = [{"w": t, "s": 3600.0 + i * 0.4, "e": 3600.3 + i * 0.4} for i, t in enumerate(
    ("mana shield dazed dismounted pulled mobs bring these over here burning going all the way andre firebird quest "
     "pulling those mobs bringing them here pretty big pool way over twenty mobs").split())]
heard_clip = [{"w": w["w"], "s": round(w["s"] - 3600.0 - 6.0, 2), "e": 0} for w in words_live[12:]]   # the clip starts 6 s into those words
sa = live.Session(dict(job, clips=[], source={"url": "https://www.twitch.tv/x", "name": "x"}), {"buffer_min": 20})
sa.words = words_live
live.media.extract_audio = lambda src, wav: None
live.media.load_audio = lambda wav: np.zeros(16000, dtype=np.int16)
live.transcribe.transcribe = lambda audio, lang=None: heard_clip
check("the clip is placed by its own words even 25 s from where the clocks say", round(sa._align(Path(str(d / "x.ts")), 3581.0, 20.0), 2), 3606.0)
check("...and with nothing to line up, the clock's guess is kept", (setattr(live.transcribe, "transcribe", lambda a, lang=None: []), sa._align(Path(str(d / "x.ts")), 3581.0, 20.0))[1], 3581.0)

# ---- emoji on about one clip in three: the best clips keep theirs
from studio import render   # noqa: E402
many = {"id": "m", "dir": str(d), "duration": 60.0, "options": {"min_len": 15, "max_len": 60}, "title": "x", "source_path": str(src),
        "source": {"type": "url"}, "width": 320, "height": 180,
        "creator": None, "campaign": "",
        "clips": [{"id": f"c{i}", "hook": f"Heading number {i} 💀", "score": 90 - i, "start": 5.0, "end": 25.0, "status": "done",
                   "title": "t", "description": "", "hashtags": []} for i in range(6)]}
jobs.brain.listen = no_speech
seen_hooks = []
render.render_clip = lambda src_, out, cid, clip, words, w, h, extras=None: seen_hooks.append(clip["hook"]) or {"file": "x.mp4"}
jobs.brain.english_captions = lambda raw, *a, **k: raw
for c in many["clips"]:
    c.update(status="queued", asked=True)
for c in many["clips"]:
    jobs.render_one(many, c, [])
check("in a video of 6 clips, only the 2 best keep their emoji", sum(1 for h in seen_hooks if render.split_hook(h)[1]), 2)
check("...and they are the best two", [render.split_hook(h)[1] != [] for h in seen_hooks], [True, True, False, False, False, False])
check("emoji pictures can come from a folder, named by their code points", (lambda: (setattr(config, "EMOJI_DIR", str(d)),
      (d / "1f62d.png").write_bytes(b"x"), render._emoji_file("\U0001F62D") == d / "1f62d.png")[-1])())

# ---- every clip keeps its captions, in every language, on the exact moments they are said
importlib.reload(brain)
answer2 = {}
brain.ask_ai = lambda prompt, schema=None, purpose="", audio=None, **k: (answer2["now"], "m", {})
whole = [{"text": t_, "start": s_, "end": s_ + 0.6} for t_, s_ in (("Before", 2.0), ("the", 5.0), ("cut", 9.0), ("inside", 20.0), ("after", 38.0))]
answer2["now"] = {"start": 8.5, "end": 40.3, "language": "en", "spoken": "en", "phrases": whole}
g = brain.listen((b"x", "audio/ogg"), None, 40.0, 8.0, 30.0, 15, 60)
check("a cut a moment past the end of the sound is taken, not thrown away", (g["start"], g["end"]), (8.5, 40.0))
check("...and has its words, from the AI writing down the whole sound", [w["w"] for w in g["words"]], ["cut", "inside", "after"])
spanish = [{"text": t_, "start": s_, "end": s_ + 0.4} for t_, s_ in (("hola", 9.0), ("amigo", 12.0), ("qué", 15.0), ("tal", 20.0))]
exact = [{"w": t_, "s": s_, "e": s_ + 0.3} for t_, s_ in (("hola", 10.0), ("amigo", 10.5), ("qué", 11.0), ("tal", 11.4))]
asked = []
answer2["now"] = {"start": 8.0, "end": 30.0, "language": "es", "spoken": "es", "phrases": spanish}
g = brain.listen((b"x", "audio/ogg"), None, 40.0, 8.0, 30.0, 15, 60, retime=lambda lang: asked.append(lang) or exact)
check("words not in English are timed by the speech-to-text model, in their language", (asked, [w["s"] for w in g["words"]], g["lang"]), (["es"], [10.0, 10.5, 11.0, 11.4], "es"))
asked[:] = []
hindi = [{"w": t_, "s": s_, "e": s_ + 0.3} for t_, s_ in (("भाई", 10.0), ("क्या", 10.4), ("है", 10.8))]
brain.translate_caption_words = lambda ws, lang="", title="": [{"w": "Bro,", "s": 10.0, "e": 10.5}, {"w": "what", "s": 10.5, "e": 10.8}, {"w": "is", "s": 10.8, "e": 11.0}, {"w": "this?", "s": 11.0, "e": 11.1}]
answer2["now"] = {"start": 8.0, "end": 30.0, "language": "en", "spoken": "hi", "phrases": [{"text": "something else", "start": 9.0, "end": 9.5}]}
g = brain.listen((b"x", "audio/ogg"), None, 40.0, 8.0, 30.0, 15, 60, retime=lambda lang: asked.append(lang) or hindi)
check("Hindi: the exact words said, each in Roman letters (Hinglish) on its own moment",
      (asked, " ".join(w["w"] for w in g["words"]), [w["s"] for w in g["words"]], g["lang"]), (["hi"], "bhai kya hai", [10.0, 10.4, 10.8], "hi"))
check("English needs no extra timing call", (asked.clear(), answer2.update(now={"start": 8.0, "end": 30.0, "spoken": "en", "phrases": whole}),
      brain.listen((b"x", "audio/ogg"), None, 40.0, 8.0, 30.0, 15, 60, retime=lambda lang: asked.append(lang) or exact), asked)[-1], [])
check("captions that should be English but came out Hinglish are spotted", (brain.looks_hinglish([{"w": x} for x in "bhai ye kya hai yaar dekho".split()]),
      brain.looks_hinglish([{"w": x} for x in "bro what is this man look".split()])), (True, False))

# ---- the speech-to-text model's word times are read and noted (Gemini only)
import httpx   # noqa: E402
reply_ = {"usage": {"total_input_tokens": 501, "total_output_tokens": 64, "input_tokens_by_modality": [{"modality": "audio", "tokens": 500}]},
          "steps": [{"content": [{"text": "Hola amigo", "annotations": [
              {"type": "word_info", "text": "Hola", "start_offset": "0.400s", "end_offset": "1.300s"},
              {"type": "word_info", "text": "amigo", "start_offset": "1.300s", "end_offset": "2s"}]}]}]}
posted = []


class R:
    status_code = 200

    def json(self):
        return reply_


httpx.post = lambda url, json=None, headers=None, timeout=0: posted.append((url, json)) or R()
noted2 = []
brain.usage.record = lambda i, o, purpose: noted2.append((i, o, purpose))
w2 = brain.word_times((b"ogg", "audio/ogg"), "es")
check("the speech-to-text model's words come back with their start and end", w2, [{"w": "Hola", "s": 0.4, "e": 1.3}, {"w": "amigo", "s": 1.3, "e": 2.0}])
check("...asked for word timings, in the language heard", (posted[-1][0], posted[-1][1]["generation_config"]["transcription_config"]),
      ("https://ai.example.com/interactions", {"mode": {"type": "verbatim", "timestamp_granularities": ["word"]}, "language_codes": ["es"]}))
check("...and noted as AI use", noted2[-1], (501, 64, "Clip: timing the words"))
ai.provider = lambda: "openai"
posted.clear()
try:
    brain.word_times((b"ogg", "audio/ogg"), "es")
    refused = False
except RuntimeError:
    refused = True
check("with another AI service there is no word-timing model: nothing is sent", (refused, posted), (True, []))
ai.provider = real_provider

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
import shutil; shutil.rmtree(os.environ["PEAKCUT_DATA"], ignore_errors=True)   # noqa: E702,E402
sys.exit(1 if fails else 0)
