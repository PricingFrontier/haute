#!/usr/bin/env bash
# Assemble the Databricks app source directory: a freshly built wheel, its digest and
# the lock of the runtime it needs. Run from anywhere; output: databricks_app/ ready to
# sync/deploy.
#
# The lock is haute's dependencies with the databricks extra, resolved by uv for the
# app's Python (HAUTE_APP_PYTHON, default 3.11) and platform (HAUTE_APP_PLATFORM,
# default x86_64-manylinux_2_28), every distribution pinned by version and hash. It is
# written as requirements.txt, which the platform installs at deploy and app.py
# installs again at boot without resolving anything; the wheel then installs without
# its dependencies, once app.py has checked it against wheel.sha256. The wheel,
# requirements.txt and wheel.sha256 are build outputs, never committed.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
bundle_dir="$repo_root/databricks_app"
app_python="${HAUTE_APP_PYTHON:-3.11}"
app_platform="${HAUTE_APP_PLATFORM:-x86_64-manylinux_2_28}"

# A directory of the build's own: a shared dist/ can hold wheels of other versions,
# and choosing one of them by name bundled a stale release.
build_dir="$(mktemp -d)"
trap 'rm -rf "$build_dir"' EXIT

HAUTE_BUILD_FRONTEND="${HAUTE_BUILD_FRONTEND:-1}" uv build --wheel --out-dir "$build_dir" "$repo_root"
shopt -s nullglob
built=("$build_dir"/haute-*.whl)
shopt -u nullglob
if [ "${#built[@]}" -ne 1 ]; then
  echo "ERROR: expected the build to make exactly one haute wheel, found ${#built[@]}" >&2
  exit 1
fi
wheel_name="$(basename "${built[0]}")"

rm -f "$bundle_dir"/haute-*.whl
cp "${built[0]}" "$bundle_dir/"
wheel="$bundle_dir/$wheel_name"

# Databricks rejects any file over 10 MB; fail here rather than at deploy.
size=$(stat -f%z "$wheel" 2>/dev/null || stat -c%s "$wheel")
if [ "$size" -gt $((10 * 1024 * 1024)) ]; then
  echo "ERROR: wheel exceeds Databricks Apps 10 MB per-file limit ($size bytes)" >&2
  exit 1
fi

if command -v sha256sum >/dev/null 2>&1; then
  digest="$(sha256sum "$wheel" | cut -d' ' -f1)"
else
  digest="$(shasum -a 256 "$wheel" | cut -d' ' -f1)"
fi
printf '%s  %s\n' "$digest" "$wheel_name" > "$bundle_dir/wheel.sha256"

compiled="$build_dir/requirements.txt"
uv pip compile "$repo_root/pyproject.toml" --extra databricks \
  --python-version "$app_python" --python-platform "$app_platform" \
  --generate-hashes --emit-index-url \
  --custom-compile-command "databricks_app/scripts/build_bundle.sh" \
  --quiet -o "$compiled"
{
  echo "# The hosted app's runtime: haute's dependencies with the databricks extra,"
  echo "# resolved by $(uv --version) for CPython $app_python on $app_platform."
  echo "# Built for $wheel_name; app.py installs it with --no-deps --require-hashes."
  # The index lines become provenance comments, their credentials removed: the hashes
  # pin every file, so the platform's pip fetches them from its own index, and a
  # credential must never ship in the bundle.
  sed -E \
    -e '/^--(extra-)?index-url /s#://[^/@[:space:]]*@#://#' \
    -e 's/^(--(extra-)?index-url )/# resolved from \1/' \
    "$compiled"
} > "$bundle_dir/requirements.txt"

echo "Bundle ready: $bundle_dir ($wheel_name, $size bytes, sha256 $digest)"
