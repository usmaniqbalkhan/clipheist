"""Running the ffmpeg that records a live stream, so that it can outlive the app.

A live recording is one ffmpeg per stream, writing the video in pieces and the audio as a row of short raw files
into the recording's buffer folder; the app reads those files (live.py). Where that ffmpeg runs:

  - On a server with the recorder service (PEAKCUT_RECORDER=1, see studio/recorder.py and docker-compose.yml) it
    runs in that service. Updating the app (every push) restarts the app only: the recorder and its ffmpegs keep
    going, and the new app picks each recording up where it was (live.attach).
  - Anywhere else (your own computer, the tests) it runs inside the app, as before; a restart ends it.

The app asks the recorder through small files in DATA/recorder: <job>.json to start, <job>.stop to stop (taking
<job>.json away stops it too); the recorder answers in <job>.state.json, refreshed every couple of seconds while
ffmpeg runs. Keep this exchange as it is: the recorder is not restarted on a deploy, so an older recorder has to
understand a newer app."""
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import config

BEAT_STALE = 20       # seconds without a heartbeat after which the recorder is taken to be gone


def remote() -> bool:
    return config.RECORDER


def folder() -> Path:
    d = config.DATA_DIR / "recorder"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(path: Path, data: dict) -> bool:
    """Put the file in place in one step, so a reader never sees half of it. On Windows the step is refused while
    the other side has the file open: tried again for a moment, then given up (the next write a second or two
    later carries the same news). Never raises: neither the recorder nor a recording may stop over this."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        for _ in range(8):
            try:
                tmp.replace(path)
                return True
            except PermissionError:
                time.sleep(0.05)
    except OSError:
        pass
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    return False


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Local:
    """ffmpeg as a child of the app (no recorder service)."""

    def __init__(self, cmd, cwd: Path):
        from . import adskip, media
        cmd = adskip.prepare(cmd)                 # Twitch read through the ad-skipping relay (studio/adskip.py)
        with open(Path(cwd).parent / "ffmpeg.log", "ab") as errlog:
            self.proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=errlog, creationflags=media.NO_WINDOW)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self):
        """Ask ffmpeg to end; one that has not ended five seconds on is killed. Never waits here: the caller
        (a Stop press, the recording loop) carries on at once."""
        if not self.alive():
            return
        self.proc.terminate()
        threading.Thread(target=self._reap, daemon=True, name="ffmpeg_stop").start()

    def _reap(self):
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


class Remote:
    """ffmpeg in the recorder service."""

    def __init__(self, jid: str, run: str, asked: float):
        self.jid, self.run, self.asked = jid, run, asked

    def state(self) -> dict:
        return _read(folder() / f"{self.jid}.state.json")

    def _asked_for(self) -> bool:
        """Is our request still there? It goes when the recording ends, or when the app drops it (forget)."""
        return _read(folder() / f"{self.jid}.json").get("run") == self.run

    def alive(self) -> bool:
        st = self.state()
        if st.get("run") != self.run:
            # not picked up yet: give the recorder a little while (it looks every second)
            return self._asked_for() and time.time() - self.asked < BEAT_STALE
        return bool(st.get("running")) and time.time() - float(st.get("beat") or 0) < BEAT_STALE

    def stop(self):
        if not self._asked_for():         # already over: a stop written now would only be left lying around
            return
        try:
            (folder() / f"{self.jid}.stop").write_text(self.run, encoding="utf-8")
        except OSError:
            pass


def recorder_up() -> bool:
    """Is the recorder service running? It says so in recorder.ready every couple of seconds."""
    st = _read(folder() / "recorder.ready")
    return time.time() - float(st.get("beat") or st.get("since") or 0) < BEAT_STALE


def start(jid: str, cmd: list, cwd: Path):
    """Start recording with this ffmpeg command: in the recorder when it is there, else as the app's own child
    (the recording then works as ever, it just ends with the app)."""
    if not remote() or not recorder_up():
        return Local(cmd, cwd)
    run = uuid.uuid4().hex[:12]
    (folder() / f"{jid}.stop").unlink(missing_ok=True)
    _write(folder() / f"{jid}.json", {"run": run, "cmd": [str(x) for x in cmd], "cwd": str(cwd), "asked": time.time()})
    return Remote(jid, run, time.time())


def running(jid: str):
    """The recorder's ffmpeg for this recording, if it is still running (after an app restart), else None."""
    if not remote():
        return None
    st = _read(folder() / f"{jid}.state.json")
    if st.get("running") and time.time() - float(st.get("beat") or 0) < BEAT_STALE:
        return Remote(jid, st.get("run", ""), 0.0)
    return None


def forget(jid: str):
    """The recording is over: tidy its files in the recorder folder."""
    for f in [folder() / f"{jid}.json", folder() / f"{jid}.stop", folder() / f"{jid}.state.json", *folder().glob(f"{jid}.*.tmp")]:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass
