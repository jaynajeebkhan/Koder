$ErrorActionPreference = "Stop"

function Wait-Key {
    Write-Host ""
    Write-Host "Press any key to close this window..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}

function Ensure-Docker {
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        return
    }

    Write-Host "Docker Desktop is not installed yet."

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "winget is missing. Install Docker Desktop manually from:"
        Write-Host "https://www.docker.com/products/docker-desktop/"
        Wait-Key
        exit 1
    }

    Write-Host "Installing Docker Desktop with winget. This can take a while..."
    winget install --id Docker.DockerDesktop -e --accept-package-agreements --accept-source-agreements

    Write-Host ""
    Write-Host "Docker Desktop was installed."
    Write-Host "Restart your PC, open Docker Desktop once, then double-click START_RUVIEW_ONE_CLICK.bat again."
    Wait-Key
    exit 0
}

function Ensure-Docker-Running {
    try {
        docker info *> $null
        return
    } catch {
        Write-Host "Docker is installed but not running yet."
    }

    $dockerDesktop = Join-Path $Env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerDesktop) {
        Write-Host "Starting Docker Desktop..."
        Start-Process $dockerDesktop
    } else {
        Write-Host "Please open Docker Desktop from the Start Menu."
    }

    Write-Host "Waiting for Docker to become ready..."
    for ($i = 1; $i -le 60; $i++) {
        Start-Sleep -Seconds 5
        try {
            docker info *> $null
            Write-Host "Docker is ready."
            return
        } catch {
            Write-Host "Still waiting for Docker... ($i/60)"
        }
    }

    Write-Host "Docker did not become ready in time."
    Write-Host "Open Docker Desktop, finish any WSL/restart prompts, then run this launcher again."
    Wait-Key
    exit 1
}

Ensure-Docker
Ensure-Docker-Running

Write-Host ""
Write-Host "Starting RuView simulated WiFi sensing demo..."
Write-Host "Opening http://localhost:3000 in your browser."
Write-Host ""

Start-Process "http://localhost:3000"

docker run --rm `
    -e CSI_SOURCE=simulated `
    -p 3000:3000 `
    -p 3001:3001 `
    ruvnet/wifi-densepose:latest

