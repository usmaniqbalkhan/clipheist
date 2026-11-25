"""Live sessions: record a stream into a rolling buffer, transcribe as it goes, let the AI pick clips."""
import collections
import json
import re
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from . import (adskip, alerts, auth, brain, capture, config, creators, ctx, facecam, games, hashtags, jobs, load, media,
               render, transcribe, usage)

# ":username!user@host PRIVMSG #channel :the message"
_NICK = re.compile(r"(?m)^:([A-Za-z0-9_]{2,25})!")

SESSIONS: dict = {}
SEG_SEC = 6           # length of each buffer piece
STALL_SEC = 90        # sound arriving but no picture for this long: start the recorder again
PAD = 8               # seconds kept either side of a clip so it can be re-trimmed later
CHECK_EVERY = 180     # look at the stream every 3 minutes...
QUIET_MAX = 600       # ...but only ask the AI when something happened, or at most every 10 minutes when calm
WINDOW = 240          # seconds of transcript shown to the AI per check
CHAT_KEEP = 600       # seconds of chat names kept: only the last few minutes are ever read
SR = 16000


class NotLive(Exception):
    pass


def _trim_names(users: dict, second: int, kept: int) -> int:
    """Drop the chat names older than CHAT_KEEP, and say when this was last done.

    Only the last few minutes are ever read, and a stream that runs for eight hours would otherwise hold
    a name for every second of it. Called under the session's lock, from the chat reader thread."""
    if second - kept < 60:
        return kept              # walking the whole dict on every message would cost more than it saves
    for t in [t for t in users if t < second - CHAT_KEEP]:
        del users[t]
    return second


def media_safe(e) -> str:
    from . import safe
    return safe.friendly(e, 300)


def normalize(url: str) -> str:
    url = creators.channel_link(url)
    if not re.match(r"^https?://", url):
        url = "https://" + url
    m = re.match(r"^(https?://(?:www\.)?youtube\.com/(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+))/?(?:[?#].*)?$", url)
    return m.group(1) + "/live" if m else url


# Auto-record waits before trying a channel again after a recording could not even start, longer each time in a
# row (10 min, 20, 40 ... up to 6 h), instead of trying every two minutes and leaving a failed video each time.
_start_fails: dict = {}       # (user id, link) -> (when it last failed, failures in a row)


def _start_failed(uid, url):
    _, n = _start_fails.get((uid, url), (0, 0))
    _start_fails[(uid, url)] = (time.time(), n + 1)


def _start_wait(uid, url) -> float:
    """Seconds auto-record should still leave this channel alone; 0 when it may try."""
    t, n = _start_fails.get((uid, url), (0, 0))
    return max(0.0, t + min(6 * 3600, 600 * 2 ** (n - 1)) - time.time()) if n else 0.0


class _Quiet:
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


def resolve(url: str, height: int = 720) -> dict:
    """Ask yt-dlp for the stream's playable address (at most `height`). Raises NotLive if the channel is offline."""
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "logger": _Quiet(),
            "format": f"b[height<={height}]/bv*[height<={height}]+ba/b", **media.cookie_opts()}
    if media.is_youtube(url) and media.youtube_paused():
        raise NotLive(media.YT_BLOCKED)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        if media.note_refusal(url, e):
            raise NotLive(media.YT_BLOCKED) from e
        raise NotLive(media_safe(e)) from e
    if not info or not info.get("is_live"):
        raise NotLive("This channel is not live right now.")
    fmts = info.get("requested_formats") or [info]
    title = info.get("title") or ""
    if "(live)" in title and info.get("description"):  # Twitch puts the real stream title here
        title = str(info["description"]).splitlines()[0][:120]
    urls = [f["url"] for f in fmts if isinstance(f, dict) and f.get("url")]
    if not urls:
        raise NotLive("Could not open the live stream.")
    return {"urls": urls, "title": title, "id": str(info.get("id") or ""),
            "uploader": info.get("uploader") or info.get("channel") or ""}


def active():
    return [s for s in SESSIONS.values() if not s.finished and not s.user_stopped]


def active_mine():
    u = ctx.uid()
    return [s for s in active() if s.job["user"] == u]


def active_for(cid):
    return next((s for s in active_mine() if s.job["creator"] == cid), None)


def follow_campaign(c: dict):
    """A creator's settings or campaign changed: their running recording follows from its next clip on (the campaign
    in use, with its look, hashtags and rules). Clips already made keep theirs."""
    s = active_for(c["id"])
    if s:
        s.job["campaign"] = c.get("campaign_use") or ""
        s.settings = creators.settings_for(c, s.job["campaign"])
        if hasattr(s, "regroup"):
            s.regroup()
        jobs.save(s.job)


def mine(jid):
    """The signed-in user's recording with this id, or None."""
    s = SESSIONS.get(jid)
    return s if s and s.job["user"] == ctx.uid() else None


def piece_seconds(f: Path) -> float:
    """How long a buffer piece really plays, added up from its video frames (-1 if it can't be read)."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=duration_time",
                              "-of", "csv=p=0", str(f)], capture_output=True, text=True, timeout=30, creationflags=media.NO_WINDOW).stdout
        vals = [float(x) for x in out.replace(",", "\n").split() if x.replace(".", "", 1).isdigit()]
        return round(sum(vals), 3) if vals else -1.0
    except Exception:  # noqa: BLE001
        return -1.0


class Session:
    TROUBLE_STOP = 300       # seconds of the sound not being readable before a recording gives up, saying why

    def __init__(self, job, settings):
        # the creator's settings, with the result rules of the campaign this recording is for on top
        self.job, self.settings = job, creators.settings_for(settings, job.get("campaign"))
        self.dir = Path(job["dir"])
        self.buf = self.dir / "buffer"
        self.buf.mkdir(exist_ok=True)
        (self.dir / "clips").mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.stop_flag = threading.Event()
        self.ended = False            # recording has stopped (stream over, stopped, or failed)
        self.finished = False         # everything is wrapped up
        self.end_note = "Stream ended"
        self.user_stopped = False     # Stop pressed: wrap up at once, no last AI check
        self.cap = None               # the ffmpeg recording this stream (studio/capture.py)
        self.part_starts = {}         # part -> (session clock where it starts, audio sample where it starts)
        self.afiles = []              # audio files still on disk, oldest first: [name, the sample it starts at]
        self.level_buf = bytearray()  # audio not yet measured for loudness (less than a second of it)
        self.saved_base = 0           # how far the transcript had got at the save before last (see _publish)
        self.pending = bytearray()    # audio received but not yet transcribed
        self.clock_fix = 0.0          # how far the video-piece clock is behind the audio clock, last measured
        self.fixed = {}               # piece name -> its measured length, where ffmpeg's list could not be right (_piece_len)
        self.trouble_since = None     # since when a step of the recording has been failing (_trouble)
        self.pending_base = 0         # sample index where `pending` starts
        self.samples = 0              # total audio samples received
        self.db = []                  # loudness per second
        self.words = []
        self.t_done = 0.0             # transcript is complete up to this second
        self.segments = []            # (path, start, end) on the session clock
        self.seg_clock = 0.0
        self.csv_end = {}             # part -> where its last listed piece ended, on the stream's own clock (_place)
        self.csv_seen = {}
        self.listed = set()           # every piece ffmpeg's own list has named (the rest are unfinished)
        self.swept = 0.0              # when the buffer was last searched for unfinished pieces
        self.part = 0
        self.chat = collections.Counter()   # second -> messages
        self.chat_users = {}                 # second -> the people who talked, when it can be told
        self.chat_kept = 0                   # the second this was last pruned at
        self.chat_total = 0
        self.chat_on = False
        self.last_check = 0.0
        self.words_at_check = 0
        self.checks = 0
        self.rejected = 0
        self.verdicts = []
        self.last_trigger = -999.0
        self.started_at = time.time()
        self.skipped = 0
        self.last_ai = 0.0
        self.tokens = 0
        self.manual = []
        self.n = 0
        self.spike = None             # a jump in loudness or chat waiting for its transcript
        self.said = None              # someone said "clip": that moment, waiting for its transcript
        self.last_said = -999.0
        self.last_spike = -999.0      # when the last jump was sent to the AI (stream seconds)
        self.base = {}                # running averages the jumps are measured against, shown on the card
        self.spikes = 0
        self.stalled = 0              # times the picture stopped while the sound went on, and the recorder restarted
        self.game = ""                # which game this stream is, when it can be told
        self.facecam_box = None       # where the webcam is, found once the first clip is made
        self.facecam_checked = False  # the look is made once, even when it finds nothing
        self.ai_busy = 0.0            # when the AI check now running was started, 0 when none is

    def _ask_ai(self, window=None, why=None):
        """Ask the AI on its own thread, so a slow answer never stops the recording.

        While the AI thinks, the speech keeps being written and a "clip that" still works. One check
        runs at a time; if the running one is stuck far past its own timeout, a new one is let through.
        """
        now = time.time()
        if self.ai_busy and now - self.ai_busy < config.LIVE_AI_TIMEOUT + 60:
            return

        def work():
            try:
                self._ai_check(window, why)
            except Exception as e:  # noqa: BLE001
                self.job["ai"]["last_error"] = str(e)[-200:]
            finally:
                self.ai_busy = 0.0
        self.ai_busy = now
        threading.Thread(target=ctx.bind(work), daemon=True, name="ai_check").start()

    def _find_facecam(self, src):
        """Where the webcam is in the stream, once. Only a sure answer is used.

        One frame goes to the AI as well, and whatever it says is measured again on the
        picture. A stream where the webcam is a square picture along the right edge, or
        one where there is no webcam at all and the game has panels of its own, is told
        apart from a real camera by what moves: a person sits still, the game does not."""
        try:
            box = facecam.detect(src, ask=brain.look_for_facecam)
        except Exception:  # noqa: BLE001 - a missing facecam is not a failure
            return None
        if not box or box.get("conf") != "high":
            return None
        self.job["facecam"] = [box["x"], box["y"], box["w"], box["h"]]
        return self.job["facecam"]

    # ---------------------------------------------------------------- the game this stream is
    def _game(self):
        """The game, from what the creator set or from what the stream's title says.

        The stream title is the best place to look: on launch night it says the game."""
        if self.game:
            return self.game
        found = games.detect(self.settings.get("game"), self.job.get("title"), self.job.get("uploader"),
                             self.job.get("source", {}).get("name"))
        self.game = found[0] or ""
        if self.game:
            try:
                self.job["game"] = self.game
            except Exception:  # noqa: BLE001
                pass
        return self.game

    # ---------------------------------------------------------------- recording
    def start(self):
        SESSIONS[self.job["id"]] = self
        threading.Thread(target=ctx.bind(self._ingest), daemon=True).start()
        threading.Thread(target=ctx.bind(self._analyze), daemon=True).start()
        self.start_chat()

    def start_chat(self):
        """Count chat messages while recording (never what they say): Twitch through its chat server, Kick by
        reading its public list of recent messages every few seconds. YouTube streams have no chat signal."""
        url = self.job["source"]["url"]
        m = re.match(r"^https?://(?:www\.)?twitch\.tv/([A-Za-z0-9_]+)", url)
        if m:
            threading.Thread(target=ctx.bind(self._chat), args=(m.group(1).lower(),), daemon=True).start()
        m = re.match(r"^https?://(?:www\.)?kick\.com/([A-Za-z0-9_\-]+)", url)
        if m:
            threading.Thread(target=ctx.bind(self._kick_chat), args=(m.group(1).lower(),), daemon=True).start()

    def stop(self, note="Stopped by you"):
        """Instant for the user: the card and the creator show as stopped straight away. Clips already being made
        still finish in the background, but nothing new is transcribed or sent to the AI."""
        self.end_note = note
        self.user_stopped = True
        self.stop_flag.set()
        with self.lock:
            self.job.update(status="done", stage=note)
            cap = self.cap        # under the same lock _ingest takes when it starts ffmpeg, so a stop never misses it
        jobs.save(self.job)
        if cap:
            cap.stop()

    def _piece_len(self, name: str, a, b) -> float:
        """A piece's length for the recording's clock. ffmpeg's list gives each piece's start and end on the stream's
        own clock, and that clock jumps when the stream changes (Twitch: the opening ad ends and the real stream
        starts hours ahead): the piece the jump lands in is listed as 0.02 s or as hours. A length that can't be
        right is measured from the piece itself, once, and kept (it is saved with the recording)."""
        dur = float(b) - float(a)
        if 1.0 <= dur <= SEG_SEC * 4:
            return dur
        if name not in self.fixed:
            real = piece_seconds(self.buf / name)
            if real < 0:                 # the piece is gone or unreadable: the nearest guess, and not kept
                return dur if 0 <= dur < 1.0 else float(SEG_SEC)
            self.fixed[name] = real
        return self.fixed[name]

    def _trouble(self, e):
        """A step of the recording failed (e), or everything worked (None). A recording whose sound can't be read
        says so on its card and, if it stays that way, ends with the reason: one sat at "Recording 0:06" for 45
        minutes with nothing behind it and nothing to tell anyone why."""
        if e is None:
            if self.trouble_since:
                self.trouble_since = None
                if not self.user_stopped and str(self.job.get("stage", "")).startswith("Recording, but"):
                    self.job["stage"] = "Recording"
            return
        self.trouble_since = self.trouble_since or time.time()
        if self.user_stopped:
            return
        why = ("the speech model is missing on this server" if isinstance(e, FileNotFoundError) and "speech model" in str(e)
               else "the sound of the stream could not be read")
        if self.ended or time.time() - self.trouble_since > self.TROUBLE_STOP:
            self.stop(f"Stopped: {why}")
            alerts.notify(f"the recording of {self.job['source']['name']} was stopped: {why}.")
            return
        with self.lock:            # the card keeps counting, and says what is wrong
            self.job.update(stage=f"Recording, but clips are held up: {why}", duration=self.samples / SR)
            self.job["live"] = {**(self.job.get("live") or {}), "elapsed": round(self.samples / SR),
                                "buffer_sec": round(self.segments[-1][2] - self.segments[0][1]) if self.segments else 0,
                                "level": [round(x) for x in self.db[-60:]]}

    AUDIO = re.compile(r"p(\d+)_a(\d+)\.pcm$")

    def _read_audio(self, f: Path, offset: int) -> int:
        """Take in what ffmpeg has added to this audio file since `offset`; returns the new offset."""
        try:
            with open(f, "rb") as fh:
                fh.seek(offset)
                while True:
                    data = fh.read(SR * 2)
                    data = data[: len(data) // 2 * 2]      # whole 16-bit samples only; an odd byte waits for its pair
                    if not data:
                        return offset
                    offset += len(data)
                    with self.lock:
                        self.pending += data
                        self.samples += len(data) // 2
                        self.level_buf += data
                        while len(self.level_buf) >= SR * 2:        # loudness: one value for each second of audio
                            x = np.frombuffer(bytes(self.level_buf[: SR * 2]), dtype=np.int16).astype(np.float32) / 32768.0
                            self.db.append(float(20 * np.log10(np.sqrt((x ** 2).mean() + 1e-10))))
                            del self.level_buf[: SR * 2]
        except FileNotFoundError:
            return offset

    def _follow(self, part: int, index: int = 0, offset: int = 0, live: bool = True) -> int:
        """Take in this part's audio. ffmpeg writes it as a row of short files (pNN_a00000.pcm, pNN_a00001.pcm, ...):
        each is read as it grows and left once the next one exists. Runs for as long as the part's ffmpeg does, then
        takes what is left; for a part that ended earlier (live=False) it only takes what is there.
        Returns the bytes read."""
        got, told = 0, False
        while True:
            alive = live and bool(self.cap) and self.cap.alive()         # asked first: if it has ended, the files are final
            name = f"p{part:02d}_a{index:05d}.pcm"
            more = (self.buf / f"p{part:02d}_a{index + 1:05d}.pcm").exists()   # looked at before reading: this file is then complete
            with self.lock:
                if offset == 0 and not (self.afiles and self.afiles[-1][0] == name):
                    self.afiles.append([name, self.samples])
            new = self._read_audio(self.buf / name, offset)
            got, offset = got + new - offset, new
            if more:
                index, offset = index + 1, 0
            elif not alive:
                return got
            elif self.stop_flag.wait(0.5):      # Stop was pressed: ask ffmpeg to end (once), then take in what it still writes
                if not told:
                    self.cap.stop()
                    told = True
                time.sleep(0.3)
            elif not told and self.samples / SR - self.seg_clock > STALL_SEC:
                # The sound is still arriving but no picture has been written for a while: Twitch does this around
                # a stream hiccup, and ffmpeg can stay stuck on it for the rest of the stream, leaving a recording
                # with sound and no video. This ffmpeg is ended (what it wrote is still taken in) and the stream is
                # recorded again from the live edge; the break in the picture shows up as a gap in the buffer.
                self.stalled += 1
                self.cap.stop()
                told = True

    def _trim_audio(self, upto: int):
        """Audio that is transcribed, and saved as transcribed, is deleted: it would be 115 MB an hour otherwise."""
        with self.lock:
            while len(self.afiles) > 1 and self.afiles[1][1] <= upto:
                (self.buf / self.afiles.pop(0)[0]).unlink(missing_ok=True)

    def _ingest(self, attached=None, resumed=None):
        """Record the stream. However this ends, even when ffmpeg cannot start at all, the session is marked as
        ended, so it stops showing as recording and gives its place to the next one."""
        try:
            self._ingest_loop(attached, resumed)
        except Exception as e:  # noqa: BLE001
            self.job["error"] = f"Recording stopped: {media_safe(e)}"
        finally:
            self.ended = True

    def _ingest_loop(self, attached=None, resumed=None):
        fails = 0
        url = self.job["source"]["url"]
        if attached:      # picked up after an app update: first what was recorded but not transcribed yet, then
            part, index, offset = attached        # carry on with the part ffmpeg is still writing
            for p in range(part, self.part):
                self._follow(p, index, offset, live=False)
                index = offset = 0
            self._follow(self.part, index, offset)
            self.stop_flag.wait(3)
        if resumed:       # the recorder stopped with the app: take in all it wrote, then record the stream again below
            part, index, offset = resumed
            for p in range(part, self.part + 1):
                self._follow(p, index, offset, live=False)
                index = offset = 0
        while not self.stop_flag.is_set():
            try:
                # 720p, or 1080p for a streamer whose style zooms in (its cam, Fill the screen): sharp where it's enlarged
                look = self.settings.get("look")
                info = resolve(url, 1080 if look and render.needed_height(look, self.settings.get("cam")) > 1080 else 720)
            except NotLive as e:
                fails += 1
                if self.samples == 0 or fails >= 3:
                    if self.samples == 0:
                        self.job["error"] = f"Could not start recording: {e}"
                        _start_failed(ctx.uid_or_none(), url)
                    break
                self.stop_flag.wait(10)
                continue
            _start_fails.pop((ctx.uid_or_none(), url), None)       # it works: no waiting next time
            if self.stop_flag.is_set():     # stop() came while the stream address was being looked up
                break
            if not self.job.get("uploader"):
                self.job.update(title=info["title"], uploader=info["uploader"])
            if self.part:
                self._scan_segments()       # the last part's final pieces go before the new part's start
            self.part += 1
            with self.lock:
                self.seg_clock = max(self.seg_clock, self.samples / SR)
                self.part_starts[self.part] = (self.seg_clock, self.samples)
            p = f"p{self.part:02d}"
            cmd = ["ffmpeg", "-v", "error", "-reconnect", "1", "-reconnect_streamed", "1",
                   "-reconnect_delay_max", "10"]
            for u in info["urls"]:
                # a live list of pieces (HLS) is read from its newest piece, not three back, so the recording (and
                # "what was just said") runs about 6 s closer to what viewers see
                cmd += (["-live_start_index", "-1"] if ".m3u8" in u else []) + ["-i", adskip.url(u)]   # Twitch ads left out (adskip.py)
            a_in = "1:a:0" if len(info["urls"]) > 1 else "0:a:0"
            # Pieces are cut by the wall clock (-segment_atclocktime), not by the stream's own clock. Twitch opens with
            # a 30-second ad and then starts the real stream hours ahead on its clock; cutting by that clock, ffmpeg
            # waited for the jump to be "caught up" and never finished another piece, so the buffer stayed 30 seconds
            # long and every later clip failed with "no longer in the buffer".
            cmd += ["-map", "0:v:0", "-map", a_in, "-c", "copy", "-f", "segment", "-segment_time", str(SEG_SEC),
                    "-segment_atclocktime", "1", "-reset_timestamps", "1", "-segment_format", "mpegts", "-segment_list", f"{p}.csv",
                    "-segment_list_type", "csv", f"{p}_%05d.ts",
                    # the audio for the transcript: short raw files, written as the sound comes in (see _follow)
                    "-map", a_in, "-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", "-f", "segment",
                    "-segment_time", str(SEG_SEC), "-segment_format", "s16le", "-segment_format_options", "flush_packets=1",
                    f"{p}_a%05d.pcm"]
            self._save_state()
            jobs.save(self.job)           # the part is on record before its ffmpeg starts (see attach)
            cap = capture.start(self.job["id"], cmd, self.buf)
            with self.lock:               # same lock stop() takes, so stop() always sees this ffmpeg
                self.cap = cap
                stopped = self.stop_flag.is_set()
            if stopped:
                cap.stop()
            got = self._follow(self.part)
            fails = 0 if got > SR * 2 * 20 else fails + 1
            if fails >= 3:
                break
            self.stop_flag.wait(3)

    def _scan_segments(self):
        """Pick up buffer pieces ffmpeg has finished writing."""
        with self.lock:
            for i in range(1, self.part + 1):
                f = self.buf / f"p{i:02d}.csv"
                if not f.exists():
                    continue
                rows = [r.split(",") for r in f.read_text(encoding="utf-8", errors="ignore").splitlines() if r.count(",") >= 2]
                self._place(i, rows[self.csv_seen.get(i, 0):])
                self.listed.update(r[0] for r in rows if len(r) > 2)
                self.csv_seen[i] = len(rows)
            keep_from = self._keep_from()
            while self.segments and self.segments[0][2] < keep_from:
                self.segments.pop(0)[0].unlink(missing_ok=True)
            if time.time() - self.swept > 60:
                # A recording that is stopped in the middle of a piece leaves that piece behind with no line
                # in ffmpeg's list: no clip is ever cut from it, and it would sit in the buffer for ever. Every
                # restart of the recorder used to leave one behind: about forty megabytes each time.
                self.swept = time.time()
                now = time.time()
                for p in self.buf.glob("p*.ts"):
                    try:
                        if p.name not in self.listed and now - p.stat().st_mtime > 60:
                            p.unlink(missing_ok=True)
                    except OSError:          # already gone: a finished recording clears the buffer
                        pass

    GAP_MAX = 120             # a jump on the stream's own clock bigger than this is the clock jumping, not pieces lost

    def _place(self, part: int, rows, on_disk=False):
        """Put ffmpeg's newly listed pieces of this part on the recording's clock, one after the other.

        Each piece's length is its own (see _piece_len). Where the list says pieces are missing in between (a
        Twitch stream drops some), that much time is left as a gap: adding up the lengths alone would let the
        buffer fall behind the clock clips ask for, until a moment still in the buffer looked long gone. A jump
        on the stream's own clock that can't be lost pieces (backwards, or minutes or hours ahead: Twitch after
        its opening ad) leaves no gap. With `on_disk`, only pieces still on disk are kept (after a restart).
        Called under the lock."""
        for row in rows:
            name, a, b = row[:3]
            try:
                dur = self._piece_len(name, a, b)
                a, b = float(a), float(b)
            except ValueError:
                continue
            if dur <= 0:
                continue
            last = self.csv_end.get(part)
            gap = a - last if last is not None else 0.0
            start = self.seg_clock + (gap if 0.5 < gap <= self.GAP_MAX else 0.0)
            self.csv_end[part] = b
            if not on_disk or (self.buf / name).exists():
                self.segments.append((self.buf / name, start, start + dur))
            self.seg_clock = start + dur

    def _keep_from(self) -> float:
        """Where the buffer starts being kept, on the piece clock."""
        keep_from = self.seg_clock - self.settings["buffer_min"] * 60
        # A clip waiting to be made keeps its footage, however long it waits (a busy render queue, a restart):
        # on 16 Nov clips waited longer than the 20-minute buffer and lost their footage. The margin covers the
        # drift between the transcript's clock and the video's (a few seconds an hour).
        waiting = [c["start"] - PAD - 30 for c in list(self.job.get("clips", [])) if c.get("status") == "queued" and not c.get("src")]
        return min([keep_from] + waiting)

    # ---------------------------------------------------------------- chat (Twitch only)
    def _chat(self, channel):
        while not self.stop_flag.is_set() and not self.ended:
            try:
                raw = socket.create_connection(("irc.chat.twitch.tv", 6697), timeout=20)
                s = ssl.create_default_context().wrap_socket(raw, server_hostname="irc.chat.twitch.tv")
                s.sendall(f"NICK justinfan{int(time.time()) % 90000 + 10000}\r\nJOIN #{channel}\r\n".encode())
                s.settimeout(30)
                rest = b""
                self.chat_on = True
                while not self.stop_flag.is_set() and not self.ended:
                    try:
                        data = s.recv(8192)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    *lines, rest = (rest + data).split(b"\r\n")
                    for line in lines:
                        if line.startswith(b"PING"):
                            s.sendall(b"PONG :tmi.twitch.tv\r\n")
                        elif b" PRIVMSG #" in line:
                            with self.lock:
                                second = int(self.samples / SR)
                                self.chat[second] += 1
                                self.chat_total += 1
                                # how many different people, not how many messages: a wave of the same
                                # emotes is not a crowd getting excited
                                who = _NICK.findall(line.decode("utf-8", "replace"))
                                if who:
                                    self.chat_users.setdefault(second, set()).add(who[0].lower())
                                    self.chat_kept = _trim_names(self.chat_users, second, self.chat_kept)
                s.close()
            except Exception:  # noqa: BLE001
                pass
            self.chat_on = False
            self.stop_flag.wait(5)

    KICK_EVERY = 3            # seconds between reads of a Kick channel's recent messages

    def _kick_chat(self, slug):
        """Kick: its public list of a channel's last messages, read every few seconds. Only new message ids are
        counted, on the second they were seen; the text is never kept."""
        from . import profiles
        seen, room, wait = collections.deque(maxlen=400), None, 0
        while not self.stop_flag.is_set() and not self.ended:
            if wait:
                self.chat_on = False
                self.stop_flag.wait(wait)
                wait = 0
                continue
            try:
                if room is None:
                    _, raw = profiles._get(f"https://kick.com/api/v2/channels/{slug}", "application/json")
                    room = int(json.loads(raw)["id"])
                _, raw = profiles._get(f"https://kick.com/api/v2/channels/{room}/messages", "application/json")
                ids = [str(m.get("id")) for m in ((json.loads(raw).get("data") or {}).get("messages") or []) if m.get("id")]
                new = [i for i in ids if i not in seen]
                if self.chat_on and new:                   # the first read is the backlog: not counted
                    with self.lock:
                        self.chat[int(self.samples / SR)] += len(new)
                        self.chat_total += len(new)
                seen.extend(new)
                self.chat_on = True
                self.stop_flag.wait(self.KICK_EVERY)
            except Exception:  # noqa: BLE001 - Kick refused or is down: try again in a while, recording carries on
                wait = 30

    def _chat_spike(self, s, e):
        """True if chat was at least 3x busier than usual during (and just after) these seconds.

        The crowd is counted as people, not messages: ten people saying "GG" is a reaction, one
        person saying "GG" ten times is a bot."""
        elapsed = self.samples / SR
        if not self.chat_total or elapsed < 60:
            return False
        avg = self.chat_total / elapsed
        a, b = int(s), int(e) + 6
        rate = sum(self.chat.get(t, 0) for t in range(a, b + 1)) / (b - a + 1)
        if rate <= max(3 * avg, 1.0):
            return False
        people = self.chat_people()
        if not people:
            return True
        p_avg = sum(len(v) for v in people.values()) / max(1, len(people))
        p_rate = sum(len(people.get(t, ())) for t in range(a, b + 1)) / (b - a + 1)
        return p_rate >= max(2.0, 1.5 * p_avg)

    def chat_people(self):
        """Second -> how many different people were talking, where that is known.

        A copy is taken under the lock: the chat reader thread adds to it as it goes."""
        with self.lock:
            return {t: set(names) for t, names in self.chat_users.items()}

    # ---------------------------------------------------------------- analysis
    def _transcribe_step(self):
        with self.lock:
            n = len(self.pending) // 2
            hurry = self.ended or bool(self.manual)
            if n < (SR if hurry else SR * 5):   # about every 4 s, so "what was just said" keeps up with the stream
                return False
            audio = np.frombuffer(bytes(self.pending[: n * 2]), dtype=np.int16)
            base = self.pending_base
        cut = n
        if not hurry or n > SR * 24:
            # cut at the quietest moment between 2.5 and 4.5 s, so no word is split; the rest waits for the next pass
            _, cut = next(transcribe._chunks(audio, 2.5, 4.5))
        words = transcribe.transcribe(audio[:cut], lang=self.job.get("lang") or "en")
        off = base / SR
        for w in words:
            w["s"], w["e"] = round(w["s"] + off, 2), round(w["e"] + off, 2)
        with self.lock:
            del self.pending[: cut * 2]
            self.pending_base += cut
            self.words += words
            self.t_done = self.pending_base / SR
        self._triggers(words)
        (self.dir / "words.json").write_text(json.dumps(self.words), encoding="utf-8")
        return True

    AUTO_BEFORE, AUTO_AFTER, AUTO_GAP = 30, 20, 60   # seconds of stream sent around a jump; least time between jumps
    FORCED_MIN = 60                                   # the lowest score a forced clip (none for a while) may have
    ALIGN_SPAN = 90                                   # how far apart the video and transcript clocks may have slipped
    JUMP = 0.6                                        # default: 60% louder or busier than the last three minutes

    def _watch_spikes(self):
        """Always on. Keep a running average of loudness and chat over the last three minutes (leaving out the
        last few seconds), and mark a moment when the last five seconds jump a set percentage above it. Nothing is
        sent yet: the AI gets that moment once its transcript is ready, with the half minute before it."""
        with self.lock:
            n = len(self.db)
            if n < 60:
                self.base = {"warming": True}
                return
            db = np.array(self.db[max(0, n - 190):], dtype=np.float32)
            people = self.chat_people()
            if people:
                chat = [len(people.get(t, ())) for t in range(max(0, n - 190), n)]
            else:
                chat = [self.chat.get(t, 0) for t in range(max(0, n - 190), n)] if (self.chat_on or self.chat_total) else None
        amp = 10 ** (db / 20)                       # loudness as plain level, so a percentage means something
        base, cur = float(amp[:-10].mean()), float(amp[-5:].mean())
        loud_jump = cur / max(base, 1e-6) - 1
        try:                                        # the creator's own setting: how far above usual counts as a jump
            pct = max(0.2, min(3.0, float(self.settings.get("auto_jump_pct") or self.JUMP * 100) / 100))
        except (TypeError, ValueError):
            pct = self.JUMP
        why = []
        if loud_jump >= pct:
            why.append(f"loudness +{round(loud_jump * 100)}%")
        chat_base = chat_now = None
        if chat is not None:
            chat_base = sum(chat[:-10]) / max(1, len(chat) - 10)
            chat_now = sum(chat[-8:]) / 8          # chat is bursty, so look at a slightly longer stretch
            if chat_now >= max(1.5, chat_base * (1 + pct)):
                why.append(f"chat +{round((chat_now / max(chat_base, 0.01) - 1) * 100)}%")
        self.base = {"loud_db": round(20 * np.log10(max(base, 1e-6)), 1), "loud_now": round(loud_jump * 100),
                     "chat": round(chat_base, 2) if chat_base is not None else None,
                     "chat_now": round(chat_now, 2) if chat_now is not None else None, "pct": round(pct * 100)}
        if why and not self.spike and n - self.last_spike >= self.AUTO_GAP:
            self.spike = {"t": float(n - 3), "why": ", ".join(why)}
            self.last_spike = n
            self.spikes += 1

    def _ai_check(self, window=None, why=None):
        """Judge 1 reads only the words. If it finds a moment, judge 2 reads only the loudness and chat activity
        for the same stretch, without seeing the words. A clip is made when both say yes (or as set in Setup)."""
        st = self.settings
        every = int(st.get("ai_every_min", 1)) or 1
        if window:                    # just the stretch around a jump, or around someone saying "clip"
            ws, now = max(0.0, window[0]), window[1]
        else:
            now = self.t_done
            ws = max(0.0, now - max(every, 3) * 60 - 90)  # each check overlaps the last by 90 s, so nothing falls between
        self.last_check = self.t_done
        win = [w for w in self.words if ws <= w["s"] <= now]
        # clips that count towards the hour's limit: ones the AI picked that were made or are being made (a clip that
        # failed, was left out or was cancelled doesn't use up the hour)
        recent = [c for c in self.job["clips"] if c.get("score", 0) > 0 and c.get("status") not in ("dropped", "error", "cancelled")
                  and time.time() - c.get("made_at", 0) < 3600]
        last_clip = max((c.get("made_at", 0) for c in self.job["clips"]), default=self.started_at)
        must = st["must_clip_min"] > 0 and time.time() - last_clip >= st["must_clip_min"] * 60
        verdict = {"t": round(now), "transcript": None, "reaction": None, "trigger": why}
        if len(win) < 8:
            self.skipped += 1
            return self._verdict(verdict, "Skipped: nobody talking")
        if len(recent) >= st["live_per_hour"] and not must:
            self.skipped += 1
            return self._verdict(verdict, f"Skipped: {st['live_per_hour']} clips this hour already")
        usage.set_context(self.job["creator"], self.job["title"] or "Live stream")
        title, up = self.job["title"], self.job["uploader"]
        try:
            cands, tok = brain.judge_transcript(win, ws, now, title, up, st["min_len"], st["max_len"], self.job["clips"],
                                                n=1 if every <= 2 else 2, must=must, focus=st.get("focus", ""),
                                                game=self._game(), timeout=config.LIVE_AI_TIMEOUT)  # Auto (0) asks for one
        except Exception as e:  # noqa: BLE001
            self.job["ai"]["last_error"] = str(e)[-200:]
            return self._verdict(verdict, "AI error: " + str(e)[-80:])
        self.checks += 1
        self.tokens += tok
        # a forced pick (no clip for a while) still has to be a real moment: decent, and with people talking in it
        talk = lambda c: sum(1 for w in win if c["start"] <= w["s"] <= c["end"])          # noqa: E731
        keep = [c for c in cands if c["score"] >= st["min_score"] or (must and c["score"] >= self.FORCED_MIN and talk(c) >= 20)]
        verdict["transcript"] = max((c["score"] for c in cands), default=0)
        if not keep:
            return self._verdict(verdict, "Words judge: nothing clip-worthy")
        z = brain.loud_zscore(np.array(self.db, dtype=np.float32))
        lo, hi = int(ws), int(now) + 1
        people = self.chat_people()
        if people:
            chat = [len(people.get(t, ())) for t in range(lo, hi)]
            unit = "people"
        else:
            chat = [self.chat.get(t, 0) for t in range(lo, hi)] if (self.chat_on or self.chat_total) else None
            unit = "messages"
        try:
            sig, tok2 = brain.judge_signals(z[lo:hi], chat, lo, now, unit=unit, timeout=config.LIVE_AI_TIMEOUT)
            self.checks += 1
            self.tokens += tok2
        except Exception as e:  # noqa: BLE001
            sig = []
            self.job["ai"]["last_error"] = str(e)[-200:]
        added = 0
        for c in keep:
            match = [m for m in sig if m["end"] >= c["start"] - 10 and m["start"] <= c["end"] + 10 and m["score"] >= 50]
            rs = max((m["score"] for m in match), default=0)
            verdict["reaction"] = rs
            both = bool(match)
            ok = both or (st["decide_mode"] == "either" and c["score"] >= 90) or (must and added == 0)
            if not ok:
                self.rejected += 1
                self._verdict(verdict, "Reaction judge said no, not clipped")
                continue
            if any(abs(c["start"] - k["start"]) < st["min_gap_min"] * 60 for k in self.job["clips"]):
                self._verdict(verdict, "Too close to the last clip")
                continue
            why = "Both judges agreed" if both else ("Words judge was very sure" if c["score"] >= 90 and
                                                     st["decide_mode"] == "either" else "Forced: no clip for a while")
            c["judges"] = {"words": c["score"], "reaction": rs or None, "why": why}
            c["reason"] = f"{c['reason']} ({why.lower()}.)"
            self._add_clip(c)
            self._verdict(verdict, "Clipped: " + why.lower())
            added += 1
            must = False
            if added >= (1 if every <= 2 else 2):
                break

    def _verdict(self, v, result):
        self.verdicts.append(dict(v, result=result))
        del self.verdicts[:-6]

    CLIP_WORDS = re.compile(r"\bclip(\s+(it|that|this))?\b", re.I)
    SAID_BEFORE, SAID_AFTER, SAID_GAP = 60, 15, 45

    def _triggers(self, new_words):
        """Built in: when someone says "clip" ("clip it", "clip that"), the AI looks at that moment as soon as its
        transcript is ready; people say it right after something worth clipping. Then the creator's own trigger
        phrases from Setup, which clip straight away with no AI check."""
        for w in new_words or []:
            if self.CLIP_WORDS.search(re.sub(r"[^a-z' ]", "", w["w"].lower())) and w["s"] - self.last_said > self.SAID_GAP \
                    and not self.said:
                self.said = {"t": w["s"], "why": 'Someone said "clip"'}
                self.last_said = w["s"]
        phrases = [p.strip().lower() for p in self.settings.get("trigger_words", "").split(",") if p.strip()]
        if not phrases or not new_words:
            return
        key = [_plain(w["w"]) for w in new_words]
        for p in phrases:
            parts = [_plain(x) for x in p.split()]
            for i in range(len(key) - len(parts) + 1):
                if key[i:i + len(parts)] == parts:
                    t = new_words[i]["s"]
                    if t - self.last_trigger > 45:
                        self.last_trigger = t
                        self.manual.append({"start": max(0.0, t - 30), "end": t + 2, "why": f'Someone said "{p}"'})

    def _manual_step(self):
        for req in [m for m in self.manual if m["end"] <= self.t_done or self.ended]:
            text = " ".join(w["w"] for w in self.words if req["start"] <= w["s"] <= req["end"])
            usage.set_context(self.job["creator"], self.job["title"] or "Live stream")
            try:
                c = brain.describe(text, self.job["title"], self.job["uploader"], game=self._game(),
                                   timeout=config.LIVE_AI_TIMEOUT)
            except Exception as e:  # noqa: BLE001
                # Someone asked for this clip by hand. A slow or broken AI costs it its hook, not the clip.
                self.job["ai"]["last_error"] = str(e)[-200:]
                c = {"score": 0, "category": "other", "hook": "", "title": self.job["title"],
                     "description": "", "hashtags": [], "highlight_words": []}
            self.manual.remove(req)
            c.update(start=round(req["start"], 2), end=round(min(req["end"], self.samples / SR), 2), score=0, asked=True)
            if req.get("why"):
                c["reason"] = req["why"]
            self._add_clip(c)

    def _add_clip(self, c):
        self.n += 1
        c.update(id=f"clip{self.n:03d}", style=self.settings["style"], layout=self.settings["layout"],
                 captions=self.job["options"].get("captions", True), status="queued", version=0, made_at=time.time())
        if self.settings.get("look"):
            c.update(look=self.settings["look"], template=self.settings.get("template", ""))
        if self.job["options"].get("hook_text"):      # the person's own big text, in place of the AI's
            c["hook"] = self.job["options"]["hook_text"]
        if self.job["options"].get("description_text"):
            c["description"], c["fixed_description"] = self.job["options"]["description_text"], True
        else:
            hashtags.apply(c, self.words, self.job.get("title", ""), self.job.get("uploader", ""), self._game())
        with self.lock:
            self.job["clips"].append(c)
        jobs.save(self.job)
        jobs.submit_live(lambda: self._render(c))

    def _render(self, clip):
        """Runs on the render worker: copy the needed buffer pieces, then make the clip."""
        if not self._cut(clip):
            return
        # Only the gameplay layout uses a webcam, and only once the clip's own file is written: the
        # webcam is looked for in it, once per stream.
        if clip.get("layout") == "gameplay":
            if not self.facecam_checked:
                self.facecam_box = self._find_facecam(clip["src"])
                self.facecam_checked = True
            if self.facecam_box:
                clip["facecam"] = self.facecam_box
        jobs.render_one(self.job, clip, list(self.words))

    def _cut(self, clip) -> bool:
        """Copy the buffer pieces this clip needs into its own file and find where it really starts. False (and the
        clip marked as failed) if its footage is gone."""
        after = jobs.HEAR_AFTER + 2      # what the moment builds up to may come just after it: wait for it to be recorded
        want_a, want_b = clip["start"], clip["end"]       # on the transcript clock
        for attempt in range(2):
            ok = self._cut_once(clip, want_a, want_b, after)
            if not ok:
                return False
            # The pieces are picked on the video clock, which drifts from the transcript clock. If the moment still
            # is not inside the piece (the drift moved more than the padding since the last clip), cut again with the
            # clock just measured, instead of making a clip that ends before it starts.
            if clip["src_offset"] <= want_a + 1 and clip["src_end"] >= want_b - 1:
                break
            if attempt == 0:
                Path(clip["src"]).unlink(missing_ok=True)
        span = _clip_span({"start": want_a, "end": want_b}, clip["src_offset"], clip["src_end"])
        if span is None:
            # The end of the moment is not in the recorded picture: the picture can stop for a while on a
            # stream hiccup, and everything said meanwhile sits on the sound clock only. Cutting what is
            # there would make a clip that stops before its payoff, so this one is not made at all.
            Path(clip["src"]).unlink(missing_ok=True)
            clip.update(status="error", error="That part of the stream was not recorded: the picture stopped "
                                              "before the end of it.")
            jobs.save(self.job)
            return False
        clip["start"], clip["end"] = span
        if clip["end"] - clip["start"] < 3:
            clip.update(status="error", error="That part of the stream could not be found in the recording.")
            jobs.save(self.job)
            return False
        return True

    def _cut_once(self, clip, want_a, want_b, after) -> bool:
        a, b = want_a - self.clock_fix, want_b - self.clock_fix      # the same moment on the video pieces' clock
        # The picture comes a few seconds behind the sound, and after a hiccup on the stream it can be a
        # minute behind. Wait for it while it is still being written: a clip is never cut down to a stub.
        began, moved_at = time.time(), time.time()
        with self.lock:
            seen = self.seg_clock
        while time.time() - began < 150:
            self._scan_segments()
            if self.ended or (self.segments and self.segments[-1][2] >= b + after):
                break
            with self.lock:
                clock = self.seg_clock
            if clock > seen + 0.5:
                moved_at, seen = time.time(), clock       # the picture is still arriving: it will get there
            elif time.time() - moved_at > 20:
                break               # the picture has stopped arriving: waiting will not help
            time.sleep(1)
        with self.lock:
            segs = [s for s in self.segments if s[2] > a - PAD and s[1] < b + max(PAD, after) and s[0].exists()]
        if not segs or segs[0][1] > a + 2:
            clip.update(status="error", error="That part of the stream is no longer in the buffer.")
            jobs.save(self.job)
            return False
        if any(y[1] > x[2] + 2 for x, y in zip(segs, segs[1:]) if x[2] < b and y[1] > a):
            # Pieces are missing inside the moment: the clip would come out short, with its captions early.
            clip.update(status="error", error="That part of the stream was not recorded: the recording dropped out there.")
            jobs.save(self.job)
            return False
        if "width" not in self.job:
            meta = media.probe(segs[0][0])
            self.job.update(width=meta["width"], height=meta["height"])
        lst = self.dir / "clips" / f"{clip['id']}.txt"
        lst.write_text("".join(f"file '{s[0].as_posix()}'\n" for s in segs), encoding="utf-8")
        src = self.dir / "clips" / f"{clip['id']}.src.ts"
        try:
            media.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                       "-c", "copy", str(src)])
        except Exception as e:  # noqa: BLE001
            clip.update(status="error", error=media_safe(e))
            jobs.save(self.job)
            return False
        finally:
            lst.unlink(missing_ok=True)
        dur = segs[-1][2] - segs[0][1]
        offset = self._align(src, segs[0][1] + self.clock_fix, dur)
        self.clock_fix = offset - segs[0][1]  # the two clocks drift ~4 s an hour; start the next clip from here
        clip.update(src=str(src), src_offset=round(offset, 2), src_end=round(offset + dur, 2))
        return True

    def _align(self, src, guess, dur):
        """Where `src` really starts on the transcript clock. The video pieces and the audio feed keep separate
        clocks that drift apart, so transcribe the clip's own audio and line its words up with the live words.
        Local speech model only: no AI calls."""
        key = lambda w: _plain(w).replace("'", "")
        try:
            wav = src.with_suffix(".wav")
            media.extract_audio(src, wav)
            heard = transcribe.transcribe(media.load_audio(wav), lang=self.job.get("lang") or "en")
            wav.unlink(missing_ok=True)
            with self.lock:
                near = [w for w in self.words if guess - self.ALIGN_SPAN <= w["s"] <= guess + dur + self.ALIGN_SPAN]
            at = collections.defaultdict(list)
            for w in near:
                at[key(w["w"])].append(w["s"])
            # every way a word heard in the clip could line up with the same word in the recording's transcript;
            # the true offset is where many of them agree (within a second)
            votes = sorted(t - h["s"] for h in heard if len(key(h["w"])) >= 3 for t in at.get(key(h["w"]), [])
                           if abs(t - h["s"] - guess) <= self.ALIGN_SPAN)
            best, j = [], 0
            for i in range(len(votes)):
                while votes[i] - votes[j] > 1.0:
                    j += 1
                if i - j + 1 > len(best):
                    best = votes[j:i + 1]
            if len(best) >= 4:
                return float(np.median(best))
        except Exception:  # noqa: BLE001 - fall back to the piece clock
            pass
        return guess

    def clip_last(self, seconds):
        end = self.samples / SR
        if self.seg_clock and self.seg_clock < end:
            end = self.seg_clock        # the picture has not caught up with the sound: clip what was recorded
        if end < 5:
            raise ValueError("Nothing has been recorded yet.")
        self.manual.append({"start": max(0.0, end - seconds), "end": end})

    def _save_state(self):
        with self.lock:
            self.job["live_state"] = {"part": self.part, "part_starts": {str(k): list(v) for k, v in self.part_starts.items()},
                                      "t_done": self.t_done, "pending_base": self.pending_base, "n": self.n,
                                      "last_check": self.last_check, "started_at": self.started_at, "checks": self.checks,
                                      "rejected": self.rejected, "tokens": self.tokens, "skipped": self.skipped,
                                      "afiles": [list(a) for a in self.afiles], "db_n": len(self.db), "fixed": dict(self.fixed),
                                      "db_tail": [round(x, 1) for x in self.db[-200:]], "saved_at": time.time()}

    def _publish(self):
        self._save_state()
        elapsed = self.samples / SR
        lines = brain.make_lines([w for w in self.words if w["s"] >= self.t_done - 45])[-5:]
        with self.lock:
            sec = int(elapsed)
            self.job.update(
                duration=elapsed,
                live={"elapsed": round(elapsed), "buffer_sec": round(self.segments[-1][2] - self.segments[0][1]) if self.segments else 0,
                      "lines": [l["text"] for l in lines], "level": [round(x) for x in self.db[-60:]],
                      "chat": [self.chat.get(t, 0) for t in range(max(0, sec - 60), sec)] if self.chat_on or self.chat_total else None,
                      "checks": self.checks, "skipped": self.skipped, "tokens": self.tokens, "words": len(self.words),
                      "rejected": self.rejected, "verdicts": list(reversed(self.verdicts)),
                      "every_min": int(self.settings.get("ai_every_min", 1)) or 1,
                      "auto": None,
                      "pending_clips": len(self.manual)})
            base = self.job["live_state"]["pending_base"]
        jobs.save(self.job)
        # Audio the transcript has passed is deleted, one save behind: what is on disk always says the transcript
        # is at least that far, so picking the recording up after a restart never needs audio that is gone.
        self._trim_audio(self.saved_base)
        self.saved_base = base

    def _analyze(self):
        cid = self.job["creator"]
        started = False
        while True:
            busy = False
            try:
                if not self.user_stopped and not self.ended:      # a recording cannot run for ever
                    if time.time() - self.started_at > config.MAX_LIVE_HOURS * 3600:
                        self.stop(f"Stopped after {config.MAX_LIVE_HOURS:g} hours, the longest one recording can run")
                self._scan_segments()
                busy = self._transcribe_step()
                if not started and self.samples:
                    started = True
                    self.job.update(status="live", stage="Recording")
                    creators.log(cid, f"Recording started: {self.job['title']}")
                self._manual_step()
                # Three ways the AI is asked: on the creator's schedule (every N minutes), and always, when the
                # stream suddenly gets louder or chat busier, or when someone says "clip".
                every = int(self.settings.get("ai_every_min", 1)) or 1
                self._watch_spikes()
                sp, said = self.spike, self.said
                if self.user_stopped:
                    pass
                elif said and (self.t_done >= said["t"] + self.SAID_AFTER or (self.ended and not busy)):
                    self.said = None
                    self._ask_ai((said["t"] - self.SAID_BEFORE, said["t"] + self.SAID_AFTER), said["why"])
                elif sp and (self.t_done >= sp["t"] + self.AUTO_AFTER or (self.ended and not busy)):
                    self.spike = None
                    self._ask_ai((sp["t"] - self.AUTO_BEFORE, sp["t"] + self.AUTO_AFTER), "Jump: " + sp["why"])
                elif self.t_done - self.last_check >= every * 60 or (self.ended and not busy and self.t_done > self.last_check):
                    self._ask_ai()
                self._publish()
                self._trouble(None)
            except Exception as e:  # noqa: BLE001
                self.job["ai"]["last_error"] = str(e)[-200:]
                self._trouble(e)
            # Leaving here must not happen while an AI check is still running: it may still be adding a
            # clip, and cleanup() deletes the buffer that clip is cut from. That is true after a stop as
            # well as at the end of a stream, so both wait for it. Stopping is still instant on the page:
            # stop() has already written the status.
            if ((self.user_stopped and not self.manual and not self.ai_busy)
                    or (self.ended and not busy and not self.ai_busy
                        and len(self.pending) < SR * 2 and not self.manual)):
                break
            if not busy:
                time.sleep(2)
        failed = bool(self.job.get("error")) and not started
        self.job.update(status="error" if failed else "done", stage="Could not record" if failed else self.end_note)
        jobs.save(self.job)
        creators.log(cid, f"Recording finished ({self.end_note.lower()}): {len(self.job['clips'])} clips, "
                          f"{round(self.samples / SR / 60)} min" if not failed else f"Recording failed: {self.job['error']}")

        if failed:
            alerts.notify(f"recording failed for {self.job['source']['name']}: {self.job['error']}")
        else:
            alerts.notify(f"recording finished for {self.job['source']['name']}: {len(self.job['clips'])} clips, "
                          f"{round(self.samples / SR / 60)} min.")

        jobs.submit_live(self._cleanup)       # runs after any queued renders, then frees the buffer

    def _cleanup(self):
        if self.cap:                       # ffmpeg first: it must not write new pieces into a folder being emptied
            self.cap.stop()
            for _ in range(40):
                if not self.cap.alive():
                    break
                time.sleep(0.5)
        capture.forget(self.job["id"])
        for f in self.buf.glob("*"):
            try:
                f.unlink(missing_ok=True)
            except OSError:                # still held open for a moment: housekeeping takes it later
                pass
        self.finished = True
        SESSIONS.pop(self.job["id"], None)


def _clip_span(clip, src_offset, src_end):
    """The part of a clip the recorded picture really holds, or None when the end of it was never recorded.

    The clip's own times are on the transcript clock; the source file starts at `src_offset` and covers up to
    `src_end`. A second of give is allowed at the edges, because the picture stops between two frames."""
    if clip["end"] > src_end + 1:
        return None
    return max(clip["start"], src_offset), min(clip["end"], src_end)


def _pieces_on_disk(s):
    """Every piece still on disk, put back on the recording's clock from ffmpeg's own lists. Used after a restart,
    when the running clock is gone (see Session._place). A recording saved by an older ClipHeist has no saved state:
    its parts are then simply counted on disk."""
    st = s.job.get("live_state") or {}
    s.fixed = {str(k): float(v) for k, v in (st.get("fixed") or {}).items()}
    s.part_starts = {int(k): (float(v[0]), int(v[1])) for k, v in (st.get("part_starts") or {}).items()}
    s.part = max(int(st.get("part") or 0), len(list(s.buf.glob("p*.csv"))))
    s.seg_clock = 0.0
    s.csv_seen = {}
    with s.lock:
        for i in range(1, s.part + 1):
            f = s.buf / f"p{i:02d}.csv"
            s.seg_clock = max(s.seg_clock, s.part_starts.get(i, (s.seg_clock, 0))[0])
            if not f.exists():
                continue
            rows = [r.split(",") for r in f.read_text(encoding="utf-8", errors="ignore").splitlines() if r.count(",") >= 2]
            s._place(i, rows, on_disk=True)
            s.listed.update(r[0] for r in rows)
            s.csv_seen[i] = len(rows)


def recover(job):
    """After a restart: finish the clips of an interrupted recording from the stream pieces still on disk."""
    c = creators.get(job["creator"]) or dict(creators.DEFAULTS)
    s = Session(job, c)
    s.ended, s.end_note = True, "Recording stopped when the studio closed"
    _pieces_on_disk(s)
    try:
        s.words = json.loads((s.dir / "words.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        s.words = []
    s.n = len(job["clips"])
    todo = [k for k in job["clips"] if k.get("status") == "queued"]
    for k in todo:
        jobs.submit_live(lambda k=k: s._render(k))

    def cleanup():
        for f in s.buf.glob("*"):
            f.unlink(missing_ok=True)
        capture.forget(job["id"])
        job.pop("recover", None)
        jobs.save(job)
        creators.log(job["creator"], f"Finished {len(todo)} clip(s) from a recording interrupted by a restart")

    jobs.submit_live(cleanup)


def _plain(word: str) -> str:
    """A word reduced to its letters and digits, in any alphabet (no Arabic vowel marks), for matching words up."""
    return re.sub(r"[^\w']|_", "", transcribe.ARABIC_MARKS.sub("", word.lower()))


def attach(job) -> bool:
    """After an app update: the recorder kept recording this stream. Rebuild the session from what is on disk (the
    saved position, the transcript, the audio not transcribed yet, the video pieces) and carry on from exactly
    where the transcript had got to. False if it can't be picked up (the caller then pauses it the old way)."""
    cap = capture.running(job["id"])
    if not cap:
        return False
    got = _rebuild(job, cap)
    if not got:
        return False
    s, at = got
    _carry_on(job, s, attached=at)
    creators.log(job["creator"], "Recording carried on through an app update")
    return True


RESUME_WITHIN = 30 * 60      # a recording stopped by a restart is carried on (same timer, buffer, clips) within this
RESUMING = set()             # creators whose stopped recording is being picked up: auto-record waits for it


def resume(job) -> bool:
    """After a restart that stopped the recorder too (it runs inside the app when there is no recorder service):
    the same recording carries on, as if nothing happened. What was recorded up to the stop is taken in first, then
    the stream is recorded again into the same recording: the timer, the buffer, the clips, the transcript and the
    AI's history all continue. False if it can't be picked up."""
    got = _rebuild(job, None)
    if not got:
        return False
    s, at = got
    _carry_on(job, s, resumed=at)
    creators.log(job["creator"], "Recording carried on after a restart")
    return True


def _rebuild(job, cap):
    """The session of an interrupted recording, rebuilt from what is on disk; with where its audio should be taken
    in from (part, file, byte). None if there is not enough on disk to do it (a recording saved by an older
    ClipHeist, which kept no position: it is then recorded again as a new recording, and its waiting clips are
    finished from its buffer)."""
    st = job.get("live_state") or {}
    base = int(st.get("pending_base") or 0)                 # the first sample not transcribed yet
    held = [(str(n), int(a)) for n, a in (st.get("afiles") or []) if int(a) <= base]
    m = Session.AUDIO.match(held[-1][0]) if held else None  # the audio file that sample is in
    if not st.get("part") or not m:
        return None
    s = Session(job, creators.get(job["creator"]) or dict(creators.DEFAULTS))
    s.cap = cap
    for k in ("last_check", "started_at", "checks", "rejected", "tokens", "skipped"):
        if k in st:
            setattr(s, k, st[k])
    s.n = max(int(st.get("n") or 0), len(job.get("clips", [])))
    try:
        words = json.loads((s.dir / "words.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        words = []
    # the transcript file can be a few seconds ahead of the saved position: those words are heard again below
    s.words = [w for w in words if w["s"] < round(base / SR, 2)]
    s.pending_base = s.saved_base = s.samples = base
    s.t_done = base / SR
    s.afiles = [list(held[-1])]
    tail = [float(x) for x in st.get("db_tail") or []]      # loudness up to the update, so the first seconds back
    full = [-100.0] * max(0, int(st.get("db_n") or 0) - len(tail)) + tail    # are not taken for a sudden jump
    n = int(base / SR)
    s.db = (full + [full[-1] if full else -100.0] * n)[:n]
    s.last_spike = n
    s.verdicts = list(reversed((job.get("live") or {}).get("verdicts") or []))
    _pieces_on_disk(s)                                      # the video pieces on the session clock
    s.part = int(st["part"])
    return s, (int(m.group(1)), int(m.group(2)), (base - held[-1][1]) * 2)


def _carry_on(job, s, attached=None, resumed=None):
    job.update(status="live", stage="Recording")
    for k in ("attach", "recover", "was_live", "resuming"):
        job.pop(k, None)
    jobs.save(job)
    SESSIONS[job["id"]] = s
    for k in [k for k in job.get("clips", []) if k.get("status") == "queued"]:     # clips waiting when it stopped
        if k.get("src"):
            jobs.submit_live(lambda k=k: jobs.render_one(job, k, list(s.words)))   # its footage was already set aside
        else:
            jobs.submit_live(lambda k=k: s._render(k))
    threading.Thread(target=ctx.bind(s._ingest), kwargs={"attached": attached, "resumed": resumed}, daemon=True).start()
    threading.Thread(target=ctx.bind(s._analyze), daemon=True).start()
    s.start_chat()


def start_session(creator: dict, url=None, watermark=False) -> dict:
    url = normalize(url or creator.get("channel") or "")
    if not re.match(r"^https?://[^/]+\.[^/]+/", url + "/"):
        raise ValueError("Add this creator's channel link first.")
    if active_for(creator["id"]):
        raise ValueError("This creator is already being recorded.")
    from . import fetch_model, tools
    lang = creator.get("lang") if fetch_model.known(creator.get("lang")) else fetch_model.DEFAULT
    if lang == fetch_model.DEFAULT and not transcribe.model_ready() and not transcribe.REMOTE:
        raise ValueError("Recording isn't possible on this server yet: its speech model is missing.")
    if not media.site_allowed(url):
        raise ValueError("Channel links must be from YouTube, Twitch or Kick.")
    if media.is_youtube(url) and media.youtube_paused():
        raise ValueError(media.YT_BLOCKED.replace("download videos", "record YouTube streams")
                         .replace("Upload the video file instead, or use a Twitch or Kick link. YouTube links",
                                  "Twitch and Kick streams still work. YouTube"))
    if len(active_mine()) >= config.MAX_LIVE_PER_USER:
        raise ValueError(f"You're already recording {len(active_mine())} streams, the most this server allows at once. Stop one first.")
    if _full(url):
        raise ValueError("ClipHeist is recording as many streams as it can right now. Try again in a little while.")
    if tools.missing():
        raise ValueError(tools.HELP)
    if lang != fetch_model.DEFAULT and not transcribe.REMOTE and not fetch_model.ready(lang):
        raise ValueError(f"This creator speaks {fetch_model.label(lang)}, and its speech model isn't on this computer "
                         "yet. Add it in Settings → Speech models (an admin can do this), then start recording again.")
    # ClipHeist: every clip of a recording comes off the clip balance (credits.on_clip), so a recording only starts
    # while there is a balance, and the free plan's clips carry the watermark.
    from . import credits
    try:
        bal = credits.profile()
    except Exception as e:  # noqa: BLE001
        raise ValueError("Couldn't check your clip balance. Try again in a moment.") from e
    if not bal.get("unlimited"):
        if bal.get("missing"):
            raise ValueError("Sign in once at clipheist.site with this Google account to get your free clips.")
        if bal["left"] <= 0:
            raise ValueError("You're out of clips. Get more at clipheist.site/pricing.")
    job = jobs.create({"type": "live", "url": url, "name": f"{creator['name']} live"}, {}, enqueue=False,
                      creator=creator["id"], kind="live")
    job.update(status="live", stage="Connecting to the stream", lang=lang,
               watermark=bool(watermark or (not bal.get("unlimited") and bal.get("watermark"))))
    jobs.save(job)
    Session(job, creator).start()
    return job


def start_from_link(url: str, meta: dict, watermark=False) -> dict:
    """ClipHeist: a link pasted into "add a video" that is a stream on air right now. It has no end, so it can't be
    downloaded like a video: it is recorded and clipped as it happens. The recording is filed under the streamer's
    own workspace: the one already there for that channel, or a new one made now. Returns the recording's job (the
    one already running, when that channel is being recorded). `meta` is media.info() for the link."""
    from . import profiles
    plat = profiles.platform_of(url).lower()
    # a Twitch or Kick link is the channel itself; a YouTube link names one broadcast, and yt-dlp says whose it is
    link = creators.channel_link(meta.get("channel_url") or "" if plat == "youtube" else url)
    if plat not in creators.PLATFORMS or not re.match(r"^https?://[^/]+\.[^/]+/.", link + "/"):
        raise ValueError("This stream's channel couldn't be worked out from the link. Add the streamer under "
                         "Streamers, then press Start recording.")
    want = link.rstrip("/").lower()
    who = next((c for c in creators.all_creators()
                if want in {str(u).rstrip("/").lower() for u in (c.get("channels") or {}).values()}), None)
    if not who:
        if len(creators.all_creators()) >= config.MAX_CREATORS:
            raise ValueError(f"This server allows {config.MAX_CREATORS} streamers per account. Remove one first.")
        name = (re.sub(r"\s+", " ", str(meta.get("uploader") or "")).strip()
                or link.rstrip("/").rsplit("/", 1)[-1].lstrip("@") or "Live stream")[:60]
        who = creators.create({"name": name, "channels": {plat: link}, "channel": link, "auto_watch": False})
        creators.log(who["id"], "Added from a pasted live link")
    on = active_for(who["id"])
    if on:
        return on.job                                   # already being recorded: that recording is the answer
    return start_session(who, url if plat == "youtube" else link, watermark=watermark)


def _full(url) -> bool:
    """No room for one more stream: as many as this machine may record, or it is busy already."""
    n = len(active())
    return n >= config.MAX_LIVE_TOTAL or bool(n and load.busy())


def _watch_user(uid):
    """Start recording any of this user's auto-record creators who have gone live."""
    from . import profiles
    with ctx.as_user(uid):
        try:
            todo = [c for c in creators.all_creators() if c["auto_watch"] and c["channel"]]
            if not todo:
                return
        except Exception:  # noqa: BLE001
            return
        for c in todo:
            _watch_one(c)


def check_now(cid):
    """Auto-record was just turned on: look now, on a thread, instead of waiting for the next round."""
    uid = ctx.uid()

    def run():
        from . import profiles
        with ctx.as_user(uid):
            c = creators.get(cid)
            if not c or not c["auto_watch"] or not c["channel"]:
                return
            for url in c.get("channels", {}).values():
                profiles._live.pop(url.rstrip("/").lower(), None)     # a fresh answer, not one from two minutes ago
            _watch_one(c)
            profiles.refresh(cid, force=True)
    threading.Thread(target=run, daemon=True).start()


def _watch_one(c):
    """Start recording this auto-record creator if they are live on any of their channels."""
    from . import profiles
    try:
        if c["id"] in RESUMING or active_for(c["id"]) or len(active_mine()) >= config.MAX_LIVE_PER_USER:
            return
        # the chosen channel first, then their other platforms
        urls = [c["channel"]] + [u for u in c.get("channels", {}).values() if u != c["channel"]]
        if c.get("skip_stream"):
            # stopped by hand during this stream: not started again until they have gone offline once
            if any(profiles.live_shared(profiles.platform_of(u), u).get("live") for u in urls):
                return
            fresh = creators.get(c["id"])
            if fresh:
                fresh.pop("skip_stream", None)
                creators._write(fresh)
            return
        for url in urls:
            if _start_wait(ctx.uid(), normalize(url)):
                continue                                   # failed to start a moment ago: not again yet
            if _full(normalize(url)):
                continue
            # one shared "are they live?" answer per channel, however many people follow it
            if not profiles.live_shared(profiles.platform_of(url), url).get("live"):
                continue
            if media.is_youtube(url):
                # check YouTube will hand over the stream before making a recording that could only fail
                if media.youtube_paused():
                    continue
                try:
                    resolve(normalize(url))
                except NotLive:
                    continue
            start_session(c, url)
            where = creators.platform_key(url).title()
            creators.log(c["id"], f"Went live on {where}: recording started automatically")
            alerts.notify(f"{c['name']} went live on {where}, recording started.")
            break
    except NotLive:
        pass
    except Exception as e:  # noqa: BLE001
        creators.log(c["id"], f"Auto-record check failed: {str(e)[-150:]}")


def watch_loop():
    """Every two minutes, start recording any creator set to auto-record who has gone live. Covers every user,
    several at a time, so the round stays short however many accounts there are."""
    from concurrent.futures import ThreadPoolExecutor
    time.sleep(20)
    pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="watch")
    while True:
        t0 = time.time()
        try:
            list(pool.map(_watch_user, auth.user_ids()))
        except Exception:  # noqa: BLE001
            pass
        time.sleep(max(15, 120 - (time.time() - t0)))


def _resume_recordings(paused):
    """Recordings that were running when the server stopped, if the stream is still on: carried on as the same
    recording when it stopped not long ago (resume), else recorded again as a new one. A stream that ended while
    the server was away has its waiting clips finished. Tried for a few minutes, since the server may come back a
    little before the network."""
    time.sleep(5)
    for _ in range(6):
        left = []
        for uid, cid, url, job in paused:
            with ctx.as_user(uid):
                try:
                    c = creators.get(cid)
                    if not c or active_for(cid):         # gone, or already being recorded again
                        RESUMING.discard(cid)
                        if job:
                            _stopped_for_good(job)
                        continue
                    resolve(normalize(url))      # raises NotLive if the stream has ended: no empty recording is made
                    if job and resume(job):
                        RESUMING.discard(cid)
                        continue
                    if job:                      # can't be carried on (saved by an older ClipHeist): finished as it is,
                        _stopped_for_good(job)   # and the stream recorded again as a new recording
                        job = None
                    start_session(c, url)
                    creators.log(cid, "Recording picked up again after a server update")
                    RESUMING.discard(cid)
                except NotLive:
                    RESUMING.discard(cid)        # the stream ended while the server was away
                    if job:
                        _stopped_for_good(job)
                except Exception:  # noqa: BLE001 - try again shortly
                    left.append((uid, cid, url, job))
        if not left:
            break
        paused = left
        time.sleep(30)
    for uid, cid, url, job in paused:            # gave up: what was recorded is finished as clips
        RESUMING.discard(cid)
        if job and job.get("resuming"):
            with ctx.as_user(uid):
                _stopped_for_good(job)


def _stopped_for_good(job):
    """A recording that can't be carried on: it shows as stopped, and clips waiting on its buffer are finished."""
    job.update(status="done", stage="Recording paused by a server update")
    job.pop("resuming", None)
    if any(k.get("status") == "queued" for k in job.get("clips", [])):
        recover(job)
    else:
        jobs.save(job)


def start():
    paused = []
    for j in jobs.all_jobs():
        if j.get("attach"):
            ok = False
            with ctx.as_user(j["user"]):
                try:
                    ok = attach(j)
                except Exception:  # noqa: BLE001 - paused and started again the old way, below
                    import traceback
                    traceback.print_exc()
            if ok:
                continue
            capture.forget(j["id"])       # its ffmpeg stops with that
            j.update(status="done", stage="Recording paused by a server update", was_live=True)
            j.pop("attach", None)
            if any(k.get("status") == "queued" for k in j.get("clips", [])):
                j["recover"] = True
        st = j.get("live_state") or {}
        try:                          # when it was last saved (the job file's own time for one saved before this was kept)
            saved_at = float(st.get("saved_at") or 0) or (Path(j["dir"]) / "job.json").stat().st_mtime
        except OSError:
            saved_at = 0
        if j.get("was_live") and j.get("creator") and time.time() - saved_at < RESUME_WITHIN:
            # stopped by the restart a moment ago: carried on as the same recording if the stream is still on
            # (its waiting clips are made then, from the buffer it keeps)
            j.update(status="live", stage="Reconnecting to the stream", resuming=True)
            j.pop("was_live", None)
            jobs.save(j)
            RESUMING.add(j["creator"])
            paused.append((j["user"], j["creator"], j["source"]["url"], j))
            continue
        if j.get("recover"):
            with ctx.as_user(j["user"]):
                recover(j)
        if j.get("was_live") and j.get("creator"):
            paused.append((j["user"], j["creator"], j["source"]["url"], None))
            j.pop("was_live", None)
            jobs.save(j)
    if paused:
        threading.Thread(target=_resume_recordings, args=(paused,), daemon=True).start()
    threading.Thread(target=watch_loop, daemon=True).start()
