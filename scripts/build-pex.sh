#!/usr/bin/env bash
# Build dist/starsystem (a PEX file): a single-file executable that runs on any machine with
# Python >= 3.11, with dependencies pinned to the versions in uv.lock.
set -euo pipefail
cd "$(dirname "$0")/.."

out="dist/starsystem"
reqs="$(mktemp)"
trap 'rm -f "$reqs"' EXIT

uv build --wheel --out-dir dist/wheel --clear
uv export --frozen --no-dev --no-emit-project --no-header --format requirements-txt -o "$reqs" -q
# uv exports hashes, which puts pip into --require-hashes mode, so the wheel needs one too.
wheel="$(ls dist/wheel/starsystem-*.whl)"
echo "$PWD/$wheel --hash=sha256:$(shasum -a 256 "$wheel" | cut -d' ' -f1)" >> "$reqs"

uvx --from "pex>=2.107,<3" pex \
  --requirement "$reqs" \
  --console-script starsystem \
  --interpreter-constraint "CPython>=3.11" \
  --python-shebang "/usr/bin/env python3" \
  --venv prepend \
  --output-file "$out"

echo "Built $out"
