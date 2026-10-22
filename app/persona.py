"""人格文本从 data/persona/ 读，代码里只留默认值和拼装逻辑。

改性格 = 改文件，不用碰代码；文件删了会回落到内置默认值。
"""

from __future__ import annotations

import json

from . import config
from .persona_defaults import (
    EXAMPLES,
    GROUP_RULES,
    OUTPUT,
    PERSONA,
    PRIVATE_RULES,
    STYLE,
)

PERSONA_DIR = config.DATA_DIR / "persona"
RELATIONS_PATH = PERSONA_DIR / "relations.json"

FILES = {
    "persona": ("persona.md", PERSONA),
    "style": ("style.md", STYLE),
    "examples": ("examples.md", EXAMPLES),
    "output": ("output.md", OUTPUT),
    "group_rules": ("group_rules.md", GROUP_RULES),
    "private_rules": ("private_rules.md", PRIVATE_RULES),
}

_cache: dict[str, tuple[float, str]] = {}


def _seed(name: str) -> str:
    filename, default = FILES[name]
    path = PERSONA_DIR / filename
    PERSONA_DIR.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(default.rstrip() + "\n", "utf-8")
        return default
    return default


def get(name: str) -> str:
    """读一份文本，按修改时间缓存。文件没了就回落到默认值。"""
    filename, default = FILES[name]
    path = PERSONA_DIR / filename
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return _seed(name)
    hit = _cache.get(name)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        text = path.read_text("utf-8").strip()
    except OSError:
        return default
    if not text:
        text = default
    _cache[name] = (mtime, text)
    return text


def set_text(name: str, text: str) -> None:
    filename, _ = FILES[name]
    PERSONA_DIR.mkdir(parents=True, exist_ok=True)
    path = PERSONA_DIR / filename
    path.write_text(text.rstrip() + "\n", "utf-8")
    _cache.pop(name, None)


# ---- 按人定制 ----


def relations() -> dict:
    if not RELATIONS_PATH.exists():
        seed = {
            qq: {"name": "特殊关系", "prompt": prompt, "enabled": True, "system": False}
            for qq, prompt in SPECIAL_DEFAULT.items()
        }
        save_relations(seed)
        return seed
    try:
        data = json.loads(RELATIONS_PATH.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_relations(data: dict) -> None:
    PERSONA_DIR.mkdir(parents=True, exist_ok=True)
    RELATIONS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def relation_of(user_id: str) -> dict:
    return relations().get(str(user_id)) or {}


def is_special(user_id: str) -> bool:
    entry = relation_of(user_id)
    # 没配关系的人 relation_of 返回 {}，这里必须返回 False，
    # 否则"只有特殊关系才能做的事"会对所有人放开
    return bool(entry) and bool(entry.get("enabled", True))


def relation_hint(user_id: str) -> str:
    entry = relation_of(user_id)
    if not entry or not entry.get("enabled", True):
        return ""
    return str(entry.get("prompt") or "")


def build_system_prompt(extra: str = "", sticker_tags: list[str] | None = None) -> str:
    parts = [get("persona"), get("examples"), get("style"), get("output")]
    if sticker_tags:
        parts.append(STICKER.format(tags="、".join(sticker_tags)))
    extra = extra.strip()
    if extra:
        parts.append("【当前场景的补充信息】\n" + extra)
    return "\n\n".join(parts)


def group_rules() -> str:
    return get("group_rules")


def private_rules() -> str:
    return get("private_rules")


STICKER = """\
【表情包】
- 你能发表情包，可选标签：{tags}
- 一有情绪就配一张，两三句里就该有一张，别省着。
- 拿不准发哪个也发，别只打字，表情是语气的一部分。
- 想发的时候，在正文最后单独占一行，写：[[表情:标签]]
- 对应关系：觉得好笑或开心→高兴；对方难过→悲伤；被夸或害羞→比心；想不明白→疑惑或思考；得意→自豪；累到无语→生无可恋；卖乖→wink
- 表情是配着话说，不要只发一个表情什么都不说。
"""

SPECIAL_DEFAULT = {}


def init() -> None:
    """确保文件都在，并把旧的硬编码特殊关系迁移过去。"""
    for name in FILES:
        get(name)
    from .persona_defaults import SPECIAL as _special

    global SPECIAL_DEFAULT
    SPECIAL_DEFAULT = dict(_special or {})
    relations()
