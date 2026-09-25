$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Create .venv and install project dependencies first.'
}
Push-Location $projectRoot
try {
    & $taskPython -m pip install -e '.[package]'
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed.' }
    & $taskPython -m PyInstaller --noconfirm --clean --windowed --onedir --name JPAVLibrary --paths src src\av_library\__main__.py
    if ($LASTEXITCODE -ne 0) { throw 'Windows packaging failed.' }
    Write-Output 'Built: dist\JPAVLibrary\JPAVLibrary.exe'
} finally {
    Pop-Location
}
