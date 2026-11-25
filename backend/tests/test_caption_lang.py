"""Caption language: a video, an upload or a streamer can have its captions in a language picked from a list (or as
spoken). The translation is by meaning, line by line: each translated line is shown only while its original line is
said (never early, never late), its words spread over that time; the heading is translated too; a reply that doesn't
match the lines changes nothing. No AI is called (it is stood in for)."""
import os, sys, tempfile
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-lang-"); os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from studio import auth, brain, creators, ctx, db, jobs   # noqa: E402
db.init()
fails = []


def check(n, ok, x=""):
    print(("PASS " if ok else "FAIL ") + n + (f"  {x}" if x and not ok else "")); ok or fails.append(n)


# two sentences in Spanish, with a pause between them
said = [{"w": w, "s": s, "e": s + 0.3} for w, s in [("¿Qué", 0.0), ("hiciste", 0.4), ("hermano?", 0.8),
                                                    ("No", 3.0), ("puedo", 3.4), ("creerlo.", 3.8)]]
lines = brain.make_lines(said)
reply = {}
brain.ask_ai = lambda prompt, schema=None, purpose="", audio=None, temperature=0.4, cheap=False: (reply.update(prompt=prompt) or reply["out"], "m", {})
reply["out"] = {"lines": ["What did you do, bro?", "I can't believe it."][:len(lines)] if len(lines) == 2 else [" ".join(["What did you do, bro? I can't believe it."])], "hook": "He actually did it"}
got, hook = brain.translate_captions(said, "en", "Clutch play", "Lo hizo de verdad")
check("the captions come out in the language picked", " ".join(w["w"] for w in got).startswith("What did you do, bro?"))
check("...translated by meaning: the AI reads it all and is told never to go word by word",
      "never word by word" in reply["prompt"] and "Clutch play" in reply["prompt"])
for l, t in zip(lines, reply["out"]["lines"]):
    mine = [w for w in got if w["w"] in t.split()]
    check(f"the line “{t}” is shown only while its own line is said",
          bool(mine) and min(w["s"] for w in mine) >= l["s"] - 0.01 and max(w["e"] for w in mine) <= max(l["e"], l["s"] + 0.15 * len(t.split())) + 0.01,
          str((l, mine)))
check("words never go backwards", all(got[i]["s"] <= got[i + 1]["s"] for i in range(len(got) - 1)))
check("the heading is translated too", hook == "He actually did it")
reply["out"] = {"lines": ["only one line"] * (len(lines) + 1)}
try:
    brain.translate_captions(said, "en")
    check("a reply that doesn't match the lines is refused", False)
except RuntimeError:
    check("a reply that doesn't match the lines is refused", True)
reply["out"] = {"lines": ["yaar ye kya kiya"] * len(lines), "hook": ""}
brain.translate_captions(said, "rom")
check("Hinglish is asked for in Roman letters", "Roman letters, the way people type" in reply["prompt"])
brain.translate_captions(said, "ur")
check("Urdu is asked for in Urdu script", "Urdu script" in reply["prompt"])
brain.translate_captions(said, "hi")
check("Hindi is asked for in Devanagari", "Devanagari" in reply["prompt"])
check("the list starts with Hinglish, then English, Urdu and Hindi", list(brain.CAP_LANGS)[:4] == ["rom", "en", "ur", "hi"])
from studio import render   # noqa: E402
uw = [{"w": w, "s": i * 0.5, "e": i * 0.5 + 0.4} for i, w in enumerate("یار یہ کیا ہے".split())]
ass = render.build_ass(uw, 0, 3, "", "bounce", [], 400, 1400, script="ur")
check("Urdu captions are drawn with an Urdu font, a whole line at a time",
      "Noto Nastaliq Urdu" in ass and "یار {\c&H00F0FF&}یہ" in ass and "\pos(540," in ass, ass[-400:])
hw = [{"w": w, "s": i * 0.5, "e": i * 0.5 + 0.4} for i, w in enumerate("यार यह क्या है".split())]
check("Hindi captions are drawn with a Devanagari font", "Noto Sans Devanagari" in render.build_ass(hw, 0, 3, "", "bounce", [], 400, 1400, script="hi"))
check("a language that isn't on the list changes nothing", brain.translate_captions(said, "xx")[0] == said)

# the language is kept per video and per streamer
u, _ = auth._create("lang@example.com", "L", password_hash="x")
with ctx.as_user(u.id):
    j = jobs.create({"type": "file", "path": "", "name": "v.mp4"}, {"cap_lang": "es"}, enqueue=False)
    check("a video can pick its caption language", j["options"]["cap_lang"] == "es")
    j = jobs.create({"type": "file", "path": "", "name": "v.mp4"}, {"cap_lang": "klingon"}, enqueue=False)
    check("a language that isn't on the list means as spoken", j["options"]["cap_lang"] == "")
    c = creators.create({"name": "S", "channel": "https://www.twitch.tv/someone"})
    creators.update(c["id"], {"cap_lang": "en", "quality": 1080})
    j = jobs.create({"type": "url", "url": "https://x"}, {}, enqueue=False, creator=c["id"])
    check("a streamer's videos take its caption language and quality", (j["options"]["cap_lang"], j["options"]["quality"]) == ("en", 1080))
    j = jobs.create({"type": "url", "url": "https://x"}, {"cap_lang": "de"}, enqueue=False, creator=c["id"])
    check("...unless the video picks its own", j["options"]["cap_lang"] == "de")

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
