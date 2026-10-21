#!/bin/bash
# Compatible with the macOS system Bash 3.2. Never depend on the current directory.
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)" || exit 1
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
if [ -x "$ROOT/whack-a-claude" ]; then
  "$ROOT/whack-a-claude" "$@"
  result=$?
else
  PY=""
  for candidate in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PY="$candidate"
      break
    fi
  done
  if [ -z "$PY" ]; then
    printf '%s\n' '需要 Python 3.10+：https://www.python.org/downloads/' '也可以下载 GitHub Releases 中对应芯片的 macOS 独立版本。'
    result=1
  else
    "$PY" "$ROOT/whack_a_claude.py" "$@"
    result=$?
  fi
fi
if [ "$result" -ne 0 ]; then
  printf '\n程序退出（%s），按回车关闭窗口。' "$result"
  read -r _
fi
exit "$result"
