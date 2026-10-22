"""通过 OneBot 接口往外发消息、发表情、撤回。"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from . import config

log = logging.getLogger("dafeiyu")

# 调试用：每个用户最后一次收到的内容，给 /_debug/last 用
LAST: dict[str, str] = {}
# 自发消息序号：内容可能重复，光比文本会误判"没回复"
SEQ: dict[str, int] = {}
_counter = 0


def _headers() -> dict[str, str]:
    if config.ONEBOT_TOKEN:
        return {"Authorization": f"Bearer {config.ONEBOT_TOKEN}"}
    return {}


async def _call(action: str, payload: dict, timeout: float = 30) -> dict | None:
    url = f"{config.ONEBOT_URL}/{action}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=_headers())
        body = resp.json() if resp.content else {}
    except (httpx.HTTPError, ValueError) as exc:
        log.error("%s 失败：%s", action, exc)
        return None
    if resp.status_code >= 400:
        log.error("%s 失败 HTTP %s：%s", action, resp.status_code, resp.text[:200])
        return None
    retcode = body.get("retcode") if isinstance(body, dict) else None
    if retcode not in (None, 0, 1):
        log.error("%s 失败 retcode=%s：%s", action, retcode, body.get("message"))
        return None
    return body if isinstance(body, dict) else {}


def _message_id(body: dict | None) -> int | None:
    if not body:
        return None
    data = body.get("data")
    if isinstance(data, dict) and data.get("message_id") is not None:
        try:
            return int(data["message_id"])
        except (TypeError, ValueError):
            return None
    return None


async def send_private(user_id: str, text: str) -> int | None:
    """发文字，返回 message_id（撤回要用）。"""
    global _counter
    _counter += 1
    LAST[user_id] = text
    SEQ[user_id] = _counter

    if config.DRY_RUN:
        log.info("[DRY_RUN] 发给 %s：%s", user_id, text)
        return 0

    payload = {
        "user_id": int(user_id) if user_id.isdigit() else user_id,
        "message": text,
    }
    body = await _call("send_private_msg", payload)
    if body is None:
        return None
    log.info("已发送给 %s：%s", user_id, text)
    return _message_id(body)


async def send_private_image(user_id: str, path: Path) -> int | None:
    """发本地图片（表情包）。"""
    global _counter
    _counter += 1
    SEQ[user_id] = _counter
    LAST[user_id] = f"[图片] {path.name}"

    if config.DRY_RUN:
        log.info("[DRY_RUN] 发给 %s：[图片] %s", user_id, path)
        return 0

    payload = {
        "user_id": int(user_id) if user_id.isdigit() else user_id,
        "message": [{"type": "image", "data": {"file": path.as_uri(), "sub_type": 1}}],
    }
    body = await _call("send_private_msg", payload, timeout=90)
    if body is None:
        return None
    log.info("已发给 %s 表情：%s", user_id, path.stem)
    return _message_id(body)


async def send_private_record(user_id: str, path: Path) -> int | None:
    """发一条本地语音。给 mp3 就行，NapCat 自己转 silk 再上传。"""
    global _counter
    _counter += 1
    SEQ[user_id] = _counter
    LAST[user_id] = f"[语音] {path.name}"

    if config.DRY_RUN:
        log.info("[DRY_RUN] 发给 %s：[语音] %s", user_id, path)
        return 0

    import base64

    try:
        raw = path.read_bytes()
    except OSError as exc:
        log.error("语音读不出来：%s（%s）", path, exc)
        return None
    payload = {
        "user_id": int(user_id) if user_id.isdigit() else user_id,
        "message": [
            {
                "type": "record",
                "data": {"file": "base64://" + base64.b64encode(raw).decode()},
            }
        ],
    }
    body = await _call("send_private_msg", payload, timeout=90)
    if body is None:
        return None
    log.info("已发给 %s 语音：%s", user_id, path.stem)
    return _message_id(body)


async def send_group_record(group_id: str, path: Path) -> int | None:
    """往群里发一条语音。给 mp3 就行，NapCat 自己转 silk。"""
    global _counter
    _counter += 1
    SEQ[group_id] = _counter
    LAST[group_id] = f"[语音] {path.name}"

    if config.DRY_RUN:
        log.info("[DRY_RUN] 群 %s：[语音] %s", group_id, path)
        return 0

    import base64

    try:
        raw = path.read_bytes()
    except OSError as exc:
        log.error("语音读不出来：%s（%s）", path, exc)
        return None
    payload = {
        "group_id": int(group_id) if group_id.isdigit() else group_id,
        "message": [
            {
                "type": "record",
                "data": {"file": "base64://" + base64.b64encode(raw).decode()},
            }
        ],
    }
    body = await _call("send_group_msg", payload, timeout=90)
    if body is None:
        return None
    log.info("群里发了语音 %s：%s", group_id, path.stem)
    return _message_id(body)


async def recall(message_id: int) -> bool:
    """撤回自己发的消息（打错字用）。"""
    if config.DRY_RUN:
        log.info("[DRY_RUN] 撤回 %s", message_id)
        return True
    return await _call("delete_msg", {"message_id": message_id}) is not None


async def set_friend_add_request(flag: str, approve: bool, remark: str = "") -> bool:
    """处理别人发来的好友申请。flag 只在 request 事件里给，过期就废了。"""
    if config.DRY_RUN:
        log.info("[DRY_RUN] %s 好友申请 flag=%s", "同意" if approve else "拒绝", flag[:16])
        return True
    body = await _call(
        "set_friend_add_request",
        {"flag": flag, "approve": approve, "remark": remark},
    )
    return body is not None


async def set_group_add_request(
    flag: str, sub_type: str, approve: bool, reason: str = ""
) -> bool:
    """处理入群申请 / 进群邀请。sub_type 是 add（申请加群）或 invite（被邀请）。"""
    if config.DRY_RUN:
        log.info("[DRY_RUN] %s 入群申请(%s) flag=%s", "同意" if approve else "拒绝", sub_type, flag[:16])
        return True
    body = await _call(
        "set_group_add_request",
        {"flag": flag, "sub_type": sub_type, "approve": approve, "reason": reason},
    )
    return body is not None


async def send_group(group_id: str, text: str, at_user: str | None = None) -> int | None:
    """往群里发一条文字，可选 @ 某个人。"""
    global _counter
    _counter += 1
    SEQ[group_id] = _counter
    LAST[group_id] = text
    if config.DRY_RUN:
        log.info("[DRY_RUN] 群 %s：%s%s", group_id, "[@] " if at_user else "", text)
        return 0

    if at_user:
        message = [
            {"type": "at", "data": {"qq": at_user}},
            {"type": "text", "data": {"text": f" {text}"}},
        ]
    else:
        message = text

    payload = {
        "group_id": int(group_id) if group_id.isdigit() else group_id,
        "message": message,
    }
    body = await _call("send_group_msg", payload)
    if body is None:
        return None
    log.info("群里说了 %s：%s", group_id, text)
    return _message_id(body)


async def send_group_image(group_id: str, path: Path) -> int | None:
    """往群里发一张表情。"""
    global _counter
    _counter += 1
    SEQ[group_id] = _counter
    LAST[group_id] = f"[图片] {path.name}"
    if config.DRY_RUN:
        log.info("[DRY_RUN] 群 %s：表情 %s", group_id, path.name)
        return 0
    payload = {
        "group_id": int(group_id) if group_id.isdigit() else group_id,
        "message": [{"type": "image", "data": {"file": path.as_uri(), "sub_type": 1}}],
    }
    body = await _call("send_group_msg", payload, timeout=90)
    if body is None:
        return None
    log.info("群里发了表情 %s：%s", group_id, path.stem)
    return _message_id(body)
