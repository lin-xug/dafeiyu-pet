#!/usr/bin/env bash
# 后台运行：关掉终端也不停。
#
#   ./daemon.sh start     启动
#   ./daemon.sh stop      停止
#   ./daemon.sh restart   重启
#   ./daemon.sh status    看状态
#   ./daemon.sh log       跟踪日志（Ctrl+C 退出，不影响服务）
set -uo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-$(grep -oP '^PORT=\K\d+' .env 2>/dev/null || true)}"
PORT="${PORT:-8000}"
DATA_DIR="${DATA_DIR:-$(grep -oP '^DATA_DIR=\K.+' .env 2>/dev/null || true)}"
DATA_DIR="${DATA_DIR:-$HOME/文档/大肥鱼}"
LOG_DIR="$DATA_DIR/logs"
PID_FILE=".run/bot.pid"
mkdir -p "$LOG_DIR" .run

alive() {
  local pid
  pid=$(cat "$PID_FILE" 2>/dev/null) || return 1
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

health() {
  curl -s -m 2 "http://127.0.0.1:${PORT}/health" 2>/dev/null
}

do_start() {
  if health >/dev/null; then
    echo "已经在跑了（端口 $PORT）"
    return 0
  fi

  [ -d .venv ] || {
    echo "没有 .venv，先建环境"
    python3 -m venv .venv
    ./.venv/bin/pip install -q -r requirements.txt
  }
  [ -f .env ] || {
    echo "没有 .env，从模板复制一份"
    cp .env.example .env
  }

  bash stop.sh --quiet 2>/dev/null

  unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
  # setsid: 脱离当前终端会话，nohup: 忽略挂断信号
  nohup setsid ./.venv/bin/python run.py >>"$LOG_DIR/stdout.log" 2>&1 < /dev/null &

  for _ in $(seq 1 20); do
    sleep 0.5
    if health >/dev/null; then
      local pid
      pid=$(lsof -ti "tcp:${PORT}" -sTCP:LISTEN 2>/dev/null | head -1)
      [ -n "$pid" ] && echo "$pid" >"$PID_FILE"
      echo "已启动（PID ${pid:-?}，端口 $PORT）"
      echo "  状态：./daemon.sh status"
      echo "  日志：./daemon.sh log"
      return 0
    fi
  done

  echo "启动失败，日志最后几行："
  tail -15 "$LOG_DIR/stdout.log" 2>/dev/null
  return 1
}

do_stop() {
  bash stop.sh
  rm -f "$PID_FILE"
}

do_status() {
  local info
  info=$(health)
  if [ -n "$info" ]; then
    local pid
    pid=$(cat "$PID_FILE" 2>/dev/null || lsof -ti "tcp:${PORT}" -sTCP:LISTEN 2>/dev/null | head -1)
    echo "运行中  PID ${pid:-?}  端口 $PORT"
    echo "  $info"
    if [ -n "$pid" ]; then
      echo "  已运行 $(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')"
    fi
  else
    echo "没在跑"
    return 1
  fi
}

case "${1:-}" in
  start) do_start ;;
  stop) do_stop ;;
  restart)
    do_stop
    sleep 1
    do_start
    ;;
  status) do_status ;;
  log)
    echo "跟踪 $LOG_DIR/bot.log（Ctrl+C 退出，不影响服务）"
    tail -f "$LOG_DIR/bot.log"
    ;;
  *)
    echo "用法：./daemon.sh {start|stop|restart|status|log}"
    exit 1
    ;;
esac
