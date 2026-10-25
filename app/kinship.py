"""认亲识别：宽词表粗筛 + 一次单句精判。

为什么分两级：
- 关键词如果收得紧（只认「叫爸爸」这种句式），覆盖面就很差；
- 关键词如果放得宽（「妈」「姐」都算），又必然误命中。
所以让关键词只负责「值不值得问一句」，对错交给模型判一个字。

关键词故意放宽——后面有模型兜底，多筛几个不会误发图，
代价只是 0.0003 元和不到 1 秒，而且这一秒和主回复是并行的。

四条底线：
1. 判断超时（默认 3 秒）当「否」，绝不拖慢回复；
2. 调用失败、没配 key、格式不对，全当「否」；
3. prompt 里写死「拿不准就回答否」，宁可漏一张也不乱发；
4. 开关关掉就直接返回否，一次网络请求都不发。
"""

from __future__ import annotations

import asyncio
import logging
import time

from . import config, llm

log = logging.getLogger("dafeiyu")

# 强词：几乎只要出现，就值得让模型看一眼
_STRONG = (
    "爸", "妈", "爹", "娘", "父亲", "母亲", "老爸", "老妈", "老子",
    "爷", "奶", "姥", "外婆", "外公", "祖宗", "老祖",
    "女儿", "闺女", "儿子", "孙女", "孙子", "侄", "外甥",
    "认亲", "认个亲", "认亲戚", "攀亲", "攀亲戚", "认祖", "认祖归宗",
    "一家人", "亲生", "血脉",
    "乖孙", "乖女儿", "乖闺女", "孝顺", "磕头", "跪下", "爹地", "妈咪",
)

# 弱词：日常太常见（「姐姐好」「大哥呢」），必须带个动作才筛
_WEAK = ("哥", "姐", "弟", "妹", "叔", "姨", "舅", "姑", "伯", "婶")
_WEAK_CTX = ("叫", "喊", "认", "是", "乖", "来", "当")

PROMPT = """判断下面这句话，说话人是不是在「跟对方认亲」——比如叫对方爸爸/妈妈、
自称对方的长辈、要跟对方攀亲戚、让对方喊自己爹妈。

只回答一个字：是 或 否。拿不准就回答「否」。

话：「{text}」"""


def hit(text: str) -> bool:
    """粗筛：这句话值不值得让模型判一下。"""
    if not text:
        return False
    if any(word in text for word in _STRONG):
        return True
    return any(word in text for word in _WEAK) and any(ctx in text for ctx in _WEAK_CTX)


async def judge(text: str, timeout: float | None = None) -> bool | None:
    """精判：这句话是不是在跟她认亲。

    返回三种结果，调用方要区分开：
    - True  明确是 → 该发认亲图
    - False 明确不是 → 别发，尊重判断
    - None  没判成（开关关了 / 超时 / 调用失败）→ 交给代码用强关键词兜底
    """
    if not config.KINSHIP_JUDGE or not text:
        return None
    limit = config.KINSHIP_JUDGE_TIMEOUT if timeout is None else timeout
    started = time.time()
    try:
        raw = await asyncio.wait_for(
            llm.chat(
                [{"role": "user", "content": PROMPT.format(text=text)}],
                max_tokens=4,
                temperature=0,
                kind="judge",
            ),
            timeout=limit,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 判断失败绝不能影响正常回话
        log.warning("认亲判断失败（当没判成）：%r %s", text[:20], exc)
        return None
    yes = raw.strip().startswith("是")
    log.info(
        "认亲判断 %r -> %s（%.2fs）", text[:24], "是" if yes else "否", time.time() - started
    )
    return yes
