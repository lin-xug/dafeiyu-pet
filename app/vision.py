"""把 QQ 发来的图片变成模型能看的东西。

QQ 的图走的是腾讯 CDN，链接会过期、也不保证公网可达，
所以本地下载、压小、转成 base64 再塞给模型，最稳。
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

import httpx
from PIL import Image

log = logging.getLogger("dafeiyu")

MAX_SIDE = 768  # 压到这个尺寸，够看清内容，又不至于把请求撑爆
MAX_BYTES = 6 * 1024 * 1024


def _shrink(raw: bytes) -> bytes:
    """压到 MAX_SIDE 以内，转成 PNG（表情包多是透明底）。"""
    with Image.open(io.BytesIO(raw)) as img:
        img = img.convert("RGBA")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def to_data_uri(raw: bytes) -> str:
    try:
        raw = _shrink(raw)
    except Exception as exc:  # noqa: BLE001 - 动图/损坏的图都可能失败，退回原图
        log.warning("图片压缩失败，用原图：%s", exc)
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:image/png;base64,{encoded}"


async def _from_napcat(source: str) -> bytes | None:
    """让协议端去取图（延迟导入，避免 onebot ↔ vision 循环依赖）。"""
    from . import onebot

    return await onebot.get_image_bytes(source)


async def fetch_bytes(source: str) -> bytes | None:
    """把 http(s)/file:///本地路径/协议端 file 标识 都读成 bytes。"""
    raw: bytes | None = None
    try:
        if source.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.get(source)
            if resp.status_code >= 400:
                log.warning("下载图片失败 HTTP %s，改用协议端取", resp.status_code)
            else:
                raw = resp.content
            if raw is None:
                # 直链多半只对 QQ 客户端有效（一次性 rkey），让 NapCat 自己去取
                raw = await _from_napcat(source)
        elif source.startswith("file://"):
            try:
                raw = Path(source[7:]).read_bytes()
            except OSError:
                raw = await _from_napcat(source[7:])
        else:
            local = Path(source)
            if local.is_file():
                raw = local.read_bytes()
            else:
                # 事件里的 `XXXX.image` 这种标识，只能交给协议端
                raw = await _from_napcat(source)
    except Exception as exc:  # noqa: BLE001
        log.warning("读取图片失败：%s", exc)
        return None
    if raw is None:
        return None
    if len(raw) > MAX_BYTES:
        log.warning("图片太大（%d 字节），跳过", len(raw))
        return None
    return raw


async def fetch_as_data_uri(source: str) -> str | None:
    """source 可以是 http(s) 链接、file:// 或者本地绝对路径。"""
    raw = await fetch_bytes(source)
    return to_data_uri(raw) if raw else None
