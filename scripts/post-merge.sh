#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
python -m pip install \
  --upgrade \
  --target ".pythonlibs/lib/python$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')/site-packages" \
  -r requirements.txt
python -m unittest discover -s tests -v