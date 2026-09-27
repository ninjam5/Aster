---
name: aster-build-and-env
description: Load this when setting up the Aster development environment, installing dependencies, recreating the system on a new machine, fixing import/DLL/install errors, or answering "what do I need installed", "pip install fails", "torch/CUDA setup", "dlib won't build", "Tesseract not found", "do I need Rust". Covers Python, CUDA, llama-server, system deps, and the two frontend toolchains, with known traps.
---

# Aster Build and Environment

Target platform: **Windows 11 + NVIDIA GPU (CUDA)**. Python **3.12** (verified from
`__pycache__` tags and the live interpreter, 2026-07-05).

**When NOT to use this skill:** starting/operating an already-built system →
`aster-run-and-operate`; a running component misbehaving →
`aster-debugging-playbook`.

## Rule zero: no virtual environments

All packages install into the **global** interpreter:
```powershell
python -m pip install <package>
```
Enforced by `.github/instructions/python-global-packages.instructions.md`. If any
guide says "create a venv", adapt it to global installs. Never `pip install` bare —
always `python -m pip` so the interpreter binding is explicit.

## There is no requirements.txt (verified 2026-07-05)

Dependency discovery is ImportError-driven; the working install has ~335 packages
(`packaging.md`). The dependency families below are **inferred from imports** —
install on demand when an import fails:

| Family | Packages | Notes |
|---|---|---|
| Torch stack | `torch` (CUDA build) | Install the cu12x wheel, NOT the CPU default: see pytorch.org for the current `--index-url` command |
| STT | `faster-whisper` (pulls CTranslate2), `nvidia-cublas-cu12`, `nvidia-cuda-nvrtc-cu12` | The nvidia pip packages supply DLLs that main.py path-patches (see trap 1) |
| TTS | `kokoro`, `soundfile` | |
| Voice transport | `livekit`, `livekit-agents`, `livekit-plugins-silero`, `openwakeword` | |
| Speaker/emotion | `speechbrain`, `transformers`, `hsemotion-onnx`, `onnxruntime`, `sounddevice`, `webrtcvad` | hsemotion-onnx, NOT the torch `hsemotion` package (broken on timm 1.x — see `aster-failure-archaeology`) |
| Vision/GUI | `opencv-python`, `mss`, `pytesseract`, `face_recognition` (pulls dlib), `mediapipe`, `ultralytics`, `huggingface_hub`, `pyautogui`, `pygetwindow` | |
| Memory/RAG | `chromadb`, `wikipedia`, `firecrawl-py` | `wikipedia` is required or research always falls through to Firecrawl |
| Bridges | `pyTelegramBotAPI` (imports as `telebot`), `discord.py`, `spotipy`, `requests` | |
| System | `psutil`, `pycaw`, `winotify`, `PyYAML`, `numpy` | |
| Google | `google-auth-oauthlib`, `google-api-python-client` | |
| Server | `fastapi`, `uvicorn` | face_server |
| Tests | `pytest` | |

## System dependencies (not pip)

1. **NVIDIA driver + CUDA-capable GPU** — the reference machine is an RTX 3080
   12 GB. `nvidia-smi` must work.
2. **Tesseract OCR** — installer from the UB-Mannheim build or official; expected at
   `C:\Program Files\Tesseract-OCR\tesseract.exe`, override with the
   `TESSERACT_CMD` env var. Boot prints `[Tesseract] Using executable: ...`.
3. **ffmpeg on PATH** — Telegram voice-note transcoding.
4. **llama-server binary** — vendored in the repo: `llama-server/` (~1.2 GB with
   CUDA DLLs + per-CPU-microarch ggml backends) and an alternate
   `llama-server-turboquant/` bundle. No build step needed. (`build_cuda.py` is
   LEGACY — it rebuilt llama-cpp-python, which the project no longer uses.)
5. **Model files**: `Aster_Vault/Models/gemma-e4b-q4km.gguf` (~5.41 GB) +
   `Aster_Vault/Models/mmproj-F16.gguf` (~0.99 GB), from Hugging Face (Gemma-4-E4B
   GGUF repos). NOTE: `start_new.bat`/`Start-all.bat` reference an additional QAT
   model at `E:\Models\` outside the repo — see `aster-run-and-operate`.
6. **Node.js + npm** — both frontend apps.
7. **Rust toolchain + MSVC "Desktop development with C++"** — ONLY for
   `npm run tauri build`/`tauri dev`. Browser dev (`npm run dev` in Aster-UI)
   needs neither.

## Per-install configuration

```powershell
python first_run_setup.py    # interactive wizard -> writes secrets.yaml + identity fields of self_config.yaml
# or copy the templates by hand:
Copy-Item secrets.example.yaml secrets.yaml
Copy-Item self_config.example.yaml self_config.yaml
```
Every credential is optional — blank disables that integration gracefully. The
wizard is NOT auto-invoked by main.py (would hang non-interactive contexts like
pytest). Never commit either real yaml.

First live boot also downloads on demand: openWakeWord models, Hugging Face caches
(emotion/speaker models), hsemotion weights (loader pre-fetches to work around the
package's broken downloader), OmniParser v2 (on first smart_click fallback).

## Smoke-test ladder (run in order; 1–3 need NO llama-server)

```powershell
# 1. Offline unit tests (expect 118 pass / 1 known fail as of 2026-07-05):
python -m pytest tests/ -q
# 2. Standalone mock harnesses (19/19, 23/23, 22/22):
python emotion-test.py; python ambient-audio-test.py; python face-emotion-test.py
# 3. Core import (loads face models, ~1 min; prints "68 tools registered"):
python -c "import core.brain as b; print(len(b.ADMIN_TOOLS))"
# 4. Engine up:
.\start.bat        # separate window; wait for load
Invoke-RestMethod http://localhost:8080/health
# 5. Full boot:
python main.py
# 6. Frontends:
cd Aster-UI; npm install; npm test; npm run dev
cd ..\aster-face; npm install; npm run tauri build
```

## Known traps

| Trap | Symptom | Fix |
|---|---|---|
| CUDA DLLs not found by CTranslate2 | `Could not locate cublas64_*.dll` loading Whisper | Already handled: `main.py:14-19` and `local_stt.py` prepend `site-packages/nvidia/{cublas,cuda_nvrtc}/bin` to PATH. If it still fails, `python -m pip install nvidia-cublas-cu12 nvidia-cuda-nvrtc-cu12` |
| CPU torch wheel installed | `torch.cuda.is_available()` → False | Reinstall the CUDA wheel from pytorch.org index |
| dlib build failure (`face_recognition`) | pip tries to compile dlib, needs CMake+MSVC | Install CMake + MSVC build tools, or use a prebuilt dlib wheel for cp312 |
| `hsemotion` (torch) instead of `hsemotion-onnx` | `conv_s2d` attribute error on timm 1.x | Uninstall it; the project deliberately uses the ONNX variant |
| Tesseract missing | vision tools fail at import/use | Install Tesseract or set `TESSERACT_CMD` |
| Port 8080 taken | llama-server won't bind / brain gets garbage | Only llama-server owns 8080; Spotify OAuth redirect defaults to 8081 for exactly this reason |
| Port 8000 taken | face_server fails, UIs get no data | Free the port; one main.py at a time |
| Accidentally created a venv | Imports fine in shell, main.py (or a daemon) can't find packages | Delete the venv, reinstall globally (rule zero) |
| SpeechBrain load errors | lazy-module / torch.load failures | Handled in-code by `_prepare_speechbrain_imports()` (`tools/emotion_recognition.py`) — don't "fix" by downgrading torch; see `aster-failure-archaeology` |

## Provenance and maintenance

Authored 2026-07-05. Package list is inferred-from-imports (no requirements.txt
exists — verified same day).

- requirements file appeared? `Get-ChildItem requirements*.txt`
- Python version: `python --version` (3.12 expected)
- DLL patch still present: `Select-String -Path main.py -Pattern "nvidia"`
- Vendored server bundles: `Get-ChildItem llama-server*, -Directory`
- Test-ladder baseline: `python -m pytest tests/ -q`
