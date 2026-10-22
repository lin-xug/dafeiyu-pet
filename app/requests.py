"""待处理的加好友 / 入群申请。

OneBot 的 flag 只在 request 事件里给一次，而且会过期；
协议端也没有「按 QQ 号查待处理申请」这种接口。
所以只能这样：事件一来就把 flag 存起来，等主人下命令，再拿 flag 去处理。
"""

from __future__ import annotations

import json
import re
import time

from . import config, onebot

FRIEND = "friend"
GROUP = "group"

# 同意/拒绝 必须紧挨着对象（好友 / 入群），中间最多隔空格。
# 这样"我不同意这个群的说法"不会误触发，"同意好友申请"能中。
# 注意顺序：长的在前，否则"不同意"会被拆成"同意"。
_CMD_RE = re.compile(r"(不同意|不通过|拒绝|同意|通过|批准)\s*(好友|入群|进群|加群)")
_REJECT_WORDS = ("不同意", "不通过", "拒绝")
_APPROVE_EXACT = {"y", "yes", "ok", "okay"}
_REJECT_EXACT = {"n", "no"}


def _path():
    return config.DATA_DIR / "requests.json"


def _load() -> list[dict]:
    try:
        data = json.loads(_path().read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save(items: list[dict]) -> None:
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(items, ensure_ascii=False, indent=2), "utf-8")


def owners() -> set[str]:
    """谁能收到通知、谁能下命令。没配就退回"特殊关系的人"。"""
    return {str(u) for u in config.OWNER_IDS if str(u)}


def ingest(event: dict) -> dict | None:
    """存一条申请事件。不是申请、或没有 flag 就返回 None。"""
    if event.get("post_type") != "request":
        return None
    kind = str(event.get("request_type") or "")
    if kind not in (FRIEND, GROUP):
        return None
    flag = str(event.get("flag") or "")
    if not flag:
        return None
    item = {
        "id": f"{kind}:{flag[:12]}",
        "kind": kind,
        "sub_type": str(event.get("sub_type") or ""),
        "flag": flag,
        "user_id": str(event.get("user_id") or ""),
        "group_id": str(event.get("group_id") or ""),
        "comment": str(event.get("comment") or "").strip(),
        "ts": int(time.time()),
    }
    items = [i for i in _load() if i.get("id") != item["id"]]
    items.append(item)
    _save(items[-20:])
    return item


def pending(ttl: int | None = None) -> list[dict]:
    """还没过期、还没处理的申请，按时间从旧到新。顺手清掉过期的。"""
    limit = config.REQUEST_TTL if ttl is None else ttl
    items = _load()
    now = time.time()
    alive = [i for i in items if now - float(i.get("ts", 0)) < limit]
    if len(alive) != len(items):
        _save(alive)
    return alive


def take(kind: str) -> dict | None:
    for item in pending():
        if item.get("kind") == kind:
            return item
    return None


def drop(item: dict) -> None:
    _save([i for i in _load() if i.get("id") != item.get("id")])


def describe(item: dict) -> str:
    who = item.get("user_id") or "?"
    comment = item.get("comment") or "（没写）"
    if item.get("kind") == FRIEND:
        return f"好友申请：{who}\n验证消息：{comment}"
    verb = "被邀请进群" if item.get("sub_type") == "invite" else "申请进群"
    return f"{verb}：{who} → 群 {item.get('group_id') or '?'}\n验证消息：{comment}"


def notify_text(item: dict) -> str:
    what = "好友" if item.get("kind") == FRIEND else "入群"
    return (
        "有人敲门：\n"
        f"{describe(item)}\n\n"
        f"回复「同意{what}」或「拒绝{what}」（说「待处理」能看全部）"
    )


def list_text() -> str:
    items = pending()
    if not items:
        return "现在没有待处理的申请"
    lines = [f"{n}. {describe(i)}" for n, i in enumerate(items, 1)]
    return "待处理的申请：\n" + "\n".join(lines)


def _decide(text: str) -> tuple[str, str] | None:
    """(kind, 同意/拒绝)。不是命令就返回 None。

    精确匹配 y/n 时不要求写对象，但那种情况下必须只有一条待处理，
    由调用方兜底。
    """
    low = text.strip().lower()
    if low in _APPROVE_EXACT:
        return "", "同意"
    if low in _REJECT_EXACT:
        return "", "拒绝"
    m = _CMD_RE.search(text)
    if not m:
        return None
    action = "拒绝" if m.group(1) in _REJECT_WORDS else "同意"
    kind = FRIEND if m.group(2) == "好友" else GROUP
    return kind, action


async def _resolve(item: dict, approve: bool) -> bool:
    """拿 flag 去协议端处理。失败基本都是 flag 过期了。"""
    if item.get("kind") == FRIEND:
        return await onebot.set_friend_add_request(item.get("flag", ""), approve)
    return await onebot.set_group_add_request(
        item.get("flag", ""), item.get("sub_type") or "add", approve
    )


async def handle_command(text: str, user_id: str, allowed: bool) -> str | None:
    """主人的私聊命令。返回要说的话，不是命令返回 None。"""
    if not allowed:
        return None
    if "待处理" in text:
        return list_text()
    decision = _decide(text)
    if not decision:
        return None
    kind, action = decision
    if not kind:
        # 只回了个 y/n：必须只有一条待处理，否则让他说清楚
        items = pending()
        if not items:
            return "现在没有待处理的申请"
        if len(items) > 1:
            return f"有 {len(items)} 条申请，说清楚是「{action}好友」还是「{action}入群」"
        kind = items[0].get("kind", FRIEND)

    what = "好友申请" if kind == FRIEND else "入群申请"
    item = take(kind)
    if not item:
        return f"现在没有待处理的{what}"
    ok = await _resolve(item, action == "同意")
    # 处理完就丢掉，不论成败：失败多半是过期了，留着也没用
    drop(item)
    if not ok:
        return f"没处理成功，多半是这条申请已经过期了（让对方重新{what[:2]}一次就行）"
    return f"已经{action}了：{describe(item).splitlines()[0]}"
