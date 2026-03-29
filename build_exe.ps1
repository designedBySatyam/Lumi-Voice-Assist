param(
    [string]$Name = "LumiAssistant",
    [switch]$SkipInstaller,
    [switch]$NoClean
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Set-Location -LiteralPath $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$pythonCmd = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "python" }

Write-Host "Using Python:" -ForegroundColor Cyan
& $pythonCmd --version

if (-not $SkipInstaller) {
    Write-Host "Installing/upgrading PyInstaller..." -ForegroundColor Cyan
    & $pythonCmd -m pip install --upgrade pip pyinstaller
}

$pyInstallerArgs = @(
    "--noconfirm"
    "--onefile"
    "--name", $Name
    "--collect-all", "pyttsx3"
    "--collect-all", "speech_recognition"
    "--collect-all", "pyautogui"
    "--collect-all", "pyscreeze"
    "--hidden-import", "pyttsx3.drivers.sapi5"
)

if (-not $NoClean) {
    $pyInstallerArgs += "--clean"
}

$pyInstallerArgs += "assistant.py"

Write-Host "Building EXE with PyInstaller..." -ForegroundColor Cyan
& $pythonCmd -m PyInstaller @pyInstallerArgs

$distDir = Join-Path $PSScriptRoot "dist"
$exePath = Join-Path $distDir "$Name.exe"

if (Test-Path -LiteralPath ".env") {
    Copy-Item -LiteralPath ".env" -Destination (Join-Path $distDir ".env") -Force
}
if (Test-Path -LiteralPath ".env.example") {
    Copy-Item -LiteralPath ".env.example" -Destination (Join-Path $distDir ".env.example") -Force
}

if (Test-Path -LiteralPath $exePath) {
    Write-Host ""
    Write-Host "Build complete:" -ForegroundColor Green
    Write-Host "  $exePath"
    Write-Host ""
    Write-Host "Run it with terminal mode:" -ForegroundColor Yellow
    Write-Host "  .\dist\$Name.exe --no-ui"
    Write-Host ""
    Write-Host "Note: On Python 3.14, UI mode may fail because pywebview/pythonnet is not fully supported."
    exit 0
}

throw "Build finished but EXE was not found at: $exePath"
