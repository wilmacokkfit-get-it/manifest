#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
python -m pip install \
  --upgrade \
  --target ".pythonlibs/lib/python$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')/site-packages" \
  -r requirements.txt
# These databases are disposable test fixtures, not application data. Avoid
# slow disk fsyncs in the container while retaining every durability assertion.
# Never recursively delete this root: a failed DB shutdown must keep its files.
if [[ -d /dev/shm && -w /dev/shm ]]; then
  merge_tmp="$(mktemp -d /dev/shm/manifest-merge.XXXXXX)"
  export TMPDIR="$merge_tmp"
  trap 'rmdir "$merge_tmp" 2>/dev/null || true' EXIT
fi
python -m unittest discover -s tests -v
