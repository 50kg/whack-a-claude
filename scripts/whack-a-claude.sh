#!/bin/bash
# Legacy entry point. On Windows use Start.bat; Git Bash users can still call this.
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)" || exit 1
exec /bin/bash "$ROOT/Start.command" "$@"
