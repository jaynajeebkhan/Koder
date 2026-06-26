# RuView Local Setup for Trae IDE

This is the safe beginner path for testing RuView on your own PC.

Important reality check: RuView does not turn normal WiFi into a normal video camera. On a plain Windows laptop it can only do coarse WiFi/RSSI sensing if the native server is built. Full pose/vitals needs real CSI data from ESP32-S3 boards or supported CSI hardware. Use it only in spaces where everyone knows and consents.

## What I Found on This PC

- Python is installed: `Python 3.14.4`
- Node/npm are installed: `node v25.9.0`, `npm 11.12.1`
- `winget` is installed.
- `git`, `docker`, and `rustc` were not available in PATH.
- PyPI install failed because this shell could not resolve `files.pythonhosted.org`.
- npm package lookup also failed.

Because of that, I could create the local venv, but I could not finish the RuView package install here.

## Best Path: Docker Demo

No-Docker Python launcher:

```powershell
.\outputs\START_RUVIEW_DIRECT_PYTHON.bat
```

Fastest one-click launcher:

```powershell
.\outputs\START_RUVIEW_ONE_CLICK.bat
```

Optional one-command prerequisite install:

```powershell
.\outputs\install_ruview_prereqs.ps1
```

Restart the PC after that, then open Docker Desktop once.

Install Docker Desktop first, then open PowerShell in this folder and run:

```powershell
.\outputs\run_ruview_docker_demo.ps1
```

That runs the repo's recommended simulated demo:

```powershell
docker run --rm -e CSI_SOURCE=simulated -p 3000:3000 -p 3001:3001 ruvnet/wifi-densepose:latest
```

Then open:

```text
http://localhost:3000
```

Test endpoints:

```powershell
Invoke-RestMethod http://localhost:3000/health
Invoke-RestMethod http://localhost:3000/api/v1/sensing/latest
Invoke-RestMethod http://localhost:3000/api/v1/vital-signs
Invoke-RestMethod http://localhost:3000/api/v1/pose/current
```

## Trae IDE Source Build Path

Install Git and Rust first:

```powershell
winget install --id Git.Git -e
winget install --id Rustlang.Rustup -e
```

Restart PowerShell/Trae after installing, then run:

```powershell
git clone https://github.com/ruvnet/RuView.git work\RuView
cd work\RuView\v2
cargo build --release
.\target\release\sensing-server.exe --source simulate --http-port 3000 --ws-port 3001
```

In Trae IDE, open:

```text
work\RuView
```

## Python Package Path

Once PyPI works from your terminal:

```powershell
python -m venv work\ruview-venv
.\work\ruview-venv\Scripts\python.exe -m pip install --pre "ruview[client]"
.\work\ruview-venv\Scripts\python.exe -c "import ruview; print('RuView import OK')"
```

The Python wheel is for DSP/API usage. It is not the full Three.js web UI.

## Real Hardware Path

For real WiFi sensing instead of simulation:

- Use ESP32-S3 boards for CSI. The original ESP32 and ESP32-C3 are not supported by the repo docs.
- Start with 2 or more nodes for better spatial resolution.
- Use only your own room/lab and get consent from people nearby.
- Run the native server or Docker with `CSI_SOURCE=esp32` and UDP port `5005`.

Docker example:

```powershell
docker run --rm -e CSI_SOURCE=esp32 -p 3000:3000 -p 3001:3001 -p 5005:5005/udp ruvnet/wifi-densepose:latest
```

## What Not To Expect

- A normal camera feed from WiFi.
- Reliable through-wall human images from a regular laptop alone.
- Medical-grade breathing/heart measurements without validated hardware and calibration.
- Good pose estimation without CSI-capable hardware and model/data setup.
