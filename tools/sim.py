"""不用 NapCat、不用真 key，往本地服务灌假的私聊消息，看它怎么回。

用法：
    # 先起服务（另开一个终端）：MOCK_LLM=1 DRY_RUN=1 python run.py
    python tools/sim.py 在吗 你玩过月亮计划吗 脑叶公司好玩吗
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request

SELF_ID = "99999"


def post(url: str, payload: dict, timeout: float = 10) -> str:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def get(url: str, timeout: float = 10) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def wait_for_replies(
    base: str, user_id: str, previous_seq: int, timeout: float = 30, quiet: float = 3.0
) -> tuple[int, list[str]]:
    """收集这一轮发出的所有分条：序号停住 quiet 秒才算说完。"""
    parts: list[str] = []
    last_seq = previous_seq
    deadline = time.time() + timeout
    last_change = time.time()
    while time.time() < deadline:
        try:
            data = json.loads(get(f"{base}/_debug/last?user_id={user_id}"))
        except (urllib.error.URLError, json.JSONDecodeError):
            time.sleep(0.3)
            continue
        seq = int(data.get("seq") or 0)
        if seq > last_seq:
            parts.append(str(data.get("last") or ""))
            last_seq = seq
            last_change = time.time()
        elif parts and time.time() - last_change >= quiet:
            break
        time.sleep(0.3)
    return last_seq, parts


def main() -> None:
    parser = argparse.ArgumentParser(description="离线模拟私聊")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--user", default="10001")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("texts", nargs="*")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    texts = args.texts or ["在吗", "你玩过月亮计划吗", "脑叶公司到底好不好玩"]

    try:
        print("服务状态：", get(f"{base}/health").strip())
    except urllib.error.URLError:
        print(f"连不上 {base}，先把服务起起来：MOCK_LLM=1 DRY_RUN=1 python run.py")
        return

    previous_seq = 0
    for index, text in enumerate(texts):
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": SELF_ID,
            "user_id": args.user,
            "message_id": int(time.time() * 1000) + index,
            "time": int(time.time()),
            "message": [{"type": "text", "data": {"text": text}}],
        }
        print(f"\n>>> {text}")
        post(f"{base}/qq", event)
        previous_seq, parts = wait_for_replies(base, args.user, previous_seq)
        if not parts:
            print("    （没等到回复，看服务端日志）")
        else:
            for part in parts:
                print(f"    {part}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
