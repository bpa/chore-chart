$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Project virtual environment not found at $python."
}

Push-Location $root
try {
    & $python -m pytest
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    & $python -m coverage report --include="src/chore_chart/domain.py,src/chore_chart/scheduler.py" --fail-under=90
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}