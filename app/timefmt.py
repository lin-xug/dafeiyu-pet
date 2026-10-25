"""给模型看的相对时间：越近越细，越远越粗。

存在的意义只有一个——让它分得清「这句是刚才说的」和「这句是前天说的」。
不带年份、不带秒，省 token。
"""

from __future__ import annotations

from datetime import datetime


def stamp(ts: float | None, *, now: float | None = None) -> str:
    """时间戳 → 短标记。

    今天 → 20:15
    昨天 → 昨天20:15
    今年 → 10-23 20:15
    更早 → 2025-10-23
    """
    try:
        moment = datetime.fromtimestamp(float(ts or 0))
    except (ValueError, OSError, OverflowError):
        return ""
    if moment.year < 2000:  # 0 或者明显不对的时间戳
        return ""
    ref = datetime.fromtimestamp(now) if now else datetime.now()
    today = ref.date()
    day = moment.date()
    if day == today:
        return moment.strftime("%H:%M")
    gap = (today - day).days
    if gap == 1:
        return moment.strftime("昨天%H:%M")
    if day.year == today.year:
        return moment.strftime("%m-%d %H:%M")
    return moment.strftime("%Y-%m-%d")


def span(start: float | None, end: float | None = None, *, now: float | None = None) -> str:
    """一段对话跨了多久，比如「昨天21:30 到 20:00」。"""
    head = stamp(start, now=now)
    if not head:
        return ""
    tail = stamp(end, now=now) if end else "现在"
    return f"{head} 到 {tail}"
