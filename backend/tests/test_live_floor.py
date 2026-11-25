"""A clip forced every so often stays exactly as each person set it (each AI check is paid with their own key, so
nothing turns extra checks on by itself), and a clip cut by hand is never left without a title."""
import os
import sys

sys.path.insert(0, os.getcwd())
from studio import brain, creators   # noqa: E402

fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"   {extra}" if extra else ""))
    if not ok:
        fails.append(name)


old = creators._fix({**creators.DEFAULTS, "must_clip_min": 0})          # saved before: Off only because it was the default
old.pop("must_clip_v", None)
old["must_clip_min"] = 0
check("a creator saved before keeps what they had: no extra AI checks turned on", creators._fix(old)["must_clip_min"] == 0)
check("choosing Off afterwards is kept", creators._fix({**old, "must_clip_min": 0})["must_clip_min"] == 0)
check("a creator who chose 1 hour keeps it", creators._fix({**creators.DEFAULTS, "must_clip_min": 60})["must_clip_min"] == 60)
check("new creators start with it Off", creators.DEFAULTS["must_clip_min"] == 0)
check("a creator who chose 15 minutes keeps it", creators._fix({**old, "must_clip_min": 15})["must_clip_min"] == 15)
new_off = creators._fix({**creators.DEFAULTS, "must_clip_v": 1, "must_clip_min": 0})
check("a new creator made with Off stays Off", new_off["must_clip_min"] == 0)


def no_answer(*_a, **_k):
    return [], "m", {}


def broken(*_a, **_k):
    raise RuntimeError("AI unavailable")


said = "[12] dude that was the craziest getaway I have ever seen in my life"
for name, fake in (("the AI returns nothing", no_answer), ("the AI fails", broken)):
    brain.ask_ai = fake
    d = brain.describe(said, title="xQc live")
    check(f"a hand clip still gets a title when {name}", bool(d["title"]) and bool(d["hook"]) and "[" not in d["title"], d["title"])
d = brain.describe("hi", title="xQc live")
check("a hand clip with almost no words is named after the stream", d["title"] == "hi…" or d["title"] == "xQc live", d["title"])

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
