"""Video quality follows the style: 1080p for a full frame, more where a style enlarges part of the picture (Fill the
screen, the streamer's cam); with PEAKCUT_TIDY=1, clips' kept stretches above 1080p are made 1080p after a few days
(never without it). Nothing is downloaded."""
import os, subprocess, sys, tempfile, time
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-q-"); os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from pathlib import Path   # noqa: E402
from studio import auth, config, creators, ctx, db, jobs, media, render, templates   # noqa: E402
db.init()
fails = []


def check(n, ok, x=""):
    print(("PASS " if ok else "FAIL ") + n + (f"  {x}" if x and not ok else "")); ok or fails.append(n)


look = lambda tid: templates.get(tid)["look"]                                     # noqa: E731
check("a full-frame style needs 1080p", render.needed_height(look("b-bounce")) == 1080)
check("Fill the screen needs more (the picture is cropped and enlarged)", render.needed_height(look("b-full")) == 1440)
check("a style with the streamer's cam on top asks for the most the video has", render.needed_height(look("b-cam")) == 2160)
check("a bigger cam in the stream needs less", render.needed_height(look("b-cam"), {"x": 0, "y": 0, "w": 0.5, "h": 0.5}) < 2160)

u, _ = auth._create("q@example.com", "Q", password_hash="x")
with ctx.as_user(u.id):
    j = jobs.create({"type": "file", "path": "", "name": "v.mp4"}, {"template": "b-bounce"}, enqueue=False)
    check("a video's quality is worked out from its style", j["options"]["quality"] == 1080)
    j = jobs.create({"type": "file", "path": "", "name": "v.mp4"}, {"template": "b-cam"}, enqueue=False)
    check("...higher for a style that zooms in", j["options"]["quality"] == 2160)
    j = jobs.create({"type": "file", "path": "", "name": "v.mp4"}, {"template": "b-cam", "quality": 720}, enqueue=False)
    check("a quality asked for is kept", j["options"]["quality"] == 720)

# a 1440p stretch, as kept for a clip
d = Path(os.environ["PEAKCUT_DATA"]) / "src"
d.mkdir()
f = d / "piece.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=2560x1440:d=2:r=30", "-f", "lavfi", "-i", "sine=d=2",
                "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(f)], check=True)

# kept stretches above 1080p are made 1080p after a few days, the moments the same
clip_src = d / "clip01.src.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(f), "-c", "copy", str(clip_src)], check=True)
job = {"id": "j1", "clips": [{"id": "clip01", "src": str(clip_src), "status": "done"}], "dir": str(d), "user": u.id}
jobs.save = lambda j: None
config.TIDY = False
check("left alone unless tidying is switched on (PEAKCUT_TIDY=1)", jobs.shrink_stretches(job) == 0 and media.probe(clip_src)["height"] == 1440)
config.TIDY = True
freed = jobs.shrink_stretches(job)
meta = media.probe(clip_src)
check("an old stretch above 1080p is made 1080p", meta["height"] == 1080 and job["clips"][0]["shrunk"], str(meta))
check("...as long as it was", abs(meta["duration"] - 2.0) < 0.2, str(meta["duration"]))
check("...and done once", jobs.shrink_stretches(job) == 0)

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
