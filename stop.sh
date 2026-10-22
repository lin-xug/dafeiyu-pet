#!/usr/bin/env bash
# 停掉占着端口的 bot。默认只结束我们自己的进程，--any 才强杀别的。
set -uo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-$(grep -oP '^PORT=\K\d+' .env 2>/dev/null || true)}"
PORT="${PORT:-8000}"

QUIET=0
ANY=0
for arg in "$@"; do
  case "$arg" in
    --quiet) QUIET=1 ;;
    --any) ANY=1 ;;
  esac
done

say() { [ "$QUIET" = "1" ] || echo "$@"; }

listening() { lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true; }

pids=$(listening)
if [ -z "$pids" ]; then
  say "端口 $PORT 没人占用"
  exit 0
fi

targets=""
for pid in $pids; do
  cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
  if [ "$ANY" = "1" ] || printf '%s' "$cmd" | grep -qE 'run\.py|uvicorn'; then
    targets="$targets $pid"
  else
    say "跳过 PID $pid（不是我们的进程）：$cmd"
  fi
done

if [ -z "$targets" ]; then
  say "端口 $PORT 被别人占着，没动它。要强杀：bash stop.sh --any"
  exit 1
fi

for pid in $targets; do
  say "停止 PID $pid"
  kill -TERM "$pid" 2>/dev/null || true
done

# 最多等 5 秒优雅退出
for _ in $(seq 1 20); do
  [ -z "$(listening)" ] && break
  sleep 0.25
done

# 还赖着就强制结束
for pid in $targets; do
  if listening | grep -qx "$pid"; then
    say "强制结束 PID $pid"
    kill -9 "$pid" 2>/dev/null || true
  fi
done

sleep 0.3
if [ -z "$(listening)" ]; then
  say "端口 $PORT 已释放"
else
  say "端口 $PORT 仍被占用，手动查：ss -lptn 'sport = :$PORT'"
  exit 1
fi
