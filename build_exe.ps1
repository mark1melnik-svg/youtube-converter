param(
    [string]$ProjectRoot = "E:\youtubeconverter"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$ytDlpPath = python -c "import shutil; print(shutil.which('yt-dlp') or shutil.which('yt-dlp.exe') or '')"
if (-not $ytDlpPath) {
    throw "yt-dlp.exe not found in PATH. Install it first: python -m pip install -U yt-dlp"
}

$ffmpegPath = Join-Path $ProjectRoot "ffmpeg.exe"
if (-not (Test-Path $ffmpegPath)) {
    throw "ffmpeg.exe not found at $ffmpegPath"
}

pyinstaller --noconfirm --clean `
  --name "YouTubeConverter" `
  --windowed `
  --icon "$ProjectRoot\icon.ico" `
  --collect-data sv_ttk `
  --add-binary "$ffmpegPath;." `
  --add-binary "$ytDlpPath;." `
  "$ProjectRoot\yt_converter.py"

$releaseDir = Join-Path $ProjectRoot "release"
if (-not (Test-Path $releaseDir)) {
    New-Item -ItemType Directory -Path $releaseDir | Out-Null
}

$zipPath = Join-Path $releaseDir "YouTubeConverter-win64.zip"
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

Compress-Archive -Path "$ProjectRoot\dist\YouTubeConverter\*" -DestinationPath $zipPath -CompressionLevel Optimal
Write-Host "Build complete:"
Write-Host "  EXE folder: $ProjectRoot\dist\YouTubeConverter"
Write-Host "  ZIP:        $zipPath"
