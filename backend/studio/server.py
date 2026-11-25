"""ClipHeist web server. Run with:  python -m studio.server

Public pages:  /  /login  /signup          The app:  /app  (signed-in users only)
Everything under /api, /media and /oauth needs a session, except /api/auth/* and /api/public."""
import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import re
import shutil
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import quote, urlparse

import sqlalchemy as sa
import uvicorn
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from . import admin as admins
from . import campaigns, discover
from . import account, load
from . import (accounts, ai, alerts, auth, brain, config, creators, ctx, db, jobs, live, media, posting,
               profiles, render, safe, templates, transcribe, usage)

from . import tools as _tools  # noqa: E402


def _quiet(fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - said again, in plain words, when a video needs it
        print(f"  {e}", flush=True)


app = FastAPI(title="ClipHeist", docs_url=None, redoc_url=None, openapi_url=None)
WEB = config.WEB_DIR
OPEN = ("/api/auth/", "/pub/", "/api/public", "/auth/google/", "/auth/verify", "/brand/", "/assets/", "/webhooks/meta/", "/healthz", "/favicon.ico", "/robots.txt", "/sitemap.xml")
PAGES = {"/": "landing.html", "/login": "auth.html", "/signup": "auth.html", "/reset": "reset.html", "/sso": "sso.html",
         "/privacy": "legal.html", "/terms": "legal.html", "/acceptable-use": "legal.html",
         "/data-deletion": "legal.html"}
VIDEO_TYPES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".ts", ".flv"}
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
       "frame-src https://player.twitch.tv https://www.youtube-nocookie.com https://player.kick.com; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://accounts.google.com")


BOOT = f"{int(time.time()) % 100000:x}"     # part of every sync token, so pages reload their data after a restart


@app.on_event("startup")
async def _more_threads():
    import anyio.to_thread
    anyio.to_thread.current_default_thread_limiter().total_tokens = int(config.env("PEAKCUT_REQUEST_THREADS", "64"))


def _tell_release():
    """Every account that existed before this start gets one bell notice about the new release (once: its ref is
    the release). Pages opened before the update show it too, as their usual notice pop-up, and its link reloads
    the page onto the new version."""
    from . import notices
    time.sleep(20)
    try:
        with db.engine.connect() as c:
            ids = [r[0] for r in c.execute(sa.select(db.users.c.id).where(db.users.c.disabled == sa.false(), db.users.c.created_at < STARTED))]
        for uid in ids:
            notices.add(uid, "update", "ClipHeist has been updated", "Tap to see what's new.", "/whats-new", ref="release:" + config.RELEASE)
    except Exception:  # noqa: BLE001
        traceback.print_exc()


STARTED = time.time()


@app.get("/whats-new")
def whats_new_page():
    """A full page load onto the new version, then its updates (a page from before the update follows this link)."""
    return RedirectResponse("/app/updates", 303)


@app.on_event("startup")
def _startup():
    db.init()
    if config.RELEASE_NOTICE:
        threading.Thread(target=_tell_release, daemon=True).start()
    for f in (config.TMP_DIR / "uploads").glob("*"):      # uploads cut short by a restart
        try:
            f.unlink()
        except OSError:
            pass
    render.install_fonts()
    from . import credits
    if credits.on_clip not in jobs.on_clip_ready:
        jobs.on_clip_ready.append(credits.on_clip)      # ClipHeist: a live recording's clips are charged one by one
    if _tools.missing():
        print("\n  " + _tools.HELP + "\n", flush=True)
    if not transcribe.REMOTE and config.env("PEAKCUT_FETCH_MODEL", "1") != "0":     # the speech model: fetched now, in the background, if it was never downloaded
        from . import fetch_model
        threading.Thread(target=lambda: _quiet(fetch_model.ensure), daemon=True).start()
    jobs.start()
    live.start()
    posting.start()
    from . import updates
    updates.start()                  # looks for a newer ClipHeist every minute, in the background
    discover.warm_up()


# ================================================================ the gate: who is asking, and are they allowed
class Gate:
    """Runs before every request: finds the signed-in user, blocks what needs sign-in, blocks cross-site form
    posts, caps upload size and adds security headers."""

    def __init__(self, app):
        self.inner = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        req = Request(scope)
        path, method = scope["path"], scope["method"]

        async def deny(status, msg, redirect=None):
            no_keep = {"Cache-Control": "private, no-store"}      # never kept by a browser or CDN
            resp = RedirectResponse(redirect, 303, headers=no_keep) if redirect else JSONResponse({"detail": msg}, status, headers=no_keep)
            await resp(scope, receive, send)

        if method not in ("GET", "HEAD", "OPTIONS"):
            if not _from_here(req):
                return await deny(403, "Cross-site requests are not allowed.")
            # Only the upload may be big. Everything else is a small form, and is cut off at 256 KB whatever its
            # headers claim, so nobody can fill the server's memory by posting junk (signed in or not).
            piece = method == "PUT" and path.startswith("/api/upload/")
            cap = ((min(config.MAX_UPLOAD_MB, config.UPLOAD_PART_MB) if piece else config.MAX_UPLOAD_MB) + 8) * 1024 * 1024 \
                if path == "/api/upload" or piece else 256 * 1024
            if method == "POST" and path.endswith("/logo") and path.startswith(("/api/my-campaigns/", "/api/creators/")):
                cap = 26 * 1024 * 1024          # a campaign's logo: a picture, a GIF or a short video (MAX_LOGO_MB)
            try:
                if int(req.headers.get("content-length") or 0) > cap:
                    return await deny(413, f"That file is too big. The limit is {config.MAX_UPLOAD_MB} MB." if path == "/api/upload"
                                      else "That logo is too big. The limit is 25 MB." if path.endswith("/logo") else "That request is too large.")
            except ValueError:
                return await deny(400, "Bad request.")
            seen, inner_receive = 0, receive

            async def receive():
                nonlocal seen
                m = await inner_receive()
                if m["type"] == "http.request":
                    seen += len(m.get("body", b""))
                    if seen > cap:
                        raise HTTPException(413, "That request is too large.")
                return m

        is_open = path in PAGES or any(path.startswith(p) for p in OPEN)
        admin_area = path == "/admin" or path.startswith(("/admin/", "/api/admin/"))
        if config.ACCOUNTS:
            cookie = req.cookies.get(auth.COOKIE, "")
            user = auth._sess.get(auth._h(cookie)) if cookie and not admin_area else None      # remembered: no database, no thread hop
            user = user[1] if user and time.time() - user[0] < auth.SESS_FOR else (
                await run_in_threadpool(auth.user_for, cookie, admin_area) if cookie else None)
        else:
            # No sign-in: whoever opens the page is the owner. Because nothing else stands in the way, the page
            # must have been asked for by its real address (a number, "localhost", or the address in settings),
            # so another website cannot reach it through a made-up name that points at this machine.
            if not _own_address(req.headers.get("host", "")):
                return await deny(403, "Open ClipHeist by its own address.")
            if path == "/" or path == "/reset" or path.startswith("/auth/"):
                return await deny(303, "", redirect="/app")
            if path.startswith("/api/auth/") and path != "/api/auth/me":
                return await deny(404, "Not Found")
            user = auth._local or await run_in_threadpool(auth.local_user)
        if admin_area:
            # The admin area does not exist for anyone else: same "not found" whether signed out or a normal user.
            if not user or not user.is_admin:
                return await deny(404, "Not Found")
            if method not in ("GET", "HEAD"):
                # changes must come from our own pages: the browser has to say so, and it has to match
                if not _from_here(req, must_say=True):
                    return await deny(403, "Cross-site requests are not allowed.")
        if not user and not is_open:
            if path == "/app" or path.startswith("/app/"):
                return await deny(401, "", redirect="/login")
            return await deny(401, "Please sign in.")
        if user and not user.is_admin and not admin_area and not await run_in_threadpool(_confirmed, user.id):
            # an account whose email is not confirmed yet gets nowhere: the app sends it back to sign in, where it
            # is told to confirm; the rest answers "confirm your email first"
            if path == "/app" or path.startswith("/app/"):
                return await deny(303, "", redirect="/login?confirm=1")
            if not is_open:
                return await deny(403, "Confirm your email first. We sent you a link.")
            user = None if path in ("/login", "/signup") else user
        if user and path in ("/login", "/signup"):
            return await deny(303, "", redirect="/app")
        scope.setdefault("state", {})["user"] = user
        if user and path in ("/api/upload", "/api/upload/start") and method == "POST":
            # decided before a single byte of the file is accepted: verified, has a free slot, has disk space
            def allowed():
                with ctx.as_user(user.id):
                    _need_verified(); _job_slots(); _quota(); _credits()         # noqa: E702
            try:
                await run_in_threadpool(allowed)
            except HTTPException as e:
                return await deny(e.status_code, e.detail)
        if user and path.startswith("/api/") and not auth.seen_recently(user.id):
            await run_in_threadpool(auth.touch, user.id)

        unchanged = False

        async def send_with_headers(message):
            nonlocal unchanged
            if unchanged:                       # the browser's copy is current: send no body at all
                if message["type"] == "http.response.body" and not message.get("more_body"):
                    await send({"type": "http.response.body", "body": b""})
                return
            if message["type"] == "http.response.start":
                h = message.setdefault("headers", [])
                if method == "GET" and message.get("status") == 200:
                    tag = next((v.decode("latin-1") for k, v in h if k.lower() == b"etag"), "")
                    have = req.headers.get("if-none-match", "")
                    if tag and have and tag.removeprefix("W/") in [x.strip().removeprefix("W/") for x in have.split(",")]:
                        unchanged = True
                        message["status"] = 304
                        h[:] = [(k, v) for k, v in h if k.lower() not in (b"content-length", b"content-type", b"content-encoding")]
                h += [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
                      (b"referrer-policy", b"strict-origin-when-cross-origin"), (b"content-security-policy", CSP.encode()),
                      (b"permissions-policy", b"camera=(), microphone=(), geolocation=()")]
                if config.HTTPS:
                    h.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                if path.startswith("/api/"):
                    h.append((b"cache-control", b"no-store"))
                elif message.get("status", 200) >= 300 and not any(k.lower() == b"cache-control" for k, _ in h):
                    # redirects and errors are never kept by a CDN: a "not found" for a clip that is still being
                    # made, or a sign-in redirect, must not be shown to anyone later
                    h.append((b"cache-control", b"private, no-store"))
                if path.startswith(("/app", "/admin", "/api/", "/auth/", "/reset", "/media/")):
                    h.append((b"x-robots-tag", b"noindex, nofollow"))
            await send(message)

        token = ctx._uid.set(user.id if user else None)
        try:
            if user and path == "/api/sync" and method == "GET":
                # By far the most common request. When nothing of this user's changed it is answered right here:
                # no worker thread, no database, no disk. Anything else falls through to the normal route.
                quick = _sync_unchanged(req.query_params.get("v", ""))
                if quick is not None:
                    return await JSONResponse(quick)(scope, receive, send_with_headers)
            await self.inner(scope, receive, send_with_headers)
        finally:
            ctx._uid.reset(token)


app.add_middleware(Gate)


class Compress:
    """gzip for text responses. Videos and images are already compressed and need byte ranges, so they pass through."""
    def __init__(self, app):
        from starlette.middleware.gzip import GZipMiddleware
        self.plain, self.gzip = app, GZipMiddleware(app, minimum_size=800, compresslevel=6)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        media = path.startswith(("/media/", "/api/upload")) or path.endswith((".mp4", ".jpg", ".png", ".webp", ".ico"))
        await (self.plain if scope["type"] != "http" or media else self.gzip)(scope, receive, send)


app.add_middleware(Compress)


class HeadAsGet:
    """HEAD is answered like GET without the body. Without this every page said 405 to HEAD, and site checkers
    (Google's app verification among them) read that as "the home page is unresponsive"."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if scope["type"] != "http" or scope.get("method") != "HEAD" or path.startswith(("/api/", "/oauth/", "/logout")):
            return await self.app(scope, receive, send)      # pages only: a HEAD never starts a sign-in or an action
        done = False

        async def headers_only(msg):
            nonlocal done
            if msg["type"] == "http.response.start":
                await send(msg)
            elif msg["type"] == "http.response.body" and not done:
                if not msg.get("more_body"):
                    done = True
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
            else:
                await send(msg)
        await self.app(dict(scope, method="GET"), receive, headers_only)


app.add_middleware(HeadAsGet)


# Cloudflare's published address ranges (cloudflare.com/ips). PEAKCUT_CDN_RANGES replaces them if they ever change.
_CDN_RANGES = None


def _from_cdn(addr: str) -> bool:
    global _CDN_RANGES
    import ipaddress
    if _CDN_RANGES is None:
        ranges = config.env("PEAKCUT_CDN_RANGES") or (
            "173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22 141.101.64.0/18 108.162.192.0/18 "
            "190.93.240.0/20 188.114.96.0/20 197.234.240.0/22 198.41.128.0/17 162.158.0.0/15 104.16.0.0/13 "
            "104.24.0.0/14 172.64.0.0/13 131.0.72.0/22 2400:cb00::/32 2606:4700::/32 2803:f800::/32 2405:b500::/32 "
            "2405:8100::/32 2a06:98c0::/29 2c0f:f248::/32")
        _CDN_RANGES = [ipaddress.ip_network(r) for r in ranges.replace(",", " ").split()]
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in n for n in _CDN_RANGES)


# Private and local addresses: the only places a reverse proxy in front of ClipHeist can sit.
_PROXY_CIDRS = ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16",
                "::1/128", "fc00::/7", "fe80::/10")
_PROXY_RANGES = [ipaddress.ip_network(r) for r in _PROXY_CIDRS]


def _trust_proxy() -> bool:
    return (config.env("PEAKCUT_TRUST_PROXY") or "").strip().lower() not in ("", "0", "false", "no")


def _valid_ip(text: str) -> str:
    """The address as text if it really is an IP address, otherwise ""."""
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return ""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return str(ip)


def _from_proxy(addr: str) -> bool:
    clean = _valid_ip(addr)
    if not clean:
        return False
    ip = ipaddress.ip_address(clean)
    return any(ip in n for n in _PROXY_RANGES)


def _ip(request: Request) -> str:
    """The visitor's address, used for rate limits. The connection's own address, or the one the proxy in front
    reports (PEAKCUT_TRUST_PROXY). If that connection comes from the CDN, the CDN's header names the real visitor
    (PEAKCUT_CLIENT_IP_HEADER). The header is believed only when the CDN really sent the request, so nobody can
    pick their own address by sending it themselves.

    The proxy adds the address it saw at the END of X-Forwarded-For. Everything before that was written by the
    visitor, so only the last entry is believed, and only when the connection came from a proxy on a private address."""
    direct = request.client.host if request.client else ""
    peer = direct
    fwd = ",".join(request.headers.getlist("x-forwarded-for"))
    if _trust_proxy() and fwd and _from_proxy(direct):
        peer = _valid_ip(fwd.rsplit(",", 1)[-1].strip()) or direct
    named = config.env("PEAKCUT_CLIENT_IP_HEADER")
    if named and request.headers.get(named) and _from_cdn(peer):
        return _valid_ip(request.headers[named].split(",")[0].strip()[:64]) or peer
    return peer


def _from_here(req, must_say=False) -> bool:
    """Did this change come from ClipHeist's own page, and not from another website?

    The browser's own answer is trusted first: it marks every request "same-origin" or not, and a web page cannot
    fake that mark. Comparing the page's address with the address the request arrived at is only the fallback,
    because that comparison goes wrong when something in between rewrites either of them (a proxy, an antivirus
    web shield, a browser extension), which made every save fail with "Cross-site requests are not allowed"."""
    if (req.headers.get("sec-fetch-site") or "").lower() == "same-origin":
        return True
    origin = (req.headers.get("origin") or "").rstrip("/").lower()
    if not origin:
        return not must_say
    return urlparse(origin).netloc == (req.headers.get("host") or "").lower() or origin == config.PUBLIC_URL.lower()


def _own_address(host: str) -> bool:
    """Is this the address ClipHeist is really reached by? A number (127.0.0.1, 192.168.1.20), "localhost", or the
    host in PEAKCUT_PUBLIC_URL."""
    import ipaddress
    name = host.strip().lower()
    name = name[1:name.index("]")] if name.startswith("[") and "]" in name else name.rsplit(":", 1)[0] if ":" in name else name
    if name in ("localhost", urlparse(config.PUBLIC_URL).hostname or "") or name.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def _me(request: Request):
    return request.state.user


def _confirmed(uid: str) -> bool:
    try:
        return auth.is_verified(uid)
    except Exception:  # noqa: BLE001 - can't tell right now: let them in rather than lock everyone out
        return True


def _admin(request: Request):
    """The signed-in admin, read from the database for this very request. Anyone else gets "not found"."""
    u = getattr(request.state, "user", None)
    if not u or not u.is_admin or u.disabled:
        raise HTTPException(404)
    return u


def _need_verified():
    if not auth.is_verified(ctx.uid()):
        raise HTTPException(403, "Confirm your email first. We sent you a link when you signed up; you can send a new one from the banner at the top.")


# ================================================================ pages
def _page(name, shared=False):
    """A page. The browser keeps its copy and asks each time whether it has changed; when it has not, the answer
    is a few bytes ("not modified") instead of the whole page again. Signed-in pages are never kept by anything
    in between (a proxy or CDN), only by the person's own browser."""
    headers = {"Cache-Control": ("public" if shared else "private") + ", no-cache"}
    if SITE != LIVE_SITE and name in ("sso.html", "index.html"):
        # the pages link to the website by its live address; with CLIPHEIST_SITE set (a local run, a staging
        # copy) they point at that website instead
        html = (WEB / name).read_text(encoding="utf-8").replace(LIVE_SITE, SITE)
        return HTMLResponse(html, headers={**headers, "ETag": '"' + hashlib.md5(html.encode()).hexdigest() + '"'})
    return FileResponse(WEB / name, headers=headers)


_LANDING: dict = {}


# The public website (frontend/). Signed-out people are sent there to sign in; CLIPHEIST_SITE points a local run
# at a local copy of it, e.g. http://localhost:5500.
LIVE_SITE = "https://clipheist.site"
SITE = (os.environ.get("CLIPHEIST_SITE") or LIVE_SITE).rstrip("/")


def _site_login() -> bool:
    """Sign-in happens once, on the website. The app's own sign-in page is only kept for ?direct=1 (admins)."""
    return config.ACCOUNTS and auth_mod_ready()


def auth_mod_ready() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY"))


@app.get("/sso")
def sso_page():
    return _page("sso.html", shared=True)


@app.post("/api/auth/supabase")
def supabase_login(request: Request, body: dict = Body(...)):
    try:
        auth.throttle("sso:" + _ip(request), 40, 600)
        user, first = auth.supabase_finish(str(body.get("access_token", ""))[:4000])
    except auth.AuthError as e:
        raise HTTPException(401, str(e))
    return _signed_in(request, user, first)


@app.get("/")
def landing(request: Request):
    if _site_login():      # the website is the front page; the app's address goes straight into the app
        return RedirectResponse("/app" if _me(request) else SITE + "/start", 303)
    f = WEB / "landing.html"
    m = f.stat().st_mtime
    if _LANDING.get("m") != m:
        html = f.read_text(encoding="utf-8").replace("{{SITE}}", config.PUBLIC_URL)
        _LANDING.update(m=m, html=html, etag='"' + hashlib.md5(html.encode()).hexdigest() + '"')
    # fresh for five minutes; after that a CDN or browser may show its copy while it quietly fetches the new one
    return HTMLResponse(_LANDING["html"], headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=86400",
                                                   "ETag": _LANDING["etag"]})


@app.get("/robots.txt")
def robots():
    return Response("User-agent: *\nAllow: /\nDisallow: /app\nDisallow: /api/\nDisallow: /auth/\nDisallow: /reset\nDisallow: /media/\n"
                    f"\nSitemap: {config.PUBLIC_URL}/sitemap.xml\n", media_type="text/plain",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/sitemap.xml")
def sitemap():
    day = time.strftime("%Y-%m-%d")
    pages = [("/", "1.0", "weekly"), ("/signup", "0.8", "monthly"), ("/login", "0.4", "monthly"), ("/terms", "0.3", "yearly"),
             ("/privacy", "0.3", "yearly"), ("/acceptable-use", "0.2", "yearly")]
    body = "".join(f"<url><loc>{config.PUBLIC_URL}{u}</loc><lastmod>{day}</lastmod><changefreq>{c}</changefreq>"
                   f"<priority>{p}</priority></url>" for u, p, c in pages)
    return Response('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    + body + "</urlset>", media_type="application/xml", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/login")
@app.get("/signup")
def auth_page(request: Request, direct: str = "", error: str = ""):
    if _site_login() and not direct and not error:
        return RedirectResponse(SITE + "/start", 303)
    return _page("auth.html", shared=True)


@app.get("/reset")
def reset_page():
    return _page("reset.html", shared=True)


LEGAL = {
    "privacy": ("Privacy Policy", "How ClipHeist collects, uses and protects your data, including Google user data, cookies and your privacy rights."),
    "terms": ("Terms of Service", "The terms for using ClipHeist, the AI clipping tool for streams and videos: accounts, availability and liability."),
    "acceptable-use": ("Acceptable Use Policy", "What you may and may not do with ClipHeist, the AI stream clipping tool."),
    "data-deletion": ("Data deletion", "How to remove your connected accounts and delete your ClipHeist data."),
}


@app.get("/privacy")
@app.get("/terms")
@app.get("/acceptable-use")
@app.get("/data-deletion")
def legal_page(request: Request):
    """Public, so Google's app review (and anyone) can read them. Business name, law and contact come from settings."""
    import html as _h
    page = request.url.path.strip("/")
    title, desc = LEGAL[page]
    contact = config.CONTACT_EMAIL
    who = (f'<a href="mailto:{_h.escape(contact)}">{_h.escape(contact)}</a>' if contact
           else "the contact details on our website")
    name = _h.escape(config.LEGAL_NAME or "ClipHeist")
    law = (f"the laws of {_h.escape(config.LEGAL_COUNTRY)}" if config.LEGAL_COUNTRY
           else "the laws of the country where ClipHeist's operator is established")
    out = (_LEGAL_HTML or (WEB / "legal.html").read_text(encoding="utf-8"))
    for k, v in {"{{CONTACT}}": who, "{{PAGE}}": page, "{{PAGE_PATH}}": page, "{{SITE}}": _h.escape(config.PUBLIC_URL),
                 "{{NAME}}": name, "{{LAW}}": law, "{{TITLE}}": title, "{{DESC}}": desc, "{{EXPIRY}}": ""}.items():
        out = out.replace(k, v)
    return HTMLResponse(out, headers={"Cache-Control": "public, max-age=3600"})


_LEGAL_HTML = ""


@app.get("/app")
@app.get("/app/{rest:path}")
def app_page(rest: str = ""):
    """Every app address (/app/videos, /app/creators/<id>/setup ...) is the same page; it shows the right view."""
    return _page("index.html")


@app.get("/admin")
@app.get("/admin/{rest:path}")
def admin_page(request: Request, rest: str = ""):
    _admin(request)
    return _page("admin.html")


@app.get("/healthz")
def healthz(request: Request):
    with db.engine.connect() as c:
        c.execute(sa.text("select 1"))
    # From inside the server only (no proxy in between): what is running, so a restart is never done blind. Loading
    # the job files in another process can't tell: it reads every live recording as already paused.
    if request.client and request.client.host in ("127.0.0.1", "::1") and not request.headers.get("x-forwarded-for"):
        busy = [j for j in jobs.all_jobs() if j["status"] in ("queued", "running")]
        return {"ok": True, "live": len(live.active()), "videos": len(busy),
                "clips_waiting": sum(1 for j in jobs.all_jobs() for k in j.get("clips", []) if k.get("status") in ("queued", "rendering"))}
    return {"ok": True}


@app.get("/brand/{name}")
def brand(name: str):
    if not re.fullmatch(r"[\w\-]+\.(svg|png|ico|webp|jpg|mp4)", name) or not (WEB / "brand" / name).is_file():
        raise HTTPException(404)
    return FileResponse(WEB / "brand" / name, headers={"Cache-Control": "public, max-age=604800, stale-while-revalidate=86400"})


@app.get("/assets/{name}")
def asset(name: str):
    if not re.fullmatch(r"[\w\-]+\.js", name) or not (WEB / name).is_file():
        raise HTTPException(404)
    return FileResponse(WEB / name, media_type="text/javascript", headers={"Cache-Control": "public, max-age=86400, stale-while-revalidate=604800"})


@app.get("/favicon.ico")
def favicon():
    return RedirectResponse("/brand/favicon.svg", 308)


# ================================================================ sign up, sign in, sign out
@app.get("/api/public")
def public_config():
    from . import mailer
    return {"google": auth.google_ready(), "mail": mailer.ready(), "verify_email": config.VERIFY_EMAIL}


def _signed_in(request: Request, user, first: bool, redirect=None):
    token = auth.start_session(user.id, _ip(request), request.headers.get("user-agent", ""))
    resp = RedirectResponse(redirect, 303) if redirect else JSONResponse({"user": auth.public(user)})
    resp.set_cookie(**auth.cookie_args(token))
    return resp


@app.post("/api/auth/signup")
def signup(request: Request, body: dict = Body(...)):
    try:
        auth.throttle("signup:" + _ip(request), 8, 3600)
        user, first = auth.signup(str(body.get("email", "")), str(body.get("password", "")), str(body.get("name", "")))
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    if not auth.is_verified(user.id):
        auth.send_verify(user)
        return {"confirm": True, "email": user.email}        # not signed in: the email has to be confirmed first
    return _signed_in(request, user, first)


@app.post("/api/auth/resend-verification")
def resend_verification(request: Request):
    u = _me(request)
    if not u:
        raise HTTPException(401, "Please sign in.")
    if auth.is_verified(u.id):
        return {"verified": True}
    try:
        auth.throttle("verify:" + u.id, 3, 900)
    except auth.AuthError:
        raise HTTPException(429, "We've sent a few already. Check your spam folder, or try again in 15 minutes.")
    from . import mailer
    return {"sent": auth.send_verify(u), "mail_ready": mailer.ready()}


@app.get("/auth/verify")
def verify_email(request: Request, t: str = ""):
    try:
        auth.verify_link(t)
    except auth.AuthError as e:
        return RedirectResponse("/login?verify_error=" + quote(str(e)), status_code=303)
    return RedirectResponse("/login?verified=1", status_code=303)


@app.post("/api/auth/forgot")
def forgot_password(request: Request, body: dict = Body(...)):
    email = str(body.get("email", "")).strip().lower()
    try:
        auth.throttle("forgot-ip:" + _ip(request), 10, 3600)
        auth.throttle("forgot:" + email, 3, 3600)
    except auth.AuthError as e:
        raise HTTPException(429, str(e))
    auth.forgot(email)
    return {"ok": True}   # the same answer whether or not the account exists


@app.post("/api/auth/reset")
def reset_password(request: Request, body: dict = Body(...)):
    try:
        auth.throttle("reset-ip:" + _ip(request), 20, 3600)
        user = auth.reset_password(str(body.get("token", "")), str(body.get("password", "")))
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    return _signed_in(request, user, False)


@app.post("/api/auth/login")
def login(request: Request, body: dict = Body(...)):
    email = str(body.get("email", ""))
    try:
        auth.throttle("login-ip:" + _ip(request), 30, 600)
        auth.throttle(f"login:{email.strip().lower()[:254]}:{_ip(request)}", 8, 600)
        auth.throttle("login:" + email.strip().lower()[:254], 60, 600)
        user = auth.login(email, str(body.get("password", "")))
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    if not user.is_admin and not _confirmed(user.id):
        try:                                            # a fresh link, a few at most
            auth.throttle("verify:" + user.id, 3, 900)
            auth.send_verify(user)
        except auth.AuthError:
            pass
        return JSONResponse({"confirm": True, "email": user.email}, status_code=403)
    return _signed_in(request, user, False)


@app.post("/api/auth/logout")
def logout(request: Request):
    auth.end_session(request.cookies.get(auth.COOKIE, ""))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.get("/api/auth/me")
def me(request: Request):
    return {"user": auth.public(_me(request)) if _me(request) else None}


@app.post("/api/auth/password")
def change_password(request: Request, body: dict = Body(...)):
    if not _me(request):
        raise HTTPException(401, "Please sign in.")
    try:
        auth.throttle("pw:" + _me(request).id, 6, 600)
        auth.change_password(_me(request).id, str(body.get("current", "")), str(body.get("new", "")),
                             keep_token=request.cookies.get(auth.COOKIE, ""))
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/auth/google/start")
def google_start(next: str = "/app"):
    if not auth.google_ready():
        raise HTTPException(404)
    url, state = auth.google_url(next)
    resp = RedirectResponse(url)
    # the sign-in can only be finished by the browser that started it
    resp.set_cookie("g_state", state, max_age=900, httponly=True, samesite="lax", secure=config.HTTPS, path="/auth/google")
    return resp


@app.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error or not code or not state or not hmac.compare_digest(request.cookies.get("g_state", ""), state):
        return RedirectResponse("/login?error=google", 303)
    try:
        auth.throttle("google:" + _ip(request), 30, 600)
        user, first, nxt = auth.google_finish(code, state)
    except auth.AuthError:
        return RedirectResponse("/login?error=google", 303)
    return _signed_in(request, user, first, redirect=nxt)


# ================================================================ app status
def _safe_credits():
    from . import credits
    try:
        return credits.profile()
    except Exception:
        return None


@app.get("/api/status")
def status(request: Request):
    from . import notices
    from . import updates
    me_ = _me(request)
    return {"user": auth.public(me_), "update": updates.latest() if me_.is_admin else None, "accounts": config.ACCOUNTS, "disk_full": load.disk_full(), "no_ffmpeg": bool(_tools.missing()), "links": {"repo": config.REPO_URL, "follow": config.FOLLOW_URL}, "ai": usage.brief(), "credits": _safe_credits(), "unread": notices.unread(),
            "tour": not accounts.settings()["tour_done"], "ask_rating": _ask_rating(),
            "youtube": accounts.youtube_ready(), "connect": {p: accounts.can_connect(p) and (accounts.app_open(p) or bool(getattr(me_, "is_admin", False))) for p in accounts.PLATFORM_NAMES}, "open": {p: accounts.can_connect(p) and accounts.app_open(p) for p in accounts.PLATFORM_NAMES}, "max_upload_mb": config.MAX_UPLOAD_MB, "upload_part_mb": config.UPLOAD_PART_MB, "max_batch": config.MAX_ACTIVE_JOBS_PER_USER,
            "styles": {k: v["label"] for k, v in render.STYLES.items()}, "layouts": render.LAYOUTS, "speech": _speech_brief(),
            "cap_langs": brain.CAP_LANGS,
            "waiting_posts": posting.waiting(), "posted_posts": posting.posted(), "release": config.RELEASE}


def _speech_brief() -> dict:
    """Which spoken languages can be transcribed here: for the language choice when adding a video."""
    from . import fetch_model
    return {k: {"label": m[0], "size_mb": m[3], "state": fetch_model.status(k)} for k, m in fetch_model.MODELS.items()}


def _ask_rating() -> bool:
    """Ask for stars once, after someone's 5th finished clip. Only on a site with accounts: on your own computer
    there is nobody else to tell."""
    if not config.ACCOUNTS or accounts.settings().get("rated"):
        return False
    try:
        return sum(1 for j in jobs.list_jobs() for k in j.get("clips", []) if k.get("status") == "done") >= 5
    except Exception:  # noqa: BLE001
        return False


@app.post("/api/rating")
def send_rating(request: Request, body: dict = Body(...)):
    from . import feedback
    u = _me(request)
    try:
        auth.throttle("rating:" + u.id, 10, 3600)
        if body.get("skip"):
            accounts.save_settings({"rated": True})
            return {"ok": True}
        out = feedback.rate(u.id, body.get("stars", 0))
        accounts.save_settings({"rated": True})
        return out
    except (feedback.Refused, ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    except auth.AuthError as e:
        raise HTTPException(429, str(e))


@app.post("/api/rating/{fid}")
def send_rating_more(request: Request, fid: int, body: dict = Body(...)):
    from . import feedback
    u = _me(request)
    try:
        auth.throttle("rating:" + u.id, 10, 3600)
        return {"ok": feedback.rate_more(u.id, fid, body.get("reasons") or [], str(body.get("text", "")))}
    except feedback.Refused as e:
        raise HTTPException(400, str(e))
    except auth.AuthError as e:
        raise HTTPException(429, str(e))


# ================================================================ videos
def _job_or_404(jid: str):
    job = jobs.get(jid) if re.fullmatch(r"[\w\-]{8,60}", jid) else None
    if not job:
        raise HTTPException(404, "No such video")
    return job


def _credits() -> dict:
    """Block when the user has no clips left; returns the profile so the job can be capped and watermarked."""
    from . import credits
    try:
        p = credits.profile()
    except Exception:
        raise HTTPException(503, "Couldn't check your clip balance. Try again in a moment.")
    if p.get("missing"):
        raise HTTPException(402, "Sign in once at clipheist.site with this Google account to get your free clips.")
    if p["left"] <= 0:
        raise HTTPException(402, "You're out of clips. Get more at clipheist.site/pricing.")
    return p


def _apply_credits(job, p):
    """Cap the clip count at the balance and mark free-plan jobs for a watermark."""
    if p.get("unlimited"):
        return
    o = job["options"]
    o["max_clips"] = max(1, min(int(o.get("max_clips") or 6), p["left"]))
    job["watermark"] = bool(p.get("watermark"))
    jobs.save(job)


def _job_slots():
    from . import tools
    if tools.missing():
        raise HTTPException(503, tools.HELP)
    if jobs.busy_count() >= config.MAX_ACTIVE_JOBS_PER_USER:
        raise HTTPException(429, f"You already have {config.MAX_ACTIVE_JOBS_PER_USER} videos in progress. Wait for one to finish.")


_create_lock = threading.Lock()      # "do I have a free slot?" and "take it" happen together, so bursts cannot slip past the limit
_lookups = threading.BoundedSemaphore(8)   # link and channel previews running at once, whole server
_disk: dict = {}


def _quota():
    """Each account has a fixed amount of disk space. Measured at most once a minute."""
    if load.disk_full():
        from . import storage
        raise HTTPException(507, storage.full_message())
    uid = ctx.uid()
    got = _disk.get(uid)
    if not got or time.time() - got[0] > 60:
        total = 0
        for root, _dirs, files in os.walk(config.user_dir(uid)):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        got = _disk[uid] = (time.time(), total)
    if got[1] > config.MAX_USER_GB * 1e9:
        raise HTTPException(507, "Your storage is full. Delete some older videos to make room.")


def _throttle(key, limit, window, msg="Too many requests at once. Wait a moment."):
    try:
        auth.throttle(key, limit, window)
    except auth.AuthError:
        raise HTTPException(429, msg)


def _lookup(fn, *a):
    """Run a preview lookup, unless the server is already doing as many as it should."""
    if not _lookups.acquire(blocking=False):
        raise HTTPException(429, "Lots of people are looking things up right now. Try again in a few seconds.")
    try:
        return fn(*a)
    finally:
        _lookups.release()


def _creator_rows():
    out = []
    for c in creators.all_creators():
        if profiles.needs_refresh(c):
            profiles.refresh(c["id"])
        s = live.active_for(c["id"])
        out.append({**c, "live_job": s.job["id"] if s else None, "log": creators.read_log(c["id"], 30)})
    return out


def _sync_token() -> str:
    return f"{BOOT}.{ctx.version()}.{jobs.queue_epoch() if jobs.busy_count() else 0}"


def _sync_unchanged(v: str):
    """{"v": token} if the page is up to date and this user's creators are already in memory, else None."""
    mine = creators._mem.get(ctx.uid())
    if mine is None or not v or v != _sync_token():
        return None
    for c in list(mine.values()):                      # still keep live status fresh while the page is open
        if profiles.needs_refresh(c):
            profiles.refresh(c["id"])
    return {"v": v}


@app.get("/api/sync")
def sync(v: str = ""):
    """What the page asks every couple of seconds. It sends the token it got last time; when nothing of this
    user's has changed, the answer is just that token: no files read, nothing built."""
    token = _sync_token()
    if v == token:
        return _sync_unchanged(v) or {"v": token}
    from . import notices
    return {"v": token, "jobs": [jobs.public(j) for j in jobs.list_jobs()], "creators": _creator_rows(),
            "accounts": accounts.list_accounts(), "ai": usage.brief(), "unread": notices.unread()}


@app.get("/api/jobs")
def list_jobs():
    return [jobs.public(j) for j in jobs.list_jobs()]


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    return jobs.public(_job_or_404(jid))


@app.get("/api/preview")
def link_preview(request: Request, url: str = ""):
    url = url.strip()[:500]
    if not media.site_allowed(url):
        raise HTTPException(400, "Paste a full link to a YouTube, Twitch or Kick video.")
    try:
        auth.throttle(f"preview:{_me(request).id}", 12, 60)
    except auth.AuthError:
        raise HTTPException(429, "Too many links at once. Wait a moment.")
    try:
        return _lookup(media.preview, url)
    except media.YouTubeBlocked as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "Couldn't open that video. Check the link is public and complete.")


@app.post("/api/jobs")
def create_from_link(body: dict = Body(...)):
    _need_verified()
    _quota()
    cred = _credits()
    _throttle("newjob:" + ctx.uid(), 30, 600)
    url = str(body.get("url", "")).strip()[:500]
    if not media.site_allowed(url):
        raise HTTPException(400, "Paste a full link to a YouTube, Twitch or Kick video.")
    # even while YouTube is refusing us the video is queued: if it can't be fetched, its row offers the one-click
    # download and upload instead of an error
    creator = str(body.get("creator") or "") or None
    if creator and not creators.get(creator):
        raise HTTPException(404, "No such streamer")
    # ClipHeist: the link is a stream that is on air (seen when it was looked up as it was typed). It is recorded and
    # clipped as it happens, and the answer is that recording. A live link not looked up yet takes the usual way
    # and is handed over to a recording when its turn comes (jobs._run_job).
    if (media._PREVIEWS.get(url) or {}).get("is_live"):
        try:
            meta = media.info(url)
        except Exception:  # noqa: BLE001 - the usual way says what is wrong with the link
            meta = None
        if meta and meta.get("is_live"):
            try:
                return jobs.public(live.start_from_link(url, meta, watermark=bool(cred.get("watermark")) and not cred.get("unlimited")))
            except ValueError as e:
                raise HTTPException(400, str(e))
    # Same link, same look, already clipped by anyone: hand over those clips at once instead of making them again.
    try:
        opts_in = dict(body.get("options") or {})
        if not cred.get("unlimited"):
            opts_in["max_clips"] = max(1, min(int(opts_in.get("max_clips") or jobs.DEFAULTS["max_clips"]), cred["left"]))
        ready = jobs.find_ready(url, opts_in, watermark=bool(cred.get("watermark")))
        if ready:
            job = jobs.clone_ready(ready, opts_in, creator=creator, watermark=bool(cred.get("watermark")))
            from . import credits
            try:
                credits.spend(ctx.uid(), len(job["clips"]))
                job["charged"] = len(job["clips"])
                jobs.save(job)
            except Exception:  # noqa: BLE001
                pass
            return jobs.public(job)
    except (ValueError, TypeError):
        pass
    try:
        with _create_lock:
            _job_slots()
            job = jobs.create({"type": "url", "url": url, "name": url}, body.get("options") or {}, creator=creator, enqueue=False)
            _apply_credits(job, cred)
            jobs.enqueue(job)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e) if isinstance(e, ValueError) else "Those options aren't valid.")
    seen = media._PREVIEWS.get(url)  # already looked up while the user was typing: show it straight away
    if seen:
        job.update(title=seen["title"], uploader=seen["uploader"], duration=seen["duration"])
        jobs.save(job)
        uid, jid = ctx.uid(), job["id"]

        def thumb():                 # its picture is fetched after answering, so the page gets its card at once
            with ctx.as_user(uid):
                j = jobs.get(jid)
                if j and media.save_thumb(jobs.job_dir(jid), url=url):
                    j["thumb"] = True
                    jobs.save(j)
        threading.Thread(target=thumb, daemon=True).start()
    return jobs.public(job)


def _upload_name(raw) -> tuple:
    """A safe file name for an upload, and its type. Long names (downloaders make them long) are shortened in the
    middle, never at the end, so ".mp4" survives; a name with no ending at all is taken as mp4 (the file itself is
    checked when it is opened)."""
    name = re.sub(r"[^\w.\- ]", "_", Path(str(raw or "") or "video.mp4").name) or "video.mp4"
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    if not ext or not re.fullmatch(r"\.[a-z0-9]{2,4}", ext):
        stem, ext = name, ".mp4"
    return (stem[:110].rstrip(" .") or "video") + ext, ext


@app.post("/api/upload")
def create_from_file(file: UploadFile = File(...), options: str = Form("{}"), creator: str = Form("")):
    _need_verified()
    _job_slots()
    name, ext = _upload_name(file.filename)
    if ext not in VIDEO_TYPES:
        raise HTTPException(400, "Upload a video file (mp4, mov, mkv or webm).")
    if creator and not creators.get(creator):
        raise HTTPException(404, "No such streamer")
    try:
        opts = json.loads(options)
        opts = opts if isinstance(opts, dict) else {}
    except ValueError:
        opts = {}
    updir = config.TMP_DIR / "uploads"
    updir.mkdir(exist_ok=True)
    tmp = updir / f"{os.urandom(8).hex()}{ext}"
    limit, size = config.MAX_UPLOAD_MB * 1024 * 1024, 0
    try:
        with tmp.open("wb") as f:
            while chunk := file.file.read(4 * 1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"That file is too big. The limit is {config.MAX_UPLOAD_MB} MB.")
                f.write(chunk)
        try:
            with _create_lock:
                _job_slots()
                job = jobs.create({"type": "file", "path": str(tmp), "name": name}, opts, enqueue=False, creator=creator or None)
                _apply_credits(job, _credits())
        except (ValueError, TypeError):
            raise HTTPException(400, "Those options aren't valid.")
        _disk.pop(ctx.uid(), None)
        dest = jobs.job_dir(job["id"]) / ("source" + ext)
        shutil.move(str(tmp), dest)
    finally:
        tmp.unlink(missing_ok=True)
    job["source"]["path"] = str(dest)
    jobs.enqueue(job)
    return jobs.public(job)


# ---------------------------------------------------------------- big files, sent in pieces
# The page cuts a large file into pieces, sends each one, and asks for them to be joined. Each piece is small enough
# for anything standing in front of the app. Nothing is trusted from the page: the sizes are fixed when the upload
# starts, every piece must be exactly its size, and only the account that started an upload can add to it.
_UP_ID = re.compile(r"[0-9a-f]{24}")
_up_lock = threading.Lock()


def _up_root() -> Path:
    d = config.TMP_DIR / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


UP_IDLE_SEC = 15 * 60     # an upload of your own that hasn't moved for this long was left behind (tab closed, page reloaded)


def _up_active(d: Path) -> float:
    """When this upload last moved: its folder, or any piece in it (a piece being sent grows as it arrives)."""
    newest = d.stat().st_mtime
    for f in d.iterdir():
        try:
            newest = max(newest, f.stat().st_mtime)
        except OSError:
            pass
    return newest


def _up_sweep():
    """Forget uploads that were started and never finished: anyone's after six hours, and the asker's own once
    they have stood still for UP_IDLE_SEC. An upload cut short by closing the tab or reloading the page never
    says it stopped, and it must not keep counting against the limit of uploads at once."""
    me = ctx.uid()
    for d in _up_root().glob("up-*"):
        try:
            if not d.is_dir():
                continue
            idle = time.time() - _up_active(d)
            if idle > 6 * 3600:
                shutil.rmtree(d, ignore_errors=True)
            elif idle > UP_IDLE_SEC and json.loads((d / "meta.json").read_text(encoding="utf-8")).get("user") == me:
                shutil.rmtree(d, ignore_errors=True)
        except (OSError, ValueError):
            pass


def _up_get(upid: str) -> dict:
    """The upload, only if it belongs to whoever is asking."""
    if not _UP_ID.fullmatch(upid or ""):
        raise HTTPException(404, "No such upload")
    try:
        meta = json.loads((_up_root() / f"up-{upid}" / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(404, "This upload has expired. Start it again.")
    if meta.get("user") != ctx.uid():
        raise HTTPException(404, "No such upload")
    return meta


@app.post("/api/upload/start")
def upload_start(body: dict = Body(...)):
    _need_verified()
    _job_slots()
    _quota()
    _throttle("upstart:" + ctx.uid(), 30, 600)
    name, ext = _upload_name(body.get("name"))
    if ext not in VIDEO_TYPES:
        raise HTTPException(400, "Upload a video file (mp4, mov, mkv or webm).")
    size = body.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise HTTPException(400, "Bad file size.")
    if size > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"That file is too big. The limit is {config.MAX_UPLOAD_MB} MB.")
    if _disk.get(ctx.uid(), (0, 0))[1] + size > config.MAX_USER_GB * 1e9:
        raise HTTPException(507, "There isn't room for this file in your storage. Delete some older videos to make room.")
    creator = str(body.get("creator") or "")
    if creator and not creators.get(creator):
        raise HTTPException(404, "No such streamer")
    opts = body.get("options") if isinstance(body.get("options"), dict) else {}
    try:
        jobs.clean_options(opts)                 # refused now rather than after the whole file has been sent
    except (ValueError, TypeError):
        raise HTTPException(400, "Those options aren't valid.")
    _up_sweep()
    # Only uploads still moving count. One that got no piece for 15 minutes was abandoned (tab closed, page reloaded,
    # browser froze before the last step): it is dropped here, or it would block new uploads for hours.
    mine = 0
    for d in _up_root().glob("up-*"):
        try:
            if json.loads((d / "meta.json").read_text(encoding="utf-8")).get("user") != ctx.uid():
                continue
            if time.time() - max(f.stat().st_mtime for f in [d, *d.iterdir()]) > 15 * 60:
                shutil.rmtree(d, ignore_errors=True)
            else:
                mine += 1
        except (OSError, ValueError):
            pass
    if mine >= config.MAX_ACTIVE_JOBS_PER_USER:
        raise HTTPException(429, f"You already have {mine} uploads in progress. Let one finish first.")
    part = config.UPLOAD_PART_MB * 1024 * 1024
    upid = os.urandom(12).hex()
    d = _up_root() / f"up-{upid}"
    d.mkdir()
    meta = {"user": ctx.uid(), "name": name, "ext": ext, "size": size, "part": part, "parts": -(-size // part),
            "options": opts, "creator": creator, "t": time.time()}
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return {"id": upid, "part": part, "parts": meta["parts"]}


@app.put("/api/upload/{upid}/{n}")
async def upload_part(request: Request, upid: str, n: int):
    meta = await run_in_threadpool(_up_get, upid)
    if not 0 <= n < meta["parts"]:
        raise HTTPException(400, "No such piece.")
    want = meta["part"] if n < meta["parts"] - 1 else meta["size"] - meta["part"] * (meta["parts"] - 1)
    d = _up_root() / f"up-{upid}"
    tmp = d / f"{n:05d}.{os.urandom(4).hex()}.tmp"
    got = 0
    try:
        with tmp.open("wb") as f:
            async for chunk in request.stream():
                got += len(chunk)
                if got > want:
                    raise HTTPException(400, "That piece is the wrong size.")
                await run_in_threadpool(f.write, chunk)
        if got != want:
            raise HTTPException(400, "That piece is the wrong size.")
        tmp.replace(d / f"{n:05d}.part")           # sending a piece again simply replaces it
    finally:
        tmp.unlink(missing_ok=True)
    return {"ok": True, "n": n}


@app.post("/api/upload/{upid}/finish")
def upload_finish(upid: str):
    meta = _up_get(upid)
    _need_verified()
    d = _up_root() / f"up-{upid}"
    with _up_lock:                                    # two "finish" calls for one upload make one video, not two
        if not (d / "meta.json").is_file():
            raise HTTPException(404, "This upload has expired. Start it again.")
        parts = [d / f"{i:05d}.part" for i in range(meta["parts"])]
        missing = [i for i, p in enumerate(parts) if not p.is_file()]
        if missing:
            raise HTTPException(409, f"Piece {missing[0] + 1} of {meta['parts']} has not arrived yet.")
        if sum(p.stat().st_size for p in parts) != meta["size"]:
            raise HTTPException(400, "The pieces do not add up to the file.")
        try:
            with _create_lock:
                _job_slots()
                job = jobs.create({"type": "file", "path": "", "name": meta["name"]}, meta["options"], enqueue=False,
                                  creator=meta["creator"] or None)
                _apply_credits(job, _credits())
        except (ValueError, TypeError):
            raise HTTPException(400, "Those options aren't valid.")
        dest = jobs.job_dir(job["id"]) / ("source" + meta["ext"])
        try:
            with dest.open("wb") as out:
                for p in parts:
                    with p.open("rb") as src:
                        shutil.copyfileobj(src, out, 4 * 1024 * 1024)
        except OSError:
            jobs.delete(job["id"])
            raise HTTPException(507, "There wasn't room to save this file. Delete some older videos and try again.")
        shutil.rmtree(d, ignore_errors=True)
    _disk.pop(ctx.uid(), None)
    job["source"]["path"] = str(dest)
    jobs.enqueue(job)
    return jobs.public(job)


@app.delete("/api/upload/{upid}")
def upload_cancel(upid: str):
    _up_get(upid)
    shutil.rmtree(_up_root() / f"up-{upid}", ignore_errors=True)
    return {"ok": True}


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    job = jobs.get(jid)
    if not job:
        raise HTTPException(404, "No such video")
    try:
        return jobs.public(jobs.cancel(job))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/jobs/{jid}/clip-anyway")
def clip_anyway(jid: str):
    """The AI found nothing clip-worthy: look again, and this time take the best moments there are. The transcript
    is reused; only the AI looks again."""
    job = jobs.get(jid)
    if not job:
        raise HTTPException(404, "No such video")
    if job.get("kind") == "live":
        raise HTTPException(400, "For live recordings, use Clip now while it is recording.")
    if job["status"] in ("queued", "running", "live"):
        raise HTTPException(400, "This video is still being worked on.")
    if any(c.get("status") == "done" for c in job.get("clips", [])):
        raise HTTPException(400, "This video already has clips.")
    if not (jobs.job_dir(jid) / "words.json").is_file():
        raise HTTPException(400, "This video has no transcript to look at again. Add it again to make clips.")
    if (job.get("source") or {}).get("type") != "url" and not jobs._source_files(job):
        raise HTTPException(400, "The original upload is no longer on our server. Upload the video again to clip it.")
    _job_slots()
    job["options"]["force"] = True
    job.update(status="queued", stage="Waiting to look again", progress=0.0, error=None)
    jobs.enqueue(job)
    creators.log(job["creator"], f"Clip anyway asked for: {job.get('title') or 'a video'}") if job.get("creator") else None
    return jobs.public(job)


@app.post("/api/jobs/{jid}/clips/{cid}/render")
def rerender(jid: str, cid: str, body: dict = Body(...)):
    job = _job_or_404(jid)
    clip = next((c for c in job["clips"] if c["id"] == cid), None)
    if not clip:
        raise HTTPException(404, "No such clip")
    if job["status"] in ("queued", "running") or clip.get("status") in ("queued", "rendering"):
        raise HTTPException(409, "This clip is still being made. Try again when it has finished.")
    _throttle("render:" + ctx.uid(), 20, 600, "That's a lot of re-makes in a short time. Wait a few minutes.")
    try:
        jobs.request_render(jid, cid, body)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    return jobs.public(job)


def _clip_or_404(jid: str, cid: str):
    job = _job_or_404(jid)
    clip = next((c for c in job["clips"] if c["id"] == cid), None)
    if not clip:
        raise HTTPException(404, "No such clip")
    return job, clip


@app.put("/api/jobs/{jid}/clips/{cid}/text")
def clip_text(jid: str, cid: str, body: dict = Body(...)):
    """Save a clip's title, description and hashtags. The video is not made again."""
    job, clip = _clip_or_404(jid, cid)
    _throttle("cliptext:" + ctx.uid(), 120, 600)
    try:
        jobs.save_post_text(job, clip, body)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    return jobs.public(job)


@app.post("/api/jobs/{jid}/clips/{cid}/write")
def clip_write(jid: str, cid: str):
    """Ask the AI for a new title, description and hashtags for one clip. Returned to the page, not saved."""
    job, clip = _clip_or_404(jid, cid)
    _throttle("clipwrite:" + ctx.uid(), 20, 600, "That's a lot of rewrites in a short time. Wait a few minutes.")
    usage.set_context(job.get("creator"), job.get("title") or "")
    try:
        return brain.post_text(jobs.spoken_in(job, clip), job.get("title") or "", job.get("uploader") or "",
                               game=job.get("game") or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@app.patch("/api/jobs/{jid}/clips/{cid}")
def edit_clip_text(jid: str, cid: str, body: dict = Body(...)):
    job = _job_or_404(jid)
    if not any(c["id"] == cid for c in job["clips"]):
        raise HTTPException(404, "No such clip")
    jobs.update_text(jid, cid, body)
    return jobs.public(job)


@app.post("/api/jobs/{jid}/clips/{cid}/copy")
def copy_clip(jid: str, cid: str, body: dict = Body(...)):
    job = _job_or_404(jid)
    clip = next((c for c in job["clips"] if c["id"] == cid), None)
    if not clip:
        raise HTTPException(404, "No such clip")
    if job["status"] in ("queued", "running") or clip.get("status") in ("queued", "rendering"):
        raise HTTPException(409, "This clip is still being made. Try again when it has finished.")
    _throttle("render:" + ctx.uid(), 20, 600, "That's a lot of re-makes in a short time. Wait a few minutes.")
    try:
        new = jobs.copy_clip(jid, cid, body)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    return {"job": jobs.public(job), "clip": new}


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str, keep_clips: int = 0):
    job = _job_or_404(jid)
    if job["status"] in ("queued", "running", "live") or jid in live.SESSIONS:
        raise HTTPException(409, "This is still being worked on. Stop it or wait for it to finish first.")
    if keep_clips:
        account.drop_video(jid)              # the video goes, the clips made from it stay
    else:
        jobs.delete(jid)
    return {"ok": True, "kept": bool(keep_clips)}


def _pairs(v, limit=300) -> list:
    """[[video id, clip id], ...] from the page, checked."""
    out = []
    for x in (v if isinstance(v, list) else [])[:limit]:
        if isinstance(x, (list, tuple)) and len(x) == 2 and all(isinstance(y, str) and re.fullmatch(r"[\w\-]{1,60}", y) for y in x):
            out.append((x[0], x[1]))
    return out


@app.delete("/api/jobs/{jid}/clips/{cid}")
def delete_clip(jid: str, cid: str):
    _job_or_404(jid)
    try:
        if not account.delete_clip(jid, cid):
            raise HTTPException(404, "No such clip")
    except account.Busy as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.post("/api/bulk/delete")
def bulk_delete(body: dict = Body(...)):
    _throttle("bulkdel:" + ctx.uid(), 30, 600)
    vids = [x for x in (body.get("videos") if isinstance(body.get("videos"), list) else [])[:300]
            if isinstance(x, str) and re.fullmatch(r"[\w\-]{8,60}", x)]
    return account.delete_many(vids, _pairs(body.get("clips")), keep_clips=bool(body.get("keep_clips")))


@app.get("/api/clips.zip")
def clips_zip(ids: str = ""):
    """Many clips as one download. `ids` is video.clip pairs separated by commas."""
    _throttle("zip:" + ctx.uid(), 12, 600, "Give it a moment: you just downloaded several groups of clips.")
    pairs = _pairs([p.split(".", 1) for p in ids.split(",") if "." in p][:300])
    try:
        f = account.zip_clips(pairs)
    except ValueError as e:
        raise HTTPException(400, str(e))
    from starlette.background import BackgroundTask
    return FileResponse(f, filename=f"peakcut-clips-{len(pairs)}.zip", media_type="application/zip",
                        headers={"Cache-Control": "private, no-store"}, background=BackgroundTask(lambda: f.unlink(missing_ok=True)))


@app.delete("/api/creators/{cid}")
def delete_creator(cid: str, keep_videos: int = 0):
    _throttle("cdel:" + ctx.uid(), 30, 600)
    try:
        r = account.delete_creator(cid, keep_videos=bool(keep_videos))
    except account.Busy as e:
        raise HTTPException(409, str(e))
    if not r.get("ok"):
        raise HTTPException(404, "No such streamer")
    return r


@app.post("/api/account/delete")
def delete_my_account(request: Request, body: dict = Body(...)):
    """Deletes the signed-in account and everything in it. The page asks for the email to be typed, so it cannot
    happen by a stray click or a request the person did not mean to make."""
    me_ = _me(request)
    if str(body.get("confirm") or "").strip().lower() != me_.email.lower():
        raise HTTPException(400, "Type your email address exactly to confirm.")
    try:
        account.delete_account(me_.id)
    except account.Busy as e:
        raise HTTPException(409, str(e))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.cookie_args("x")["key"], path="/")
    return resp


@app.get("/media/{jid}/{name}")
def media_file(jid: str, name: str, download: int = 0):
    if not re.fullmatch(r"[\w\-]+\.(mp4|jpg)", name):
        raise HTTPException(404)
    job = _job_or_404(jid)
    f = Path(job["dir"]) / "clips" / name
    if not f.is_file():
        raise HTTPException(404)
    save_as = name
    if download and name.endswith(".mp4"):
        # a clip is saved under its title, not "clip01.mp4", the same as when several are downloaded as a zip
        clip = next((c for c in job.get("clips", []) if Path(str(c.get("file") or "")).name == name), None)
        if clip:
            save_as = account.clip_title_name(clip, Path(name).stem) + ".mp4"
    return FileResponse(f, filename=save_as if download else None, headers={"Cache-Control": "private, max-age=300"})


# ================================================================ creators and live recording
def _channel_ok(body: dict):
    links = [str(body.get("channel") or "").strip()]
    chs = body.get("channels")
    if chs is not None and not isinstance(chs, dict):
        raise HTTPException(400, "Bad channel list.")
    for plat, url in (chs or {}).items():
        url = str(url or "").strip()
        if url and creators.platform_key(url) != plat:
            raise HTTPException(400, f"That link isn't a {plat.title()} channel.")
        links.append(url)
    for ch in links:
        if ch and not media.site_allowed(live.normalize(ch)):
            raise HTTPException(400, "Channel links must be from YouTube, Twitch or Kick.")


@app.get("/api/admin/email-preview")
def email_preview(request: Request, kind: str = "verify"):
    """What account emails look like, for admins."""
    from . import mailer
    _admin(request)
    if kind == "reset":
        page = mailer.preview("Reset your password", "Someone asked to reset the password for this ClipHeist account. If that was you, choose a new one below.",
                              "Choose a new password", config.PUBLIC_URL + "/reset?t=example",
                              "The link works for one hour. If you didn't ask for this, ignore this email and your password stays the same.")
    else:
        page = mailer.preview()
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@app.post("/api/admin/test-email")
def test_email(request: Request):
    """Send the confirm-email template to the admin, to check the email service works."""
    from . import mailer
    me_ = _admin(request)
    if not mailer.ready():
        raise HTTPException(400, "No email service is set up yet. Add the PEAKCUT_SMTP settings to .env first.")
    mailer.send(me_.email, "ClipHeist test email", "Your emails are working",
                "This is a test from your ClipHeist server. Account emails like this one will now reach people.",
                "Open ClipHeist", config.PUBLIC_URL + "/app", "You can ignore this message.")
    return {"sent_to": me_.email}


@app.post("/api/feedback")
def send_feedback(request: Request, body: dict = Body(...)):
    from . import feedback
    u = _me(request)
    try:
        auth.throttle("feedback:" + u.id, 10, 3600)
        return feedback.submit(u.id, str(body.get("kind", "")), str(body.get("message", "")),
                               str(body.get("page", "")), str(body.get("context", "")))
    except auth.AuthError:
        raise HTTPException(429, "You've sent a lot of messages in the last hour. Try again a bit later.")
    except feedback.Refused as e:
        raise HTTPException(400, str(e))


@app.get("/api/admin/feedback")
def admin_feedback(request: Request, status: str = ""):
    from . import feedback
    _admin(request)
    return feedback.listing(status)


@app.post("/api/admin/feedback/seen")
def admin_feedback_seen(request: Request, body: dict = Body(...)):
    """The admin has these messages on screen, so they stop counting as new."""
    from . import feedback
    _admin(request)
    try:
        return {"seen": feedback.mark_seen(body.get("ids"))}
    except feedback.Refused as e:
        raise HTTPException(400, str(e))


@app.post("/api/admin/feedback/remove")
def admin_feedback_remove(request: Request, body: dict = Body(...)):
    from . import feedback
    me_ = _admin(request)
    try:
        return {"removed": feedback.remove(body.get("ids"), me_.id)}
    except feedback.Refused as e:
        raise HTTPException(400, str(e))


@app.post("/api/admin/feedback/{fid}")
def admin_feedback_status(request: Request, fid: int, body: dict = Body(...)):
    from . import feedback
    _admin(request)
    try:
        feedback.set_status(fid, str(body.get("status", "")))
    except feedback.Refused as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/notifications")
def notifications_list():
    from . import notices
    return notices.mine()


@app.post("/api/notifications/read")
def notifications_read():
    from . import notices
    notices.read_all()
    return {"ok": True}


@app.get("/api/me/photo")
def my_photo(request: Request):
    u = _me(request)
    got = auth.photo(u.id) if u else None
    if not got:
        raise HTTPException(404)
    return FileResponse(got[0], media_type=got[1], headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/channel-preview")
def channel_preview(request: Request, url: str = ""):
    url = url.strip()[:300]
    if not url or not media.site_allowed(live.normalize(url)):
        raise HTTPException(400, "Paste a YouTube, Twitch or Kick channel link.")
    try:
        auth.throttle(f"chanprev:{_me(request).id}", 12, 60)
    except auth.AuthError:
        raise HTTPException(429, "Too many lookups at once. Wait a moment.")
    try:
        return _lookup(profiles.preview, url)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "Couldn't open that channel. Check the link.")


# ================================================================ Live now: find a streamer, clip them 24/7
@app.get("/api/discover/img")
def discover_img(u: str = "", w: int = 160):
    """Streamers' and campaigns' pictures, fetched from the platforms' own image servers only (discover.IMG_HOSTS)."""
    try:
        got = discover.image(u[:600], w)
    except Exception:  # noqa: BLE001
        got = None
    if not got:
        raise HTTPException(404)
    return Response(got[0], media_type=got[1], headers={"Cache-Control": "private, max-age=" + ("240" if "previews-ttv" in u else "3600")})


@app.get("/api/discover/live")
def discover_live(lang: str = "en", kind: str = "gaming"):
    _throttle("disc:" + ctx.uid(), 40, 60)
    try:
        return {"streams": discover.top_live(lang, kind), "langs": discover.LANGS}
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "Couldn't reach Twitch just now. Try again in a minute.")


@app.get("/api/discover/gta")
def discover_gta():
    _throttle("disc:" + ctx.uid(), 40, 60)
    try:
        return {"streams": discover.game_live()}
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "Couldn't reach Twitch just now. Try again in a minute.")


@app.get("/api/discover/famous")
def discover_famous():
    _throttle("disc:" + ctx.uid(), 40, 60)
    try:
        return {"streamers": discover.famous(), "groups": discover.GROUPS, "max_live": config.MAX_LIVE_PER_USER}
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "Couldn't load the streamers just now. Try again in a minute.")


@app.get("/api/discover/search")
def discover_search(q: str = ""):
    _throttle("dsearch:" + ctx.uid(), 30, 60, "Searching too fast. Wait a moment.")
    try:
        return {"results": _lookup(discover.search, q)}
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "Search isn't working just now. Try again in a minute.")


@app.get("/api/campaigns")
def campaign_list():
    """Clipping campaigns to earn from (read from Content Rewards' public page, kept 30 minutes)."""
    _throttle("camp:" + ctx.uid(), 30, 60)
    try:
        return {"campaigns": campaigns.listing(), "source": campaigns.SOURCE}
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "Couldn't load the campaigns just now. Try again in a few minutes.")


@app.get("/api/campaigns/{camp}")
def campaign_one(camp: str):
    """One campaign's brief: the full description, pay on each platform, and how much more there is after joining."""
    _throttle("camp1:" + ctx.uid(), 60, 60)
    try:
        return campaigns.detail(camp)
    except LookupError:
        raise HTTPException(404, "That campaign isn't listed any more.")
    except Exception:
        raise HTTPException(503, "Couldn't load that campaign just now. Try again in a few minutes.")


def _campaign_done(cid: str, c: dict, e: dict, added: list, what: str) -> dict:
    if cid:
        creators.log(cid, f"{what}: {e['name']}")
    for x in creators.all_creators():          # running recordings follow from their next clip on
        if x.get("campaign_use") == e["key"] or x["id"] == cid:
            live.follow_campaign(x)
    return {"creator": c, "campaign": e, "tags": added, "campaigns": creators.lib_all()}


def _campaign_or_404(cid: str, key: str):
    c = creators.get(cid) if cid else None
    e = creators.campaign(c, key)
    if not e:
        raise HTTPException(404, "That campaign isn't in your campaigns any more.")
    return c, e


# ---- My campaigns: set up once, used by any streamer, video or recording
def _usage() -> dict:
    out = {}
    for e in creators.lib_all():
        out[e["key"]] = {"streamers": [], "videos": 0, "clips": 0, "posted": 0, "planned": 0}
    for c in creators.all_creators():
        for k in c.get("campaign_keys", []):
            if k in out:
                out[k]["streamers"].append({"id": c["id"], "name": c["name"], "use": c.get("campaign_use") == k})
    for j in jobs.list_jobs():
        u = out.get(j.get("campaign") or "")
        if not u:
            continue
        u["videos"] += 1
        for cl in j.get("clips", []):
            if cl.get("status") != "done":
                continue
            u["clips"] += 1
            st = [p.get("status") for p in (cl.get("posts") or {}).values()]
            u["posted"] += "posted" in st
            u["planned"] += any(x in ("scheduled", "posting") for x in st)
    return out


@app.get("/api/templates")
def list_templates():
    return templates.all_templates()


def _tpl_err(fn, *a):
    try:
        return fn(*a)
    except KeyError:
        raise HTTPException(404, "That style isn't there any more.")
    except ValueError as er:
        raise HTTPException(400, str(er))


@app.post("/api/templates")
def add_template(body: dict = Body(...)):
    _throttle("tpl:" + ctx.uid(), 60, 600)
    t = _tpl_err(templates.add, body)
    return {**templates.all_templates(), "saved": t["id"]}


def _restyle():
    """A style changed: running recordings make their next clips with it straight away, and clips still waiting
    to be made in any video take it too."""
    for c in creators.all_creators():
        if live.active_for(c["id"]):
            live.follow_campaign(creators.get(c["id"]))
    jobs.restyle()


@app.put("/api/templates/{tid}")
def edit_template(tid: str, body: dict = Body(...)):
    _throttle("tpl:" + ctx.uid(), 120, 600)
    _tpl_err(templates.edit, tid, body)
    _restyle()
    return {**templates.all_templates(), "saved": tid}


@app.delete("/api/templates/{tid}")
def drop_template(tid: str):
    _tpl_err(templates.drop, tid)
    for c in creators.all_creators():          # streamers that used it go back to the default template
        if c.get("template") == tid:
            creators.update(c["id"], {"template": ""})
    _restyle()
    return templates.all_templates()


@app.post("/api/templates/{tid}/default")
def default_template(tid: str):
    _tpl_err(templates.set_default, tid)
    _restyle()
    return templates.all_templates()


@app.get("/api/templates/sample.{ext}")
def style_sample(ext: str):
    """The ten seconds of video (its first picture, and a blurred one) that style previews play."""
    if ext not in ("mp4", "jpg", "blur.jpg"):
        raise HTTPException(404)
    try:
        mp4, jpg = templates.sample()
        if ext == "jpg":
            templates.sample_cam()
    except Exception:  # noqa: BLE001
        raise HTTPException(404, "No sample yet")
    f = mp4 if ext == "mp4" else jpg if ext == "jpg" else jpg.with_name("style-sample-blur.jpg")
    return FileResponse(f, media_type="video/mp4" if ext == "mp4" else "image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.post("/api/templates/sample/cam")
def style_sample_cam():
    """Find the streamer's cam in the preview video again, now (to try the AI on it)."""
    _throttle("tplcam:" + ctx.uid(), 20, 600)
    try:
        templates.sample()
        (config.user_dir(ctx.uid()) / "style-sample-cam.json").unlink(missing_ok=True)
        cam = templates.sample_cam()
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "Couldn't read your preview video. Pick another one in Preview video.")
    return {**templates.all_templates(), "found": bool(cam)}


@app.get("/api/templates/videos")
def style_videos():
    """Your videos and recordings that style previews can play."""
    return {"videos": templates.sample_videos()}


@app.get("/api/templates/videos/{jid}/thumb.jpg")
def style_video_thumb(jid: str):
    f = templates.video_thumb(jid)
    if not f:
        raise HTTPException(404)
    return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.post("/api/templates/videos/{jid}")
def style_video_pick(jid: str):
    """Style previews play this video from now on."""
    _throttle("tplvid:" + ctx.uid(), 20, 600)
    try:
        cam = templates.pick_sample(jid)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]))
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "Couldn't read that video. Try another one.")
    return {**templates.all_templates()}


def _creator_or_404(cid):
    try:
        c = creators.get(cid)
    except ValueError:
        c = None
    if not c:
        raise HTTPException(404, "No such streamer")
    return c


@app.get("/api/creators/{cid}/cam-frame.jpg")
def creator_cam_frame(cid: str):
    """A recent picture of the streamer's stream, to see and set where their cam is."""
    c = _creator_or_404(cid)
    srcs = jobs.sources_of(c["id"], 3)
    if not srcs:
        raise HTTPException(404, "No video of this streamer yet.")
    out = creators.root(c["id"]) / "cam-frame.jpg"
    if not out.is_file() or time.time() - out.stat().st_mtime > 6 * 3600:
        try:
            media.run(["ffmpeg", "-y", "-v", "error", "-ss", "2", "-i", srcs[0], "-frames:v", "1", "-vf", "scale=1280:720", "-q:v", "4", str(out)], timeout=60)
        except Exception:  # noqa: BLE001
            raise HTTPException(404, "Couldn't read a picture from this streamer's video.")
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/creators/{cid}/cam/find")
def creator_cam_find(cid: str):
    """Look for the streamer's cam in their newest videos again."""
    from . import facecam
    c = _creator_or_404(cid)
    _throttle("camfind:" + ctx.uid(), 20, 600)
    srcs = jobs.sources_of(c["id"], 6)
    if not srcs:
        raise HTTPException(400, "Clip a video or a live stream of this streamer first, then their cam can be found.")
    box = facecam.find(srcs, 12)
    c = creators.update(c["id"], {"cam": box, "cam_auto": bool(box), "cam_none": not box})
    return {"creator": c, "found": bool(box)}


@app.get("/fonts/{name}")
def caption_font(name: str):
    """The caption fonts, so the template preview draws with the same letters as the clips."""
    if name not in templates.FONTS.values():
        raise HTTPException(404, "No such font")
    return FileResponse(Path(render.__file__).resolve().parent / "fonts" / name, media_type="font/ttf",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/api/my-campaigns")
def my_campaigns():
    return {"campaigns": creators.lib_all(), "usage": _usage()}


@app.post("/api/my-campaigns")
def my_campaign_add(body: dict = Body(...)):
    """A campaign joins My campaigns: a listed one by "id" (its name, link, pay and brief filled in), or one set up
    by hand. It waits to be set up (logo, hashtags) before anything uses it."""
    _throttle("ccamp:" + ctx.uid(), 60, 600)
    entry = _listed_or_hand(body)
    try:
        e, tags = creators.lib_add(entry)
    except ValueError as er:
        raise HTTPException(400, str(er))
    return _campaign_done("", None, e, tags, "Campaign added")


@app.put("/api/my-campaigns/{key}")
def my_campaign_edit(key: str, body: dict = Body(...)):
    _throttle("ccamp:" + ctx.uid(), 60, 600)
    try:
        e, added = creators.lib_edit(key, body)
    except KeyError:
        raise HTTPException(404, "That campaign isn't in your campaigns any more.")
    except ValueError as er:
        raise HTTPException(400, str(er))
    return _campaign_done("", None, e, added, "Campaign changed")


@app.delete("/api/my-campaigns/{key}")
def my_campaign_drop(key: str):
    try:
        e = creators.lib_drop(key)
    except KeyError:
        raise HTTPException(404, "That campaign isn't in your campaigns any more.")
    return {"campaign": e, "campaigns": creators.lib_all()}


def _listed_or_hand(body: dict) -> dict:
    camp = str(body.get("id") or "")
    if not camp:
        return {k: body.get(k) for k in ("name", "url", "rate", "brief")}
    try:
        return campaigns.for_creator(camp)
    except LookupError:
        raise HTTPException(404, "That campaign isn't listed any more.")
    except Exception:
        raise HTTPException(503, "Couldn't load that campaign just now. Try again in a few minutes.")


@app.post("/api/creators/{cid}/campaigns")
def creator_campaign_add(cid: str, body: dict = Body(...)):
    """Add a campaign to a creator; a creator can be clipped for several. With "id" it is a listed campaign and its
    name, link, pay and brief are filled in; without, it is set up by hand (name, url, rate, brief). The hashtags
    in the brief go onto every post."""
    _throttle("ccamp:" + ctx.uid(), 60, 600)
    if not creators.get(cid):
        raise HTTPException(404, "No such streamer")
    entry = _listed_or_hand(body)
    try:
        return _campaign_done(cid, *creators.add_campaign(cid, entry), "Campaign added")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/creators/{cid}/campaigns/{key}")
def creator_campaign_edit(cid: str, key: str, body: dict = Body(...)):
    _throttle("ccamp:" + ctx.uid(), 60, 600)
    try:
        return _campaign_done(cid, *creators.edit_campaign(cid, key, body), "Campaign changed")
    except KeyError:
        raise HTTPException(404, "That campaign isn't on this streamer any more.")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/creators/{cid}/campaigns/{key}")
def creator_campaign_drop(cid: str, key: str):
    try:
        c, e = creators.drop_campaign(cid, key)
    except KeyError:
        raise HTTPException(404, "That campaign isn't on this streamer any more.")
    return _campaign_done(cid, c, e, [], "Campaign removed")


@app.post("/api/my-campaigns/{key}/logo/brand")
def my_campaign_logo_brand(key: str):
    campaign_logo_brand("", key)
    return creators.campaign(None, key)


@app.post("/api/creators/{cid}/campaigns/{key}/logo/brand")
def campaign_logo_brand(cid: str, key: str):
    """Use the picture a listed campaign's brand shows on its page as that campaign's clip logo."""
    _throttle("logo:" + ctx.uid(), 20, 600)
    c, e = _campaign_or_404(cid, key)
    try:
        got = campaigns.brand_logo(e["id"]) if e["id"] else None
    except Exception:
        got = None
    ext = {"image/webp": ".webp", "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif"}.get(got[1]) if got else None
    if not ext or len(got[0]) > MAX_LOGO_MB * 1024 * 1024:
        raise HTTPException(404, "This campaign has no brand picture to use. Upload the logo from its brief instead.")
    name = f"logo-{key}{ext}"
    (creators.lib_logo_dir() / name).write_bytes(got[0])
    return creators.set_logo(cid, key, name)


@app.post("/api/discover/follow")
def discover_follow(body: dict = Body(...)):
    """Clip this streamer 24/7: add them as a creator (or use the one already added) with auto-record on, and if
    they are live right now, start recording straight away. With "record": false they are only added: nothing is
    recorded and auto-record stays as it is, until the person says so."""
    only_add = body.get("record") is False
    _throttle("dfollow:" + ctx.uid(), 20, 600)
    name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()[:60]
    raw = body.get("channels")
    if not name or not isinstance(raw, dict):
        raise HTTPException(400, "Pick a streamer first.")
    chs = {k: creators.channel_link(str(v).strip()[:300]) for k, v in raw.items() if k in creators.PLATFORMS and str(v or "").strip()}
    if not chs:
        raise HTTPException(400, "Pick a streamer first.")
    _channel_ok({"channels": chs})
    want = {u.rstrip("/").lower() for u in chs.values()}
    mine = next((c for c in creators.all_creators() if want & {u.rstrip("/").lower() for u in c["channels"].values()}), None)
    try:
        if mine:
            c = creators.update(mine["id"], {"channels": {**chs, **mine["channels"]}, **({} if only_add else {"auto_watch": True})})
        else:
            if len(creators.all_creators()) >= config.MAX_CREATORS:
                raise HTTPException(400, f"This server allows {config.MAX_CREATORS} creators per account.")
            first = chs.get(str(body.get("live_on") or "")) or next(iter(chs.values()))
            c = creators.create({"name": name, "channels": chs, "channel": first, "auto_watch": not only_add})
            creators.log(c["id"], "Added from the suggestions" if only_add else "Added from Live now: clipping 24/7 whenever they go live")
    except ValueError as e:
        raise HTTPException(400, str(e))
    profiles.refresh(c["id"], force=True)
    s = live.active_for(c["id"])
    if s:
        s.settings = c
    if only_add:
        return {"creator": c, "existed": bool(mine), "recording": bool(s), "waiting_for": ""}
    others = [x["name"] for x in creators.all_creators() if x["id"] != c["id"] and live.active_for(x["id"])]
    waiting = others[0] if not s and len(others) >= config.MAX_LIVE_PER_USER else ""
    if not s and not waiting:
        live.check_now(c["id"])
    return {"creator": c, "existed": bool(mine), "recording": bool(s), "waiting_for": waiting}


@app.get("/api/creators/{cid}/avatar")
def creator_avatar(cid: str, p: str = ""):
    c = creators.get(cid)
    if not c:
        raise HTTPException(404)
    key = p if p in creators.PLATFORMS else creators.platform_key(c["channel"])
    prof = (c.get("profiles") or {}).get(key) or {}
    f = creators.root(cid) / f"avatar-{key}.img"
    if not f.is_file() or prof.get("pic") not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(404)
    return FileResponse(f, media_type=prof["pic"], headers={"Cache-Control": "private, max-age=86400"})


MAX_LOGO_MB = 25


@app.post("/api/my-campaigns/{key}/logo")
async def my_campaign_logo(key: str, file: UploadFile = File(...)):
    await campaign_logo("", key, file)
    return creators.campaign(None, key)


@app.post("/api/creators/{cid}/campaigns/{key}/logo")
async def campaign_logo(cid: str, key: str, file: UploadFile = File(...)):
    """A campaign's logo: a picture, an animated GIF or a short video, put on every clip made for that campaign."""
    _throttle("logo:" + ctx.uid(), 20, 600)
    _campaign_or_404(cid, key)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in creators.LOGO_TYPES:
        raise HTTPException(400, "Upload a PNG, JPG, WebP or GIF picture, or an MP4, MOV or WebM video.")
    dest = creators.lib_logo_dir() / f"logo-{key}{ext}"
    tmp = dest.with_suffix(ext + ".part")
    size = 0
    try:
        with tmp.open("wb") as f:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_LOGO_MB * 1024 * 1024:
                    raise HTTPException(413, f"That logo is too big. The limit is {MAX_LOGO_MB} MB.")
                f.write(chunk)
        def picture(path):          # a logo has no sound: only ask whether ffmpeg can read a picture out of it
            out = media.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                             "-of", "json", str(path)])
            st = (json.loads(out).get("streams") or [{}])[0]
            return {"width": int(st.get("width") or 0), "height": int(st.get("height") or 0)}
        try:
            meta = await run_in_threadpool(picture, tmp)
        except Exception:  # noqa: BLE001
            meta = {}
        if not meta.get("width") or not meta.get("height"):
            raise HTTPException(400, "That file couldn't be read as a picture or video. Try a PNG with a see-through background.")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    c = creators.set_logo(cid, key, dest.name)
    if cid:
        creators.log(cid, f"Logo added to {creators.campaign(c, key)['name']}: it goes on that campaign's new clips")
    return c


@app.delete("/api/my-campaigns/{key}/logo")
def my_campaign_logo_remove(key: str):
    _campaign_or_404("", key)
    creators.set_logo("", key, "")
    return creators.campaign(None, key)


@app.get("/api/my-campaigns/{key}/logo")
def my_campaign_logo_file(key: str):
    return campaign_logo_file("", key)


@app.delete("/api/creators/{cid}/campaigns/{key}/logo")
def campaign_logo_remove(cid: str, key: str):
    _campaign_or_404(cid, key)
    return creators.set_logo(cid, key, "")


@app.get("/api/creators/{cid}/campaigns/{key}/logo")
def campaign_logo_file(cid: str, key: str):
    f = creators.logo_path(None, key)
    if not f:
        raise HTTPException(404)
    kinds = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
             ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
    return FileResponse(f, media_type=kinds.get(f.suffix.lower(), "application/octet-stream"), headers={"Cache-Control": "private, no-cache"})


@app.get("/api/creators")
def list_creators():
    return _creator_rows()


@app.post("/api/creators")
def new_creator(body: dict = Body(...)):
    _channel_ok(body)
    if len(creators.all_creators()) >= config.MAX_CREATORS:
        raise HTTPException(400, f"This server allows {config.MAX_CREATORS} creators per account.")
    try:
        c = creators.create(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if c.get("channels"):
        profiles.refresh(c["id"], force=True)
    return c


@app.put("/api/creators/{cid}")
def edit_creator(cid: str, body: dict = Body(...)):
    _throttle("cedit:" + ctx.uid(), 60, 600)
    _channel_ok(body)
    try:
        c = creators.update(cid, body)
    except KeyError:
        raise HTTPException(404, "No such streamer")
    except ValueError as e:
        raise HTTPException(400, str(e))
    live.follow_campaign(c)     # new settings and the campaign in use apply to the running recording straight away
    if "channels" in body or "channel" in body:
        profiles.refresh(cid, force=True)
    if body.get("auto_watch") and c.get("auto_watch") and not live.active_for(cid):
        live.check_now(cid)    # if they are live right now, recording starts within seconds
    return c


@app.post("/api/creators/{cid}/live")
def go_live(cid: str, body: dict = Body(default={})):
    _need_verified()
    try:
        c = creators.get(cid)
    except ValueError:
        c = None
    if not c:
        raise HTTPException(404, "No such streamer")
    # the stream the user is watching, else where they are live right now, else the channel chosen in Setup
    on_air = [p["channel"] for k, p in (c.get("profiles") or {}).items() if p.get("live") and c["channels"].get(k) == p.get("channel")]
    asked = str((body or {}).get("channel") or "")
    url = asked if asked in c["channels"].values() else c["channel"] if c["channel"] in on_air or not on_air else on_air[0]
    if c.get("skip_stream"):                     # recording by hand again: auto-record is back to normal too
        c.pop("skip_stream", None)
        creators._write(c)
    try:
        return jobs.public(live.start_session(c, url))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/live/{jid}/stop")
def stop_live(jid: str, body: dict = Body(default={})):
    """Stop a recording. `auto` says what auto-record does next: "skip" waits for their next stream (it doesn't
    start this one again), "off" turns auto-record off, anything else leaves it as it is."""
    s = live.mine(jid)
    if not s:
        raise HTTPException(404, "This recording has already ended")
    cid, auto = s.job.get("creator"), str((body or {}).get("auto") or "")
    s.stop()
    if cid and auto in ("skip", "off"):
        c = creators.get(cid)
        if c:
            if auto == "off":
                creators.update(cid, {"auto_watch": False})
            else:
                c["skip_stream"] = True          # cleared by the watcher once they are seen offline
                creators._write(c)
    return {"ok": True}


@app.post("/api/live/{jid}/clip")
def clip_now(jid: str, body: dict = Body(...)):
    s = live.mine(jid)
    if not s or s.ended:
        raise HTTPException(404, "This recording has already ended")
    _throttle("clipnow:" + ctx.uid(), 6, 60, "Give it a moment: your last clips are still being made.")
    if len(s.manual) >= 3:
        raise HTTPException(429, "Give it a moment: your last clips are still being made.")
    try:
        s.clip_last(max(5, min(180, float(body.get("seconds", 30)))))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ================================================================ AI usage
@app.get("/api/usage")
def ai_usage():
    return usage.summary({c["id"]: c["name"] for c in creators.all_creators()})


# ---------------------------------------------------------------- the AI key and model, set from the dashboard
# Only the owner (or an admin, when accounts are on) reaches these. The key is never sent back to the page.
def _ai_error(e) -> str:
    return safe.friendly(e, 300).replace(ai.key() or "\0", "<key>")


@app.get("/api/ai")
def ai_get(request: Request):
    _admin(request)
    return ai.public()


@app.put("/api/ai")
def ai_put(request: Request, body: dict = Body(...)):
    _admin(request)
    k = body.get("key")
    try:
        out = ai.save(body.get("provider"), body.get("models"), body.get("base_url") or "",
                      None if k is None or (isinstance(k, str) and not k.strip() and not body.get("remove_key")) else str(k))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    usage.forget_all()
    ctx.bump()
    return out


@app.post("/api/ai/models")
def ai_models(request: Request, body: dict = Body(default={})):
    _admin(request)
    _throttle("aimodels:" + ctx.uid(), 30, 600)
    try:
        return {"models": ai.list_models(body.get("provider"), body.get("base_url"), body.get("key"))}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        reason = ai.explain(e)
        raise HTTPException(502, f"The service did not give its list of models. {reason}" if reason else
                            "The service did not give its list of models. Check the key, or type the model yourself. " + _ai_error(e)[:160])


@app.post("/api/ai/test")
def ai_test(request: Request):
    _admin(request)
    _throttle("aitest:" + ctx.uid(), 20, 600)
    try:
        return ai.test()
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, ai.explain(e) or "The AI did not answer: " + _ai_error(e))


# ---------------------------------------------------------------- speech models, one per spoken language
# Only the owner (or an admin, when accounts are on) can download or delete them: they are shared by everyone here.
def _speech_lang(lang: str) -> str:
    from . import fetch_model
    if not fetch_model.known(lang):
        raise HTTPException(400, "ClipHeist has no speech model for that language.")
    return lang


@app.get("/api/speech")
def speech_list(request: Request):
    _admin(request)
    from . import fetch_model
    return {"models": [{"lang": k, "label": m[0], "size_mb": m[3], "credit": m[4], "state": fetch_model.status(k),
                        "percent": fetch_model.state[k]["percent"], "error": fetch_model.state[k]["error"],
                        "default": k == fetch_model.DEFAULT} for k, m in fetch_model.MODELS.items()]}


@app.post("/api/speech/{lang}")
def speech_download(request: Request, lang: str):
    _admin(request)
    from . import fetch_model
    fetch_model.start(_speech_lang(lang))
    ctx.bump()
    return speech_list(request)


@app.delete("/api/speech/{lang}")
def speech_remove(request: Request, lang: str):
    _admin(request)
    from . import fetch_model
    try:
        fetch_model.remove(_speech_lang(lang))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    ctx.bump()
    return speech_list(request)


@app.post("/webhooks/meta/deauthorize")
@app.post("/webhooks/meta/data-deletion")
async def meta_removal(request: Request):
    """Meta calls this when someone removes ClipHeist in Facebook, or asks for their data to be deleted: their
    connected Instagram accounts are removed here, and a page tells them it was done."""
    form = await request.form()
    try:
        fb_user = await run_in_threadpool(accounts.meta_user, str(form.get("signed_request", "")))
    except ValueError:
        return Response(status_code=400)
    n = await run_in_threadpool(accounts.forget_facebook_user, fb_user)
    code = hashlib.sha256(f"{fb_user}:{time.time()}".encode()).hexdigest()[:16]
    print(f"meta removal: {n} Instagram account(s) removed, code {code}", flush=True)
    return JSONResponse({"url": f"{config.PUBLIC_URL}/data-deletion?code={code}", "confirmation_code": code})


# ================================================================ posting, accounts, settings
@app.get("/api/overview")
def overview():
    return posting.overview()


@app.get("/api/settings")
def get_settings():
    return accounts.public_settings()


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    try:
        accounts.save_settings(body)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    return accounts.public_settings()


@app.get("/api/accounts")
def list_accounts():
    return accounts.list_accounts()


@app.delete("/api/accounts/{aid}")
def remove_account(aid: str):
    accounts.remove(aid)
    return {"ok": True}


# ---------------------------------------------------------------- connecting YouTube, Instagram and TikTok
# Connect sends the browser to the platform's own sign-in page; the platform sends it back here, and the person lands
# on Settings, Connected apps, with the account added (or a message saying what went wrong).
def _back(**q):
    from urllib.parse import urlencode as _ue
    return RedirectResponse("/app/settings/apps?" + _ue(q), 303)


@app.get("/oauth/{platform}/start")
def connect_start(request: Request, platform: str):
    if platform not in accounts.PLATFORM_NAMES:
        raise HTTPException(404)
    if not accounts.app_open(platform) and not getattr(_me(request), "is_admin", False):
        return _back(connect_error=f"Connecting {accounts.PLATFORM_NAMES[platform]} isn't open to everyone here yet. An admin can open it in Settings, Connected apps.")
    try:
        return RedirectResponse(accounts.connect_url(platform), 303)
    except ValueError as e:
        return _back(connect_error=str(e))


@app.get("/oauth/{platform}/callback")
def connect_callback(platform: str, code: str = "", state: str = "", error: str = "", error_description: str = "",
                     error_reason: str = ""):
    if platform not in accounts.PLATFORM_NAMES:
        raise HTTPException(404)
    name = accounts.PLATFORM_NAMES[platform]
    if error or not code:
        why = "you cancelled" if error in ("access_denied",) or error_reason == "user_denied" else (error_description or error or "no sign-in code came back")
        return _back(connect_error=f"{name} was not connected: {why[:160]}.")
    try:
        _throttle("connect:" + ctx.uid(), 30, 600)
        acc = accounts.connect_finish(platform, code, state)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raw = str(e)
        if "has not been used in project" in raw or "SERVICE_DISABLED" in raw or "accessNotConfigured" in raw:
            # a setting of the server's own app (the platform's API is switched off for it), not something the user can fix
            print(f"{name} connect refused, its API is off for this app: {raw[:300]}", flush=True)
            return _back(connect_error=f"{name} posting isn't switched on for this server's {name} app yet. "
                                       "Whoever runs ClipHeist needs to turn its API on in the platform's developer console.")
        return _back(connect_error=f"{name} was not connected: {safe.friendly(e, 420)}")
    return _back(connected=platform, name=acc["name"])


@app.get("/pub/{token}.mp4")
def public_clip(token: str):
    """A clip for a platform to fetch while posting it: one clip, one hour, signed (see accounts.clip_link)."""
    got = accounts.read_clip_link(token)
    if not got:
        raise HTTPException(404)
    uid, job_id, name = got
    with ctx.as_user(uid):
        job = jobs.get(job_id) if re.fullmatch(r"[\w\-]{8,60}", job_id) else None
        f = Path(job["dir"]) / "clips" / name if job else None
    if not f or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})


# ---------------------------------------------------------------- where ClipHeist keeps everything (Settings, Storage)
def _on_this_computer(request: Request) -> bool:
    """Is the person sitting at the computer ClipHeist runs on? Only then can a folder window be opened for them."""
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost") and not config.env("PEAKCUT_TRUST_PROXY")


@app.get("/api/storage")
def storage_get(request: Request):
    from . import storage
    _admin(request)
    return storage.info(_on_this_computer(request))


@app.post("/api/storage/open")
def storage_open(request: Request):
    from . import storage
    _admin(request)
    if not _on_this_computer(request):
        raise HTTPException(400, "The folder can only be opened on the computer ClipHeist runs on.")
    try:
        storage.open_folder()
    except OSError as e:
        raise HTTPException(500, "Couldn't open the folder: " + safe.friendly(e, 200))
    return {"ok": True}


@app.post("/api/storage/pick")
def storage_pick(request: Request):
    from . import storage
    _admin(request)
    if not _on_this_computer(request):
        raise HTTPException(400, "Type the folder's path instead: a folder window can only be shown on the computer ClipHeist runs on.")
    return {"path": storage.pick_folder()}


@app.put("/api/storage")
def storage_put(request: Request, body: dict = Body(...)):
    from . import storage
    _admin(request)
    try:
        if body.get("cancel"):
            storage.cancel_change()
            return storage.info(_on_this_computer(request))
        storage.change(str(body.get("path") or ""), bool(body.get("move", True)))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return storage.info(_on_this_computer(request))


@app.get("/api/apps")
def apps_get(request: Request):
    _admin(request)
    return accounts.apps_public()


@app.put("/api/apps")
def apps_put(request: Request, body: dict = Body(...)):
    _admin(request)
    try:
        return accounts.save_app(str(body.get("platform", "")), body.get("client_id"), body.get("client_secret"),
                                 body.get("config_id"), body.get("open"))
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------- YouTube cookies, for servers YouTube refuses
# A secret like a password: it is saved to a file only this server reads, and never sent back to the page.
@app.get("/api/youtube-cookies")
def yt_cookies_get(request: Request):
    _admin(request)
    f = config.YT_COOKIES
    return {"saved": f.is_file(), "updated": int(f.stat().st_mtime) if f.is_file() else 0}


@app.put("/api/youtube-cookies")
def yt_cookies_put(request: Request, body: dict = Body(...)):
    _admin(request)
    f, text = config.YT_COOKIES, str(body.get("text") or "")
    if body.get("remove"):
        f.unlink(missing_ok=True)
    else:
        rows = [l.split("\t") for l in text.splitlines() if l.strip() and not l.startswith("# ")]
        if len(text) > 1_000_000 or not any(len(r) == 7 and "youtube.com" in r[0] for r in rows):
            raise HTTPException(400, "That doesn't look like a cookies.txt file with YouTube cookies in it. Export it "
                                     "from a browser signed in to YouTube, with an extension such as \"Get cookies.txt LOCALLY\".")
        text = text if text.startswith("# Netscape") else "# Netscape HTTP Cookie File\n" + text
        f.write_text(text, encoding="utf-8")
        with contextlib.suppress(OSError):
            f.chmod(0o600)
    media._yt_refused_until = 0.0        # try YouTube again straight away
    return yt_cookies_get(request)


@app.post("/api/posts")
def add_posts(body: dict = Body(...)):
    job = jobs.get(str(body.get("job")))
    clip = next((c for c in job["clips"] if c["id"] == body.get("clip")), None) if job else None
    if not clip or clip.get("status") != "done":
        raise HTTPException(404, "That clip is not ready")
    try:
        made = posting.enqueue(job, clip, [str(a) for a in body.get("accounts", [])][:20], now=bool(body.get("now")), at=body.get("at"), day=body.get("day"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not made:
        raise HTTPException(400, "Nothing was queued. Pick at least one account this clip has not already gone to.")
    return {"queued": len(made), "paused": accounts.settings()["paused"]}


@app.get("/api/schedule")
def get_schedule(start: float, end: float):
    try:
        return posting.schedule(start, end)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/posts/{pid}/move")
def move_post(pid: str, body: dict = Body(...)):
    try:
        return posting.move(pid, body.get("at"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/posts/{pid}/{action}")
def post_action(pid: str, action: str):
    try:
        {"cancel": posting.cancel, "retry": posting.retry, "now": posting.post_now}[action](pid)
    except KeyError:
        raise HTTPException(404)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/alerts/test")
def test_alert():
    if not alerts.configured():
        raise HTTPException(400, "Save a Discord webhook or Telegram details first.")
    auth_problem = None
    try:
        auth.throttle("alert:" + ctx.uid(), 10, 600)
    except auth.AuthError as e:
        auth_problem = str(e)
    if auth_problem:
        raise HTTPException(429, auth_problem)
    problems = alerts.send("ClipHeist: test message. Alerts are working.")
    if problems:
        raise HTTPException(400, "; ".join(problems))
    return {"ok": True}


# ================================================================ admin (only accounts marked as admin in the database)
# The gate above already answers "not found" to everyone else; each route checks again so neither can be forgotten.
def _uid_ok(uid: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]{6,40}", uid):
        raise HTTPException(404)
    return uid


@app.get("/api/admin/overview")
def admin_overview(request: Request):
    _admin(request)
    return admins.overview()


@app.get("/api/admin/users")
def admin_users(request: Request, q: str = "", show: str = "all", sort: str = "created_at", page: int = 1):
    _admin(request)
    return admins.user_list(q, show, sort, page)


@app.get("/api/admin/users/{uid}")
def admin_user(request: Request, uid: str):
    _admin(request)
    try:
        return admins.user_detail(_uid_ok(uid))
    except admins.Refused as e:
        raise HTTPException(e.status, str(e))


@app.post("/api/admin/users/{uid}/disabled")
def admin_disable(request: Request, uid: str, body: dict = Body(...)):
    me_ = _admin(request)
    try:
        return admins.set_disabled(me_, _uid_ok(uid), body.get("disabled"))
    except admins.Refused as e:
        raise HTTPException(e.status, str(e))


@app.post("/api/admin/users/{uid}/delete")
def admin_delete_user(request: Request, uid: str, body: dict = Body(...)):
    me_ = _admin(request)
    target = auth.get_user(_uid_ok(uid))
    if not target:
        raise HTTPException(404, "No such account")
    if target.id == me_.id:
        raise HTTPException(400, "You can't delete your own account from here.")
    if str(body.get("confirm") or "").strip().lower() != target.email.lower():
        raise HTTPException(400, "Type the account's email address exactly to confirm.")
    try:
        account.delete_account(target.id)
    except account.Busy as e:
        raise HTTPException(409, str(e))
    with db.engine.begin() as c:
        admins._log(c, me_.id, "delete", target.id, "account deleted")
    return {"ok": True}


def main():
    print(f"\n  ClipHeist is running:  {config.PUBLIC_URL}\n")
    if not config.ACCOUNTS and (config.HTTPS or config.HOST not in ("127.0.0.1", "localhost", "::1")):
        print("  Sign-in is off, so anyone who can reach this address can use ClipHeist and its AI key.\n"
              "  On a server other people can reach, set PEAKCUT_ACCOUNTS=1 in .env.\n", flush=True)
    # https served by ClipHeist itself (PEAKCUT_SSL_CERT and PEAKCUT_SSL_KEY): Meta only accepts https sign-in return
    # addresses, so a copy without a web server in front needs it to connect Instagram. Servers usually get https
    # from the web server in front instead.
    tls = {"ssl_certfile": config.SSL_CERT, "ssl_keyfile": config.SSL_KEY} if config.SSL_CERT and config.SSL_KEY else {}
    if config.env("PEAKCUT_AUTO_RELOAD") == "1":
        # restart by itself whenever the server code changes (pages in web/ never need a restart)
        uvicorn.run("studio.server:app", host=config.HOST, port=config.PORT, log_level="warning", backlog=4096, reload=True,
                    reload_dirs=[str(config.ROOT / "studio")], reload_includes=["*.py"],
                    proxy_headers=_trust_proxy(), forwarded_allow_ips=list(_PROXY_CIDRS), **tls)
        return
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning", backlog=4096,
                proxy_headers=_trust_proxy(), forwarded_allow_ips=list(_PROXY_CIDRS), **tls)


if __name__ == "__main__":
    main()
