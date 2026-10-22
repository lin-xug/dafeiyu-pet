"""记忆：最近若干轮原话 + 一份长期摘要。

- `data/history/<QQ>.json`       最近几轮原话
- `data/history/<QQ>.summary.md` 更早内容的压缩摘要
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import config

MAX_TURNS = config.HISTORY_TURNS
# 文件里最多留多少条，防止摘要一直失败把文件撑爆
HARD_CAP = 120
_lock = threading.Lock()


def _path(user_id: str) -> Path:
    return config.HISTORY_DIR / f"{user_id}.json"


def _summary_path(user_id: str) -> Path:
    return config.HISTORY_DIR / f"{user_id}.summary.md"


def load(user_id: str) -> list[dict]:
    path = _path(user_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    messages = data.get("messages")
    if not isinstance(messages, list):
        return []
    keep = []
    for m in messages:
        if (
            isinstance(m, dict)
            and m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
        ):
            keep.append(m)
    return keep


def _write(user_id: str, messages: list[dict]) -> None:
    path = _path(user_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {"updated_at": time.time(), "messages": messages},
            ensure_ascii=False,
            indent=2,
        ),
        "utf-8",
    )
    tmp.replace(path)


def append(user_id: str, role: str, content: str) -> None:
    with _lock:
        messages = load(user_id)
        messages.append({"role": role, "content": content, "ts": time.time()})
        _write(user_id, messages[-HARD_CAP:])


def replace(user_id: str, messages: list[dict]) -> None:
    """摘要写完后，把已经压缩掉的部分从原话里删掉。"""
    with _lock:
        _write(user_id, messages)


def recent(user_id: str, turns: int | None = None) -> list[dict]:
    """最近 N 轮的对话，转成模型能吃的格式。"""
    limit = turns or MAX_TURNS
    messages = load(user_id)
    return [{"role": m["role"], "content": m["content"]} for m in messages[-limit * 2 :]]


def load_summary(user_id: str) -> str:
    path = _summary_path(user_id)
    if not path.exists():
        return ""
    try:
        return path.read_text("utf-8").strip()
    except OSError:
        return ""


def save_summary(user_id: str, text: str) -> None:
    path = _summary_path(user_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text.strip() + "\n", "utf-8")
    tmp.replace(path)


def summary_stale(user_id: str) -> bool:
    """原话是不是多到该压缩了。"""
    return len(load(user_id)) > config.SUMMARY_TRIGGER
