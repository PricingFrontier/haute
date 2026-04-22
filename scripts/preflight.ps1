$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

$Quick = $false
$RunBackend = $true
$RunFrontend = $true
$PytestWorkers = if ([string]::IsNullOrWhiteSpace($env:PYTEST_WORKERS)) { "4" } else { $env:PYTEST_WORKERS }

foreach ($Arg in $args) {
    switch ($Arg) {
        "--quick" { $Quick = $true; continue }
        "-Quick" { $Quick = $true; continue }
        "--backend-only" { $RunFrontend = $false; continue }
        "-BackendOnly" { $RunFrontend = $false; continue }
        "--frontend-only" { $RunBackend = $false; continue }
        "-FrontendOnly" { $RunBackend = $false; continue }
        default {
            Write-Host "Unknown argument: $Arg" -ForegroundColor Red
            exit 2
        }
    }
}

if (-not $RunBackend -and -not $RunFrontend) {
    Write-Host "Nothing to run: choose at most one of --backend-only/--frontend-only." -ForegroundColor Red
    exit 2
}

function Get-ProjectEnvironmentPath {
    if ([string]::IsNullOrWhiteSpace($env:UV_PROJECT_ENVIRONMENT)) {
        return Join-Path $RepoRoot ".venv"
    }
    if ([System.IO.Path]::IsPathRooted($env:UV_PROJECT_ENVIRONMENT)) {
        return $env:UV_PROJECT_ENVIRONMENT
    }
    return Join-Path $RepoRoot $env:UV_PROJECT_ENVIRONMENT
}

$ProjectEnvironment = Get-ProjectEnvironmentPath
$PyvenvConfig = Join-Path $ProjectEnvironment "pyvenv.cfg"
if (Test-Path -LiteralPath $PyvenvConfig) {
    $ConfigText = Get-Content -LiteralPath $PyvenvConfig -Raw
    if ($env:OS -eq "Windows_NT" -and $ConfigText -match "(?m)^home\s*=\s*/") {
        $Message = @"
The selected Python environment ($ProjectEnvironment) was created by Linux/WSL.
Remove it or set UV_PROJECT_ENVIRONMENT to a Windows-specific path before running
the Windows preflight script.
"@
        Write-Host $Message -ForegroundColor Red
        exit 2
    }
}

$script:Failed = $false

function Write-Step {
    param([string] $Message)
    Write-Host "> $Message" -ForegroundColor Yellow
}

function Write-Pass {
    param([string] $Message)
    Write-Host "OK $Message" -ForegroundColor Green
}

function Write-Fail {
    param([string] $Message)
    Write-Host "FAIL $Message" -ForegroundColor Red
    $script:Failed = $true
}

function Invoke-Check {
    param(
        [string] $Name,
        [scriptblock] $Command,
        [string] $FailureMessage
    )

    Write-Step $Name
    $global:LASTEXITCODE = 0
    try {
        & $Command
        $Succeeded = $?
        $ExitCode = $global:LASTEXITCODE
    }
    catch {
        Write-Host $_.Exception.Message -ForegroundColor Red
        $Succeeded = $false
        $ExitCode = 1
    }

    if ($Succeeded -and $ExitCode -eq 0) {
        Write-Pass $Name
    }
    else {
        Write-Fail $FailureMessage
    }
}

if ($RunBackend) {
    Invoke-Check "Ruff lint (Python)" {
        & uv run ruff check .
    } "Ruff lint - run 'uv run ruff check . --fix' to auto-fix"

    Invoke-Check "Ruff format check (Python)" {
        & uv run ruff format --check .
    } "Ruff format - run 'uv run ruff format .' to fix"

    Invoke-Check "Mypy type check (Python)" {
        & uv run mypy src/haute/
    } "Mypy type errors"

    if (-not $Quick) {
        Invoke-Check "Python tests with coverage" {
            & uv run pytest tests/ -q -n $PytestWorkers --cov=src/haute --cov-branch --cov-report=term-missing --cov-fail-under=85
        } "Python tests"

        Invoke-Check "Python package build" {
            & uv build
        } "Python package build"
    }
    else {
        Write-Host "Skipping backend tests/build (--quick mode)" -ForegroundColor Yellow
    }
}

if ($RunFrontend) {
    Invoke-Check "TypeScript type check" {
        Push-Location frontend
        try {
            & npm run typecheck
        }
        finally {
            Pop-Location
        }
    } "TypeScript errors"

    Invoke-Check "ESLint (frontend)" {
        Push-Location frontend
        try {
            & npm run lint
        }
        finally {
            Pop-Location
        }
    } "ESLint errors - run 'cd frontend && npm run lint -- --fix' to auto-fix"

    if (-not $Quick) {
        Invoke-Check "Frontend build" {
            Push-Location frontend
            try {
                & npm run build
            }
            finally {
                Pop-Location
            }
        } "Frontend build failed"

        Invoke-Check "Frontend bundle budget" {
            Push-Location frontend
            try {
                & npm run check:bundle
            }
            finally {
                Pop-Location
            }
        } "Frontend bundle budget"

        Invoke-Check "Frontend tests" {
            Push-Location frontend
            try {
                & npm test
            }
            finally {
                Pop-Location
            }
        } "Frontend tests"
    }
    else {
        Write-Host "Skipping frontend build/tests (--quick mode)" -ForegroundColor Yellow
    }
}

Write-Host ""
if ($script:Failed) {
    Write-Host "Some checks failed. Fix before push." -ForegroundColor Red
    exit 1
}

Write-Host "All checks passed. Safe to push." -ForegroundColor Green
