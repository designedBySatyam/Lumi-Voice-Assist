param(
    [string]$Name = "LumiAssistant",
    [ValidateSet("console", "ui")]
    [string]$Mode = "console",
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

$pyMajorMinor = (& $pythonCmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
$pyVersion = [version]$pyMajorMinor

if ($Mode -eq "ui" -and $pyVersion -ge [version]"3.14") {
    throw "UI mode requires Python 3.13.x because pywebview/pythonnet is not reliable on Python $pyMajorMinor."
}

if (-not $SkipInstaller) {
    Write-Host "Installing/upgrading PyInstaller..." -ForegroundColor Cyan
    & $pythonCmd -m pip install --upgrade pip pyinstaller
    if ($Mode -eq "ui") {
        Write-Host "Installing pywebview for UI mode..." -ForegroundColor Cyan
        & $pythonCmd -m pip install pywebview
    }
}

if ($Mode -eq "ui") {
    # Validate pywebview import before packaging to avoid silent windowless fallback in a GUI exe.
    & $pythonCmd -c "import webview; print(webview.__version__)" | Out-Null
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

if ($Mode -eq "ui") {
    $pyInstallerArgs += @("--windowed", "--collect-all", "webview")
}

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
    if ($Mode -eq "ui") {
        Write-Host "Run it with Siri-style UI:" -ForegroundColor Yellow
        Write-Host "  .\dist\$Name.exe"
    }
    else {
        Write-Host "Run it with terminal mode:" -ForegroundColor Yellow
        Write-Host "  .\dist\$Name.exe --no-ui"
    }
    Write-Host ""
    if ($pyVersion -ge [version]"3.14") {
        Write-Host "Note: Python 3.14 is best for console mode here. Use Python 3.13 for UI builds."
    }
    exit 0
}

throw "Build finished but EXE was not found at: $exePath"
