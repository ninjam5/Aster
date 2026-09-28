@echo off
cd /d "%~dp0"

echo [Start-all] Launching llama-server (Qwen 3.6 35B-A3B, 60k ctx, thinking off, vision on)...
start "Aster - LLM Backend" powershell -NoExit -Command "E:\Models\beellama-v0.4.7-bin-win-cuda-12.4-x64\llama-server.exe --model E:\Models\Qwen3.6-35B-A3B-UD-IQ4_XS.gguf --mmproj E:\Models\Qwen3.6-35B-A3B-mmproj-F16.gguf --no-mmproj-offload --chat-template-file E:\Models\qwen36_chat_template.jinja --jinja --host 0.0.0.0 --port 8080 --n-gpu-layers 99 --n-cpu-moe 21 --flash-attn on --cache-type-k kvarn4 --cache-type-v kvarn2 --image-min-tokens 1024 --kv-tail-tokens 1024 --spec-type draft-mtp --spec-draft-n-max 3 --spec-draft-p-min 0.75 --reasoning off --ctx-size 60000 --parallel 1 --threads 8 --batch-size 1024 --ubatch-size 512"

echo [Start-all] Waiting for llama-server to report healthy (up to 300s)...
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(300); while((Get-Date) -lt $deadline){ try{ if((Invoke-WebRequest -Uri 'http://localhost:8080/health' -TimeoutSec 3 -UseBasicParsing).StatusCode -eq 200){ Write-Host '[Start-all] llama-server healthy - launching Aster.'; exit 0 } }catch{}; Start-Sleep -Seconds 3 }; Write-Host '[Start-all] WARNING: llama-server not healthy after 300s; launching Aster anyway.'; exit 0"

echo [Start-all] Launching Aster (brain + face server + UI)...
start "Aster - Main" cmd /k python main.py

echo [Start-all] All services launching.
echo   LLM backend : http://localhost:8080
echo   Face server : http://localhost:8000
echo   Dashboard   : http://localhost:5173
