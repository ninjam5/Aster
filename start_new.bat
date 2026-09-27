@echo off
cd /d "%~dp0"
powershell -NoExit -Command ".\llama-server\llama-server.exe --model E:\Models\gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf --mmproj Aster_Vault/Models/mmproj-F16.gguf --port 8080 -ngl 99 -ngld 99 --ctx-size 128000 --parallel 1  --chat-template-file Aster_Vault/gemma4-multimodal.jinja -ctk q4_0 -ctv q4_0 -fa on --slot-save-path Aster_Vault/kv_cache/"
pause