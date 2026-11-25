"""The recorder service keeps a live recording going when the app restarts. A real ffmpeg records a test signal
(no internet needed) through the recorder; a fresh look from "another app process" finds it still running, its
video pieces and audio file keep growing, and stopping it works."""
import os, subprocess, sys, tempfile, time
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-rec-")
os.environ.update(DATABASE_URL="", PEAKCUT_RECORDER="1")
sys.path.insert(0, ".")
from pathlib import Path   # noqa: E402
from studio import capture, config   # noqa: E402
fails = []


def check(n, got, want=True):
    print(("PASS " if got == want else "FAIL ") + n + ("" if got == want else f"   got {got!r}")); got == want or fails.append(n)


def wait_for(fn, secs):
    end = time.time() + secs
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.3)
    return False


rec = subprocess.Popen([sys.executable, "-m", "studio.recorder"], env={**os.environ}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
try:
    check("the recorder starts", wait_for(lambda: (capture.folder() / "recorder.ready").exists(), 15))
    buf = config.DATA_DIR / "users" / "u1" / "rec" / "buffer"
    buf.mkdir(parents=True)
    # a "stream": a short test video played back at real speed and copied, as a live stream is
    src = config.DATA_DIR / "src.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-f", "lavfi",
                    "-i", "sine=frequency=440:sample_rate=44100", "-t", "40", "-c:v", "libx264", "-preset", "ultrafast",
                    "-g", "10", "-c:a", "aac", str(src)], check=True)
    cmd = ["ffmpeg", "-v", "error", "-re", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-f", "segment",
           "-segment_time", "2", "-reset_timestamps", "1", "-segment_format", "mpegts", "-segment_list", "p01.csv",
           "-segment_list_type", "csv", "p01_%05d.ts",
           "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "segment", "-segment_time", "2",
           "-segment_format", "s16le", "-segment_format_options", "flush_packets=1", "p01_a%05d.pcm"]
    cap = capture.start("job1", cmd, buf)
    check("the recording is running in the recorder", wait_for(lambda: capture.running("job1") is not None, 15))
    heard = lambda: sum(f.stat().st_size for f in buf.glob("p01_a*.pcm"))   # noqa: E731
    check("the audio is being written as it comes", wait_for(lambda: heard() > 16000 * 2, 15))

    # the app restarts: a new process knows nothing but the files, and finds the recording still going
    again = capture.running("job1")
    size1 = heard()
    time.sleep(3)
    check("after the 'restart' it is still found running", again is not None and again.alive())
    check("the audio kept growing meanwhile", heard() > size1 + 16000 * 2)
    check("in short files, not one that grows for ever", len(list(buf.glob("p01_a*.pcm"))) >= 2)
    check("video pieces are being cut", wait_for(lambda: len(list(buf.glob("p01_*.ts"))) >= 2, 15))

    again.stop()
    check("stopping it works", wait_for(lambda: capture.running("job1") is None, 20))
    check("its state says it ended", capture._read(capture.folder() / "job1.state.json").get("running"), False)

    bad = capture.start("job2", ["notepad.exe"], buf)
    check("anything other than ffmpeg is refused", wait_for(lambda: capture._read(capture.folder() / "job2.state.json").get("note") == "refused", 10))
    capture.forget("job1"); capture.forget("job2")
    check("a finished recording's files are tidied", not any(capture.folder().glob("job1*")))

    cap3 = capture.start("job3", cmd, buf)                      # the app drops a recording altogether (forget):
    check("a third recording starts", wait_for(lambda: capture.running("job3") is not None, 15))
    capture.forget("job3")                                      # that stops its ffmpeg and leaves nothing behind
    check("forgetting a running recording stops it", wait_for(lambda: not cap3.alive() and not any(capture.folder().glob("job3*")), 20))
    check("the app knows the recorder is up", capture.recorder_up())
finally:
    rec.terminate()
    try:
        rec.wait(15)
    except subprocess.TimeoutExpired:
        rec.kill()

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
