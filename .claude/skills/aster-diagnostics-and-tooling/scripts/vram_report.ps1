# Aster VRAM / RAM report - read-only. ASCII-only on purpose (PS 5.1 misparses
# UTF-8 punctuation in unsigned scripts without a BOM).
# Usage:  powershell -File .claude/skills/aster-diagnostics-and-tooling/scripts/vram_report.ps1
# Shows board VRAM totals, per-process VRAM, and python.exe RAM working sets
# (the LiveKit-devmode duplicate-process check).

$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($null -eq $smi) {
    Write-Output "nvidia-smi not found on PATH - no NVIDIA driver visible. (torch.cuda would also be unavailable.)"
} else {
    Write-Output "=== Board totals ==="
    nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv
    Write-Output ""
    Write-Output "=== Per-process VRAM ==="
    nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
}

Write-Output ""
Write-Output "=== python.exe RAM working sets (devmode duplicate check) ==="
$py = Get-Process python -ErrorAction SilentlyContinue
if ($null -eq $py) {
    Write-Output "no python.exe running"
} else {
    $py | Format-Table Id, @{L='WorkingSetGB';E={[math]::Round($_.WorkingSet64/1GB,2)}} -AutoSize
    $big = @($py | Where-Object { $_.WorkingSet64 -gt 2GB })
    if ($big.Count -ge 2) {
        Write-Output "WARNING: $($big.Count) python processes over 2 GB RAM - possible LiveKit devmode worker fork (see aster-vram-discipline)."
    }
}
