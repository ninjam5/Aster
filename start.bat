@echo off
cd /d "%~dp0"
echo [start] Launching llama-server: Qwen 3.6 35B-A3B (BeeLlama KVarN build, 60k ctx, thinking off, vision on)...
powershell -NoExit -Command "E:\Models\beellama-v0.4.7-bin-win-cuda-12.4-x64\llama-server.exe --model E:\Models\Qwen3.6-35B-A3B-UD-IQ4_XS.gguf --mmproj E:\Models\Qwen3.6-35B-A3B-mmproj-F16.gguf --no-mmproj-offload --chat-template-file E:\Models\qwen36_chat_template.jinja --jinja --host 0.0.0.0 --port 8080 --n-gpu-layers 99 --n-cpu-moe 20 --flash-attn on --cache-type-k kvarn4 --cache-type-v kvarn2 --image-min-tokens 1024 --kv-tail-tokens 1024 --spec-type draft-mtp --spec-draft-n-max 3 --spec-draft-p-min 0.75 --reasoning off --ctx-size 60000 --parallel 1 --threads 8 --batch-size 1024 --ubatch-size 512"
pause
