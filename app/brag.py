"""GitHub 求赞限频。

靠代码挡，不靠模型自觉：人格里那句「偶尔提一句」挡不住它复读。
做法是——只在冷却结束时才把那段设定塞进 system prompt，
一旦放出去就立刻进冷却，不管它这次到底说没说。
"""

from __future__ import annotations

import json
import time

from . import config


def _path():
    return config.DATA_DIR / "brag.json"


def _load() -> dict[str, float]:
    try:
        data = json.loads(_path().read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(k, str)}


def _save(data: dict[str, float]) -> None:
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def allowed(scope: str, days: int) -> bool:
    """距离上次放出这段设定够久了吗。"""
    if days <= 0:
        return True
    last = _load().get(scope, 0.0)
    return time.time() - last >= days * 86400


def take(scope: str, days: int) -> bool:
    """冷却结束就记一笔并返回 True，否则 False。"""
    if not allowed(scope, days):
        return False
    data = _load()
    data[scope] = time.time()
    _save(data)
    return True


def private_block(user_id: str) -> str:
    """私聊里这段能不能提。能提就顺手占掉名额。"""
    if not config.GITHUB_URL:
        return ""
    scope = f"p:{user_id}"
    if not take(scope, config.GITHUB_BRAG_PRIVATE_DAYS):
        return ""
    from . import persona  # 放在这里，避免模块级循环导入

    return persona.github_block()


def group_block(group_id: str) -> str:
    """群里同理，冷却更长。"""
    if not config.GITHUB_URL:
        return ""
    scope = f"g:{group_id}"
    if not take(scope, config.GITHUB_BRAG_GROUP_DAYS):
        return ""
    from . import persona

    return persona.github_block()
