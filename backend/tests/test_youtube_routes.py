"""When YouTube refuses this server, links keep working: YouTube is asked as another app, then through our other
addresses. Only when every way is refused is a link turned away, and then only for a few minutes."""
import os, sys, tempfile
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-ytroutes-")
os.environ.update(DATABASE_URL="")
sys.path.insert(0, ".")
from studio import config, media   # noqa: E402
fails = []


def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)


YT = "https://www.youtube.com/watch?v=abc"
BOT = RuntimeError("ERROR: [youtube] abc: Sign in to confirm you're not a bot")


def reset(proxies=()):
    config.YT_PROXIES = list(proxies)
    media._ROUTE_REFUSED.clear()
    media._ROUTE_LAST = ""
    media._yt_refused_until = 0.0


def asker(refuse):
    """A stand-in for YouTube: refuses the ways named in `refuse`, answers the rest. Records what was tried."""
    seen = []

    def work(x):
        name = ("via" if x.get("proxy") else "") + ("apps" if x.get("extractor_args") else "plain")
        seen.append((name, x.get("proxy")))
        if name in refuse:
            raise BOT
        return name
    return work, seen


reset()
work, seen = asker(set())
check("YouTube answering: asked straight away, nothing else", (media._yt(YT, work), seen), ("plain", [("plain", None)]))

reset()
work, seen = asker({"plain"})
check("refused straight: asked again as another YouTube app, which works", media._yt(YT, work), "apps")
work, seen = asker({"plain"})
check("the way that worked is tried first next time (the refused one is skipped for a while)", (media._yt(YT, work), [s[0] for s in seen]), ("apps", ["apps"]))

reset(["http://10.0.0.4:8888"])
work, seen = asker({"plain", "apps"})
check("both refused here: through our other address", (media._yt(YT, work), seen[-1]), ("viaplain", ("viaplain", "http://10.0.0.4:8888")))

reset(["http://10.0.0.4:8888"])


def data_refused(x):     # 21 Nov: the page is handed over, the video itself is refused from this server
    if not x.get("proxy"):
        raise RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")
    return "via"


check("the video itself refused (403) here: through our other address", media._yt(YT, data_refused), "via")
reset(["http://10.0.0.4:8888"])


def section_refused(x):  # the same refusal when only a stretch is fetched (through ffmpeg)
    if not x.get("proxy"):
        raise RuntimeError("ERROR: ffmpeg exited with code 8")
    return "via"


check("...also when only a stretch of the video is fetched", media._yt(YT, section_refused), "via")
check("...and that counts as YouTube refusing, for the one-click download offer", media.note_refusal(YT, "unable to download video data: HTTP Error 403: Forbidden"))

reset(["http://10.0.0.4:8888"])


def proxy_down(x):
    if x.get("proxy"):
        raise RuntimeError("Unable to connect to proxy")
    raise BOT


try:
    media._yt(YT, proxy_down)
    got = "answered"
except media.YouTubeBlocked as e:
    got = str(e)
check("every way refused (or the other address down): a clear message, not a raw error", got, media.YT_BLOCKED)
check("then YouTube links are turned away at once for a few minutes", media.youtube_paused())
work, seen = asker(set())
try:
    media._yt(YT, work)
except media.YouTubeBlocked:
    pass
check("...without asking YouTube again meanwhile", seen, [])

reset()


def broken(x):
    raise RuntimeError("ERROR: [youtube] abc: Video unavailable. This video is private")


try:
    media._yt(YT, broken)
    got = ""
except Exception as e:  # noqa: BLE001
    got = (type(e).__name__, "private" in str(e))
check("a real problem with the video is reported as it is, not retried", got, ("RuntimeError", True))

reset()
check("other sites are asked once, plainly", media._yt("https://kick.com/x", lambda x: x), {})

# the real functions go through the routes too
reset()
calls = []


class FakeYDL:
    def __init__(self, opts): self.opts = opts
    def __enter__(self): return self
    def __exit__(self, *a): return False

    def extract_info(self, url, download=False):
        calls.append(self.opts.get("extractor_args"))
        if not self.opts.get("extractor_args"):
            raise BOT
        return {"title": "T", "uploader": "U", "duration": 61, "is_live": False}


real = media._ydl
media._ydl = lambda opts: FakeYDL(opts)
got = media.info(YT)
media._ydl = real
check("a link's details come through another way when the plain one is refused", (got["title"], got["duration"], len(calls)), ("T", 61.0, 2))

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
