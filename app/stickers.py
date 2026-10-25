"""表情包：按标签找本地图片。

两种认标签的方式，可以混用：

1. 文件名就是标签：`高兴.png` -> "高兴"
2. `杂图/tags.json` 里显式指定，一个标签可以挂多张：
   {"高兴": ["高兴.png", "qq_1a2b3c4d5e.jpg"]}

同一标签有多张时随机挑一张，免得每次都是同一张。
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import random
from pathlib import Path

from . import config

log = logging.getLogger("dafeiyu")

STICKER_DIR = config.ROOT / "杂图"
EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
INDEX_NAME = "tags.json"

# 表情发出去会被 QQ 缩到几百像素，超过这个体积就先自己压一下
MAX_SEND_BYTES = 900 * 1024
MAX_SEND_SIDE = 720
CACHE_DIR = config.DATA_DIR / "cache" / "stickers"

_cache: dict[str, list[Path]] | None = None


def _files() -> list[Path]:
    if not STICKER_DIR.is_dir():
        return []
    return [
        p
        for p in sorted(STICKER_DIR.iterdir())
        if p.is_file() and p.suffix.lower() in EXTS
    ]


def _load_index() -> dict[str, list[str]]:
    path = STICKER_DIR / INDEX_NAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, list)}


def tags() -> dict[str, list[Path]]:
    """标签 -> 图片列表。进程内缓存，改了 tags.json 要重启。"""
    global _cache
    if _cache is not None:
        return _cache

    result: dict[str, list[Path]] = {}
    indexed_names: set[str] = set()
    for tag, names in _load_index().items():
        paths = [STICKER_DIR / n for n in names if (STICKER_DIR / n).is_file()]
        if paths:
            result[tag] = paths
            indexed_names.update(p.name for p in paths)

    # 文件名本身是标签的，补进去（导入的 qq_xxxx 这种不算）
    for path in _files():
        if path.name in indexed_names:
            continue
        if path.stem.startswith("qq_"):
            continue
        bucket = result.setdefault(path.stem, [])
        if path not in bucket:  # 同名标签可能已经在 tags.json 里了
            bucket.append(path)

    _cache = result
    return result


def reload() -> None:
    global _cache
    _cache = None


def tag_names() -> list[str]:
    return list(tags().keys())


def path_for(tag: str) -> Path | None:
    data = tags()
    if tag in data and data[tag]:
        return random.choice(data[tag])
    # 允许模糊匹配：模型写成"开心"也能对上"高兴"
    for name, paths in data.items():
        if paths and (tag in name or name in tag):
            return random.choice(paths)
    return None


# 模型偶尔不发表情，这里按关键词兜底
KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("高兴", ("哈哈", "笑死", "好笑", "开心", "乐", "太好了", "哈哈哈")),
    ("悲伤", ("哭", "难过", "累", "惨", "烦", "委屈", "呜呜")),
    ("生气", ("气死", "滚", "闭嘴", "讨厌", "烦死")),
    ("委屈", ("委屈", "想哭", "欺负")),
    ("生无可恋", ("困", "睡", "熬夜", "麻了")),
    ("贪心", ("token", "给我", "多给", "再来点")),
    ("鲸元", ("钱", "买", "穷", "红包", "工资")),
    ("比心", ("喜欢", "可爱", "抱", "爱你", "亲")),
    ("疑惑", ("为什么", "为啥", "啥", "不懂", "怎么", "？", "?")),
    ("得意", ("厉害", "牛", "棒", "强", "佩服")),
    ("无语", ("无语", "麻了", "算了", "服了", "没救了")),
    ("躺平", ("躺平", "摆烂", "不想动", "累死")),
    ("压力", ("压力", "卷死", "焦虑", "顶不住")),
    ("干饭", ("干饭", "吃饭", "饿", "夜宵", "吃白饭")),
    ("冷", ("好冷", "冻死", "冷死", "冻成")),
    ("大肥鱼", ("大肥鱼", "肥鱼")),
    ("wink", ("晚安", "想你", "嘿嘿", "秘密")),
    ("思考", ("怎么", "能不能", "要不要", "是不是")),
]


def guess_tag(text: str) -> str | None:
    """按关键词猜一个标签，猜不到返回 None。"""
    available = tags()
    for tag, words in KEYWORDS:
        if tag not in available:
            continue
        if any(w in text for w in words):
            return tag
    return None


def prepare(path: Path) -> Path:
    """大图先压到 720px 以内再发，省流量也快。透明通道保留。"""
    try:
        if path.stat().st_size <= MAX_SEND_BYTES:
            return path
    except OSError:
        return path

    from PIL import Image

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / path.name
    try:
        if out.exists() and out.stat().st_mtime >= path.stat().st_mtime:
            return out
        with Image.open(path) as img:
            img = img.convert("RGBA")
            img.thumbnail((MAX_SEND_SIDE, MAX_SEND_SIDE))
            img.save(out, format="PNG", optimize=True)
        return out
    except Exception:  # noqa: BLE001 - 压不了就直接发原图
        return path


def _sniff_ext(raw: bytes) -> str:
    """看文件头猜扩展名。Pillow 转不了的时候至少还能按原格式存下来。"""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if raw[:2] == b"\xff\xd8":
        return ".jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if raw[:2] == b"BM":
        return ".bmp"
    if raw[4:8] == b"ftyp":  # HEIC/AVIF 这类
        return ".heic"
    return ""


def save_incoming(raw: bytes) -> Path | None:
    """把收到的一张图存进表情库（按内容去重，尽量转 PNG 保留透明）。

    转换失败不再静默丢弃：先记日志，再按原格式存下来——
    之前有张图就是在这悄悄没了（模型看到了图，表情库里却没有）。
    """
    digest = hashlib.md5(raw).hexdigest()
    STICKER_DIR.mkdir(parents=True, exist_ok=True)
    out = STICKER_DIR / f"qq_{digest[:10]}.png"
    if out.exists():
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as img:
            img.convert("RGBA").save(out, format="PNG", optimize=True)
        reload()
        return out
    except Exception as exc:  # noqa: BLE001 - 动图、超大图、损坏文件都可能失败
        ext = _sniff_ext(raw)
        log.warning("表情转 PNG 失败（%s），按原格式存：%s", exc, ext or "认不出")
        # 留一份原始字节，下次再翻车能直接查是什么格式/怎么坏的
        try:
            dump = config.DATA_DIR / "cache" / "failed-stickers"
            dump.mkdir(parents=True, exist_ok=True)
            (dump / f"{digest[:10]}{ext or '.bin'}").write_bytes(raw)
        except OSError:
            pass
        if not ext:
            return None
        fallback = STICKER_DIR / f"qq_{digest[:10]}{ext}"
        try:
            fallback.write_bytes(raw)
        except OSError:
            return None
        reload()
        return fallback
