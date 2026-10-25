"""FastAPI 入口。"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from . import admin, affinity, backup, bot, config, gmemory, onebot, persona, tasks

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s | %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%H:%M:%S")

# 同时写到文件，方便事后查（终端日志一重启就没了）
_file_handler = logging.handlers.RotatingFileHandler(
    config.LOG_DIR / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
logging.getLogger().addHandler(_file_handler)
log = logging.getLogger("dafeiyu")

app = FastAPI(title="大肥鱼")
app.include_router(admin.router)
app.mount(
    "/admin/static",
    StaticFiles(directory=str(config.ROOT / "web" / "static")),
    name="admin-static",
)

# 同一个人的消息串行处理，避免它自己跟自己抢上下文
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def _process(event: dict) -> None:
    user_id = str(event.get("user_id") or "")
    async with _locks[user_id]:
        try:
            await bot.handle_event(event)
        except Exception:  # noqa: BLE001 - 单条消息出错不能拖垮整个服务
            log.exception("处理消息时出错")


@app.on_event("startup")
async def _startup() -> None:
    persona.init()
    # 老数据只存了最后一个昵称：从群日志补回「这个人用过哪些名字」，
    # 否则改了名的人会被模型当成两个不同的人（同类问题：重名的人被当成一个）
    try:
        filled = gmemory.rebuild_nicks()
        if filled:
            log.info("从群日志补全了 %d 个人的昵称历史", filled)
    except Exception:  # noqa: BLE001 - 迁移失败不该拦住启动
        log.exception("补全昵称历史失败，跳过")
    # 定时把内存里的好感度/群个人记忆刷到磁盘
    tasks.spawn(affinity.flush_loop(), name="flush")
    tasks.spawn(gmemory_flush_loop(), name="gflush")
    tasks.spawn(backup.daily_loop(), name="backup")


async def gmemory_flush_loop(interval: float = 10.0) -> None:
    while True:
        await asyncio.sleep(interval)
        gmemory.flush_all()


@app.on_event("shutdown")
async def _shutdown() -> None:
    affinity.flush_all()
    gmemory.flush_all()


@app.post("/qq")
async def qq(request: Request):
    """OneBot 事件上报地址，配到 NapCat 里就是 http://127.0.0.1:8000/qq。"""
    try:
        event = await request.json()
    except Exception:  # noqa: BLE001
        return {"status": "bad json"}
    if isinstance(event, dict):
        # 立刻 200，处理放后台，否则 NapCat 会超时
        tasks.spawn(_process(event), name=f"msg:{event.get('message_id')}")
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {
        "ok": True,
        "dry_run": config.DRY_RUN,
        "mock_llm": config.MOCK_LLM,
        "model": config.MODEL,
        "history_turns": config.HISTORY_TURNS,
        "groups": sorted(config.groups()),
    }


@app.get("/_debug/last")
async def debug_last(user_id: str | None = None):
    """调试用：看看最后发出去的是什么。"""
    if user_id:
        return {
            "user_id": user_id,
            "last": onebot.LAST.get(user_id),
            "seq": onebot.SEQ.get(user_id, 0),
        }
    return {"last": dict(onebot.LAST), "seq": dict(onebot.SEQ)}
