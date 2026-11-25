"""yt-dlp's raw developer wording ("No video formats found!; please report this issue on github...") never reaches
users: video and live recording errors are turned into plain sentences. Nothing leaves this machine."""
import os, sys, tempfile
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-errors-"); os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
import yt_dlp
from studio import live, media, safe
fails = []
def check(n, ok, x=""):
    print(("PASS " if ok else "FAIL ") + n + (f"  {x}" if x and not ok else "")); ok or fails.append(n)

RAW = ("ERROR: [youtube] abcDEF12345: No video formats found!; please report this issue on  "
       "https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template. "
       "Confirm you are on the latest version using  yt-dlp -U")
msg = safe.friendly(RAW)
check("no formats becomes a plain sentence", msg == safe.NO_FORMATS, msg)
check("...without github or yt-dlp in it", "github" not in msg.lower() and "yt-dlp" not in msg.lower(), msg)

other = safe.friendly("ERROR: [twitch:stream] somebody: The channel is not currently live; please report this "
                      "issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the template")
check("other yt-dlp errors lose the 'please report' tail and the ERROR prefix",
      other == "The channel is not currently live", other)
check("ordinary errors are left alone", safe.friendly(RuntimeError("Disk full")) == "Disk full")
check("file paths are still hidden", "[file]" in safe.friendly("ffmpeg failed: /data/users/1/a.mp4 broken"))


class FakeYDL:
    def __init__(self, opts): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def extract_info(self, url, download=False):
        raise yt_dlp.utils.DownloadError(RAW)


yt_dlp.YoutubeDL = FakeYDL
try:
    live.resolve("https://www.youtube.com/@someone/live")
    check("a live stream with no formats is refused", False)
except live.NotLive as e:
    check("a live stream with no formats gives the plain sentence", str(e) == safe.NO_FORMATS, str(e))
check("...and does not pause YouTube for everyone", not media.youtube_paused())

sys.exit(1 if fails else 0)
