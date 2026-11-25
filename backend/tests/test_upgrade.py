"""Updating from an older PeakCut: everything someone had must still be there and still work.

A data folder is written the way the first open-source PeakCut wrote it (before campaigns, templates and the rest
came in): a creator with the old settings (Hype style, Gameplay layout, a game, captions off, their own big text),
a finished video with its clips, the AI settings and key in ai.json, storage.json, a connected YouTube account, a
post waiting to go out, and the database with the old tables only. Then the new code is started on it, with no
sign-in (the default), and everything is checked through the app itself: nothing is lost, nothing is deleted, and an
old clip can still be made again. Nothing outside this machine is contacted."""
import json, os, sqlite3, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("PEAKCUT_UPGRADE_PORT", "8788"))
BASE = f"http://127.0.0.1:{PORT}"
fails = []


def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)


top = Path(tempfile.mkdtemp(prefix="pc-upgrade-"))
data = top / "PeakCut data"                      # a folder chosen in Settings, Storage (with a space, as people pick)
U = "u_local"                                    # the owner when sign-in is off
now = time.time()

# ---------------------------------------------------------------- the database, as the old db.py made it
OLD_TABLES = """
CREATE TABLE users (id VARCHAR(40) NOT NULL, email VARCHAR(254) NOT NULL, name VARCHAR(120) DEFAULT '' NOT NULL,
  password_hash VARCHAR(300), google_sub VARCHAR(64), is_admin BOOLEAN DEFAULT 0 NOT NULL, disabled BOOLEAN DEFAULT 0 NOT NULL,
  created_at FLOAT NOT NULL, last_seen FLOAT, PRIMARY KEY (id), UNIQUE (email), UNIQUE (google_sub));
CREATE TABLE email_ok (user_id VARCHAR(40) NOT NULL, verified_at FLOAT NOT NULL, PRIMARY KEY (user_id));
CREATE TABLE admin_log (id INTEGER NOT NULL, t FLOAT NOT NULL, admin_id VARCHAR(40) NOT NULL, action VARCHAR(30) NOT NULL,
  target_id VARCHAR(40), detail VARCHAR(300) DEFAULT '' NOT NULL, PRIMARY KEY (id));
CREATE INDEX ix_admin_log_t ON admin_log (t);
CREATE TABLE sessions (token_hash VARCHAR(64) NOT NULL, user_id VARCHAR(40) NOT NULL, created_at FLOAT NOT NULL,
  expires_at FLOAT NOT NULL, ip VARCHAR(64), agent VARCHAR(300), PRIMARY KEY (token_hash),
  FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE);
CREATE INDEX ix_sessions_user_id ON sessions (user_id);
CREATE TABLE usage (id INTEGER NOT NULL, user_id VARCHAR(40) NOT NULL, t FLOAT NOT NULL, tokens_in INTEGER DEFAULT '0' NOT NULL,
  tokens_out INTEGER DEFAULT '0' NOT NULL, purpose VARCHAR(60) NOT NULL, creator VARCHAR(80), place VARCHAR(200), PRIMARY KEY (id),
  FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE);
CREATE INDEX usage_user_t ON usage (user_id, t);
CREATE TABLE email_tokens (token_hash VARCHAR(64) NOT NULL, user_id VARCHAR(40) NOT NULL, purpose VARCHAR(10) NOT NULL,
  expires_at FLOAT NOT NULL, used_at FLOAT, PRIMARY KEY (token_hash), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE);
CREATE INDEX ix_email_tokens_user_id ON email_tokens (user_id);
CREATE TABLE feedback (id INTEGER NOT NULL, user_id VARCHAR(40), t FLOAT NOT NULL, kind VARCHAR(10) NOT NULL, message TEXT NOT NULL,
  page VARCHAR(200) DEFAULT '' NOT NULL, context VARCHAR(1000) DEFAULT '' NOT NULL, status VARCHAR(10) DEFAULT 'new' NOT NULL,
  updated_at FLOAT NOT NULL, PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL);
CREATE INDEX ix_feedback_t ON feedback (t);
CREATE INDEX ix_feedback_status ON feedback (status);
CREATE TABLE notices (id INTEGER NOT NULL, user_id VARCHAR(40) NOT NULL, t FLOAT NOT NULL, kind VARCHAR(20) NOT NULL,
  title VARCHAR(160) NOT NULL, body VARCHAR(400) DEFAULT '' NOT NULL, link VARCHAR(200) DEFAULT '' NOT NULL, ref VARCHAR(80),
  read_at FLOAT, PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE);
CREATE INDEX notices_user_t ON notices (user_id, t);
"""
data.mkdir(parents=True)
con = sqlite3.connect(str(data / "peakcut.db"))
con.executescript(OLD_TABLES)
con.execute("INSERT INTO users (id, email, name, is_admin, disabled, created_at) VALUES (?, 'you@localhost', 'You', 1, 0, ?)", (U, now - 86400 * 30))
con.execute("INSERT INTO email_ok VALUES (?, ?)", (U, now - 86400 * 30))
con.execute("INSERT INTO usage (user_id, t, tokens_in, tokens_out, purpose, creator, place) VALUES (?, ?, 5000, 800, 'Video: finding clips', 'streamer', 'Old stream')",
            (U, now - 3600))
con.execute("INSERT INTO notices (user_id, t, kind, title, body, link) VALUES (?, ?, 'clips', '1 clip ready', 'Old stream', '/app/creators/streamer')",
            (U, now - 3500))
con.execute("INSERT INTO feedback (user_id, t, kind, message, status, updated_at) VALUES (?, ?, 'idea', 'An idea from before', 'new', ?)",
            (U, now - 3400, now - 3400))
con.commit()
con.close()

# ---------------------------------------------------------------- settings files
(data / "ai.json").write_text(json.dumps({"provider": "openai", "models": ["gpt-old-model"], "base_url": "", "key": "sk-old-test-key-1234567890"}),
                              encoding="utf-8")
(top / "storage.json").write_text(json.dumps({"path": str(data)}, indent=1), encoding="utf-8")   # kept in PeakCut's own folder

udir = data / "users" / U
creator = {"name": "Streamer", "channel": "https://www.twitch.tv/somebody", "style": "hype", "layout": "gameplay", "captions": False,
           "min_len": 15, "max_len": 60, "max_clips": 6, "min_score": 80, "auto_watch": False, "buffer_min": 20, "live_per_hour": 4,
           "ai_every_min": 3, "ai_only_on_spike": True, "auto_jump_pct": 60, "plan": "free", "decide_mode": "both", "must_clip_min": 0,
           "min_gap_min": 2, "game": "valorant", "hook_text": "WATCH THIS", "description_text": "", "trigger_words": "clip that, clip it",
           "autopost": False, "accounts": [], "post_min_score": 75, "post_gap_min": 45, "daily_cap": 6, "privacy": "public",
           "post_footer": "", "rate_per_1k": 0.0, "channels": {"twitch": "https://www.twitch.tv/somebody"}, "lang": "en",
           "id": "streamer", "created": now - 86400 * 20}
other = dict(creator, name="Second", id="second", style="bounce", layout="blur", captions=True, game="", hook_text="",
             channel="", channels={}, created=now - 86400 * 10)
for c in (creator, other):
    (udir / "creators" / c["id"]).mkdir(parents=True)
    (udir / "creators" / c["id"] / "creator.json").write_text(json.dumps(c, indent=1), encoding="utf-8")
    (udir / "creators" / c["id"] / "log.jsonl").write_text(json.dumps({"t": now - 3600, "msg": "Clip ready (score 88): A big play"}) + "\n",
                                                          encoding="utf-8")

JID = "20261110-120000-0123456789"
jd = udir / "creators" / "streamer" / "videos" / JID
(jd / "clips").mkdir(parents=True)
src = jd / "source.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=20:r=30", "-f", "lavfi", "-i", "sine=frequency=300:d=20",
                "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=360x640:d=12", "-c:v", "libx264", "-preset", "ultrafast",
                str(jd / "clips" / "clip01.mp4")], check=True)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=360x640", "-frames:v", "1", str(jd / "clips" / "clip01.jpg")], check=True)
words = [{"w": w, "s": 2.0 + i * 0.5, "e": 2.4 + i * 0.5} for i, w in enumerate("no way he just hit that shot from across the whole map".split())]
(jd / "words.json").write_text(json.dumps(words), encoding="utf-8")
clip = {"start": 2.0, "end": 14.0, "score": 88, "category": "clutch", "hook": "WATCH THIS", "title": "A big play",
        "description": "He hit it from across the map. #valorant #clutch", "hashtags": ["valorant", "clutch"], "highlight_words": ["shot"],
        "reason": "The shot.", "id": "clip01", "style": "hype", "layout": "gameplay", "captions": True, "status": "done", "version": 1,
        "facecam": [0.7, 0.05, 0.25, 0.25], "file": "clip01.mp4", "cover": "clip01.jpg", "made_at": now - 3600, "error": None,
        "hashtags_in_description": True, "fixed_words": words, "fixed_for": [2.0, 14.0, "en2"]}
(jd / "clips" / "clip01.json").write_text(json.dumps({k: clip[k] for k in ("title", "description", "hashtags", "hook", "start", "end", "score")}),
                                         encoding="utf-8")
job = {"id": JID, "user": U, "kind": "video", "creator": "streamer", "dir": str(jd), "created": now - 7200,
       "source": {"type": "file", "path": str(src), "name": "Old stream.mp4"}, "source_path": str(src),
       "options": {"max_clips": 6, "min_len": 15, "max_len": 60, "style": "hype", "layout": "gameplay", "quality": 720, "range_start": None,
                   "range_end": None, "captions": False, "hook_text": "WATCH THIS", "description_text": "", "lang": "en"},
       "status": "done", "stage": "Done", "progress": 1.0, "title": "Old stream", "uploader": "somebody", "duration": 20.0,
       "width": 640, "height": 360, "lang": "en", "facecam": [0.7, 0.05, 0.25, 0.25], "clips": [clip], "error": None,
       "ai": {"model": "gpt-old-model", "tokens": 5800, "note": "", "log": []}, "took": 95, "paid_blocks": 1, "done_blocks": 1}
(jd / "job.json").write_text(json.dumps(job, indent=1), encoding="utf-8")

JID2 = "20261111-090000-abcdef0123"                 # a video added without a creator
jd2 = udir / "jobs" / JID2
(jd2 / "clips").mkdir(parents=True)
(jd2 / "clips" / "clip01.mp4").write_bytes(b"\x00" * 2000)
job2 = dict(job, id=JID2, creator=None, dir=str(jd2), source={"type": "url", "url": "https://www.youtube.com/watch?v=abcdefghijk", "name": "A video"},
            source_path="", title="A video", options=dict(job["options"], style="bounce", layout="blur", captions=True, hook_text=""),
            clips=[dict(clip, style="bounce", layout="blur", hook="Old heading", facecam=None, fixed_words=None, fixed_for=None)])
job2.pop("facecam")
(jd2 / "job.json").write_text(json.dumps(job2, indent=1), encoding="utf-8")

(udir / "accounts").mkdir(parents=True)
acc = {"id": "yt_UC123", "platform": "youtube", "name": "My Channel", "added": now - 86400, "refresh_token": "old-refresh-token",
       "access_token": "old-access-token", "expires": now - 60}
(udir / "accounts" / "accounts.json").write_text(json.dumps([acc], indent=1), encoding="utf-8")
post = {"id": "0123456789abcdef", "user": U, "job": JID, "clip": "clip01", "creator": "streamer", "account": "yt_UC123",
        "account_name": "My Channel", "platform": "youtube", "status": "scheduled", "due_at": now + 7 * 86400, "created": now - 600,
        "title": "A big play", "score": 88, "tries": 0, "url": "", "remote_id": "", "error": None, "views": None}
(data / "posts.json").write_text(json.dumps([post], indent=1), encoding="utf-8")

before = {str(p.relative_to(data)): p.stat().st_size for p in data.rglob("*") if p.is_file()}

# ---------------------------------------------------------------- the new code finds the folder from storage.json
env = {k: v for k, v in os.environ.items() if not k.startswith("PEAKCUT_") and k not in ("DATABASE_URL",)}
env.update(PEAKCUT_STORAGE_FILE=str(top / "storage.json"), PEAKCUT_MODELS=os.environ.get("PEAKCUT_MODELS", ""), PYTHONIOENCODING="utf-8")
# ClipHeist: whatever this machine's own backend/.env holds must not leak in (an empty value here wins over .env)
env.update(PEAKCUT_ACCOUNTS="0", PEAKCUT_PUBLIC_URL="", CLIPHEIST_SITE="", SUPABASE_URL="", SUPABASE_SECRET_KEY="",
           DEEPSEEK_API_KEY="", PEAKCUT_ASR_URL="", PEAKCUT_ASR_TOKEN="", PEAKCUT_RENDER_URL="")
r = subprocess.run([sys.executable, "-c", "from studio import config; print(config.DATA_DIR); print(config.ACCOUNTS)"], cwd=ROOT, env=env,
                   capture_output=True, text=True, encoding="utf-8")
check("the folder chosen in Settings, Storage is found again (storage.json)", (r.stdout.splitlines() or [""])[0], str(data))
check("sign-in stays off by default", (r.stdout.splitlines() or ["", ""])[-1], "False")

# ---------------------------------------------------------------- start it, as after git pull
env.update(PEAKCUT_DATA=str(data), PEAKCUT_PORT=str(PORT), PEAKCUT_HOST="127.0.0.1", PEAKCUT_AUTO_RELOAD="0", PEAKCUT_FETCH_MODEL="0",
           PEAKCUT_UPDATE_CHECK="0", PYTHONUNBUFFERED="1", GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="", GOOGLE_CLIENT_FILE="")
log = top / "server.log"
out = log.open("w", encoding="utf-8")
server = subprocess.Popen([sys.executable, "-m", "studio.server"], cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT)
H = {"Origin": BASE, "Content-Type": "application/json"}


def api(path, body=None, method=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), headers=H,
                                 method=method or ("GET" if body is None else "POST"))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


try:
    for _ in range(90):
        try:
            urllib.request.urlopen(BASE + "/healthz", timeout=2)
            break
        except Exception:  # noqa: BLE001
            if server.poll() is not None:
                break
            time.sleep(1)
    up = server.poll() is None
    check("the new version starts on the old folder", up)
    if not up:
        out.flush()
        print(log.read_text(encoding="utf-8", errors="replace")[-3000:])
        sys.exit(1)

    st, me = api("/api/auth/me")
    me = (me or {}).get("user") or {} if isinstance(me, dict) else {}
    check("no sign-in: the owner is the same account as before, and admin", (st, me.get("id"), me.get("is_admin")), (200, U, True))
    st, cs = api("/api/creators")
    rows = cs if isinstance(cs, list) else (cs or {}).get("creators", []) if isinstance(cs, dict) else []
    by = {c.get("id"): c for c in rows}
    check("both creators are there", (st, sorted(by)), (200, ["second", "streamer"]))
    s_ = by.get("streamer") or {}
    check("...with their settings: channel, game, their own big text, captions off",
          (s_.get("channel"), s_.get("game"), s_.get("hook_text"), s_.get("captions")),
          ("https://www.twitch.tv/somebody", "valorant", "WATCH THIS", False))
    check("...and their old look (Hype, Gameplay with the cam) becomes a template of the same look",
          (s_.get("style"), s_.get("layout"), bool(s_.get("template"))), ("hype", "gameplay", True))
    check("...while one on the old defaults keeps the account's default template", ((by.get("second") or {}).get("style"), (by.get("second") or {}).get("template")),
          ("bounce", ""))

    st, js = api("/api/jobs")
    jl = {j.get("id"): j for j in (js if isinstance(js, list) else [])}
    check("both videos are listed", (st, sorted(jl)), (200, sorted([JID, JID2])))
    j = jl.get(JID) or {}
    check("the finished video is still finished, with its clip", (j.get("status"), [(c.get("id"), c.get("status")) for c in j.get("clips", [])]),
          ("done", [("clip01", "done")]))
    c1 = (j.get("clips") or [{}])[0]
    check("...its title, heading, description and hashtags kept", (c1.get("title"), c1.get("hook"), c1.get("hashtags")),
          ("A big play", "WATCH THIS", ["valorant", "clutch"]))
    req = urllib.request.Request(f"{BASE}/media/{JID}/clip01.mp4", headers=H)
    try:
        got = urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:  # noqa: BLE001
        got = repr(e).encode()
    check("the clip's video can still be opened", got == (jd / "clips" / "clip01.mp4").read_bytes())

    st, a = api("/api/ai")
    check("the AI service, model and key from ai.json are still set", (st, (a or {}).get("provider"), (a or {}).get("models"), (a or {}).get("has_key")),
          (200, "openai", ["gpt-old-model"], True))
    check("...and the key is never sent back", "sk-old-test-key" in json.dumps(a), False)

    st, accs = api("/api/accounts")
    check("the connected YouTube account is still connected", (st, [(x.get("platform"), x.get("name")) for x in (accs if isinstance(accs, list) else [])]),
          (200, [("youtube", "My Channel")]))
    check("...and its tokens never reach the page", "old-refresh-token" in json.dumps(accs), False)
    st, sch = api(f"/api/schedule?start={now - 86400}&end={now + 30 * 86400}")
    row = next((p for p in (sch or {}).get("posts", []) if p.get("id") == "0123456789abcdef"), {}) if isinstance(sch, dict) else {}
    check("the post waiting to go out is still waiting", (st, row.get("status")), (200, "scheduled"))

    st, us = api("/api/usage")
    check("the AI use recorded before is still counted", (st, "5800" in json.dumps(us) or "5000" in json.dumps(us)), (200, True))

    # an old clip, made again with the new code (same cut: its captions were already tidied, so no AI is asked)
    st, _ = api(f"/api/jobs/{JID}/clips/clip01/render", {"title": "A big play, made again"})
    check("an old clip can be made again", st, 200)
    done = None
    for _ in range(180):
        time.sleep(1)
        st, j = api(f"/api/jobs/{JID}")
        c1 = next((c for c in (j or {}).get("clips", []) if c.get("id") == "clip01"), {}) if isinstance(j, dict) else {}
        if c1.get("status") in ("done", "error"):
            done = c1
            break
    check("...and comes out done", ((done or {}).get("status"), (done or {}).get("error")), ("done", None))
    check("...as a new version, with the change", ((done or {}).get("version", 0) >= 2, (done or {}).get("title")), (True, "A big play, made again"))
    f = jd / "clips" / str((done or {}).get("file") or "clip01.mp4")
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(f)],
                           capture_output=True, text=True).stdout.strip()
    check("...a real vertical video", probe, lambda v: bool(v) and int(v.split(",")[1]) > int(v.split(",")[0]))
finally:
    server.terminate()
    try:
        server.wait(timeout=15)
    except subprocess.TimeoutExpired:
        server.kill()
    out.close()

# ---------------------------------------------------------------- nothing was deleted
after = {str(p.relative_to(data)) for p in data.rglob("*") if p.is_file()}
gone = sorted(set(before) - after)
check("no file of theirs was deleted (the whole video, clips, settings, accounts, posts, logs)", gone, [])
con = sqlite3.connect(str(data / "peakcut.db"))
counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("users", "email_ok", "usage", "notices", "feedback")}
tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
con.close()
check("no database row was lost", counts, lambda c: c["users"] == 1 and c["email_ok"] == 1 and c["usage"] >= 1 and c["notices"] >= 1 and c["feedback"] == 1)
check("the old tables are all still there, and new ones were added beside them", (sorted({"users", "sessions", "usage", "email_ok", "email_tokens",
      "feedback", "notices", "admin_log"} - tables), len(tables) >= 8), ([], True))
check("the AI settings file is unchanged", json.loads((data / "ai.json").read_text(encoding="utf-8"))["key"], "sk-old-test-key-1234567890")
check("the connected account is unchanged", json.loads((udir / "accounts" / "accounts.json").read_text(encoding="utf-8"))[0]["refresh_token"], "old-refresh-token")
check("the whole video is kept (nothing is tidied away unless switched on)", src.is_file())

if fails:
    print(log.read_text(encoding="utf-8", errors="replace")[-2500:])
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
import shutil   # noqa: E402
shutil.rmtree(top, ignore_errors=True)
sys.exit(1 if fails else 0)
