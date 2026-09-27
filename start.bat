@echo off
cd /d "%~dp0"
powershell -NoExit -Command ".\llama-server\llama-server.exe --model Aster_Vault/Models/gemma-e4b-q4km.gguf --mmproj Aster_Vault/Models/mmproj-F16.gguf --port 8080 -ngl 99 --ctx-size 128000 --chat-template-file Aster_Vault/gemma4-multimodal.jinja -ctk q4_0 -ctv q4_0 -fa on --slot-save-path Aster_Vault/kv_cache/"
pause