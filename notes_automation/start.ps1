# Windows: start UPSC Notes Studio in Docker, on the NVIDIA GPU when available, else on the CPU.
#   powershell -ExecutionPolicy Bypass -File start.ps1        (add "down" to stop)
# Override: $env:UPSC_NOTES_ACCEL = "nvidia" | "cpu";  $env:UPSC_NOTES_MODEL = "qwen2.5:7b"
param([string]$Action = "up")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($Action -eq "down") { docker compose -f docker-compose.yml -f docker-compose.gpu.yml down; exit 0 }

docker info *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "Docker is not running. Start Docker Desktop and retry."; exit 1 }

$accel = $env:UPSC_NOTES_ACCEL
if (-not $accel) {
    $accel = "cpu"
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        docker run --rm --gpus all busybox true *> $null
        if ($LASTEXITCODE -eq 0) { $accel = "nvidia" }
    }
}
if (-not $env:UPSC_NOTES_MODEL) { $env:UPSC_NOTES_MODEL = if ($accel -eq "cpu") { "qwen2.5:3b" } else { "qwen2.5:7b" } }

$files = @("-f", "docker-compose.yml")
if ($accel -eq "nvidia") { Write-Host "Using the NVIDIA GPU."; $files += @("-f", "docker-compose.gpu.yml") }
else { Write-Host "No usable GPU found: running on the CPU." }
Write-Host "Model: $($env:UPSC_NOTES_MODEL) (first start downloads it)"

docker compose @files up -d --build
Write-Host "Waiting for the app..."
for ($i = 0; $i -lt 180; $i++) {
    try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/api/status -TimeoutSec 3 | Out-Null
          Write-Host "Ready: http://127.0.0.1:8765"; exit 0 } catch { Start-Sleep 5 }
}
Write-Host "Still starting. Check: docker compose logs -f"
