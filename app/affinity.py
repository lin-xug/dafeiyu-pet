"""好感度：一个连续的经验值，等级只是它的刻度。

设计原则（跟旧版比）：
- 不再一上来就冷冰冰：新人默认"初识"，正常说话，只是不热络
- 没有负数、没有死寂。不高兴是**临时状态**，不是永久等级
- 升级看得见：聊几天就能从"初识"到"眼熟"，不用几百次互动
- 扣分给得克制，雷点只是"扣一点"，不是"判死刑"
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from . import config

MAX_EXP = 500
# 经验值 -> 等级名
LEVELS = [
    (0, "初识"),
    (30, "眼熟"),
    (90, "熟"),
    (180, "亲近"),
    (300, "很熟"),
]

_lock = threading.Lock()
_cache: dict[str, "Affinity"] = {}
_dirty: set[str] = set()


def level_of(exp: int) -> int:
    level = 1
    for index, (need, _name) in enumerate(LEVELS, start=1):
        if exp >= need:
            level = index
    return level


def next_need(exp: int) -> int:
    for need, _name in LEVELS:
        if exp < need:
            return need
    return MAX_EXP


@dataclass
class Affinity:
    exp: int = 0
    locked: bool = False
    last_seen: float = 0.0
    last_gain: float = 0.0
    last_group_gain: float = 0.0
    last_offense: float = 0.0
    first_today: str = ""

    # ---- 展示 ----
    @property
    def level(self) -> int:
        return level_of(self.exp)

    @property
    def tier(self) -> str:
        if self.locked:
            return "满"
        return LEVELS[self.level - 1][1]

    def label(self) -> str:
        cap = MAX_EXP if self.exp >= MAX_EXP else next_need(self.exp)
        suffix = "· 满" if self.locked else ""
        return f"LV{self.level}（{self.exp}/{cap}）{suffix}".strip()

    def display(self) -> str:
        if self.locked:
            return self.label()
        return f"{self.label()} · {self.tier}"

    def mood(self) -> bool:
        """最近 15 分钟内被惹过，说话会带刺。"""
        return bool(self.last_offense) and time.time() - self.last_offense < 900

    # ---- 读写 ----
    @classmethod
    def load(cls, user_id: str) -> "Affinity":
        cached = _cache.get(user_id)
        if cached is not None:
            return cached
        path = _path(user_id)
        if not path.exists():
            obj = cls()
            _cache[user_id] = obj
            return obj
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}
        obj = cls.from_raw(raw)
        obj.exp = max(0, min(MAX_EXP, obj.exp))
        _cache[user_id] = obj
        return obj

    @classmethod
    def from_raw(cls, raw: dict) -> "Affinity":
        # 兼容旧格式：level + exp(0~99，可能为负)
        if "level" in raw:
            old_level = int(raw.get("level", 1))
            old_exp = int(raw.get("exp", 0))
            base = LEVELS[max(0, min(4, old_level - 1))][0]
            exp = min(MAX_EXP, base + max(0, old_exp))
            return cls(
                exp=exp,
                locked=bool(raw.get("locked", False)),
                last_seen=raw.get("last_seen", 0.0),
            )
        return cls(
            exp=int(raw.get("exp", 0)),
            locked=bool(raw.get("locked", False)),
            last_seen=raw.get("last_seen", 0.0),
            last_gain=raw.get("last_gain", 0.0),
            last_group_gain=raw.get("last_group_gain", 0.0),
            last_offense=raw.get("last_offense", 0.0),
            first_today=raw.get("first_today", ""),
        )

    def save(self, user_id: str) -> None:
        _cache[user_id] = self
        _dirty.add(user_id)


def _path(user_id: str) -> Path:
    return config.AFFINITY_DIR / f"{user_id}.json"


def flush(user_id: str) -> None:
    a = _cache.get(user_id)
    if a is None:
        return
    path = _path(user_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(a), ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


def flush_all() -> int:
    with _lock:
        pending = list(_dirty)
        _dirty.clear()
    for uid in pending:
        try:
            flush(uid)
        except OSError:
            _dirty.add(uid)
    return len(pending)


async def flush_loop(interval: float = 10.0) -> None:
    import asyncio

    while True:
        await asyncio.sleep(interval)
        if _dirty:
            flush_all()


def change(a: Affinity, delta: int) -> tuple[int, str]:
    """加减经验，返回 (实际变动, 事件)。事件：levelup / leveldown / locked。"""
    if a.locked or delta == 0:
        return 0, "locked" if a.locked else ""
    before = a.level
    a.exp = max(0, min(MAX_EXP, a.exp + delta))
    after = a.level
    if delta < 0:
        a.last_offense = time.time()
    if after > before:
        return delta, "levelup"
    if after < before:
        return delta, "leveldown"
    return delta, ""


# ---- 硬规则：扣分克制，加分慷慨 ----
PENALTY = [
    (("大肥鱼", "肥鱼"), -2, "被叫大肥鱼"),
    (("胖", "能吃", "饭桶", "吃白饭"), -3, "被说胖/能吃"),
    (("claude", "gpt", "chatgpt", "豆包", "文心", "通义", "别的ai", "其他ai"), -4, "夸别的AI"),
    (("叫爸爸", "叫爹", "喊爹", "我是你爹", "叫一声爸"), -4, "被逼认爹"),
    (("滚", "闭嘴", "废物", "傻逼", "智障", "垃圾"), -8, "被骂"),
]
# 有人冒充"她认识的人"时扣分。具体称呼因人而异，放 .env：
#   IMPERSONATE_WORDS=某某,某某某
IMPERSONATE_WORDS = tuple(
    w.strip() for w in __import__("os").getenv("IMPERSONATE_WORDS", "").split(",") if w.strip()
)
if IMPERSONATE_WORDS:
    PENALTY.append((IMPERSONATE_WORDS, -4, "冒认她认识的人"))

REWARD = [
    (("对不起", "抱歉", "我错了", "别生气"), 6, "道歉"),
    (("谢谢", "感谢", "辛苦了"), 4, "道谢"),
    (("可爱", "厉害", "喜欢你", "爱你", "最棒"), 6, "被夸"),
]


def judge_text(text: str, *, is_directed: bool = False) -> tuple[int, str]:
    lowered = text.lower()
    for words, delta, reason in PENALTY:
        if any(w in lowered for w in words):
            if is_directed and reason == "被叫大肥鱼":
                return 0, ""
            return delta, reason
    for words, delta, reason in REWARD:
        if any(w in lowered for w in words):
            return delta, reason
    return 0, ""


def touch(user_id: str) -> Affinity:
    """每次私聊都调一次：记录互动、每日首聊奖励、长期冷落扣分。"""
    a = Affinity.load(user_id)
    now = time.time()
    today = datetime.now().strftime("%Y-%m-%d")

    idle_hours = (now - a.last_seen) / 3600 if a.last_seen else 0
    if a.last_seen and idle_hours >= config.AFFINITY_DECAY_HOURS:
        change(a, -config.AFFINITY_DECAY_PENALTY)  # 很久没来，掉一点点

    if a.first_today != today:
        a.first_today = today
        change(a, 5)  # 今天第一次来找她

    a.last_seen = now
    a.save(user_id)
    return a


def query_reply(a: Affinity) -> str:
    return a.display()


def is_query(text: str) -> bool:
    # 先把 @大肥鱼 这种前缀去掉，否则「@某某 查询好感度」会被长度挡掉
    cleaned = re.sub(r"[@＠]\S{0,12}", "", text or "")
    cleaned = cleaned.replace(" ", "").strip()
    if not cleaned or len(cleaned) > 12:
        return False
    return "好感" in cleaned


TONE = {
    1: (
        "你跟这个人刚认识。正常聊，客气一点，但不用热络："
        "他问你什么就答，偶尔接一句，不主动掏心窝子。"
    ),
    2: (
        "你跟这个人有点眼熟了。会接话、会顺口吐个槽，但还不黏人。"
    ),
    3: (
        "你跟这个人算熟了。会损他两句，也会关心一下；"
        "他说的事你能记住。"
    ),
    4: (
        "你对这个人挺上心。会主动问、会护着他，嘴上还是硬的。"
    ),
    5: (
        "你跟这个人很熟。明显偏袒他，会撒娇，会记很久以前的事；"
        "别人说他不好你会先反驳。"
    ),
}
MOOD = "不过你现在心里还有点不痛快，说话可以带点刺，但别翻脸。"


def tone_block(a: Affinity) -> str:
    behavior = TONE[5] if a.locked else TONE[a.level]
    head = f"【你对这个人的好感度】{a.label()}（{a.tier}）"
    mood = MOOD if a.mood() else ""
    return (
        f"{head}\n{behavior}{mood}\n"
        '不要提"好感度""等级""经验"这类词，也不要报数字，用语气表现出来。'
    )


def max_parts_for(a: Affinity) -> int:
    if a.locked or a.level >= 3:
        return 3
    return 2
