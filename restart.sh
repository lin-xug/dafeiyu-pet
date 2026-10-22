#!/usr/bin/env bash
# 重启：先放端口，再拉起
set -euo pipefail
cd "$(dirname "$0")"
bash stop.sh
exec bash start.sh
