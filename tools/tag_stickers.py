"""给 `杂图/` 里的表情包自动打标签。

用视觉模型看图，从固定标签表里挑一个，结果写进 `杂图/tags.json`：

    {"高兴": ["高兴.png", "qq_1a2b3c4d5e.jpg"], "悲伤": [...]}

用法：
    python tools/tag_stickers.py --limit 10     # 先试 10 张
    python tools/tag_stickers.py                # 全部打标
    python tools/tag_stickers.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import llm, stickers, vision  # noqa: E402

TAGS = [
    "高兴", "悲伤", "生气", "害羞", "疑惑", "思考", "无语",
    "得意", "比心", "困", "饿", "震惊", "嫌弃", "哭", "杂项",
]

PROMPT = """\
这是一张 QQ 聊天用的表情包。用一个词概括它的情绪，只能从这个列表里选：
{taxonomy}
只输出那一个词，不要标点，不要解释，不要句子。
"""


def load_index(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in data.items() if isinstance(v, list)}


def save_index(path: Path, index: dict[str, list[str]]) -> None:
    path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def pick_tag(raw: str, allowed: list[str]) -> str:
    text = raw.strip().strip("。.,，\"'“”")
    for tag in allowed:
        if tag == text:
            return tag
    for tag in allowed:
        if tag in text:
            return tag
    return "杂项"


async def tag_one(path: Path, allowed: list[str]) -> str:
    uri = await vision.fetch_as_data_uri(str(path))
    if not uri:
        return "杂项"
    reply = await llm.chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT.format(taxonomy="、".join(allowed))},
                    {"type": "image_url", "image_url": {"url": uri}},
                ],
            }
        ],
        max_tokens=16,
        temperature=0.2,
    )
    return pick_tag(reply, allowed)


async def main() -> None:
    parser = argparse.ArgumentParser(description="给表情包打标签")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    index_path = stickers.STICKER_DIR / "tags.json"
    index = load_index(index_path)

    tagged = {name for names in index.values() for name in names}
    files = [
        p
        for p in sorted(stickers.STICKER_DIR.iterdir())
        if p.is_file() and p.suffix.lower() in stickers.EXTS and p.name not in tagged
    ]
    # 文件名本身就是标签的（高兴.png 这种），不用再打
    existing = set(stickers.tag_names()) | set(TAGS)
    files = [p for p in files if p.stem not in existing]

    print(f"已标 {len(tagged)} 张，待标 {len(files)} 张")
    if args.limit:
        files = files[: args.limit]

    done = 0
    for path in files:
        tag = await tag_one(path, TAGS)
        print(f"  {path.name} -> {tag}")
        if args.dry_run:
            continue
        index.setdefault(tag, []).append(path.name)
        save_index(index_path, index)  # 每张都落盘，中断不丢进度
        done += 1

    if not args.dry_run:
        print(f"\n完成 {done} 张，结果在 {index_path}")


if __name__ == "__main__":
    asyncio.run(main())
