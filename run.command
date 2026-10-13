#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print -u2 "缺少 Python 环境，请先运行本目录下的 ./setup.command。"
  if [[ -t 0 ]]; then read -r "reply?按回车退出。"; fi
  exit 1
fi
if ! .venv/bin/python -c 'import serial' >/dev/null 2>&1; then
  print -u2 "缺少 pyserial，请先运行 ./setup.command 安装依赖。"
  exit 1
fi
exec .venv/bin/python -u bridge.py "$@"
