"""ClipHeist speech-to-text on a Modal GPU.

Runs studio/asr_worker.py (same /decode protocol the main server already speaks) on a GPU, scaling to zero when
idle, so you pay only for the seconds it is working.

Deploy (from backend/):
    ~/.venvs/modal/bin/modal secret create clipheist-asr PEAKCUT_ASR_TOKEN=<long random secret>
    ~/.venvs/modal/bin/modal deploy deploy/modal_asr.py
Then in backend/.env:
    PEAKCUT_ASR_URL=<the https://...modal.run address printed by deploy>
    PEAKCUT_ASR_TOKEN=<same secret>
If the GPU is away or slow to answer, the main server transcribes on its own CPU instead (studio/transcribe.py).
"""
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent.parent
WHEEL = "sherpa-onnx==2.0.1+cuda12.cudnn9.onnxruntime1.28.2"

image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04", add_python="3.12")
    .apt_install("libasound2")
    .pip_install("numpy==2.2.6", "fastapi==0.162.5", "uvicorn==0.54.0", "anyio")
    .run_commands(f"pip install '{WHEEL}' -f https://k2-fsa.github.io/sherpa/onnx/cuda.html")
    .env({"PEAKCUT_ASR_PROVIDER": "cuda", "PEAKCUT_DATA": "/data", "PEAKCUT_MODELS": "/models",
          "PEAKCUT_ASR_BATCH": "16", "PEAKCUT_ASR_MODEL": "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-fp16"})
    .add_local_dir(HERE / "studio", "/root/studio", copy=True,
                   ignore=["__pycache__", "fonts", "*.pyc"])
    .run_commands("cd /root && python -m studio.fetch_model")
)

app = modal.App("clipheist-asr", image=image)


@app.function(gpu=["L4", "A10G", "T4", "L40S"], secrets=[modal.Secret.from_name("clipheist-asr")], scaledown_window=120,
              timeout=600, max_containers=4)
@modal.concurrent(max_inputs=6)
@modal.asgi_app()
def web():
    import sys
    sys.path.insert(0, "/root")
    from studio import asr_worker, transcribe
    transcribe._recognizer()          # load the model when the container starts, not on the first request
    return asr_worker.app
