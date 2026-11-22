$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

Write-Host ''
Write-Host 'UNIVERSAL CLIPPER - EDITOR STUDIO' -ForegroundColor Green
Write-Host ''

if (-not (Test-Path '.env') -and (Test-Path '.env.example')) {
    Copy-Item '.env.example' '.env'
}

$pythonSource = $null
$pythonVersionArg = $null
$py = Get-Command py -ErrorAction SilentlyContinue
$python = Get-Command python -ErrorAction SilentlyContinue

# Prefer the newest supported interpreter. Python 3.14 is included for current
# Windows installs; package compatibility is checked by pip during installation.
if ($py) {
    foreach ($minor in @('3.14', '3.13', '3.12', '3.11')) {
        & $py.Source ("-" + $minor) --version *> $null
        if ($LASTEXITCODE -eq 0) {
            $pythonSource = $py.Source
            $pythonVersionArg = "-" + $minor
            break
        }
    }
}

if (-not $pythonSource -and $python) {
    $versionCheck = & $python.Source --version 2>&1
    if ($versionCheck -match 'Python 3\.(11|12|13|14)\.') {
        $pythonSource = $python.Source
    }
}

if (-not $pythonSource) {
    throw 'Python 3.11, 3.12, 3.13 or 3.14 is required. Install Python and try again.'
}

if ($pythonVersionArg) {
    $detectedVersion = & $pythonSource $pythonVersionArg --version 2>&1
} else {
    $detectedVersion = & $pythonSource --version 2>&1
}
Write-Host ('Using ' + $detectedVersion) -ForegroundColor Cyan

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host 'Creating Python virtual environment...' -ForegroundColor Cyan
    if ($pythonVersionArg) {
        & $pythonSource $pythonVersionArg -m venv .venv
    } else {
        & $pythonSource -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
}

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
Write-Host 'Installing application dependencies...' -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Pip upgrade failed.' }
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Review the pip error above.' }

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
$ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
if (-not $ffmpeg -or -not $ffprobe) {
    Write-Host 'WARNING: FFmpeg or FFprobe was not found on PATH.' -ForegroundColor Yellow
    Write-Host 'Install a full FFmpeg build, restart PowerShell, and launch again.' -ForegroundColor Yellow
} else {
    Write-Host ('FFmpeg detected: ' + $ffmpeg.Source) -ForegroundColor Green
    Write-Host ('FFprobe detected: ' + $ffprobe.Source) -ForegroundColor Green
}

Write-Host ''
Write-Host 'Starting Universal Clipper at http://127.0.0.1:8765' -ForegroundColor Green
& $venvPython -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
