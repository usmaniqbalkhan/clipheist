"""The streamer's cam found by the AI on real frames: its box (0-1000 of the frame, as the AI gives it) becomes the
cam's place; an answer of "no cam" is taken; a box with no face in it (when there are faces) or one that can't be a
cam is not, and our own finder decides; with no AI at all (or one that can't read pictures), our own finder is used.
Any AI service that reads pictures will do (studio/ai.py). The AI is stood in for."""
import os, subprocess, sys, tempfile
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-fc-"); os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from pathlib import Path   # noqa: E402
import json   # noqa: E402
from studio import ai, facecam   # noqa: E402
fails = []


def check(n, ok, x=""):
    print(("PASS " if ok else "FAIL ") + n + (f"  {x}" if x and not ok else "")); ok or fails.append(n)


vid = Path(os.environ["PEAKCUT_DATA"]) / "v.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:d=3:r=30", "-c:v", "libx264",
                "-preset", "ultrafast", str(vid)], check=True)
reply, asked = {}, []
ai.see = lambda prompt, image, timeout=None: (asked.append((prompt, image)) or (json.dumps(reply["out"]), 100, 10))
facecam._faces = lambda grays: [[] for _ in grays]           # no faces to check against in a test picture

reply["out"] = {"found": True, "box_2d": [400, 0, 625, 200]}
box, ok = facecam.find_ai([vid])
check("the AI is shown a real frame, with its exact size", asked and asked[-1][1][:2] == bytes([0xFF, 0xD8]) and "1280 x 720 pixels" in asked[-1][0])
check("the box it gives becomes the cam's place", ok and box and abs(box["y"] - 0.4) < 0.04 and box["x"] == 0.0 and abs(box["w"] - 0.2) < 0.04, str(box))
reply["out"] = {"found": False}
check("“no cam” (just chatting, IRL) is taken", facecam.find_ai([vid]) == (None, True))
reply["out"] = {"found": True, "box_2d": [0, 0, 1000, 1000]}
check("a box over the whole picture is not a cam", facecam.find_ai([vid])[1] is False)
facecam._faces = lambda grays: [[(1100, 100, 60, 60)] for _ in grays]
reply["out"] = {"found": True, "box_2d": [400, 0, 625, 200]}
check("a box with no face in it, while there is a face elsewhere, is not taken", facecam.find_ai([vid])[1] is False)
reply["out"] = {"found": True, "box_2d": [50, 820, 400, 1000]}
check("...one around the face is", facecam.find_ai([vid])[1] is True)


def no_ai(*a, **k):
    raise RuntimeError("The AI is not set up on this server.")
ai.see = no_ai
check("with no AI, our own finder is asked", facecam.find_ai([vid]) == (None, False))
ai.see = lambda prompt, image, timeout=None: ("", 0, 0)
check("...and with an AI that can't read pictures too", facecam.find_ai([vid]) == (None, False))

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
