$ErrorActionPreference = 'Stop'

$toolsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$zipPath = Join-Path $toolsDir 'ffmpeg-release-essentials.zip'
$extractDir = Join-Path $toolsDir 'ffmpeg'
$downloadUrl = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'

Write-Host 'Downloading ffmpeg...'
$ProgressPreference = 'SilentlyContinue'
Invoke-WebRequest -Uri $downloadUrl -OutFile $zipPath

Write-Host 'Extracting ffmpeg...'
if (Test-Path $extractDir) {
    Remove-Item -Recurse -Force $extractDir
}
Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

Write-Host 'Cleaning up archive...'
Remove-Item -Force $zipPath

Write-Host 'ffmpeg is ready under tools\ffmpeg'