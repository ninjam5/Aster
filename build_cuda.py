"""One-shot script to set CUDA_PATH system-wide and rebuild llama-cpp-python with CUDA."""
import subprocess
import os
import sys

CUDA_PATH = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1"

# 1. Set CUDA_PATH as a permanent Machine-level env var via PowerShell
print("[1/3] Setting CUDA_PATH as system environment variable...")
ps_cmd = f'[System.Environment]::SetEnvironmentVariable("CUDA_PATH", "{CUDA_PATH}", "Machine")'
result = subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, text=True)
print(f"  Set CUDA_PATH: rc={result.returncode}")

ps_cmd2 = f'[System.Environment]::SetEnvironmentVariable("CUDA_PATH_V12_1", "{CUDA_PATH}", "Machine")'
result2 = subprocess.run(["powershell", "-Command", ps_cmd2], capture_output=True, text=True)
print(f"  Set CUDA_PATH_V12_1: rc={result2.returncode}")

# 2. Also add CUDA bin to system PATH
ps_cmd3 = (
    f'$p = [System.Environment]::GetEnvironmentVariable("Path", "Machine"); '
    f'$cudaBin = "{CUDA_PATH}\\bin"; '
    f'if ($p -notlike "*$cudaBin*") {{ '
    f'[System.Environment]::SetEnvironmentVariable("Path", "$p;$cudaBin", "Machine") }}'
)
result3 = subprocess.run(["powershell", "-Command", ps_cmd3], capture_output=True, text=True)
print(f"  Add CUDA bin to PATH: rc={result3.returncode}")

# 3. Set in current process too
os.environ["CUDA_PATH"] = CUDA_PATH
os.environ["CUDA_PATH_V12_1"] = CUDA_PATH
os.environ["PATH"] = os.path.join(CUDA_PATH, "bin") + ";" + os.environ.get("PATH", "")

# 4. Run pip install
print("\n[2/3] Building llama-cpp-python with CUDA...")
os.environ["CMAKE_ARGS"] = "-DGGML_CUDA=ON"
os.environ["FORCE_CMAKE"] = "1"

result4 = subprocess.run(
    [sys.executable, "-m", "pip", "install", "llama-cpp-python", "--force-reinstall", "--no-cache-dir"],
    capture_output=True, text=True, timeout=600
)

# Print last 30 lines of output
output = result4.stdout + result4.stderr
lines = output.strip().split("\n")
for line in lines[-30:]:
    print(line)

if result4.returncode == 0:
    print("\n[3/3] Verifying GPU offload...")
    result5 = subprocess.run(
        [sys.executable, "-c", "import llama_cpp; import llama_cpp.llama_cpp as ll; print('GPU offload:', ll.llama_supports_gpu_offload())"],
        capture_output=True, text=True
    )
    print(result5.stdout.strip())
    if result5.stderr:
        print(result5.stderr.strip())
else:
    print(f"\nBuild failed with exit code {result4.returncode}")
