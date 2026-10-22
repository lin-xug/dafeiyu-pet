"""启动入口：python run.py"""

import socket
import sys

import uvicorn

from app import config


def port_available(host: str, port: int) -> bool:
    """SO_REUSEADDR 只能复用 TIME_WAIT，占着端口的活进程仍然会 bind 失败。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


if __name__ == "__main__":
    if not port_available(config.HOST, config.PORT):
        print(
            f"端口 {config.PORT} 已被占用。\n"
            f"  释放办法：bash stop.sh\n"
            f"  看看是谁：ss -lptn 'sport = :{config.PORT}'",
            file=sys.stderr,
        )
        sys.exit(1)

    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        log_level="info",
        timeout_graceful_shutdown=3,  # 退出时最多等 3 秒，然后立刻放端口
    )
