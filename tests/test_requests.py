import asyncio
import os
import time

from app import config, onebot, requests

CALLED: list[tuple[str, dict]] = []


async def fake_call(action: str, payload: dict, timeout: float = 30) -> dict:
    CALLED.append((action, payload))
    return {"status": "ok", "retcode": 0, "data": None}


def setup_function():
    CALLED.clear()
    config.OWNER_IDS = {"10001"}
    config.REQUEST_TTL = 1800
    p = config.DATA_DIR / "requests.json"
    if p.exists():
        p.unlink()
    onebot._call = fake_call  # type: ignore[assignment]


def _friend_event(flag="flag-aaa", uid="20002"):
    return {
        "post_type": "request",
        "request_type": "friend",
        "user_id": uid,
        "comment": "我是xxx",
        "flag": flag,
    }


def _group_event(flag="flag-bbb", sub="add"):
    return {
        "post_type": "request",
        "request_type": "group",
        "sub_type": sub,
        "group_id": "10001",
        "user_id": "20003",
        "comment": "带我一个",
        "flag": flag,
    }


def test_ingest_friend():
    item = requests.ingest(_friend_event())
    assert item and item["kind"] == "friend"
    assert item["flag"] == "flag-aaa"
    assert len(requests.pending()) == 1


def test_ignore_non_request():
    assert requests.ingest({"post_type": "message"}) is None
    assert requests.ingest({"post_type": "request", "request_type": "unknown"}) is None
    assert requests.ingest({"post_type": "request", "request_type": "friend"}) is None  # 没 flag


def test_expired_is_dropped():
    requests.ingest(_friend_event())
    assert len(requests.pending(ttl=0)) == 0


def test_notify_text_mentions_command():
    text = requests.notify_text(requests.ingest(_friend_event()))
    assert "好友申请" in text and "同意好友" in text


def test_approve_friend():
    requests.ingest(_friend_event())
    reply = asyncio.run(requests.handle_command("同意好友", "10001", True))
    assert "已经同意" in reply
    action, payload = CALLED[0]
    assert action == "set_friend_add_request"
    assert payload["approve"] is True
    assert requests.pending() == []  # 处理完就清掉


def test_reject_group_invite():
    requests.ingest(_group_event(sub="invite"))
    reply = asyncio.run(requests.handle_command("拒绝入群", "10001", True))
    assert "已经拒绝" in reply
    action, payload = CALLED[0]
    assert action == "set_group_add_request"
    assert payload["sub_type"] == "invite"
    assert payload["approve"] is False


def test_not_owner_gets_nothing():
    requests.ingest(_friend_event())
    assert asyncio.run(requests.handle_command("同意好友", "99999", False)) is None


def test_no_pending_reply():
    assert "没有待处理" in asyncio.run(requests.handle_command("同意好友", "10001", True))


def test_list_text():
    requests.ingest(_friend_event())
    requests.ingest(_group_event())
    text = requests.list_text()
    assert "好友申请" in text and "进群" in text


def test_unrelated_text_is_not_command():
    assert asyncio.run(requests.handle_command("今天吃啥", "10001", True)) is None
    assert asyncio.run(requests.handle_command("我觉得可以", "10001", True)) is None


def test_exact_yn():
    requests.ingest(_friend_event())
    assert asyncio.run(requests.handle_command("y", "10001", True)) is not None
    requests.ingest(_friend_event(flag="flag-ccc"))
    assert asyncio.run(requests.handle_command("n", "10001", True)) is not None


def test_casual_chat_does_not_trigger():
    # 聊天里随口提到"群"和"同意"不能当成命令
    for t in ("我不同意这个群的说法", "群里的事我同意", "这个群挺好 我同意了"):
        assert asyncio.run(requests.handle_command(t, "10001", True)) is None


def test_approve_with_suffix():
    requests.ingest(_friend_event())
    assert asyncio.run(requests.handle_command("同意好友申请吧", "10001", True)) is not None


def test_not_sure():
    requests.ingest(_friend_event())
    assert "已经拒绝" in asyncio.run(requests.handle_command("不同意好友", "10001", True))
    requests.ingest(_friend_event(flag="flag-ddd"))
    assert "已经拒绝" in asyncio.run(requests.handle_command("不通过好友", "10001", True))
