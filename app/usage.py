"""用量统计：模型 token 和语音字符，按调用追加到 data/usage/YYYY-MM.jsonl。

两种记录：
  - record()       对话：prompt / completion token
  - record_voice() 语音：合成字符数（命中缓存的不记账，因为没花钱）

汇总用 summary(days)，days=1 表示"今天"（从 00:00 起），days=0 表示全部历史。
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import config

_lock = threading.Lock()


def _path() -> Path:
    return config.USAGE_DIR / f"{datetime.now().strftime('%Y-%m')}.jsonl"


def _append(item: dict) -> None:
    try:
        config.USAGE_DIR.mkdir(parents=True, exist_ok=True)
        with _lock, _path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 记账失败不该影响主流程


def record(
    prompt_tokens: int,
    completion_tokens: int,
    *,
    model: str = "",
    kind: str = "chat",
    cached_tokens: int = 0,
) -> None:
    """记一次对话调用。

    `cached_tokens` 是**命中上下文缓存**的输入 token（DeepSeek 返回
    `prompt_cache_hit_tokens`）。命中价只有未命中的 1/4，不分开记会把花费高估好几倍。
    """
    if prompt_tokens <= 0 and completion_tokens <= 0:
        return
    hit = max(0, min(int(cached_tokens or 0), int(prompt_tokens)))
    _append(
        {
            "t": time.time(),
            "model": model,
            "kind": kind,
            "p": int(prompt_tokens),
            "c": int(completion_tokens),
            "h": hit,
        }
    )


def record_voice(chars: int, *, model: str = "", cached: bool = False) -> None:
    """记一次合成。`cached=True` 表示走了缓存——只统计命中率，不计费。"""
    if chars <= 0:
        return
    _append(
        {
            "t": time.time(),
            "model": model,
            "kind": "voice_cache" if cached else "voice",
            "chars": int(chars),
        }
    )


def _load(since_ts: float | None = None) -> list[dict]:
    """把月的文件全读一遍。跨月也能算，所以不按当前月路径硬查。"""
    rows: list[dict] = []
    try:
        paths = sorted(config.USAGE_DIR.glob("*.jsonl"))
    except OSError:
        return rows
    for path in paths:
        try:
            text = path.read_text("utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            if since_ts is not None and float(item.get("t") or 0) < since_ts:
                continue
            rows.append(item)
    return rows


def _since(days: int) -> float | None:
    """days=1 → 今天 00:00；days=7 → 6 天前的 00:00；days<=0 → 不限。"""
    if days <= 0:
        return None
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start -= timedelta(days=days - 1)
    return start.timestamp()


def summary(days: int = 1) -> dict:
    return _aggregate(_load(_since(days)), days)


def cost_between(start_ts: float, end_ts: float | None = None) -> dict:
    """某个时间区间内的花费——用来和"余额差"对账，窗口必须一致才有意义。"""
    rows = [
        row
        for row in _load(None)
        if float(row.get("t") or 0) >= start_ts
        and (end_ts is None or float(row.get("t") or 0) <= end_ts)
    ]
    return _aggregate(rows, 0)


def _aggregate(rows: list[dict], days: int) -> dict:
    prompt = completion = chars = hit = known = 0
    calls = voice_calls = voice_cached = 0
    by_kind: dict[str, int] = {}
    for row in rows:
        kind = str(row.get("kind") or "chat")
        if kind in ("voice", "voice_cache"):
            n = int(row.get("chars") or 0)
            if kind == "voice_cache":
                voice_cached += 1  # 命中缓存：不算钱，只记次数
                continue
            chars += n
            voice_calls += 1
            by_kind["voice"] = by_kind.get("voice", 0) + n
            continue
        p = int(row.get("p") or 0)
        c = int(row.get("c") or 0)
        calls += 1
        prompt += p
        completion += c
        if "h" in row:  # 老记录没有这个字段，无法区分缓存，不计入命中率
            hit += min(int(row.get("h") or 0), p)
            known += p
        by_kind[kind] = by_kind.get(kind, 0) + p + c

    miss = max(0, prompt - hit)
    chat_cost = (
        hit / 1_000_000 * config.PRICE_CACHE_HIT
        + miss / 1_000_000 * config.PRICE_INPUT
        + completion / 1_000_000 * config.PRICE_OUTPUT
    )
    voice_cost = chars / 10_000 * config.VOICE_PRICE
    return {
        "days": days,
        "calls": calls,
        "prompt": prompt,
        "completion": completion,
        "total": prompt + completion,
        "hit": hit,
        "miss": miss,
        "hit_prompt": known,
        "cache_rate": round(hit / known, 3) if known else 0.0,
        "chars": chars,
        "voice_calls": voice_calls,
        "voice_cached": voice_cached,
        "chat_cost": round(chat_cost, 4),
        "voice_cost": round(voice_cost, 4),
        "cost": round(chat_cost + voice_cost, 4),
        "by_kind": by_kind,
    }


def today() -> dict:
    """管理后台用的当天汇总（保留原来的返回键）。"""
    return summary(1)


def recent(kind: str = "voice", limit: int = 10) -> list[dict]:
    """最近几条某类记录（新的在前），后台展示用。"""
    rows = [row for row in _load(None) if str(row.get("kind")) == kind]
    return rows[-limit:][::-1]
