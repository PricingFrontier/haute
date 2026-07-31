#!/usr/bin/env bash
# Deploy the app bundle to a Databricks workspace.
#
# Usage: deploy.sh <profile> <workspace-dir>
#   e.g. deploy.sh haute-spike /Users/me@example.com/haute-spike
#
# Why this exists rather than a bare `databricks sync && databricks apps deploy`:
# `databricks sync` HONOURS .gitignore, and the vendored wheel is gitignored (a
# 2 MB build artifact has no business in git history). The wheel is therefore
# invisible to sync, and every deploy would silently keep whatever wheel the
# workspace already had — source files updating while the package froze. The
# explicit import below is the fix; keep it whenever the wheel stays gitignored.
set -euo pipefail

profile="${1:?usage: deploy.sh <profile> <workspace-dir>}"
target="${2:?usage: deploy.sh <profile> <workspace-dir>}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
bundle_dir="$repo_root/databricks_app"

bash "$bundle_dir/scripts/build_bundle.sh"
wheel="$(ls "$bundle_dir"/haute-*.whl | tail -n1)"

databricks sync "$bundle_dir" "$target" --full --profile "$profile"

# --format RAW keeps the bytes intact (the default would treat it as a notebook
# source file); --overwrite because the filename is stable across builds.
databricks workspace import \
  --file "$wheel" \
  --format RAW \
  --overwrite \
  "$target/$(basename "$wheel")" \
  --profile "$profile"

databricks apps deploy haute-spike \
  --source-code-path "/Workspace$target" \
  --profile "$profile"
