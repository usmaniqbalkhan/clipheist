"""Small helpers for what is safe to show a user."""
import re

_PATH = re.compile(r"(?:[A-Za-z]:)?[\\/][^\s'\"]*[\\/][^\s'\"]+")
# yt-dlp's own wording, meant for its developers rather than for ClipHeist's users
_NO_FORMATS = re.compile(r"no video formats found", re.I)
_REPORT = re.compile(r"[;.]?\s*please report this issue on.*", re.I | re.S)
_PREFIX = re.compile(r"^\s*ERROR:\s*(?:\[[^\]]*\]\s*(?:[\w-]+:\s*)?)?")
NO_FORMATS = ("The site didn't hand over this video's picture and sound. If it's a live stream that has only just "
              "started or ended, try again in a few minutes.")


def friendly(e, limit=600) -> str:
    """An error message for the user: trimmed, with any server file paths and yt-dlp's developer notes removed. A
    missing ffmpeg, which Windows reports only as "the system cannot find the file specified", is said in plain words
    with what to do."""
    from . import tools
    told = tools.explain(e)
    if told:
        return told
    s = str(e)
    if _NO_FORMATS.search(s):
        return NO_FORMATS[-limit:]
    s = _PREFIX.sub("", _REPORT.sub("", s)).strip() or s
    return _PATH.sub("[file]", s[-limit:])
