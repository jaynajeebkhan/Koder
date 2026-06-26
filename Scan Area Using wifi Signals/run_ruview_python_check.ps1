$ErrorActionPreference = "Stop"

$venvPython = Join-Path (Get-Location) "work\ruview-venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    python -m venv work\ruview-venv
}

& $venvPython -m pip install --pre "ruview[client]"
& $venvPython -c "import ruview; print('RuView import OK')"

