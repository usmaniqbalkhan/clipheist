"""The recorder service: runs the ffmpegs that record live streams, apart from the app.

Started on its own (python -m studio.recorder, the "recorder" service in docker-compose.yml) and left running
when the app is updated, so a push never cuts a recording. It only runs ffmpeg commands the app writes into
DATA/recorder/<job>.json (see studio/capture.py), in that recording's own buffer folder, and reports on each in
<job>.state.json every couple of seconds. It knows nothing else about ClipHeist, so it rarely needs updating."""
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path

from . import adskip, capture, config

POLL = 1.0
BEAT_EVERY = 2.0


def _allowed(cmd, cwd: Path) -> bool:
    """Only ffmpeg, only inside the data folder."""
    try:
        inside = Path(cwd).resolve().is_relative_to(config.DATA_DIR.resolve())
    except (OSError, ValueError):
        inside = False
    return bool(cmd) and Path(str(cmd[0])).name in ("ffmpeg", "ffmpeg.exe") and inside and Path(cwd).is_dir()


def _gone(f: Path):
    try:
        f.unlink(missing_ok=True)
    except OSError:            # held open by the other side for a moment (Windows): gone on a later round
        pass


def _round(d: Path, procs: dict, ending: dict, last_beat: float, born: float) -> float:
    """One look at the folder: start what is asked for, stop what is to stop, report what ended, say we are alive."""
    now = time.time()
    for f in sorted(d.glob("*.json")):
        if f.name.endswith(".state.json"):
            continue
        jid, want = f.stem, capture._read(f)
        run, cmd, cwd = want.get("run"), want.get("cmd") or [], Path(want.get("cwd") or ".")
        have = procs.get(jid)
        if not run or (have and have[0] == run):
            continue
        if have:                                   # a new run for the same recording: the old one goes
            have[1].terminate()
        if not _allowed(cmd, cwd):
            capture._write(d / f"{jid}.state.json", {"run": run, "running": False, "beat": now, "note": "refused"})
            _gone(f)
            procs.pop(jid, None)
            continue
        cmd = adskip.prepare(cmd)                  # Twitch read through the ad-skipping relay, here next to ffmpeg
        with open(cwd.parent / "ffmpeg.log", "ab") as errlog:
            p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=errlog)
        procs[jid] = (run, p)
        ending.pop(jid, None)
        capture._write(d / f"{jid}.state.json", {"run": run, "running": True, "pid": p.pid, "beat": now, "started": now})
    for jid, (run, p) in list(procs.items()):
        stop = d / f"{jid}.stop"
        forgotten = not (d / f"{jid}.json").exists()       # the app is done with this recording altogether
        if stop.exists() or forgotten or jid in ending:
            if p.poll() is None:
                if jid not in ending:                      # asked to end once; killed if it has not ten seconds on.
                    p.terminate()                          # Never waited for here: the other recordings' heartbeats go on
                    ending[jid] = now
                elif now - ending[jid] > 10:
                    p.kill()
            _gone(stop)
        if p.poll() is not None:                   # finished: the stream ended or it was stopped
            if forgotten:
                _gone(d / f"{jid}.state.json")
            else:
                capture._write(d / f"{jid}.state.json", {"run": run, "running": False, "beat": now, "code": p.returncode})
            _gone(d / f"{jid}.json")
            procs.pop(jid, None)
            ending.pop(jid, None)
    for f in d.glob("*.stop"):                     # a stop for a recording that is already over
        if f.stem not in procs:
            _gone(f)
    if now - last_beat >= BEAT_EVERY:
        last_beat = now
        capture._write(d / "recorder.ready", {"pid": os.getpid(), "since": born, "beat": now})
        for jid, (run, p) in procs.items():
            if p.poll() is None:
                capture._write(d / f"{jid}.state.json", {"run": run, "running": True, "pid": p.pid, "beat": now})
    return last_beat


def main():
    d = capture.folder()
    procs = {}           # job -> (run, Popen)
    last_beat = 0.0
    born = time.time()
    # Recordings asked for before this recorder started: their ffmpeg is gone. Say so, so the app moves on.
    # (One asked for while it was starting is kept and started below.)
    for f in d.glob("*.json"):
        if f.name.endswith(".state.json") or float(capture._read(f).get("asked") or 0) >= born - 1:
            continue
        st = capture._read(d / f"{f.stem}.state.json")
        capture._write(d / f"{f.stem}.state.json", {"run": st.get("run"), "running": False, "beat": time.time(), "note": "recorder restarted"})
        _gone(f)
    stopping = []

    def shutdown(*_):
        stopping.append(1)
    signal.signal(signal.SIGTERM, shutdown)
    capture._write(d / "recorder.ready", {"pid": os.getpid(), "since": born})
    print("[recorder] ready", flush=True)
    ending = {}          # job -> when its ffmpeg was told to end
    while not stopping:
        try:
            last_beat = _round(d, procs, ending, last_beat, born)
        except Exception:  # noqa: BLE001 - one bad file must never take every recording down with it
            traceback.print_exc()
        time.sleep(POLL)
    # the recorder itself is being stopped (rare: only when it is updated): stop its ffmpegs cleanly, and say it
    # is gone, so the app records by itself until it is back
    _gone(d / "recorder.ready")
    for jid, (run, p) in procs.items():
        if p.poll() is None:
            p.terminate()
        capture._write(d / f"{jid}.state.json", {"run": run, "running": False, "beat": time.time(), "note": "recorder stopped"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
