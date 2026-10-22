"""QQ 号 → 备注：让后台看得懂谁是谁。

优先级：**手填备注 → 群里最近用过的昵称 → 空**（前端回退成 QQ 号）。
昵称是从 gmemory 里捞的，不用另外存——她本来就知道谁是谁。
"""

from __future__ import annotations

import json
import re
import threading
import time

from . import config

FILE = config.DATA_DIR / "aliases.json"
_lock = threading.Lock()

# 昵称缓存：88 个文件才 384K，但每次请求都读也没必要，30 秒一次够了
_NICKS: tuple[float, dict[str, str]] = (0.0, {})
_NICK_TTL = 30.0
# 只从文件里抠 "nick": "..."，不整体 json.loads（文件可能很大）
NICK_RE = re.compile(r'"nick"\s*:\s*"((?:[^"\\]|\\.)*)"')
# urlencoded 表单如果用裸 UTF-8 字节提交（curl -d 不编码就会这样），
# 服务端可能按 latin-1 解出 "æµè¯" 这种乱码。这里兜一下。
MOJI_RE = re.compile(r"[\u00c0-\u00ff]")


def _repair(text: str) -> str:
    """把 "æµ‹è¯•" 这种按 cp1252/latin-1 误解码的乱码还原成中文。"""
    if not MOJI_RE.search(text):
        return text
    for codec in ("cp1252", "latin-1"):
        try:
            fixed = text.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if fixed:
            return fixed
    return text


def load() -> dict[str, str]:
    try:
        data = json.loads(FILE.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): _repair(str(v).strip()) for k, v in data.items() if str(v).strip()}


def save(data: dict[str, str]) -> None:
    with _lock:
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8"
            )
        except OSError:
            pass


def names() -> dict[str, str]:
    return load()


def get(qq: str) -> str:
    return load().get(str(qq), "")


def set_alias(qq: str, name: str) -> str:
    """存备注；空字符串等于删除。返回最终保存的值。"""
    qq = str(qq).strip()
    name = _repair(name or "")
    name = re.sub(r"\s+", " ", name).strip()[:32]
    data = load()
    if name:
        data[qq] = name
    else:
        data.pop(qq, None)
    save(data)
    return name


def nick_map() -> dict[str, str]:
    """每个 QQ 最近一次出现的群昵称（取文件 mtime 最新的那个）。"""
    global _NICKS
    stamp, cached = _NICKS
    if cached and time.time() - stamp < _NICK_TTL:
        return cached

    best: dict[str, tuple[float, str]] = {}
    try:
        paths = list(config.GMEMORY_DIR.glob("*/*.json"))
    except OSError:
        paths = []
    for path in paths:
        try:
            mtime = path.stat().st_mtime
            text = path.read_text("utf-8")
        except OSError:
            continue
        found = NICK_RE.search(text)
        if not found:
            continue
        try:
            nick = json.loads(f'"{found.group(1)}"')  # 反转义 \uXXXX 之类
        except json.JSONDecodeError:
            continue
        nick = str(nick).strip()
        qq = path.stem
        if not nick or nick == qq:
            continue
        if qq not in best or mtime > best[qq][0]:
            best[qq] = (mtime, nick)

    result = {qq: value[1] for qq, value in best.items()}
    _NICKS = (time.time(), result)
    return result


def display(qq: str) -> str:
    """人能看的名字；没备注也没昵称就返回空串，由调用方决定显示什么。"""
    return get(qq) or nick_map().get(str(qq), "")
