"""模式开关：普通 / 超活跃。

超活跃是手动开的，开了之后这个群的随机回复率和冷却都会变激进。
状态存在 data/modes.json，重启不丢。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import config

_cache: tuple[float, dict] | None = None


def _path() -> Path:
    return config.DATA_DIR / "modes.json"


def load() -> dict[str, str]:
    global _cache
    path = _path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _cache and _cache[0] == mtime:
        return _cache[1]
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    _cache = (mtime, data if isinstance(data, dict) else {})
    return _cache[1]


def save(data: dict[str, str]) -> None:
    global _cache
    path = _path()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _cache = (path.stat().st_mtime, data)


def set_mode(
    group_id: str,
    mode: str,
    random_rate: float | None = None,
    mention_rate: float | None = None,
) -> None:
    data = dict(load())
    if mode == "normal":
        data.pop(str(group_id), None)
        save(data)
        return
    if mode == "silent":
        data[str(group_id)] = {"mode": "silent"}
        save(data)
        return
    old = data.get(str(group_id))
    entry: dict = dict(old) if isinstance(old, dict) else {"mode": "hyper"}
    entry["mode"] = "hyper"
    if random_rate is not None:
        entry["random"] = round(random_rate, 3)
    if mention_rate is not None:
        entry["mention"] = round(mention_rate, 3)
    data[str(group_id)] = entry if len(entry) > 1 else "hyper"
    save(data)


def _entry(group_id: str):
    return load().get(str(group_id), "normal")


def mode_of(group_id: str) -> str:
    entry = _entry(group_id)
    if isinstance(entry, dict):
        return str(entry.get("mode", "normal"))
    return str(entry)


def is_hyper(group_id: str) -> bool:
    return mode_of(group_id) == "hyper"


def is_silent(group_id: str) -> bool:
    """关闭模式：这个群里她一个字都不说（连 @ 也不回），但消息照旧记进时间线。"""
    return mode_of(group_id) == "silent"


def mention_of(group_id: str) -> float | None:
    """被叫名字时的回复概率，没设置返回 None。"""
    entry = _entry(group_id)
    if isinstance(entry, dict) and isinstance(entry.get("mention"), (int, float)):
        return float(entry["mention"])
    return None


def random_of(group_id: str) -> float | None:
    """这个群自定义的随机回复率，没有就返回 None（用默认）。"""
    entry = _entry(group_id)
    if isinstance(entry, dict) and isinstance(entry.get("random"), (int, float)):
        return float(entry["random"])
    return None


ON_RE = re.compile(
    r"^超活跃(?:模式)?"
    r"\s*(\d+(?:\.\d+)?)?\s*[%％]?"
    r"\s*(?:[，,、/]?\s*(\d+(?:\.\d+)?)\s*[%％]?)?$"
)
ON_WORDS = ("超活跃模式", "开启超活跃", "超活跃")
OFF_WORDS = ("普通模式", "正常模式", "关闭超活跃")
SILENT_WORDS = ("关闭模式", "闭嘴模式", "静音模式", "睡觉模式", "闭嘴", "别说了", "安静模式", "安静点")
WAKE_WORDS = ("打开模式", "开启模式", "醒醒", "别睡了", "开麦", "起床")
QUERY_WORDS = ("当前模式", "模式")

MODE_LABEL = {"silent": "关闭模式", "hyper": "超活跃模式", "normal": "普通模式"}

# 有人在群里发「规则」就把这段发出去。写给不懂技术的人看，别用术语。
RULE_WORDS = ("规则", "模式规则", "模式帮助")

RULE_HELP = """群里怎么让我闭嘴、怎么让我说话：

- 普通（默认）：该说就说，不该说就不说，平常就这样。
- 闭嘴：一个字都不吭，@ 我也不回。但群里说的话我都记着，喊一声就回来。

直接在这个群发这几个字就行：

  闭嘴       我立刻安静（也可以说「安静点」「别说了」）
  打开模式   恢复平常（也可以说「醒醒」「普通模式」）
  规则       再看一遍这段

谁能换：群主和管理员。普通群员说了不算，喊了我也当没听见。"""

RULE_HELP_OWNER = "（另外你能开话痨模式：发「超活跃」，或者「超活跃 30 50」调细一点）"


def _split_gid(raw: str) -> tuple[str, str | None]:
    """把「关闭模式 123456789」拆成（关闭模式, 123456789）。"""
    m = re.match(r"^(.+?)\s*(\d{6,})\s*[%％]?$", raw.strip())
    if m:
        return m.group(1).replace(" ", ""), m.group(2)
    return raw.replace(" ", ""), None


def handle_command(
    text: str,
    group_id: str | None,
    *,
    allowed: bool = False,
    owner: bool = False,
) -> str | None:
    """在群里直接说命令就能切模式。返回要给用户看的话，None 表示不是命令。

    三种模式**同级**：关闭 / 普通 / 超活跃，互相都能直接切，不用先回普通模式。
    每个命令都支持带群号（私聊里远程操作某个群）。

    权限分两级：
    - `allowed`：主人或群管理员，能切「闭嘴 / 普通」。
    - `owner`：只有主人，能开「超活跃」。管理员说超活跃一律不理。
    没资格时返回 None，当成普通聊天交给模型去顶回去——比代码生硬拒绝更像人。
    查模式、看规则不需要资格。
    """
    raw = text.strip()
    word = raw.replace(" ", "")
    body, gid_suffix = _split_gid(raw)
    gid = gid_suffix or group_id

    # ---- 「规则」：把用法发出去，谁问都给 ----
    if word in RULE_WORDS:
        return RULE_HELP + ("\n\n" + RULE_HELP_OWNER if owner else "")

    # ---- 开关类：关闭 / 打开（同级，写法完全对称）----
    for words, mode, msg in (
        (SILENT_WORDS, "silent", "行吧，闭嘴。有事叫我。"),
        (WAKE_WORDS, "normal", "醒了醒了，普通模式。"),
        (OFF_WORDS, "normal", "收，切回普通模式。"),
    ):
        if body in words or word in words:
            if not allowed:
                return None
            if not gid:
                return f"这个得在群里说，或者带上群号：{words[0]} 123456789"
            set_mode(gid, mode)
            if gid_suffix:
                return f"{gid} 已切到{MODE_LABEL[mode]}"
            return msg

    # ---- 超活跃：只有主人能开 ----
    match = ON_RE.match(raw.replace(" ", " ").strip()) or ON_RE.match(word)
    if match:
        if not owner:
            return None
        if not gid:
            return "这个得在群里说，或者带上群号：超活跃 123456789 10"
        first, second = match.group(1), match.group(2)

        def _rate(token: str) -> float:
            value = float(token)
            value = value if value <= 1 else value / 100
            return max(0.01, min(0.99, value))

        rate = _rate(first) if first else None
        mention = _rate(second) if second else None
        if rate is None and mention is None:
            set_mode(gid, "hyper")
            return (
                f"超活跃模式，开了。随机 {config.HYPER_RANDOM:.0%}，"
                f"提及 {config.GROUP_MENTION_RATE:.0%}。"
            )
        set_mode(gid, "hyper", rate, mention)
        parts = []
        if rate is not None:
            parts.append(f"随机 {rate:.0%}")
        if mention is not None:
            parts.append(f"提及 {mention:.0%}")
        return "超活跃模式，" + "，".join(parts) + "。"

    # ---- 只写「超活跃」两个字 ----
    if word in ON_WORDS:
        if not owner:
            return None
        if not gid:
            return "这个得在群里说，或者带上群号：超活跃 123456789"
        set_mode(gid, "hyper")
        return "超活跃模式，开了。"

    # ---- 查询 ----
    if word in QUERY_WORDS:
        if gid:
            mode = mode_of(gid)
            if mode == "silent":
                return "现在是关闭模式（我不说话），要听我说就喊「打开模式」"
            if mode == "hyper":
                rate = random_of(gid)
                mention = mention_of(gid)
                bits = []
                if rate is not None:
                    bits.append(f"随机 {rate:.0%}")
                if mention is not None:
                    bits.append(f"提及 {mention:.0%}")
                extra = "，" + "，".join(bits) if bits else ""
                return f"现在是超活跃模式{extra}"
            return "现在是普通模式"
        if not load():
            return "所有群都是普通模式"
        return "\n".join(f"{g}: {MODE_LABEL.get(mode_of(g), mode_of(g))}" for g in load())
    return None
