"""ClipHeist clip credits, kept in Supabase (table `profiles`, filled by the website's sign-up and Polar orders).

A profile is matched by the user's email. Without SUPABASE_URL / SUPABASE_SECRET_KEY (local runs), credits are off
and everyone is unlimited. Admins are never charged.
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request

from . import auth, ctx

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SECRET_KEY", "")
FREE = {"plan": "free", "clips_total": 3, "clips_used": 0}
_lock = threading.Lock()
_cache: dict = {}


def enabled() -> bool:
    return bool(URL and KEY)


def _req(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{URL}/rest/v1/{path}", data=data, method=method, headers={
        "apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
        "Prefer": "return=representation"})
    with urllib.request.urlopen(r, timeout=8) as resp:
        return json.loads(resp.read() or b"null")


def _user(uid=None):
    return auth.get_user(uid or ctx.uid())


def profile(uid=None, fresh=False) -> dict:
    """{plan, clips_total, clips_used, left, watermark, unlimited}"""
    u = _user(uid)
    if not enabled() or not u or u.is_admin:
        return {"plan": "admin" if u and u.is_admin else "local", "left": 9999, "watermark": False, "unlimited": True}
    hit = _cache.get(u.email)
    if fresh or not hit or time.time() - hit[0] > 20:
        rows = _req("GET", "profiles?select=plan,clips_total,clips_used&email=eq." + urllib.parse.quote(u.email.lower()))
        hit = _cache[u.email] = (time.time(), rows[0] if rows else dict(FREE, missing=True))
    p = dict(hit[1])
    p["left"] = max(0, int(p.get("clips_total") or 0) - int(p.get("clips_used") or 0))
    p["watermark"] = p.get("plan", "free") == "free"
    p["unlimited"] = False
    return p


def spend(uid: str, n: int):
    """Take n clips off the user's balance (called once per finished video)."""
    if n <= 0:
        return
    u = _user(uid)
    if not enabled() or not u or u.is_admin:
        return
    with _lock:
        p = profile(uid, fresh=True)
        if p.get("missing"):
            return
        _req("PATCH", "profiles?email=eq." + urllib.parse.quote(u.email.lower()),
             {"clips_used": int(p.get("clips_used") or 0) + n})
        _cache.pop(u.email, None)
