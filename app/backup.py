"""每天把记忆目录打成一个压缩包，留最近 7 份。"""

from __future__ import annotations

import asyncio
import logging
import tarfile
import time
from datetime import datetime
from pathlib import Path

from . import config

log = logging.getLogger("dafeiyu")

BACKUP_DIR = config.DATA_DIR / "backups"
KEEP = 7
# 跳过这些，别把备份本身和表情缓存打进去
SKIP = {"backups", "cache"}


def create_backup(force: bool = False) -> Path | None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S") if force else datetime.now().strftime("%Y-%m-%d")
    target = BACKUP_DIR / f"dafeiyu-{stamp}.tar.gz"
    if target.exists():
        return target
    try:
        with tarfile.open(target, "w:gz") as tar:
            for item in config.DATA_DIR.iterdir():
                if item.name in SKIP or item == BACKUP_DIR:
                    continue
                tar.add(item, arcname=item.name)
    except Exception:  # noqa: BLE001
        log.exception("备份失败")
        target.unlink(missing_ok=True)
        return None

    # 只留最近 KEEP 份
    olds = sorted(BACKUP_DIR.glob("dafeiyu-*.tar.gz"))
    for old in olds[:-KEEP]:
        old.unlink(missing_ok=True)
    log.info("已备份 %s（%.1f KB）", target.name, target.stat().st_size / 1024)
    return target


def _last_backup_ts() -> float:
    files = list(BACKUP_DIR.glob("dafeiyu-*.tar.gz"))
    return max((f.stat().st_mtime for f in files), default=0.0)


async def daily_loop(interval: float = 3600) -> None:
    """每小时看一眼：超过 24 小时没备份就备一份。"""
    while True:
        try:
            if time.time() - _last_backup_ts() > 24 * 3600:
                await asyncio.to_thread(create_backup)
        except Exception:  # noqa: BLE001
            log.exception("定时备份出错")
        await asyncio.sleep(interval)
