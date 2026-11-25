"""Twitch ads are left out of recordings: pieces labelled as ads (by title or by an ad date range) never reach ffmpeg,
the pieces that do are numbered without gaps, a new stretch is marked after an ad, other sites are not touched,
and when the relay can't run the recording reads Twitch directly."""
import sys
sys.path.insert(0, ".")
from studio import adskip   # noqa: E402

fails = []


def check(n, ok, x=""):
    print(("PASS " if ok else "FAIL ") + n + (f"  {x}" if x and not ok else "")); ok or fails.append(n)


UP = "https://video-weaver.fra05.hls.ttvnw.net/v1/playlist/abc.m3u8"


def pl(seq, segs, extra=""):
    out = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:6", f"#EXT-X-MEDIA-SEQUENCE:{seq}", extra]
    for i, (name, title, t) in enumerate(segs):
        out += [f"#EXT-X-PROGRAM-DATE-TIME:2026-11-23T20:00:{t:02d}.000Z", f"#EXTINF:2.000,{title}", f"https://x.hls.ttvnw.net/v1/segment/{name}.ts"]
    return "\n".join(x for x in out if x) + "\n"


check("only Twitch streams go through the relay", adskip.url(UP).startswith("http://127.0.0.1:") and adskip.url("https://kick.com/x.m3u8") == "https://kick.com/x.m3u8")

# an ad by its title
out = adskip.relay(UP, pl(100, [("a", "live", 0), ("b", "live", 2), ("ad1", "Amazon|123", 4), ("ad2", "Amazon|124", 6)]))
check("ad pieces (by title) are left out", "ad1" not in out and "ad2" not in out and "/a.ts" in out and "/b.ts" in out, out)
out = adskip.relay(UP, pl(102, [("ad1", "Amazon|123", 4), ("ad2", "Amazon|124", 6), ("c", "live", 8), ("d", "live", 10)]))
segs = [l for l in out.splitlines() if l.startswith("https://")]
check("pieces are numbered with no gap", "#EXT-X-MEDIA-SEQUENCE:0" in out and segs[-2:] == ["https://x.hls.ttvnw.net/v1/segment/c.ts", "https://x.hls.ttvnw.net/v1/segment/d.ts"], out)
lines = out.splitlines()
check("a new stretch starts after an ad", lines[lines.index("https://x.hls.ttvnw.net/v1/segment/c.ts") - 3] == "#EXT-X-DISCONTINUITY", out)
check("the seconds of ads skipped are counted", adskip.skipped[UP] == 4.0, str(adskip.skipped[UP]))
out2 = adskip.relay(UP, pl(104, [("c", "live", 8), ("d", "live", 10), ("e", "live", 12)]))
check("a piece already handed on keeps its number", out2.count("/c.ts") == 1 and "/e.ts" in out2)

# an ad by its date range
UP2 = UP.replace("abc", "def")
dr = '#EXT-X-DATERANGE:ID="stitched-ad-1",CLASS="twitch-stitched-ad",START-DATE="2026-11-23T20:00:04.000Z",DURATION=4.0'
out = adskip.relay(UP2, pl(1, [("p", "live", 2), ("q", "live", 4), ("r", "live", 6), ("s", "live", 8)], dr))
check("ad pieces (by date range) are left out", "/q.ts" not in out and "/r.ts" not in out and "/p.ts" in out and "/s.ts" in out, out)

# the relay can't run: the recording reads Twitch directly
cmd = ["ffmpeg", "-i", adskip.url(UP), "-c", "copy", "x.ts"]
adskip.ensure = lambda: False
check("without the relay, ffmpeg reads Twitch directly", adskip.prepare(cmd)[2] == UP)
check("a command without Twitch is left as it is", adskip.prepare(["ffmpeg", "-i", "a.mp4"]) == ["ffmpeg", "-i", "a.mp4"])

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
