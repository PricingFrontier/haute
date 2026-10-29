#!/usr/bin/env bash
# Deploy the app bundle to a Databricks workspace.
#
# Usage: deploy.sh <profile> <workspace-dir> <app-name>
#   e.g. deploy.sh my-profile /Users/me@example.com/haute-app haute-app
#
# Why this exists rather than a bare `databricks sync && databricks apps deploy`:
# `databricks sync` HONOURS .gitignore, and every build output is gitignored — the
# vendored wheel (a 2 MB build artifact has no business in git history), the lock
# (requirements.txt) and the wheel's digest (wheel.sha256). They are therefore
# invisible to sync, and a full sync may even remove the copies a previous deploy
# imported, so each is imported explicitly AFTER the sync; without them the platform
# would keep whatever wheel and dependencies the workspace already had.
set -euo pipefail

usage="usage: deploy.sh <profile> <workspace-dir> <app-name>"
profile="${1:?$usage}"
target="${2:?$usage}"
app_name="${3:?$usage}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
bundle_dir="$repo_root/databricks_app"

bash "$bundle_dir/scripts/build_bundle.sh"

databricks sync "$bundle_dir" "$target" --full --profile "$profile"

# --format RAW keeps the bytes intact (the default would treat a file as a notebook
# source, and the lock's hashes and the wheel's digest must match byte for byte);
# --overwrite because the names are stable across builds.
for built in "$bundle_dir"/haute-*.whl "$bundle_dir/requirements.txt" "$bundle_dir/wheel.sha256"; do
  databricks workspace import \
    --file "$built" \
    --format RAW \
    --overwrite \
    "$target/$(basename "$built")" \
    --profile "$profile"
done

databricks apps deploy "$app_name" \
  --source-code-path "/Workspace$target" \
  --profile "$profile"
