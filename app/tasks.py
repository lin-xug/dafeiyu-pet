"""后台任务的引用管理。

asyncio.create_task 的返回值如果不保留引用，任务可能在跑完前被回收。
这里统一持有，跑完再释放。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

log = logging.getLogger("dafeiyu")

_pending: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any], name: str = "") -> asyncio.Task:
    task = asyncio.create_task(coro, name=name or None)
    _pending.add(task)

    def _done(t: asyncio.Task) -> None:
        _pending.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            log.error("后台任务出错 %s：%r", t.get_name(), exc)

    task.add_done_callback(_done)
    return task
