"""知识库索引：按关键词给单条消息挂上「判断用」的额外知识。

跟 `kb`（按群配置的背景资料）不是一回事，也不改它：
- kb 是**这个群**始终带着的背景，静态、一挂就是一整场；
- 这里是**这一条消息**临时挂上的判断依据，命中关键词才出现。

典型用法：有人提到「爸/妈/女儿」时，不求正则猜准，而是把一段
「先判断是不是在跟我认亲」的说明塞进这一轮的提示词，让模型自己分。
这样既不用多调一次模型，也不会像纯正则那样硬发图。

索引优先读 `data/kb/index.json`；没有就用内置默认，开箱即用。
文件写坏了、键写错了，一律静默跳过——这个功能永远不该影响正常回话。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import config

log = logging.getLogger("dafeiyu")

# 内置默认：没有 index.json 时直接用这份
DEFAULT_INDEX: dict[str, dict] = {
    "认亲判断": {
        "trigger": ["爸", "妈", "爹", "女儿", "闺女", "认亲", "爷爷", "奶奶", "亲戚"],
        "text": (
            "【这条先判断一下：对方是不是在跟你认亲】\n"
            "他刚才的话里出现了「爸/妈/女儿/认亲」这类词，先把两种情况分清楚：\n"
            "- 冲你来的（让你叫爸爸、自称你爹、说你是他闺女、要跟你攀亲戚）\n"
            "  → 这是欠怼的场面。回一句短的，语气可以凶可以玩梗，"
            "然后必须配「认亲」那张表情：[[表情:认亲]]\n"
            "- 只是说他自己的家人（「我妈今天做饭」「我女儿三岁」）\n"
            "  → 跟你没关系。正常回，**不要**用这张图。\n"
            "- 拿不准 → 不用这张图，宁可不用。\n"
            "如果对方是主人，永远不要用这张图。"
        ),
    },
}


def _index_path() -> Path:
    return config.KB_DIR / "index.json"


def index() -> dict[str, dict]:
    """读索引，文件不存在或读不动就退回内置默认。"""
    path = _index_path()
    if not path.exists():
        return DEFAULT_INDEX
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("知识库索引读不出来（%s），先用内置的", exc)
        return DEFAULT_INDEX
    if not isinstance(data, dict) or not data:
        return DEFAULT_INDEX
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _entry_text(name: str, entry: dict) -> str:
    """一段索引对应要注入的正文：优先同目录的 md 文件，其次条目里的 text。"""
    filename = entry.get("file") or f"{name}.md"
    path = config.KB_DIR / str(filename)
    if path.exists():
        try:
            body = path.read_text("utf-8").strip()
        except OSError:
            body = ""
        if body:
            return body
    text = entry.get("text")
    return str(text).strip() if text else ""


def block(text: str) -> str:
    """这一条消息该额外挂哪些判断知识。没有命中就返回空串。"""
    if not text:
        return ""
    try:
        table = index()
    except Exception:  # noqa: BLE001 - 任何意外都不该拦住回话
        return ""

    blocks = []
    for name, entry in table.items():
        words = entry.get("trigger") or []
        if not isinstance(words, list):
            continue
        if not any(isinstance(w, str) and w and w in text for w in words):
            continue
        try:
            body = _entry_text(name, entry)
        except Exception:  # noqa: BLE001
            continue
        if body:
            blocks.append(body)
    return "\n\n".join(blocks)
