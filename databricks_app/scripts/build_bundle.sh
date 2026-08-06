#!/usr/bin/env bash
# Assemble the Databricks app source directory: fresh wheel + bundle files.
# Run from the repository root. Output: databricks_app/ ready to sync/deploy.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
bundle_dir="$repo_root/databricks_app"

HAUTE_BUILD_FRONTEND="${HAUTE_BUILD_FRONTEND:-1}" uv build --wheel

rm -f "$bundle_dir"/haute-*.whl
wheel="$(ls "$repo_root"/dist/haute-*.whl | sort | tail -n1)"
cp "$wheel" "$bundle_dir/"

# Databricks rejects any file over 10 MB; fail here rather than at deploy.
size=$(stat -f%z "$bundle_dir/$(basename "$wheel")" 2>/dev/null || stat -c%s "$bundle_dir/$(basename "$wheel")")
if [ "$size" -gt $((10 * 1024 * 1024)) ]; then
  echo "ERROR: wheel exceeds Databricks Apps 10 MB per-file limit ($size bytes)" >&2
  exit 1
fi

# Databricks decides whether to reinstall dependencies by hashing the TEXT
# of requirements.txt — a changed wheel with an unchanged filename is
# invisible to it and the old install is silently kept. Stamping the wheel
# digest into the file makes reinstalls track actual wheel content.
digest=$(shasum -a 256 "$bundle_dir/$(basename "$wheel")" | cut -d' ' -f1)
cat > "$bundle_dir/requirements.txt" <<EOF
# Vendored wheel: copied into this directory by scripts/build_bundle.sh.
# Databricks installs requirements with the app directory as cwd.
# wheel-sha256: $digest
./$(basename "$wheel")[databricks]
EOF

echo "Bundle ready: $bundle_dir ($(basename "$wheel"), $size bytes, sha256 $digest)"
