#!/usr/bin/env bash
# 启动大肥鱼。第一次用会自动建 .venv；起之前先把端口让出来。
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "没找到 .venv，先建环境..."
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi

if [ ! -f .env ]; then
  echo "没找到 .env，从 .env.example 复制一份，记得填 API_KEY"
  cp .env.example .env
fi

# 上一把没退干净的话，先把端口收回来
bash stop.sh --quiet || true

echo "启动中...  Ctrl+C 停止（会正常释放端口）"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
# exec：信号直接给到 uvicorn，Ctrl+C 才能优雅退出而不是留个孤儿进程
exec ./.venv/bin/python run.py
