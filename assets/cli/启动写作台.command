#!/bin/sh
cd -- "$(dirname -- "$0")" || exit 1
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin"
exec python3 -B server.py --open "$@"
