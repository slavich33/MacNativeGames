#!/bin/zsh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export PYTHONDONTWRITEBYTECODE=1
exec "${MACNATIVEGAMES_PYTHON:-python3}" "${0:A:h}/launch-mac.py" --scene-res 1600x900 "$@"
