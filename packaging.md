# Packaging & Auto-Setup Notes

Parking-lot doc capturing a design discussion from 2026-07-03. Nothing here is
implemented yet — revisit and firm up the open questions before writing code.

## Goal

Onboarding should be able to auto-detect the user's hardware and wire up an
appropriate model automatically (the "Basic user" onboarding tier), instead of
requiring a manual model choice and manual `start.bat` editing.

## Current state (as of 2026-07-03)

- `start.bat` is static — it hardcodes the model path, mmproj path, and every
  `llama-server` flag in one literal command line.
- `self_config.yaml`'s `runtime.llm_model` is descriptive only. Nothing reads
  it to actually select a model file at boot.
- `llama-server/` is already a vendored, self-contained binary folder
  (~1.2GB) — includes CUDA DLLs (`cublas64_12`, `cublasLt64_12`, `cudart64_12`)
  *plus* a full set of CPU-microarchitecture-specific `ggml-cpu-*.dll`
  backends (haswell, skylakex, sapphirerapids, etc.). It may already be
  hardware-adaptive without needing separate per-tier binary downloads — not
  yet confirmed whether it degrades cleanly to CPU-only with no NVIDIA driver
  present, or hard-fails looking for CUDA.
- Existing hardware-detection precedent in the codebase: `main.py`'s `/gpu`
  Telegram command tries `nvidia-smi` first, falling back to
  `torch.cuda.get_device_properties(0)`.
- `huggingface_hub` and `psutil` are already installed dependencies — no new
  packages needed for either detection or download.
- One grounded VRAM data point: E4B + 128k context + Q4_0 KV cache uses
  ~5-6GB VRAM (`checklist_test.md`), observed on a 3080 12GB.
- Three model variants already tested successfully: Gemma E2B QAT, Gemma E4B
  QAT (current default), Gemma 12B QAT. Real VRAM footprints for E2B and 12B
  haven't been recorded anywhere yet — only E4B's number is grounded above.

## Proposed direction

One launcher — either a smarter `start.bat`, or (leaning this way) a small
Python script, since `torch.cuda`/`psutil`/`huggingface_hub`/health-check
polling are all nicer to write in Python than batch — that does, in order:

1. Detect hardware: VRAM tier (`nvidia-smi` → `torch.cuda` fallback), system
   RAM (`psutil`), free disk space at the models directory.
2. Pick a tier → model file, plus matching context-window / KV-cache flags
   (context size is a second VRAM lever independent of model size — a 12B
   model at 128k context is a very different footprint than E2B at 128k).
3. Ensure the chosen `.gguf` (+ mmproj) is present in `Aster_Vault/Models/`,
   downloading via `huggingface_hub.hf_hub_download` if missing (resumable,
   cached — no new dependency).
4. Launch `llama-server.exe` as a subprocess with the picked flags.
5. Poll `llama-server`'s `/health` endpoint until it reports ready.
6. Launch `main.py` (the brain).
7. Launch the dashboard (`aster-face` and/or `Aster-UI`).

This one script *is* both "auto-detect and pick a model" and "one app that
boots everything" — they turned out to be the same piece of work, not two
separate features.

## Open questions (resolve before implementing)

- Real VRAM thresholds — need actual `nvidia-smi` numbers from Mohamed's own
  E2B/E4B/12B testing, not estimates.
- Do the E2B and 12B QAT GGUFs ship their own `mmproj`, or share the one
  already in use for E4B? Needs checking against the actual Hugging Face repos.
- Does the vendored `llama-server` binary actually fall back to CPU-only
  cleanly with no NVIDIA GPU/driver, or hard-fail looking for CUDA? Determines
  whether "no GPU" is a real supported tier or needs a separate build.
- Batch/PowerShell script vs. a Python launcher — leaning Python, not decided.
- Which dashboard does the launcher open — `aster-face` (reactor face),
  `Aster-UI` (full dashboard), both, or a user choice?
- Where does the "Basic user" vs. "Advanced user" choice live —
  `first_run_setup.py` seems like the natural home, not yet decided.

## Bigger idea, deliberately out of scope for now

A full one-click *install* (not just boot) on a machine with nothing set up
would additionally require bundling or auto-installing:

- The Python backend's full dependency graph (335 packages currently
  installed — CUDA torch, SpeechBrain, MediaPipe, ONNX Runtime, ChromaDB,
  dlib-based `face_recognition`).
- Tesseract OCR, which is a separate non-pip system installer today.

Aster's Tauri apps (`aster-face`, `Aster-UI`) already produce native
installers (`npm run tauri build`), and Tauri's `externalBin` sidecar
mechanism could eventually bundle `llama-server` directly into that installer
— this is the same shape LM Studio / Ollama / Jan / GPT4All already ship
(native shell + bundled/managed llama.cpp-family server + models downloaded
separately post-install). But freezing the Python ML stack with
PyInstaller/Nuitka is a much larger, separate effort (dlib and MediaPipe in
particular are known pain points for that kind of bundling) — not the
near-term target. Revisit only if "one-click install from zero" becomes an
actual goal, separately from the launcher described above.
