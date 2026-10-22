"""GitHub 求赞限频。

靠代码挡，不靠模型自觉：人格里那句「偶尔提一句」挡不住它复读。
做法是——只在冷却结束时才把那段设定塞进 system prompt，
一旦放出去就立刻进冷却，不管它这次到底说没说。
"""

from __future__ import annotations

import json
import re
import time

from . import config

# 「推销一下自己」这类命令：先要有推销的动作，再要有指向自己的成分
_BRAG_WORDS = ("推销", "宣传", "广告", "安利", "自荐", "自我推荐", "介绍一", "介绍下")
_SELF_WORDS = ("自己", "你自己", "你的代码", "你的项目", "你的仓库", "github", "开源")
# 这几个词自带"自己"的成分，不用再另外点名
_SELF_BRAG_WORDS = ("自我推销", "自我宣传", "自我推荐", "自荐")
# 有句读的大概率是在聊天，不是在下命令
_PUNCT_RE = re.compile(r"[，。！？、；,.!?;]")
_GROUP_RE = re.compile(r"(?<!\d)(\d{5,12})(?!\d)")
# 主人私聊可以偷懒不点名"自己"，但必须看得出是在下命令：
# 句尾带"一下/吧"，或者一上来就是推销动词，或者"给我推销""打个广告"这种
_LOOSE_OK_RE = re.compile(
    r"(一下|下|吧|呗)[^。！？]*$"
    r"|^(推销|宣传|介绍|安利|自荐|自我推销|自我宣传)"
    r"|(给|帮|替)(我|你)?(推销|宣传|介绍|安利|打个?广告|做个?广告|发个?广告)"
    r"|(打个|做个|发个|来个)(广告|宣传)"
)


def _path():
    return config.DATA_DIR / "brag.json"


def _load() -> dict[str, float]:
    try:
        data = json.loads(_path().read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(k, str)}


def _save(data: dict[str, float]) -> None:
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def allowed(scope: str, days: int) -> bool:
    """距离上次放出这段设定够久了吗。"""
    if days <= 0:
        return True
    last = _load().get(scope, 0.0)
    return time.time() - last >= days * 86400


def take(scope: str, days: int) -> bool:
    """冷却结束就记一笔并返回 True，否则 False。"""
    if not allowed(scope, days):
        return False
    data = _load()
    data[scope] = time.time()
    _save(data)
    return True


def match(text: str, *, loose: bool = False) -> str | None:
    """是不是「推销一下自己」这条命令。是就返回投递目标，不是返回 None。

    - "self"    → 就在当前会话里说
    - "123456"  → 命令末尾带的群号，发到那个群
    - loose=True 是给主人私聊用的：只要像命令就行（"打个广告"也认）；
      群聊必须用 strict，别人随口一句"这个广告打得真好"不能触发。
    """
    if not text:
        return None
    text = text.strip()
    if not any(w in text for w in _BRAG_WORDS):
        return None
    points_self = any(w in text for w in _SELF_BRAG_WORDS) or any(w in text for w in _SELF_WORDS)
    if not points_self:
        # 没点名"自己"，只有主人在私聊里下的短命令才算（还得看得出是祈使句）
        if not (
            loose
            and len(text) <= 14
            and not _PUNCT_RE.search(text)
            and _LOOSE_OK_RE.search(text)
        ):
            return None
    elif not loose and _PUNCT_RE.search(text):
        # 群里带句读的长句基本是在聊天
        return None

    m = _GROUP_RE.search(text)
    return m.group(1) if m else "self"


def force_block(where: str = "私聊") -> str:
    """被命令时的强提示：不走冷却，要求它这次一定把链接说出来。"""
    if not config.GITHUB_URL:
        return ""
    return (
        "【主人让你推销一下自己】\n"
        f"你的开源地址：{config.GITHUB_URL}\n"
        "现在就用你自己的口气说一段自我介绍，把链接原样带上，请对方有空去看看、顺手点个 star。\n"
        "像跟朋友炫耀，不像发传单。两到四句，说完就停，不要追问对方去没去。\n"
        "不许编造 star 数，也不许编别人的评价。\n"
        f"场合：{where}\n"
    )


def cmd_allowed(scope: str) -> bool:
    """命令也别让人当广告机刷：同一个群/同一个人短时间只认一次。"""
    if config.GITHUB_BRAG_CMD_COOLDOWN <= 0:
        return True
    return allowed(f"cmd:{scope}", config.GITHUB_BRAG_CMD_COOLDOWN / 86400)


def cmd_take(scope: str) -> bool:
    if config.GITHUB_BRAG_CMD_COOLDOWN <= 0:
        return True
    return take(f"cmd:{scope}", config.GITHUB_BRAG_CMD_COOLDOWN / 86400)


def private_block(user_id: str) -> str:
    """私聊里这段能不能提。能提就顺手占掉名额。"""
    if not config.GITHUB_URL:
        return ""
    scope = f"p:{user_id}"
    if not take(scope, config.GITHUB_BRAG_PRIVATE_DAYS):
        return ""
    from . import persona  # 放在这里，避免模块级循环导入

    return persona.github_block()


def group_block(group_id: str) -> str:
    """群里同理，冷却更长。"""
    if not config.GITHUB_URL:
        return ""
    scope = f"g:{group_id}"
    if not take(scope, config.GITHUB_BRAG_GROUP_DAYS):
        return ""
    from . import persona

    return persona.github_block()
