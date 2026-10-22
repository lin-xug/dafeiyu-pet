"""主流程：收事件 → 取上下文 → 调模型 → 清洗分条 → 发出去。"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from datetime import datetime

from . import (
    affinity,
    billing,
    brag,
    config,
    facts,
    group,
    llm,
    memory,
    modes,
    onebot,
    persona,
    requests,
    stickers,
    tasks,
    vision,
    voice,
)

log = logging.getLogger("dafeiyu")


PRIVATE_RULES = """\
【这是私聊，不是群】
- 一对一聊天，可以比群里多说一点：一到三句，别用一两个字打发人。
- 对方问什么就正常答，答完顺口接一句也行。
- 但也不用长篇大论，聊天不是写作文。
"""


def _time_hint() -> str:
    """把当前时间塞进设定，让它有作息感。"""
    now = datetime.now()
    hour = now.hour
    if hour < 6:
        state = "凌晨，你困得不行，但还硬撑着"
    elif hour < 9:
        state = "早上，你刚醒，脑子还没转过来"
    elif hour < 12:
        state = "上午，你有点饿了"
    elif hour < 14:
        state = "中午，饭点，你已经很饿了"
    elif hour < 18:
        state = "下午，有点困"
    elif hour < 23:
        state = "晚上，你精神还行"
    else:
        state = "深夜，你困了但还想聊"
    return f"现在 {now:%H:%M}，{state}。"

CQ_RE = re.compile(r"\[CQ:[^\]]*\]")

# 出现这些词整条丢掉：宁可这轮不说话，也不要露出 AI 味
BANNED = (
    "作为一个ai",
    "作为ai",
    "我是一个ai",
    "语言模型",
    "总的来说",
    "需要注意的是",
    "综上所述",
    "希望这能帮到",
    "有什么可以帮",
    "首先，",
    "其次，",
)

MAX_PART_LEN = 120
MAX_PARTS = 3

# 模型想发表情时会在回复里写 [[表情:高兴]]
STICKER_RE = re.compile(r"\[\[\s*表情\s*[:：]?\s*([^\]]+?)\s*\]\]")
HOMOPHONES = {
    "在": "再", "再": "在", "的": "得", "得": "的", "吗": "嘛", "嘛": "吗",
    "他": "她", "她": "他", "了": "啦", "啦": "了", "那": "哪", "哪": "那",
    "是": "事", "事": "是", "做": "作", "作": "做",
}

# 消息去重：NapCat 重试会把同一条推多次
_seen: dict[str, float] = {}


def extract(event: dict) -> tuple[str, list[str]]:
    """从 OneBot 事件里取出文字和图片地址。"""
    message = event.get("message")
    if isinstance(message, str):
        return CQ_RE.sub("", message).strip(), []

    texts: list[str] = []
    images: list[str] = []
    if isinstance(message, list):
        for seg in message:
            if not isinstance(seg, dict):
                continue
            kind = seg.get("type")
            data = seg.get("data") or {}
            if kind == "text":
                texts.append(str(data.get("text") or ""))
            elif kind in ("image", "mface"):
                src = data.get("url") or data.get("file") or ""
                if src:
                    images.append(str(src))
    return "".join(texts).strip(), images


def _build_user_content(text: str, raws: list[bytes]):
    """有图就拼成多模态内容，没图就纯文本。"""
    if not raws:
        return text
    return [
        {"type": "text", "text": text or "（对方发了一张图，没说话）"},
        {"type": "image_url", "image_url": {"url": vision.to_data_uri(raws[0])}},
    ]


def humanize(text: str) -> str:
    """去掉一眼就是 AI 的痕迹。返回空串表示这条不要了。"""
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip("。.").strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(bad in lowered for bad in BANNED):
        return ""
    return text[:MAX_PART_LEN]


def split_reply(text: str, max_parts: int = MAX_PARTS) -> list[str]:
    """模型用 || 或换行分条，这里切开、清洗、限量。"""
    parts: list[str] = []
    for chunk in re.split(r"\|\|\|?|\n+", text):
        piece = humanize(chunk)
        if piece and piece not in parts:
            parts.append(piece)
    return parts[:max_parts]


_last_sticker: dict[str, float] = {}
# 模型挂了时的兜底台词，每个会话最多两分钟一次
FALLBACK_LINES = (
    "……等下 我脑子刚卡了一下",
    "？刚断片了 你再说一遍",
    "信号不太好 我这会儿有点糊",
    "嗯……我刚走神了",
)
_fallback_sent: dict[str, float] = {}


def fallback_line(key: str) -> str | None:
    if not config.LLM_FALLBACK_ENABLED:
        return None
    now = time.time()
    if now - _fallback_sent.get(key, 0.0) < config.LLM_FALLBACK_COOLDOWN:
        return None
    _fallback_sent[key] = now
    return random.choice(FALLBACK_LINES)
# 正在压缩摘要的人，防止连发两条起两个任务
_summarizing_users: set[str] = set()


def sticker_ready(key: str) -> bool:
    """同一个会话里别连着发，隔一会儿再发。"""
    return time.time() - _last_sticker.get(key, 0.0) >= config.STICKER_COOLDOWN


def mark_sticker(key: str) -> None:
    _last_sticker[key] = time.time()


def parse_reply(
    raw: str,
    context: str = "",
    max_parts: int = MAX_PARTS,
    sticker_allowed: bool = True,
) -> tuple[list[str], list[str]]:
    """把模型输出拆成「文字条目 + 表情标签」，私聊和群聊共用。"""
    tags = [t.strip() for t in STICKER_RE.findall(raw)]
    parts = split_reply(STICKER_RE.sub("", raw), max_parts)
    if not tags and sticker_allowed and random.random() < config.STICKER_CHANCE:
        guess = stickers.guess_tag(context) or stickers.guess_tag(raw)
        if guess:
            tags.append(guess)
    return parts, tags


def typing_delay(next_text: str) -> float:
    """发下一条前的等待，按"要打的那条"长度估算。

    中文聊天大概每秒 4~5 个字，再加上一点发呆的随机。
    """
    base = 0.5 + len(next_text) * 0.06
    return min(3.5, base) + random.uniform(0.15, 0.6)


def _is_duplicate(message_id: str) -> bool:
    now = time.time()
    if message_id in _seen and now - _seen[message_id] < 120:
        return True
    _seen[message_id] = now
    if len(_seen) > 500:
        for key in [k for k, ts in _seen.items() if now - ts > 600]:
            _seen.pop(key, None)
    return False


def make_typo(text: str) -> str:
    """造一个像手滑的错字：同音替换、重复一个字、或漏一个字。"""
    chars = list(text)
    if len(chars) < 2:
        return text
    mode = random.random()
    if mode < 0.4:
        idxs = [i for i, c in enumerate(chars) if c in HOMOPHONES]
        if not idxs:
            return text
        i = random.choice(idxs)
        chars[i] = HOMOPHONES[chars[i]]
    elif mode < 0.7:
        i = random.randrange(len(chars))
        chars.insert(i, chars[i])
    else:
        i = random.randrange(len(chars) - 1)
        chars.pop(i)
    return "".join(chars)


async def _send_with_typo(user_id: str, text: str) -> None:
    """偶尔先发一个错字版，过两秒撤回重发正确的。"""
    if len(text) >= 4 and random.random() < config.TYPO_CHANCE:
        typo = make_typo(text)
        if typo != text:
            mid = await onebot.send_private(user_id, typo)
            if mid is not None:
                await asyncio.sleep(random.uniform(1.2, 2.4))
                if await onebot.recall(mid):
                    await asyncio.sleep(random.uniform(0.3, 0.8))
    await onebot.send_private(user_id, text)


SUMMARY_PROMPT = """\
把下面这段对话压缩成不超过 5 条要点，留着以后回忆这个人。只记这几类：
- 这个人是谁、做什么的
- 他明确说过的事实（名字、住哪、养了什么、工作、喜好）
- 他在意的事、烦的事
- 你们之间说好的事

不要客套，不要写"用户情绪良好"这种废话。每条一行，用 - 开头。
和旧摘要重复的就合并，不要重复列。

【已有摘要】
{old}

【新对话】
{dialog}
"""


async def _maybe_summarize(user_id: str) -> None:
    """原话攒够了就把早的那部分压成摘要，压缩完删掉原话。"""
    if not config.SUMMARY_ENABLED or config.MOCK_LLM:
        _summarizing_users.discard(user_id)
        return
    try:
        if not memory.summary_stale(user_id):
            return
        messages = memory.load(user_id)
        keep = max(2, config.SUMMARY_KEEP)
        older, newer = messages[:-keep], messages[-keep:]
        dialog = "\n".join(
            f"{'他' if m['role'] == 'user' else '你'}：{m['content']}" for m in older
        )
        text = await llm.chat(
            [
                {
                    "role": "user",
                    "content": SUMMARY_PROMPT.format(
                        old=memory.load_summary(user_id) or "（无）", dialog=dialog
                    ),
                }
            ],
            max_tokens=400,
            temperature=0.3,
            kind="summary",
        )
        summary = text.strip()
        if not summary:
            return
        memory.save_summary(user_id, summary[:1200])
        memory.replace(user_id, newer)
        log.info("记忆压缩完成 %s：%d 条原话 -> 摘要", user_id, len(older))
    except Exception:  # noqa: BLE001 - 压缩失败不能影响聊天
        log.exception("记忆压缩失败")
    finally:
        _summarizing_users.discard(user_id)


async def _can_manage(user_id: str) -> bool:
    """谁能处理好友/入群申请。配了 OWNER_IDS 就只认那些号，没配就退回"特殊关系"。"""
    owners = requests.owners()
    if owners:
        return str(user_id) in owners
    return persona.is_special(user_id)


async def _handle_request(event: dict) -> None:
    """有人申请加好友 / 进群。存下 flag 并私信主人，等他下命令。"""
    item = requests.ingest(event)
    if not item:
        return
    log.info("收到申请：%s %s", item.get("kind"), item.get("user_id"))
    owners = requests.owners()
    if not owners:
        log.warning("没配 OWNER_IDS，申请通知发不出去")
        return
    for owner in owners:
        await onebot.send_private(owner, requests.notify_text(item))


async def handle_event(event: dict) -> None:
    if event.get("post_type") == "request":
        await _handle_request(event)
        return
    if event.get("post_type") != "message":
        return
    kind = event.get("message_type")
    if kind not in ("private", "group"):
        return

    user_id = str(event.get("user_id") or "")
    self_id = str(event.get("self_id") or "")
    if not user_id or user_id == self_id:
        return

    message_id = str(event.get("message_id") or "")
    if message_id and _is_duplicate(message_id):
        log.info("跳过重复消息 %s", message_id)
        return

    if kind == "group":
        await group.handle(event)
        return

    text, images = extract(event)
    if not text and not images:
        log.info("跳过：既没有文字也没有图")
        return

    log.info("收到 %s：%s%s", user_id, text, f" [图 x{len(images)}]" if images else "")

    if persona.is_special(user_id):
        mode_reply = modes.handle_command(text, None)
        if mode_reply:
            await onebot.send_private(user_id, mode_reply)
            return

    # 余额 / 用量：只有主人能查，命中就走这里，不打扰模型
    bill_reply = await billing.handle(text, user_id)
    if bill_reply:
        await onebot.send_private(user_id, bill_reply)
        return

    # ---- 主人命令：推销一下自己 ----
    brag_target = None
    if text and persona.is_special(user_id):
        tgt = brag.match(text, loose=True)
        if tgt:
            if not config.GITHUB_URL:
                await onebot.send_private(user_id, "还没配 GITHUB_URL，我不知道该推销什么")
                return
            brag_target = tgt
            log.info("主人命令推销自己 -> %s", tgt)

    # ---- 好友 / 入群申请：先于模型，命中就走这里 ----
    if text:
        req_reply = await requests.handle_command(text, user_id, await _can_manage(user_id))
        if req_reply:
            await onebot.send_private(user_id, req_reply)
            return

    # ---- 记一句，攒够就抽事实 ----
    facts.record_utterance(user_id, text)
    if facts.needs_extract(user_id) and not config.MOCK_LLM and facts.try_start(user_id):
        tasks.spawn(facts.extract(user_id, llm.chat, source="private"), name=f"fact:{user_id}")

    # ---- 好感度 ----
    aff = affinity.touch(user_id) if config.AFFINITY_ENABLED else affinity.Affinity(exp=150)
    if affinity.is_query(text):
        reply = affinity.query_reply(aff) if config.AFFINITY_ENABLED else "这个功能关着呢"
        await onebot.send_private(user_id, reply)
        return
    aff_line = affinity.tone_block(aff)

    # 先把图拿下来：既给模型看，也顺手存进表情库（只收特殊关系的人发的）
    image_raws: list[bytes] = []
    for src in images[:3]:
        raw = await vision.fetch_bytes(src)
        if raw:
            image_raws.append(raw)
    if config.COLLECT_IMAGES and image_raws and persona.is_special(user_id):
        saved = [p for raw in image_raws if (p := stickers.save_incoming(raw))]
        if saved:
            log.info("收了 %d 张图进 杂图/：%s", len(saved), ", ".join(p.name for p in saved))

    # 时间感 + 长期摘要 + 这个人的特殊关系（只有本人能看到）
    summary = memory.load_summary(user_id)
    extra_parts = [_time_hint(), persona.relation_hint(user_id), aff_line, facts.block(user_id, query=text)]
    if summary:
        extra_parts.append("【你记得关于这个人的事】\n" + summary)

    # 先判是不是在让她出声：要在叫模型之前就说清楚，否则它会顺着上一轮"我没声"继续说
    speak_now = voice.commanded(text, user_id)
    if speak_now:
        extra_parts.append(
            "【这条你会用语音说话】\n"
            "对方在让你出声。你接下来这句话会被直接转成语音发出去（就是你自己的声音）。\n"
            "所以：正常把话讲出来就行，不要提「语音」「发不出声」「我是文字」这类事，也不用解释。"
        )
    # GitHub 那段由代码控制频率，冷却没到就压根不塞进去；
    # 主人下命令时不看冷却，强制让它说
    github_extra = "" if brag_target else brag.private_block(user_id)
    if brag_target:
        where = "私聊" if brag_target == "self" else f"马上要发到群 {brag_target}"
        extra_parts.append(brag.force_block(where))
    if github_extra:
        extra_parts.append(github_extra)
    extra = "\n".join(part for part in extra_parts if part)

    system_prompt = persona.build_system_prompt(
        extra, sticker_tags=stickers.tag_names()
    ) + "\n\n" + persona.private_rules()

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(memory.recent(user_id))
    messages.append({"role": "user", "content": _build_user_content(text, image_raws)})

    try:
        reply = await llm.chat(messages)
    except llm.LLMError as exc:
        log.error("模型调用失败：%s", exc)
        line = fallback_line(f"p:{user_id}")
        if line:
            await onebot.send_private(user_id, line)
        return

    sticker_tags, parts = [], []
    parts, sticker_tags = parse_reply(
        reply,
        context=text,
        max_parts=affinity.max_parts_for(aff),
        sticker_allowed=sticker_ready(user_id),
    )
    if not parts and not sticker_tags:
        log.warning("清洗后没剩下内容，原始输出：%r", reply)
        return

    # 偶尔把整条回复改成一语音——被明确命令时一定念，否则按 VOICE_CHANCE 掷骰子
    spoke = False
    joined = " ".join(parts)
    if parts and voice.should_speak(joined, user_text=text, user_id=user_id):
        clip = await voice.synth(joined)
        if clip is not None:
            spoke = await onebot.send_private_record(user_id, clip) is not None
            if spoke:
                log.info("这条改用语音念了（%d 字，%s）", len(voice.clean(joined)),
                         "被命令" if voice.commanded(text, user_id) else "随机")

    if not spoke:
        to_group = brag_target if brag_target and brag_target != "self" else None
        for index, part in enumerate(parts):
            if index:
                await asyncio.sleep(typing_delay(parts[index]))
            if to_group:
                await onebot.send_group(to_group, part)
            else:
                await _send_with_typo(user_id, part)
        if to_group:
            await onebot.send_private(user_id, f"发过去了，群 {to_group}")

        for tag in [] if to_group else sticker_tags[:1]:
            path = stickers.path_for(tag)
            if path is None:
                log.warning("没有这个表情：%s（可用：%s）", tag, "、".join(stickers.tag_names()))
                continue
            await asyncio.sleep(random.uniform(0.4, 1.2))
            await onebot.send_private_image(user_id, stickers.prepare(path))
            mark_sticker(user_id)

    memory.append(user_id, "user", text or "[图片]")
    memory.append(user_id, "assistant", " ".join(parts) or "[表情]")
    if memory.summary_stale(user_id) and user_id not in _summarizing_users:
        _summarizing_users.add(user_id)
        tasks.spawn(_maybe_summarize(user_id), name=f"sum:{user_id}")

    # ---- 结算好感度 ----
    delta, reason = affinity.judge_text(text)
    if delta == 0 and time.time() - aff.last_gain > config.AFFINITY_GAIN_COOLDOWN:
        delta = 2  # 平常聊两句也算陪着
        reason = reason or "陪伴"
    if delta and config.AFFINITY_ENABLED:
        applied, event = affinity.change(aff, delta)
        if applied:
            if applied > 0:
                aff.last_gain = time.time()
            aff.save(user_id)
            log.info("好感度 %s %+d（%s）-> %s %s", user_id, applied, reason, aff.label(), event)
