@echo off
cd /d "%~dp0"

echo [Start-all] Launching llama-server...
start "Aster - LLM Backend" powershell -NoExit -Command ".\llama-server\llama-server.exe --model E:\Models\gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf --mmproj Aster_Vault/Models/mmproj-F16.gguf --port 8080 -ngl 99 -ngld 99 --ctx-size 32000 --parallel 1 --chat-template-file Aster_Vault/gemma4-multimodal.jinja -ctk q4_0 -ctv q4_0 -fa on --slot-save-path Aster_Vault/kv_cache/"

echo [Start-all] Waiting for llama-server to report healthy (up to 300s)...
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(300); while((Get-Date) -lt $deadline){ try{ if((Invoke-WebRequest -Uri 'http://localhost:8080/health' -TimeoutSec 3 -UseBasicParsing).StatusCode -eq 200){ Write-Host '[Start-all] llama-server healthy - launching Aster.'; exit 0 } }catch{}; Start-Sleep -Seconds 3 }; Write-Host '[Start-all] WARNING: llama-server not healthy after 300s; launching Aster anyway.'; exit 0"

echo [Start-all] Launching Aster (brain + face server + UI)...
start "Aster - Main" cmd /k python main.py

echo [Start-all] All services launching.
echo   LLM backend : http://localhost:8080
echo   Face server : http://localhost:8000
echo   Dashboard   : http://localhost:5173
