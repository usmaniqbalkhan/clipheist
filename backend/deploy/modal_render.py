"""ClipHeist clip maker on Modal: each clip downloads its own stretch of the video and renders on its own machine,
all clips at once, so a whole video takes about as long as one clip.

Deploy (from backend/):
    ~/.venvs/modal/bin/modal deploy deploy/modal_render.py
Then in backend/.env:
    PEAKCUT_RENDER_URL=<the https://...modal.run address printed by deploy>
(The token is the same clipheist-asr secret.) If Modal fails, the main server makes the clip itself.
"""
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent.parent

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg", "ca-certificates", "curl", "unzip")
    .run_commands("curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y")
    .pip_install("numpy==2.2.6", "fastapi==0.162.5", "uvicorn==0.54.0", "soundfile==0.14.0", "httpx==0.28.1",
                 "yt-dlp==2026.10.21", "pillow==11.3.0")
    .env({"PEAKCUT_DATA": "/data"})
    .add_local_dir(HERE / "studio", "/root/studio", copy=True, ignore=["__pycache__", "*.pyc"])
)

app = modal.App("clipheist-render", image=image)


@app.function(cpu=8.0, memory=4096, secrets=[modal.Secret.from_name("clipheist-asr")], scaledown_window=90,
              timeout=900, max_containers=40)
@modal.concurrent(max_inputs=1)
@modal.asgi_app()
def web():
    import sys
    sys.path.insert(0, "/root")
    from studio import render_worker
    return render_worker.app
