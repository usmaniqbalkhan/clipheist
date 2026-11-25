"""Twitch ads, left out of recordings.

Twitch puts its ad breaks into the stream itself, and labels every piece of an ad in the list of pieces it hands out
(the HLS playlist): the piece's title says "Amazon", or a date range of class "twitch-stitched-ad" covers it. ffmpeg
reads that list through this small relay on 127.0.0.1, which hands it on without the ad pieces, numbered as if they
were never there. During an ad the recording simply waits, and carries on when the stream comes back: no ad in the
buffer, in the transcript or in a clip. The same thing the free tool streamlink does with --twitch-disable-ads.

The relay runs next to whichever ffmpeg records (the app here, the recorder service on the server): ensure() before
starting one. Only Twitch's own video hosts are relayed. Nothing here needs an account or costs anything."""
import collections
import datetime as dt
import os
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PEAKCUT_ADSKIP_PORT") or 8791)
HOSTS = (".ttvnw.net", ".live-video.net", ".twitch.tv")
KEEP = 24                     # pieces kept per stream (Twitch lists about 6 at a time)
WAIT_FIRST = 150              # seconds a first request waits for the stream after an opening ad (most are 30-90 s)

_lock = threading.Lock()
_streams: dict = {}           # upstream list address -> what's been seen of it
_server = None
skipped = collections.Counter()      # upstream address -> seconds of ads left out (for the logs)


def is_twitch(u: str) -> bool:
    host = (urllib.parse.urlsplit(u).hostname or "").lower()
    return u.startswith("https://") and any(host.endswith(h) for h in HOSTS) and ".m3u8" in u


def url(u: str) -> str:
    """The address ffmpeg should read: through the relay for a Twitch stream, as it is for anything else."""
    return f"http://127.0.0.1:{PORT}/hls.m3u8?u={urllib.parse.quote(u, safe='')}" if is_twitch(u) else u


def _attrs(line: str) -> dict:
    return {k: v.strip('"') for k, v in re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line.split(":", 1)[1] if ":" in line else "")}


def _when(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def parse(text: str, base: str = "") -> tuple:
    """(target duration, ended, [segments]) of a media playlist; each segment {uri, dur, ad, lines}."""
    ads, segs, cur, target, ended = [], [], [], 2, False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-DATERANGE"):
            a = _attrs(line)
            if a.get("CLASS") == "twitch-stitched-ad" or a.get("ID", "").startswith("stitched-ad"):
                start = _when(a.get("START-DATE"))
                try:
                    length = float(a.get("DURATION") or a.get("PLANNED-DURATION") or 0)
                except ValueError:
                    length = 0.0
                if start is not None:
                    ads.append((start, start + max(length, 0.5)))
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            try:
                target = int(float(line.split(":", 1)[1]))
            except ValueError:
                pass
        elif line.startswith("#EXT-X-ENDLIST"):
            ended = True
        elif line.startswith(("#EXTINF", "#EXT-X-PROGRAM-DATE-TIME", "#EXT-X-DISCONTINUITY")):
            cur.append(line)
        elif not line.startswith("#"):
            info = next((x for x in cur if x.startswith("#EXTINF")), "#EXTINF:2.0,")
            title = info.split(",", 1)[1] if "," in info else ""
            try:
                dur = float(info.split(":", 1)[1].split(",")[0])
            except ValueError:
                dur = 2.0
            pdt = next((_when(x.split(":", 1)[1]) for x in cur if x.startswith("#EXT-X-PROGRAM-DATE-TIME")), None)
            segs.append({"uri": urllib.parse.urljoin(base, line), "dur": dur, "title": title, "pdt": pdt,
                         "lines": [x for x in cur if not x.startswith("#EXT-X-DISCONTINUITY")]})
            cur = []
    for s in segs:
        s["ad"] = "amazon" in s["title"].lower() or (s["pdt"] is not None and any(a <= s["pdt"] < b for a, b in ads))
    return target, ended, segs


def relay(upstream: str, text: str) -> str:
    """The playlist without its ads. Kept pieces are numbered in a row of their own, so ffmpeg never sees a gap in
    the numbers; the first piece after an ad starts a new stretch (a discontinuity)."""
    target, ended, segs = parse(text, upstream)
    with _lock:
        st = _streams.setdefault(upstream, {"next": 0, "kept": collections.deque(maxlen=KEEP), "seen": collections.OrderedDict(),
                                            "gap": False, "t": 0.0})
        st["t"] = time.time()
        for s in segs:
            if s["uri"] in st["seen"]:
                continue
            st["seen"][s["uri"]] = True
            while len(st["seen"]) > 400:
                st["seen"].popitem(last=False)
            if s["ad"]:
                st["gap"] = True
                skipped[upstream] += s["dur"]
                continue
            st["kept"].append((st["next"], s, st["gap"]))
            st["next"] += 1
            st["gap"] = False
        kept = list(st["kept"])
        for k in [k for k, v in _streams.items() if time.time() - v["t"] > 900]:       # streams nobody reads any more
            _streams.pop(k, None)
    out = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{max(target, 1)}",
           f"#EXT-X-MEDIA-SEQUENCE:{kept[0][0] if kept else 0}"]
    for _, s, gap in kept:
        if gap:
            out.append("#EXT-X-DISCONTINUITY")
        out += s["lines"]
        out.append(s["uri"])
    if ended:
        out.append("#EXT-X-ENDLIST")
    return "\n".join(out) + "\n"


def _fetch(u: str) -> str:
    import httpx
    r = httpx.get(u, timeout=10, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
    r.raise_for_status()
    return r.text


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if self.path.startswith("/ping"):
            return self._send(200, "peakcut-adskip")
        up = (q.get("u") or [""])[0]
        if not self.path.startswith("/hls.m3u8") or not is_twitch(up):
            return self._send(404, "")
        deadline = time.time() + WAIT_FIRST
        while True:
            try:
                body = relay(up, _fetch(up))
            except Exception:  # noqa: BLE001 - ffmpeg asks again
                return self._send(502, "")
            # a stream that opens with an ad has nothing to hand on yet: wait for it, rather than give ffmpeg an
            # empty list (which it takes as the end)
            if "#EXTINF" in body or "#EXT-X-ENDLIST" in body or time.time() > deadline:
                return self._send(200, body, "application/vnd.apple.mpegurl")
            time.sleep(1)

    def _send(self, code, body, ctype="text/plain"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def prepare(cmd: list) -> list:
    """An ffmpeg command, ready to run here: the relay started if it reads through it; if the relay can't run,
    the command reads Twitch directly again (ads and all, as before)."""
    mark = f"http://127.0.0.1:{PORT}/hls.m3u8?u="
    if not any(str(x).startswith(mark) for x in cmd) or ensure():
        return cmd
    return [urllib.parse.unquote(str(x)[len(mark):]) if str(x).startswith(mark) else x for x in cmd]


def ensure() -> bool:
    """Starts the relay in this process if it isn't running here or next to it already. True when it's there."""
    global _server
    if _server:
        return True
    with _lock:
        if _server:
            return True
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
        except OSError:              # the port is taken: fine if it's another copy of this relay
            try:
                import httpx
                return httpx.get(f"http://127.0.0.1:{PORT}/ping", timeout=3).text == "peakcut-adskip"
            except Exception:  # noqa: BLE001
                return False
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True, name="adskip").start()
        _server = srv
    return True
