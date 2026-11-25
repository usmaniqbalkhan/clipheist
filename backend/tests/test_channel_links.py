"""Channel links are kept as the bare channel, and auto-record backs off after a recording that could not start.
A creator saved as kick.com/<name>/videos was recorded as a single old Kick video, Kick refused it, and auto-record
tried again every two minutes (83 failed videos in three hours)."""
import os
import sys

sys.path.insert(0, os.getcwd())
from studio import creators, live   # noqa: E402

fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"   {extra}" if extra else ""))
    if not ok:
        fails.append(name)


for link, want in {
    "https://kick.com/gamerfleet/videos": "https://kick.com/gamerfleet",
    "kick.com/neyoo_gaming/about": "https://kick.com/neyoo_gaming",
    "https://www.twitch.tv/kaicenat/videos?filter=all": "https://www.twitch.tv/kaicenat",
    "https://www.youtube.com/@mrbeast/streams": "https://www.youtube.com/@mrbeast",
    "https://youtube.com/channel/UC123/videos": "https://www.youtube.com/channel/UC123",
}.items():
    got = creators.channel_link(link)
    check(f"{link} is kept as the channel", got == want, got)
for link in ("https://kick.com/n3on/videos/01a1d7b7-7940-7ddb-b7d5-7777afcd7b1b", "https://www.twitch.tv/videos/12345678",
             "https://www.youtube.com/watch?v=abc"):
    check(f"{link} (a video, not a channel) is left alone", creators.channel_link(link) == link)
check("a page of a YouTube channel still records its live stream",
      live.normalize("https://www.youtube.com/@mrbeast/streams") == "https://www.youtube.com/@mrbeast/live")

check("a channel that never failed may be tried", live._start_wait("u1", "k") == 0)
live._start_failed("u1", "k")
w1 = live._start_wait("u1", "k")
live._start_failed("u1", "k")
w2 = live._start_wait("u1", "k")
check("after a failed start, auto-record waits about 10 minutes", 590 < w1 <= 600, str(w1))
check("and longer after each failure in a row", 1190 < w2 <= 1200, str(w2))
check("one person's failures don't hold back another", live._start_wait("u2", "k") == 0)
for _ in range(20):
    live._start_failed("u1", "k")
check("the wait never goes past 6 hours", live._start_wait("u1", "k") <= 6 * 3600)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
