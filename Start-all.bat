@echo off
cd /d "%~dp0"

echo [Start-all] Launching PolyStrata (Qwen 3.6 35B-A3B, 128K ctx, q8_0 KV, mmproj on GPU)...
start "Aster - LLM Backend" "%~dp0qwen3.6_polystrata_start.bat"

echo [Start-all] Waiting for PolyStrata to report healthy (up to 300s)...
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(300); while((Get-Date) -lt $deadline){ try{ if((Invoke-WebRequest -Uri 'http://localhost:8080/health' -TimeoutSec 3 -UseBasicParsing).StatusCode -eq 200){ Write-Host '[Start-all] PolyStrata healthy - launching Aster.'; exit 0 } }catch{}; Start-Sleep -Seconds 3 }; Write-Host '[Start-all] WARNING: PolyStrata not healthy after 300s; launching Aster anyway.'; exit 0"

echo [Start-all] Launching Aster (brain + face server + UI)...
start "Aster - Main" cmd /k python main.py

echo [Start-all] All services launching.
echo   LLM backend : http://localhost:8080/v1  (PolyStrata)
echo   Face server : http://localhost:8000
echo   Dashboard   : http://localhost:5173
