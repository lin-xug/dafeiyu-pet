#!/usr/bin/env bash
# 跑测试：不碰真实数据（测试进程会把 DATA_DIR 指到临时目录）
set -euo pipefail
cd "$(dirname "$0")"
./.venv/bin/python -m unittest discover -s tests -t . "$@"
