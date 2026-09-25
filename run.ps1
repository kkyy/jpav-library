param([string]$DataDir = "")
$ErrorActionPreference = "Stop"
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    Write-Error 'Please install first: python -m venv .venv; .\.venv\Scripts\python -m pip install -e ".[dev]"'
    exit 1
}
if ($DataDir) {
    & $taskPython -m av_library --data-dir $DataDir
} else {
    & $taskPython -m av_library
}
exit $LASTEXITCODE
