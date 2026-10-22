"""事实库：这个人在聊天里明确说过的事实，跨群跨私聊共用。

跟"摘要"不一样：摘要是一段模糊的印象，这里是一条条能直接用的具体信息，
比如"养了只猫叫豆豆""下个月去成都出差（临时）"。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import config

_lock_note = "写文件用"
LINE_RE = re.compile(r"^[-*]\s*\[(长期|临时)\]\s*(.+)$")

EXTRACT_PROMPT = """\
下面是某个人在聊天里说过的话。请只抽取"关于他自己的确定事实"。

两类：
- [长期] 职业、住处、宠物的名字、长期喜好、固定习惯、家人
- [临时] 最近要做的事，比如出差、考试、搬家、约好的事

不要抽：情绪、观点、玩笑、对别人的评价、一次性的感叹。
不要重复已有的。最多 5 条。没有就只输出 NONE。

每行一条，格式：
- [长期] 养了只猫叫豆豆
- [临时] 下个月去成都出差

【已有事实】
{existing}

【他说过的话】
{utterances}
"""


def _path(user_id: str) -> Path:
    return config.FACTS_DIR / f"{user_id}.json"


def load(user_id: str) -> list[dict]:
    path = _path(user_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    facts = data.get("facts")
    return facts if isinstance(facts, list) else []


def _save(user_id: str, facts: list[dict]) -> None:
    path = _path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"facts": facts[-80:]}, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


def add(user_id: str, text: str, kind: str, source: str = "") -> bool:
    """加一条事实，重复或太像的跳过。"""
    text = text.strip().strip("。.")
    if not text:
        return False
    facts = load(user_id)
    for f in facts:
        old = f.get("text", "")
        if old == text or old in text or text in old:
            return False
    expire = None
    if kind == "临时":
        expire = time.time() + config.FACTS_EXPIRE_DAYS * 86400
    facts.append(
        {"text": text, "kind": kind, "t": time.time(), "expire": expire, "src": source}
    )
    _save(user_id, facts)
    return True


def active(user_id: str, limit: int | None = None) -> list[str]:
    """没过期的、最近的几条。"""
    now = time.time()
    rows = [f for f in load(user_id) if not f.get("expire") or f["expire"] > now]
    rows.sort(key=lambda f: f.get("t", 0), reverse=True)
    return [f["text"] for f in rows[: limit or config.FACTS_INJECT]]


def block(user_id: str, query: str = "") -> str:
    rows = active(user_id, limit=30)
    if not rows:
        return ""
    limit = config.FACTS_INJECT
    if query and len(rows) > limit:
        q = set(query)
        rows = sorted(rows, key=lambda t: len(q & set(t)), reverse=True)[:limit]
    else:
        rows = rows[:limit]
    return "【你记得关于这个人的事】\n" + "\n".join(f"- {t}" for t in rows)


# ---- 待抽取的发言缓冲（按人累积，跨群）----
_pending: dict[str, list[str]] = {}
_running: set[str] = set()


def record_utterance(user_id: str, text: str) -> None:
    if not text:
        return
    buf = _pending.setdefault(user_id, [])
    buf.append(text[:200])
    if len(buf) > 200:
        del buf[:-200]


def take_pending(user_id: str, limit: int = 40) -> list[str]:
    buf = _pending.get(user_id, [])
    chunk = buf[:limit]
    del buf[:limit]
    return chunk


def needs_extract(user_id: str) -> bool:
    return len(_pending.get(user_id, [])) >= config.FACTS_TRIGGER


def parse_facts(raw: str) -> list[tuple[str, str]]:
    out = []
    for line in raw.splitlines():
        m = LINE_RE.match(line.strip())
        if m:
            out.append((m.group(2).strip(), m.group(1)))
    return out


def try_start(user_id: str) -> bool:
    """先占坑再起任务，避免连着几条消息同时抽出两份。"""
    if user_id in _running:
        return False
    _running.add(user_id)
    return True


async def extract(user_id: str, llm_chat, source: str = "") -> int:
    """抽一遍，返回新增条数。llm_chat 传 llm.chat，避免循环导入。"""
    try:
        chunk = take_pending(user_id)
        if not chunk:
            return 0
        existing = "\n".join(f"- {t}" for t in active(user_id, limit=10)) or "（无）"
        raw = await llm_chat(
            [
                {
                    "role": "user",
                    "content": EXTRACT_PROMPT.format(
                        existing=existing, utterances="\n".join(f"- {u}" for u in chunk)
                    ),
                }
            ],
            max_tokens=300,
            temperature=0.2,
            kind="facts",
        )
        added = 0
        for text, kind in parse_facts(raw):
            if add(user_id, text, kind, source):
                added += 1
        return added
    finally:
        _running.discard(user_id)
