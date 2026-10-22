"""群记忆手册：一条条独立条目，只做增删改，不整段重写。

这样细节不会在反复压缩里蒸发（ACE 那套的思路）：
- 每条有 id、类型、内容、用过几次
- 模型只输出 ADD / UPDATE / DELETE，不重写全文
- 条目太多时按"用得少 + 太久没碰"淘汰
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import config

KINDS = ("梗", "关系", "坑", "安排", "事")
# 注入时的优先顺序：坑和梗最有用，流水账最没用
WEIGHT = {"坑": 5, "梗": 4, "关系": 3, "安排": 2, "事": 1}
MAX_ITEMS = 60
MAX_TEXT = 60
ARRANGE_TTL = 14 * 86400


def _path(group_id: str) -> Path:
    return config.GMEMORY_DIR / str(group_id) / "_playbook.json"


def load(group_id: str) -> dict:
    path = _path(group_id)
    if not path.exists():
        return {"items": [], "next_id": 1}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"items": [], "next_id": 1}
    if not isinstance(data.get("items"), list):
        data["items"] = []
    data.setdefault("next_id", 1)
    data.setdefault("rounds", 0)
    data.setdefault("last_ts", 0.0)
    return data


def save(group_id: str, data: dict) -> None:
    data["items"] = data["items"][-MAX_ITEMS * 2 :]
    path = _path(group_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


def active(group_id: str) -> list[dict]:
    now = time.time()
    items = load(group_id)["items"]
    return [i for i in items if not i.get("expire") or i["expire"] > now]


def _similar(a: str, b: str) -> float:
    """按 2-gram 算个粗略相似度，防止同义重复堆一堆。"""
    def grams(x: str) -> set[str]:
        x = x.strip()
        return {x[i : i + 2] for i in range(len(x) - 1)} if len(x) > 1 else {x}

    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def add(group_id: str, kind: str, text: str) -> bool:
    kind = kind if kind in KINDS else "事"
    text = text.strip().strip("。.")[:MAX_TEXT]
    if not text:
        return False
    data = load(group_id)
    for item in data["items"]:
        same = item["text"] == text or text in item["text"] or item["text"] in text
        sim = _similar(item["text"], text) if item["kind"] == kind else 0.0
        if same or (item["kind"] == kind and sim >= 0.35):
            # 意思一样就以新的为准，别多堆一条
            if len(text) > len(item["text"]) or not same:
                item["text"] = text
            item["updated"] = time.time()
            save(group_id, data)
            return False
    item = {
        "id": data["next_id"],
        "kind": kind,
        "text": text,
        "hits": 0,
        "created": time.time(),
        "updated": time.time(),
        "expire": time.time() + ARRANGE_TTL if kind == "安排" else None,
    }
    data["next_id"] += 1
    data["items"].append(item)
    save(group_id, data)
    return True


def update(group_id: str, item_id: int, text: str) -> bool:
    data = load(group_id)
    for item in data["items"]:
        if item["id"] == item_id:
            item["text"] = text.strip().strip("。.")[:MAX_TEXT]
            item["updated"] = time.time()
            save(group_id, data)
            return True
    return False


def delete(group_id: str, item_id: int) -> bool:
    data = load(group_id)
    before = len(data["items"])
    data["items"] = [i for i in data["items"] if i["id"] != item_id]
    if len(data["items"]) < before:
        save(group_id, data)
        return True
    return False


def prune(group_id: str) -> int:
    """条目超上限时，淘汰最"不值钱"的：用得少 + 很久没碰。"""
    data = load(group_id)
    items = data["items"]
    if len(items) <= MAX_ITEMS:
        return 0
    now = time.time()

    def score(item: dict) -> float:
        recency = now - item.get("updated", 0)
        return item.get("hits", 0) * 3600 - recency

    items.sort(key=score, reverse=True)
    dropped = len(items) - MAX_ITEMS
    data["items"] = items[:MAX_ITEMS]
    save(group_id, data)
    return dropped


def _overlap(query: str, text: str) -> float:
    if not query:
        return 0.0
    q = set(query)
    if not q:
        return 0.0
    return len(q & set(text)) / len(q)


def _score(item: dict, query: str, now: float) -> float:
    base = WEIGHT.get(item["kind"], 1) * 0.5
    rel = _overlap(query, item["text"]) * 8
    hits = min(3, item.get("hits", 0)) * 0.3
    age_days = (now - item.get("updated", 0)) / 86400
    fresh = max(0.0, 1.5 - age_days / 7)
    return base + rel + hits + fresh


def render(group_id: str, limit: int | None = None, query: str = "") -> str:
    """按和当前话题的相关度挑几条，顺便把"用过"记上。"""
    items = active(group_id)
    if not items:
        return ""
    limit = limit or config.PLAYBOOK_INJECT
    now = time.time()
    items.sort(key=lambda i: _score(i, query, now), reverse=True)
    picked = items[:limit]
    now = time.time()
    data = load(group_id)
    touched = {i["id"]: i for i in data["items"]}
    for item in picked:
        row = touched.get(item["id"])
        if row is not None:
            row["hits"] = row.get("hits", 0) + 1
            row["last_used"] = now
    save(group_id, data)
    lines = [f"- [{i['kind']}] {i['text']}" for i in picked]
    return "【这个群的事】\n" + "\n".join(lines)


def seed_from_summary(group_id: str, summary: str) -> None:
    """老版本的群摘要，搬一条进来别丢。"""
    if not summary.strip():
        return
    if load(group_id)["items"]:
        return
    add(group_id, "事", summary.strip()[:MAX_TEXT])


def last_ts(group_id: str) -> float:
    return float(load(group_id).get("last_ts", 0.0))


def set_last_ts(group_id: str, ts: float) -> None:
    data = load(group_id)
    data["last_ts"] = ts
    save(group_id, data)
