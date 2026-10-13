#!/bin/zsh
set -eu
cd -- "${0:A:h}"

if ! command -v uv >/dev/null 2>&1; then
  print -u2 "请先安装 uv：https://docs.astral.sh/uv/getting-started/installation/"
  print -u2 "已安装 Homebrew 的 Mac 可以运行：brew install uv"
  exit 1
fi

# Reuse an existing environment; never clear or overwrite it automatically.
if [[ ! -e .venv && ! -L .venv ]]; then
  uv venv --python '>=3.10' .venv
fi
if [[ ! -x .venv/bin/python ]]; then
  print -u2 ".venv 已存在但无法使用，请先将它移到别处，再重新运行。"
  exit 1
fi
.venv/bin/python -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ is required"'
uv pip install --python .venv/bin/python -r requirements.txt
print "安装完成。连接桌宠后运行 ./run.command；只看统计可运行 ./run.command --json。"
