#!/bin/zsh
set -eu
cd -- "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  print -u2 "请先运行 ./setup.command。"
  exit 1
fi
exec .venv/bin/python -u github_bridge.py "$@"
