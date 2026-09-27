#!/usr/bin/env python3
"""Aster stack health check — offline-safe, read-only, always exits 0.

Probes llama-server (:8080), the face server (:8000), Tesseract, model files,
and the ChromaDB directory, then prints a PASS/FAIL table. Reports state; it
does not fail the shell. Run from the repo root:

    python .claude/skills/aster-diagnostics-and-tooling/scripts/health_check.py
"""
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
rows = []


def check(name, ok, detail=""):
    rows.append((name, "PASS" if ok else "FAIL", detail))


def probe(url, timeout=5):
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


# 1. llama-server
status, body = probe("http://localhost:8080/health")
check("llama-server /health (:8080)", status == 200,
      "healthy" if status == 200 else f"DOWN — {body[:80]}")

status, body = probe("http://localhost:8080/props")
if status == 200:
    try:
        props = json.loads(body)
        model_path = (props.get("default_generation_settings", {}) or {}).get("model") \
            or props.get("model_path") or "unknown"
        n_ctx = (props.get("default_generation_settings", {}) or {}).get("n_ctx") \
            or props.get("n_ctx") or "?"
        check("llama-server /props", True, f"model={model_path} n_ctx={n_ctx}")
    except Exception:
        check("llama-server /props", True, "reachable (unparsed)")
else:
    check("llama-server /props", False, "DOWN")

# 2. face server (any /api probe; 404 on a bad route still proves the server is up)
status, body = probe("http://localhost:8000/api/token")
check("face_server (:8000)", status is not None and status < 500,
      f"HTTP {status}" if status else f"DOWN — {body[:80]}")

# 3. Tesseract
tess = os.environ.get("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
check("Tesseract executable", os.path.isfile(tess), tess)

# 4. Model files (the ones start.bat references inside the repo)
for rel in (os.path.join("Aster_Vault", "Models", "gemma-e4b-q4km.gguf"),
            os.path.join("Aster_Vault", "Models", "mmproj-F16.gguf")):
    p = os.path.join(REPO, rel)
    ok = os.path.isfile(p)
    size = f"{os.path.getsize(p) / 1e9:.2f} GB" if ok else "missing"
    check(rel, ok, size)

# 5. ChromaDB dir
chroma = os.path.join(REPO, "Aster_Vault", "chroma_db")
check("ChromaDB dir", os.path.isdir(chroma), chroma)

# Print table
w = max(len(r[0]) for r in rows) + 2
print(f"\n{'CHECK'.ljust(w)}{'RESULT':8}DETAIL")
print("-" * (w + 40))
for name, res, detail in rows:
    print(f"{name.ljust(w)}{res:8}{detail}")
fails = sum(1 for r in rows if r[1] == "FAIL")
print(f"\n{len(rows) - fails}/{len(rows)} checks passed "
      f"({'all services up' if fails == 0 else 'services down are reported above — may be expected if Aster is not running'})")
sys.exit(0)
