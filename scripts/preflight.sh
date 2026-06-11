#!/usr/bin/env bash
# Run the same quality gates locally that CI enforces.
#
# Usage:
#   ./scripts/preflight.sh                  # full backend + frontend check
#   ./scripts/preflight.sh --quick          # lint + types only
#   ./scripts/preflight.sh --backend-only   # backend gates only
#   ./scripts/preflight.sh --frontend-only  # frontend gates only
#   ./scripts/preflight.sh --perf           # also run opt-in Python perf tests
#
# Exit code 0 = safe to push. Non-zero = fix before pushing.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
cd "$REPO_ROOT"

UNAME_S="$(uname -s)"
case "$UNAME_S" in
  MINGW* | MSYS* | CYGWIN*)
    cat >&2 <<'EOF'
Windows preflight is supported through PowerShell:
  powershell -ExecutionPolicy Bypass -File .\scripts\preflight.ps1

Use scripts/preflight.sh on Linux and macOS.
EOF
    exit 2
    ;;
esac

IS_WSL=false
if [[ -r /proc/version ]] && grep -qiE "(microsoft|wsl)" /proc/version; then
  IS_WSL=true
fi

if [[ "$IS_WSL" == true ]]; then
  case "$REPO_ROOT" in
    /mnt/* | /media/*)
      # Avoid overwriting the Windows .venv when a WSL shell runs from a mounted
      # Windows checkout.
      export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-.venv-wsl}"
      ;;
  esac
fi

PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-.venv}"
if [[ -f "$PROJECT_ENVIRONMENT/pyvenv.cfg" ]] &&
  grep -Eq '^[Hh]ome[[:space:]]*=[[:space:]]*[A-Za-z]:\\' "$PROJECT_ENVIRONMENT/pyvenv.cfg"; then
  cat >&2 <<EOF
The selected Python environment ($PROJECT_ENVIRONMENT) was created on Windows.
Use the Windows preflight script, remove that environment, or set UV_PROJECT_ENVIRONMENT
to an OS-specific path before running this Bash script.
EOF
  exit 2
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

QUICK=false
RUN_BACKEND=true
RUN_FRONTEND=true
RUN_PERF=false
PYTEST_WORKERS="${PYTEST_WORKERS:-4}"

for arg in "$@"; do
  case "$arg" in
    --quick)
      QUICK=true
      ;;
    --backend-only)
      RUN_FRONTEND=false
      ;;
    --frontend-only)
      RUN_BACKEND=false
      ;;
    --perf)
      RUN_PERF=true
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

if [[ "$RUN_BACKEND" == false && "$RUN_FRONTEND" == false ]]; then
  echo "Nothing to run: choose at most one of --backend-only/--frontend-only." >&2
  exit 2
fi

FAIL=0

step() {
  printf "${YELLOW}> %s${NC}\n" "$1"
}

pass() {
  printf "${GREEN}OK %s${NC}\n" "$1"
}

fail() {
  printf "${RED}FAIL %s${NC}\n" "$1"
  FAIL=1
}

run_with_timeout() {
  local seconds="$1"
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "${seconds}s" "$@"
    return
  fi

  uv run python -c '
import subprocess
import sys

timeout = float(sys.argv[1])
cmd = sys.argv[2:]
try:
    raise SystemExit(subprocess.run(cmd, timeout=timeout).returncode)
except subprocess.TimeoutExpired:
    print("Command timed out after %gs: %s" % (timeout, " ".join(cmd)), file=sys.stderr)
    raise SystemExit(124)
' "$seconds" "$@"
}

if [[ "$RUN_BACKEND" == true ]]; then
  step "Ruff lint (Python)"
  if uv run ruff check .; then
    pass "Ruff lint"
  else
    fail "Ruff lint - run 'uv run ruff check . --fix' to auto-fix"
  fi

  step "Ruff format check (Python)"
  if uv run ruff format --check .; then
    pass "Ruff format"
  else
    fail "Ruff format - run 'uv run ruff format .' to fix"
  fi

  step "Mypy type check (Python)"
  if uv run mypy src/haute/; then
    pass "Mypy"
  else
    fail "Mypy type errors"
  fi

  if [[ "$QUICK" == false ]]; then
    step "Python test collection"
    if run_with_timeout 300 uv run pytest tests/ --collect-only -q; then
      pass "Python test collection"
    else
      fail "Python test collection"
    fi

    step "Python tests with coverage gates"
    PYTHON_COVERAGE_JSON=".cache/coverage/backend.json"
    mkdir -p "$(dirname "$PYTHON_COVERAGE_JSON")"
    rm -f "$PYTHON_COVERAGE_JSON"
    if uv run pytest tests/ -q -n "$PYTEST_WORKERS" --timeout=60 --timeout-method=signal --cov=src/haute --cov-branch --cov-report=term-missing --cov-report="json:${PYTHON_COVERAGE_JSON}" --cov-fail-under=90 &&
      uv run python scripts/check_critical_coverage.py --coverage-json "$PYTHON_COVERAGE_JSON"; then
      pass "Python tests (global + critical coverage gates)"
    else
      fail "Python tests or critical coverage"
    fi

    if [[ "$RUN_PERF" == true ]]; then
      step "Python perf tests"
      if uv run python scripts/run_perf_suite.py --output-dir .cache/perf; then
        pass "Python perf tests"
      else
        fail "Python perf tests"
      fi
    fi

    step "Python package build"
    if HAUTE_BUILD_FRONTEND=1 uv build; then
      pass "Python package build"
    else
      fail "Python package build"
    fi
  else
    printf "${YELLOW}Skipping backend tests/build (--quick mode)${NC}\n"
  fi
fi

if [[ "$RUN_FRONTEND" == true ]]; then
  step "TypeScript type check"
  if (cd frontend && npm run typecheck); then
    pass "TypeScript"
  else
    fail "TypeScript errors"
  fi

  step "ESLint (frontend)"
  if (cd frontend && npm run lint); then
    pass "ESLint"
  else
    fail "ESLint errors - run 'cd frontend && npm run lint -- --fix' to auto-fix"
  fi

  if [[ "$QUICK" == false ]]; then
    step "Frontend build"
    if (cd frontend && npm run build); then
      pass "Frontend build"
    else
      fail "Frontend build failed"
    fi

    step "Frontend bundle budget"
    if (cd frontend && npm run check:bundle); then
      pass "Frontend bundle budget"
    else
      fail "Frontend bundle budget"
    fi

    step "Frontend PR benchmark gate"
    if (cd frontend && npm run test:benchmark:pr); then
      pass "Frontend PR benchmark gate"
    else
      fail "Frontend PR benchmark gate"
    fi

    step "Frontend tests with coverage"
    if (cd frontend && npm run test:coverage); then
      pass "Frontend tests (coverage thresholds)"
    else
      fail "Frontend tests"
    fi
  else
    printf "${YELLOW}Skipping frontend build/tests (--quick mode)${NC}\n"
  fi
fi

echo ""
if [[ "$FAIL" -eq 0 ]]; then
  printf "${GREEN}All checks passed. Safe to push.${NC}\n"
else
  printf "${RED}Some checks failed. Fix before push.${NC}\n"
  exit 1
fi
