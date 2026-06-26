$ErrorActionPreference = "Stop"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Git is not installed or not in PATH."
    Write-Host "Install it with: winget install --id Git.Git -e"
    exit 1
}

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Write-Host "Rust/Cargo is not installed or not in PATH."
    Write-Host "Install it with: winget install --id Rustlang.Rustup -e"
    exit 1
}

$repo = Join-Path (Get-Location) "work\RuView"

if (-not (Test-Path $repo)) {
    git clone https://github.com/ruvnet/RuView.git $repo
}

Set-Location (Join-Path $repo "v2")
cargo build --release
.\target\release\sensing-server.exe --source simulate --http-port 3000 --ws-port 3001

