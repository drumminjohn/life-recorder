[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Model,
    [string] $DataDir,
    [string] $Url,
    [string] $Whisper,
    [string] $Ffmpeg,
    [string] $OpenSSL,
    [switch] $InstallTask
)

$ErrorActionPreference = "Stop"
$setup = Join-Path $PSScriptRoot "setup.py"
if (-not (Test-Path -LiteralPath $setup -PathType Leaf)) {
    throw "receiver/setup.py was not found"
}

$modelPath = (Resolve-Path -LiteralPath $Model -ErrorAction Stop).Path
if (-not $DataDir) {
    if (-not $env:LOCALAPPDATA) {
        throw "LOCALAPPDATA is not set; pass -DataDir explicitly"
    }
    $DataDir = Join-Path $env:LOCALAPPDATA "LifeRecorder"
}
$dataPath = [System.IO.Path]::GetFullPath($DataDir)

$python = Get-Command py.exe -ErrorAction SilentlyContinue
$pythonArgs = @()
if ($python) {
    $pythonArgs = @("-3")
} else {
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
}
if (-not $python) {
    throw "Python 3.10 or newer was not found as py.exe or python.exe"
}

$receiverArgs = @($setup, "--data-dir", $dataPath, "--model", $modelPath)
if ($Url) { $receiverArgs += @("--url", $Url) }
if ($Whisper) { $receiverArgs += @("--whisper", $Whisper) }
if ($Ffmpeg) { $receiverArgs += @("--ffmpeg", $Ffmpeg) }
if ($OpenSSL) { $receiverArgs += @("--openssl", $OpenSSL) }
if ($InstallTask) { $receiverArgs += "--install-agent" }

& $python.Source @($pythonArgs + $receiverArgs)
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
