@echo off
setlocal EnableExtensions
title PolyStrata - Qwen3.6 35B A3B (Aster backend)

set "APPDATA=E:\Models\_appdata"
set "TEMP=E:\Models\_tmp"
set "TMP=E:\Models\_tmp"

cd /d "E:\Models\PolyStrata-main"

echo [PolyStrata] Starting Qwen3.6-35B-A3B on http://127.0.0.1:8080/v1 ...
echo [PolyStrata] 128K context, q8_0 KV, mmproj on GPU.
echo.

"E:\Models\PolyStrata-main\.venv\Scripts\python.exe" "E:\Models\PolyStrata-main\serve\server.py" "--engine" "strata" "--config" "E:\Models\PolyStrata-main\strata-qwen36-qwen3.6-35b-a3b-ud-iq4_xs.json" "--port" "8080"

if errorlevel 1 (
  echo.
  echo [PolyStrata] The server exited with an error.
  echo See: E:\Models\PolyStrata-main\strata-qwen36-qwen3.6-35b-a3b-ud-iq4_xs.log
  pause
)
