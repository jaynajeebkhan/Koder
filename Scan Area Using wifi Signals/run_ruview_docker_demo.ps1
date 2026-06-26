$ErrorActionPreference = "Stop"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Docker is not installed or not in PATH."
    Write-Host "Install Docker Desktop, restart PowerShell/Trae, then run this script again."
    exit 1
}

Write-Host "Starting RuView simulated Docker demo..."
Write-Host "Open http://localhost:3000 after the container starts."

docker run --rm `
    -e CSI_SOURCE=simulated `
    -p 3000:3000 `
    -p 3001:3001 `
    ruvnet/wifi-densepose:latest

