$ErrorActionPreference = "Stop"

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "winget is not installed. Install App Installer from Microsoft Store first."
    exit 1
}

Write-Host "Installing Git..."
winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements

Write-Host "Installing Rust toolchain..."
winget install --id Rustlang.Rustup -e --accept-package-agreements --accept-source-agreements

Write-Host "Installing Docker Desktop..."
winget install --id Docker.DockerDesktop -e --accept-package-agreements --accept-source-agreements

Write-Host ""
Write-Host "Done. Restart your PC, open Docker Desktop once, then run:"
Write-Host ".\outputs\run_ruview_docker_demo.ps1"

