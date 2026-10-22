"""群里的"这个人说过什么"。

跟私聊记忆分开存：同一个人在群里和在私聊里说的话，不是一回事。

    data/gmemory/<群号>/<QQ>.json   最近 40 条 + 一份摘要
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import config

RAW_KEEP = 40        # 每个人在这个群最多留多少条原话
SUMMARY_TRIGGER = 30  # 攒到多少条就压缩一次
SUMMARY_KEEP = 10     # 压缩后保留多少条


def _dir(group_id: str) -> Path:
    return config.GMEMORY_DIR / str(group_id)


def _path(group_id: str, user_id: str) -> Path:
    return _dir(group_id) / f"{user_id}.json"


_cache: dict[tuple[str, str], dict] = {}
_dirty: set[tuple[str, str]] = set()


def _read(group_id: str, user_id: str) -> dict:
    cached = _cache.get((group_id, user_id))
    if cached is not None:
        return cached
    path = _path(group_id, user_id)
    if not path.exists():
        return {"nick": "", "messages": [], "summary": "", "updated_at": 0}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"nick": "", "messages": [], "summary": "", "updated_at": 0}
    data.setdefault("nick", "")
    data.setdefault("messages", [])
    data.setdefault("summary", "")
    _cache[(group_id, user_id)] = data
    return data


def _write(group_id: str, user_id: str, data: dict) -> None:
    _cache[(group_id, user_id)] = data
    _dirty.add((group_id, user_id))


def flush_all() -> int:
    pending = list(_dirty)
    _dirty.clear()
    written = 0
    for gid, uid in pending:
        data = _cache.get((gid, uid))
        if data is None:
            continue
        path = _path(gid, uid)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(path)
            written += 1
        except OSError:
            _dirty.add((gid, uid))
    return written


def record(group_id: str, user_id: str, nick: str, text: str) -> None:
    """记一条这个人在这个群的发言。"""
    if not text:
        return
    data = _read(group_id, user_id)
    data["nick"] = nick or data.get("nick", "")
    data["messages"].append({"text": text[:200], "ts": time.time()})
    data["messages"] = data["messages"][-RAW_KEEP:]
    data["updated_at"] = time.time()
    _write(group_id, user_id, data)


def recent(group_id: str, user_id: str, limit: int = 10) -> list[str]:
    return [m["text"] for m in _read(group_id, user_id)["messages"][-limit:]]


def summary(group_id: str, user_id: str) -> str:
    return _read(group_id, user_id).get("summary", "")


def save_summary(group_id: str, user_id: str, text: str, keep_messages: list[dict]) -> None:
    data = _read(group_id, user_id)
    data["summary"] = text.strip()[:800]
    data["messages"] = keep_messages[-RAW_KEEP:]
    data["updated_at"] = time.time()
    _write(group_id, user_id, data)


def needs_summary(group_id: str, user_id: str) -> bool:
    return len(_read(group_id, user_id)["messages"]) >= SUMMARY_TRIGGER


def split_for_summary(group_id: str, user_id: str) -> tuple[list[dict], list[dict]]:
    """返回 (要被压缩掉的, 要保留的)。"""
    msgs = _read(group_id, user_id)["messages"]
    return msgs[:-SUMMARY_KEEP], msgs[-SUMMARY_KEEP:]


# ---- 群整体摘要（这段时间群里发生了什么）----


def _group_path(group_id: str) -> Path:
    return _dir(group_id) / "_group.json"


def load_group(group_id: str) -> dict:
    path = _group_path(group_id)
    if not path.exists():
        return {"summary": "", "updated_at": 0, "msg_count": 0}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"summary": "", "updated_at": 0, "msg_count": 0}
    data.setdefault("summary", "")
    data.setdefault("updated_at", 0)
    data.setdefault("msg_count", 0)
    return data


def group_summary(group_id: str) -> str:
    return load_group(group_id).get("summary", "")
