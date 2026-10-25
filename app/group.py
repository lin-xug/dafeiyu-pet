"""群聊：判断什么时候该说话，以及怎么接。

思路是"规则先筛，模型后判"：
1. 每条群消息都进时间线（哪怕不说话，也要知道大家在聊什么）
2. 用打分表算一个分数，不够就直接沉默，不花模型的钱
3. 分数够了才叫模型，模型还可以选择 [[沉默]]
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import (
    affinity,
    billing,
    brag,
    config,
    facts,
    gmemory,
    llm,
    modes,
    onebot,
    persona,
    playbook,
    stickers,
    tasks,
    vision,
    voice,
)

log = logging.getLogger("dafeiyu")

TL_MAX = 40            # 时间线最多记多少条
NAME_WORDS = ("小鲸鱼", "deepseek", "大肥鱼", "肥鱼", "鲸鱼")
LAUGH_WORDS = ("哈哈", "草", "乐", "笑死", "绷不住", "难绷", "蚌埠")
QUESTION_RE = re.compile(r"[?？]|吗|呢|什么|怎么|为啥|为什么|谁|哪|多少|是不是")
SILENCE_RE = re.compile(r"\[\[\s*沉默\s*\]\]")
AT_RE = re.compile(r"\[CQ:at,qq=(\d+)\]")


@dataclass
class GroupState:
    timeline: deque = field(default_factory=lambda: deque(maxlen=TL_MAX))
    last_spoke: float = 0.0
    hour_marks: deque = field(default_factory=deque)
    direct_marks: deque = field(default_factory=deque)
    day_key: str = ""
    day_count: int = 0


_states: dict[str, GroupState] = {}
# 距离下次整理还有多少条（重启后从日志重新算）
_pending_count: dict[str, int] = {}
# 正在压缩的群，防止重复跑
_summarizing: set[str] = set()
_override_cache: tuple[float, dict] | None = None


def load_overrides() -> dict:
    """读 groups.json，按修改时间缓存。"""
    global _override_cache
    path = config.GROUP_CONFIG_PATH
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _override_cache and _override_cache[0] == mtime:
        return _override_cache[1]
    try:
        import json

        data = json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    _override_cache = (mtime, data)
    return data


def settings(group_id: str) -> dict:
    """这个群的实际参数：groups.json 覆盖 .env 默认值。"""
    base = {
        "threshold": config.GROUP_THRESHOLD,
        "hourly": config.GROUP_HOURLY_LIMIT,
        "daily": config.GROUP_DAILY_LIMIT,
        "cooldown": config.GROUP_COOLDOWN,
        "random": config.GROUP_RANDOM_CHANCE,
        "direct_hourly": config.GROUP_DIRECT_HOURLY,
        "direct_cooldown": config.GROUP_DIRECT_COOLDOWN,
        "mention": config.GROUP_MENTION_RATE,
        "kb": None,
    }
    overrides = load_overrides()
    merged = dict(base)
    for scope in ("default", group_id):
        conf = overrides.get(scope) or {}
        if isinstance(conf, dict):
            merged.update({k: conf[k] for k in base if k in conf})

    if modes.is_hyper(group_id):
        custom_mention = modes.mention_of(group_id)
        if custom_mention is not None:
            merged["mention"] = custom_mention
        custom = modes.random_of(group_id)
        merged.update(
            {
                "random": custom if custom is not None else config.HYPER_RANDOM,
                "cooldown": config.HYPER_COOLDOWN,
                "hourly": config.HYPER_HOURLY,
                "daily": config.HYPER_DAILY,
            }
        )
    return merged


def _load_timeline(group_id: str, limit: int = TL_MAX) -> list[dict]:
    """从群日志里捞最近几条，重启后接着聊。"""
    import json as _json

    path: Path = config.GROUP_LOG_DIR / f"{group_id}.jsonl"
    if not path.exists():
        return []
    tail: deque = deque(maxlen=limit)
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                tail.append(line)
    except OSError:
        return []
    items = []
    for line in tail:
        try:
            item = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("text") is not None:
            items.append(item)
    return items


def state_of(group_id: str) -> GroupState:
    if group_id not in _states:
        state = GroupState()
        for item in _load_timeline(group_id):
            state.timeline.append(item)
        if state.timeline:
            bot_ts = [m.get("ts") or 0 for m in state.timeline if m.get("is_bot")]
            state.last_spoke = max(bot_ts) if bot_ts else 0.0
            log.info("恢复群 %s 的历史 %d 条", group_id, len(state.timeline))
        _states[group_id] = state
    return _states[group_id]


def _roll_day(state: GroupState) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    if state.day_key != today:
        state.day_key = today
        state.day_count = 0


def _quota_left(state: GroupState, conf: dict) -> tuple[int, int]:
    _roll_day(state)
    now = time.time()
    while state.hour_marks and now - state.hour_marks[0] > 3600:
        state.hour_marks.popleft()
    return int(conf["hourly"]) - len(state.hour_marks), int(conf["daily"]) - state.day_count


def _direct_left(state: GroupState, conf: dict) -> int:
    now = time.time()
    while state.direct_marks and now - state.direct_marks[0] > 3600:
        state.direct_marks.popleft()
    return int(conf["direct_hourly"]) - len(state.direct_marks)


def _quiet_now() -> bool:
    hour = datetime.now().hour
    start, end = config.GROUP_QUIET_START, config.GROUP_QUIET_END
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end  # 跨午夜


def _append_log(group_id: str, item: dict) -> None:
    path: Path = config.GROUP_LOG_DIR / f"{group_id}.jsonl"
    try:
        import json

        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        if path.stat().st_size > 5 * 1024 * 1024:  # 超过 5MB 只留最近 5000 行
            lines = path.read_text("utf-8").splitlines()[-5000:]
            path.write_text("\n".join(lines) + "\n", "utf-8")
    except OSError as exc:
        log.warning("写群聊日志失败：%s", exc)


def score_message(
    *,
    text: str,
    is_at: bool,
    has_image: bool,
    state: GroupState,
    cooldown: float | None = None,
    random_chance: float | None = None,
    name_ok: bool = True,
) -> float:
    """打分：过阈值才考虑开口。"""
    score = 0.0
    if is_at:
        return 1.0
    lowered = text.lower()
    if name_ok and any(w in lowered for w in NAME_WORDS):
        score += 0.85
    if QUESTION_RE.search(text):
        score += 0.45
    if has_image:
        score += 0.30
    if any(w in text for w in LAUGH_WORDS):
        score += 0.20
    if state.last_spoke and time.time() - state.last_spoke > 40 * 60:
        score += 0.30
    # 少量主动：偶尔她自己想插一句，能单独把普通消息顶过阈值
    if random.random() < (config.GROUP_RANDOM_CHANCE if random_chance is None else random_chance):
        score += config.GROUP_RANDOM_BONUS
    # 减分
    if state.last_spoke and time.time() - state.last_spoke < (cooldown or config.GROUP_COOLDOWN):
        score -= 1.0
    if state.timeline and state.timeline[-1].get("is_bot"):
        score -= 0.6
    return score


def _knowledge_block(kb: str | None) -> str:
    """按群挂一份背景资料，只在能接上话的时候给她垫底。"""
    if not kb:
        return ""
    path = config.KB_DIR / f"{kb}.md"
    try:
        text = path.read_text("utf-8").strip()
    except OSError:
        return ""
    if not text:
        return ""
    return f"【你本来就懂的背景（{kb}）】\n{text[:1500]}"


def _group_memory_block(group_id: str, query: str = "") -> str:
    # 老版本留下的整段摘要，第一次访问时搬进手册
    if not playbook.load(group_id)["items"]:
        playbook.seed_from_summary(group_id, gmemory.group_summary(group_id))
    return playbook.render(group_id, query=query)


def _person_block(group_id: str, user_id: str, nick: str) -> str:
    """这个人在这个群说过的话 + 长期摘要。"""
    parts = []
    lines = gmemory.recent(group_id, user_id, 10)
    if lines:
        parts.append(f"【{nick} 最近在这个群说过】\n" + "\n".join(f"- {t}" for t in lines))
    psum = gmemory.summary(group_id, user_id)
    if psum:
        parts.append(f"【你对 {nick} 的了解】\n{psum}")
    return "\n\n".join(parts)


def _transcript(state: GroupState) -> str:
    lines = []
    for m in state.timeline:
        who = "你" if m.get("is_bot") else (m.get("nick") or m.get("user_id"))
        text = m.get("text") or ""
        if m.get("has_image") and not text:
            text = "（发了张图）"
        lines.append(f"{who}：{text}")
    return "\n".join(lines)


GROUP_RULES = """\
【这是群聊，不是私聊】
- 群里还有别人在说话，不是每句都冲你来。
- 没人叫你也可以接一句，但只挑你真的有话可说的，别每条都接。
- 不想说话就只输出 [[沉默]]，不要解释原因。
- 群里发言比私聊更短，最多两行，不用 @ 谁，也不用引用。
- 别人叫你"大肥鱼"照样炸毛，但别在群里长篇大论。
"""


def _is_at(event: dict, self_id: str) -> bool:
    """@ 检测：raw_message 里有 CQ 码，或者消息段里有 at。"""
    raw = str(event.get("raw_message") or "")
    if self_id in AT_RE.findall(raw):
        return True
    message = event.get("message")
    if isinstance(message, list):
        for seg in message:
            if not isinstance(seg, dict) or seg.get("type") != "at":
                continue
            if str((seg.get("data") or {}).get("qq")) == self_id:
                return True
    return False


async def handle(event: dict) -> None:
    group_id = str(event.get("group_id") or "")
    user_id = str(event.get("user_id") or "")
    self_id = str(event.get("self_id") or "")
    if not group_id or not user_id or user_id == self_id:
        return
    allowed = config.groups()  # 热加载：改 .env 的 GROUPS 不用重启
    if not allowed or group_id not in allowed:
        return

    from .bot import extract  # 避免循环导入

    text, images = extract(event)

    sender = event.get("sender") or {}
    nick = sender.get("card") or sender.get("nickname") or user_id
    is_at = _is_at(event, self_id)

    # 有的客户端把 @ 当普通文字发过来（日志里会出现 "@大肥鱼 查询好感度" 这种），
    # 这种也当 @ 处理，否则那条消息既不算直呼、查询好感也会被挡掉
    if not is_at and text and re.search(r"[@＠]", text) and any(w in text.lower() for w in NAME_WORDS):
        is_at = True
        log.info("群 %s 把文本里的 @ 当成了真的 @：%s", group_id, text[:20])

    # 光 @ 不说话也得应一声，不然像没听见。补个占位让后面的流程照常走。
    bare_at = is_at and not text and not images
    if bare_at:
        text = "（只@了你，一个字没说）"
    elif not text and not images:
        return

    # 模式命令优先处理：哪怕现在是「关闭模式」，也得让她能听见「打开模式」
    if persona.is_special(user_id):
        reply = modes.handle_command(text, group_id)
        if reply:
            await onebot.send_group(group_id, reply)
            return

    # 关闭模式：照旧记进时间线（醒来能接上话），但一个字都不回，@ 也不回
    if modes.is_silent(group_id):
        rec = {
            "user_id": user_id,
            "nick": nick,
            "text": text,
            "has_image": bool(images),
            "is_bot": False,
            "ts": time.time(),
        }
        st_silent = state_of(group_id)
        st_silent.timeline.append(rec)
        _append_log(group_id, rec)
        gmemory.record(group_id, user_id, nick, text or "[图片]")
        log.info("群 %s 关闭模式，只记录不回复：%s", group_id, (text or "[图]")[:20])
        return

    # 主人命令：推销一下自己（群里必须 @ 才认，别人喊不动它打广告）
    brag_cmd = False
    # 主人自己 @ 的可以偷懒不点名"自己"（"推销一下"也认），别人一律按 strict 判
    if is_at and text and persona.is_special(user_id) and brag.match(text, loose=True):
        if not config.GITHUB_URL:
            await onebot.send_group(group_id, "没配 GitHub 链接，我推销不了")
            return
        if brag.cmd_take(group_id):
            brag_cmd = True
            log.info("群 %s 主人命令推销自己", group_id)
        else:
            # 别让主人觉得"喊了没反应"
            await onebot.send_group(group_id, "刚推销过了，过会儿再说吧")
            return

    # touch 会顺便处理"太久没说话"的冷落扣分
    aff = affinity.touch(user_id) if config.AFFINITY_ENABLED else affinity.Affinity(exp=150)

    # 好感查询要 @ 才理，避免群里随便一句"好感"就被刷
    if is_at and affinity.is_query(text):
        reply = affinity.query_reply(aff) if config.AFFINITY_ENABLED else "这个功能关着呢"
        await onebot.send_group(group_id, reply, at_user=user_id)
        return

    # 余额 / 用量：同样要 @，而且只有主人能查
    if is_at:
        bill_reply = await billing.handle(text, user_id)
        if bill_reply:
            await onebot.send_group(group_id, bill_reply, at_user=user_id)
            return

    # 硬规则先结算：被人骂了即使她没回，也算数
    delta0, reason0 = affinity.judge_text(text, is_directed=is_at)
    if delta0 and config.AFFINITY_ENABLED:
        applied0, _ev = affinity.change(aff, delta0)
        if applied0:
            aff.save(user_id)
            log.info("群内好感度 %s %+d（%s）-> %s", user_id, applied0, reason0, aff.label())

    conf = settings(group_id)
    st = state_of(group_id)
    entry = {
        "user_id": user_id,
        "nick": nick,
        "text": text,
        "has_image": bool(images),
        "is_bot": False,
        "ts": time.time(),
    }
    st.timeline.append(entry)
    _append_log(group_id, entry)
    gmemory.record(group_id, user_id, nick, text or "[图片]")
    if not bare_at:  # 「只@了你」是补的占位，不是他真说的话，别喂给事实库
        facts.record_utterance(user_id, text)
    if facts.needs_extract(user_id) and not config.MOCK_LLM and facts.try_start(user_id):
        tasks.spawn(facts.extract(user_id, llm.chat, source=f"group:{group_id}"), name=f"fact:{user_id}")

    # 攒够了就让模型整理一次群记忆（重启后从日志里补算，不会丢）
    if group_id not in _pending_count:
        _pending_count[group_id] = _count_since(group_id, playbook.last_ts(group_id))
    _pending_count[group_id] += 1
    if _pending_count[group_id] >= config.GROUP_SUMMARY_TRIGGER and group_id not in _summarizing:
        _pending_count[group_id] = 0
        _summarizing.add(group_id)  # 先占坑，避免连发几条时起两个任务
        tasks.spawn(_curate_playbook(group_id), name=f"pbook:{group_id}")

    # @ 百分百回；叫到名字按配置的概率
    name_mentioned = any(w in text.lower() for w in NAME_WORDS)
    mention_ok = True
    if name_mentioned and not is_at:
        mention_ok = random.random() < conf["mention"]
        if not mention_ok:
            log.info("群 %s 被叫到但这次不接（提及概率 %.0f%%）", group_id, conf["mention"] * 100)
    directed = is_at or (name_mentioned and mention_ok)

    score = score_message(
        text=text,
        is_at=is_at,
        has_image=bool(images),
        state=st,
        cooldown=conf["cooldown"],
        random_chance=conf["random"],
        name_ok=mention_ok,
    )
    if not directed:
        # 好感度低，就算分数够了也更容易沉默
        if aff.mood():
            score -= 0.25   # 刚被惹毛，不太想理
        elif aff.level <= 1:
            score -= 0.10   # 不熟，话少一点

    if directed:
        score = max(score, 1.0)

    log.info(
        "群 %s [%s] %s（分 %.2f%s）",
        group_id,
        nick,
        text or "[图]",
        score,
        " 直呼" if directed else "",
    )

    if brag_cmd:
        # 主人亲自喊的：不占配额、不看冷却、不管是不是深夜——喊了就必须说
        pass
    elif directed:
        # 被点名就走宽松额度：不看静音、不占日常配额，只防刷屏
        if _direct_left(st, conf) <= 0:
            log.info("群 %s 被点名次数到顶（每小时 %d 次），这轮不回", group_id, conf["direct_hourly"])
            return
        if time.time() - st.last_spoke < conf["direct_cooldown"]:
            log.info("群 %s 刚说完 %.0f 秒，稍微等一下", group_id, time.time() - st.last_spoke)
            return
    else:
        hourly_left, daily_left = _quota_left(st, conf)
        if score < conf["threshold"]:
            log.debug("群 %s 沉默：分数 %.2f < %.2f", group_id, score, conf["threshold"])
            return
        if _quiet_now():
            log.info("群 %s 深夜静音，不接", group_id)
            return
        if hourly_left <= 0 or daily_left <= 0:
            log.info("群 %s 配额用完（时 %d / 日 %d），不接", group_id, hourly_left, daily_left)
            return

    # 走到这里才花模型的钱
    extra = "\n".join(
        p
        for p in (
            _time_text(),
            persona.relation_hint(user_id),
            affinity.tone_block(aff),
            facts.block(user_id),
            "" if brag_cmd else brag.group_block(group_id),  # 冷却没到就不出现
            brag.force_block("群里，当着大家的面") if brag_cmd else "",
        )
        if p
    )
    system = persona.build_system_prompt(extra, sticker_tags=stickers.tag_names()) + "\n\n" + persona.group_rules()
    kb_block = _knowledge_block(conf.get("kb"))
    if kb_block:
        system += "\n\n" + kb_block

    prompt_text = (
        f"【最近的群聊】\n{_transcript(st)}\n\n"
        f"{_group_memory_block(group_id)}\n\n"
        f"{_person_block(group_id, user_id, nick)}\n\n"
        f"【现在轮到你】\n（分数 {score:.2f}，"
        f"{'被点名' if is_at else '自己判断要不要接'}）"
    )
    content: object = prompt_text
    if images:  # 这条消息带图，才让模型看
        raw = await vision.fetch_bytes(images[0])
        if raw:
            content = [
                {"type": "text", "text": prompt_text},
                {"type": "image_url", "image_url": {"url": vision.to_data_uri(raw)}},
            ]

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]

    try:
        reply = await llm.chat(messages)
    except llm.LLMError as exc:
        log.error("群聊模型调用失败：%s", exc)
        if directed:
            from .bot import fallback_line

            line = fallback_line(f"g:{group_id}")
            if line:
                await onebot.send_group(group_id, line)
        return

    if not reply.strip() or SILENCE_RE.search(reply):
        log.info("群 %s 模型选择沉默", group_id)
        return

    from .bot import parse_reply  # 复用分条/表情逻辑

    from .bot import sticker_ready

    parts, tags = parse_reply(
        reply,
        context=text,
        max_parts=affinity.max_parts_for(aff),
        sticker_allowed=sticker_ready(group_id),
    )
    if not parts and not tags:
        return

    # 群里发语音的前提是「@ 她 + 语音关键词」，两个都满足才念
    spoke = False
    joined = " ".join(parts)
    if is_at and voice.commanded(text, user_id, is_at=True):
        clip = await voice.synth(joined)
        if clip is not None:
            spoke = await onebot.send_group_record(group_id, clip) is not None
            if spoke:
                log.info("群里改用语音念了（%d 字）", len(voice.clean(joined)))

    if not spoke:
        for index, part in enumerate(parts):
            if index:
                from .bot import typing_delay

                await asyncio.sleep(typing_delay(parts[index]))
            await onebot.send_group(group_id, part)

    st.timeline.append(
        {"user_id": self_id, "nick": "你", "text": " ".join(parts), "is_bot": True, "ts": time.time()}
    )
    for tag in ([] if spoke else tags[:1]):
        path = stickers.path_for(tag)
        if path is None:
            continue
        await asyncio.sleep(random.uniform(0.4, 1.2))
        await onebot.send_group_image(group_id, stickers.prepare(path))
        from .bot import mark_sticker

        mark_sticker(group_id)

    now = time.time()
    st.last_spoke = now
    if directed:
        st.direct_marks.append(now)
        # 被点名陪聊也算一点好感，十分钟最多一次
        if config.AFFINITY_ENABLED and now - aff.last_group_gain > 600:
            applied_g, _ev = affinity.change(aff, 1)
            if applied_g:
                aff.last_group_gain = now
                aff.save(user_id)
    else:
        st.hour_marks.append(now)
        # 只有「自己主动接话」才占日配额。
        # 被 @ 的回复本来就不受配额限制，如果照样 +1，活跃群一天被 @ 上百次
        # 会把 day_count 顶穿（日志里出现过「日 -124」），之后所有主动接话全被拦死。
        st.day_count += 1
    _append_log(group_id, {"user_id": self_id, "nick": "你", "text": " ".join(parts), "is_bot": True, "ts": now})
    log.info("群 %s 说了(%.2f)：%s", group_id, score, " / ".join(parts))
    tasks.spawn(_maybe_summarize_person(group_id, user_id, nick), name=f"psum:{group_id}:{user_id}")


PLAYBOOK_PROMPT = """\
你在维护一个 QQ 群的"记忆手册"。类型只有五种：梗 / 关系 / 坑 / 安排 / 事

只记以后还用得上的：
- 梗：群里反复出现的说法、笑点
- 关系：谁跟谁熟、谁跟谁爱互怼
- 坑：在这个群提什么会被围攻、什么话题会冷场
- 安排：约好的事（会自动过期）
- 事：发生过的重要事件

绝对不要记：
- 关于你自己的事（你是谁、你有什么设定、别人怎么测试你、你怎么回答）
- "用户让 AI 做了什么""AI 承认了什么"这类内容
- 一次性的闲聊、谁具体说了句什么、情绪

要记的是**群友之间**的事和这个群的玩法。

写法要求：
- 你就是群里的一员，用"我"指自己，**不要写"AI""机器人""模型"**
- 别人怎么逗你，要写成"他们爱逗我"，不要写成"用户在测试 AI"
- 例子：
  ADD 坑 群里爱叫我大肥鱼，一叫我就炸毛
  ADD 关系 张三总说自己是我哥，我从来不理
  ADD 梗 输了要请奶茶，这是群里定的规矩
每条不超过 40 个字。意思跟已有条目相近的，用 UPDATE 改那条，不要 ADD 重复的。
最多输出 6 条操作，没有新东西就只输出 NONE。

输出格式：
ADD 梗 输的请奶茶
UPDATE 3 张三和李四经常互怼
DELETE 7

【现有手册】
{existing}

【最近的聊天】
{dialog}
"""

OPS_RE = re.compile(r"^(ADD|UPDATE|DELETE)\s+(.+)$", re.IGNORECASE)


BLOCK_WORDS = ("认爹", "被迫", "提示词", "系统提示", "api key", "apikey", "评测")


def _blocked(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in BLOCK_WORDS)


def _apply_ops(group_id: str, raw: str) -> tuple[int, int, int]:
    added = updated = deleted = 0
    for line in raw.splitlines():
        match = OPS_RE.match(line.strip())
        if not match:
            continue
        op, rest = match.group(1).upper(), match.group(2).strip()
        if op == "ADD":
            parts = rest.split(None, 1)
            if len(parts) == 2 and parts[0] in playbook.KINDS and not _blocked(parts[1]):
                if playbook.add(group_id, parts[0], parts[1]):
                    added += 1
        elif op == "UPDATE":
            parts = rest.split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                if playbook.update(group_id, int(parts[0]), parts[1]):
                    updated += 1
        elif op == "DELETE":
            token = rest.split()[0] if rest.split() else ""
            if token.isdigit() and playbook.delete(group_id, int(token)):
                deleted += 1
    return added, updated, deleted


MERGE_PROMPT = """\
下面是一个 QQ 群的记忆手册。请把重复、过时、能合并的条目整理掉。

规则：
- 说同一件事的条目，不管类型是否相同，都合并：保留信息最全的那条的 id，UPDATE 它，
  剩下的 DELETE
- 已经过去、以后用不上的事，DELETE
- 最多 8 条操作；已经很干净就输出 NONE

例子：
id=3 [安排] 李四周五请奶茶
id=4 [事] 李四兑现了请奶茶的赌约
→ UPDATE 3 李四已兑现请奶茶的赌约
→ DELETE 4

手册：
{items}
"""


async def _merge_playbook(group_id: str) -> None:
    items = playbook.load(group_id)["items"]
    listing = "\n".join(
        f"id={i['id']} [{i['kind']}] {i['text']}" for i in items
    )
    try:
        raw = await llm.chat(
            [{"role": "user", "content": MERGE_PROMPT.format(items=listing)}],
            max_tokens=400,
            temperature=0.2,
            kind="playbook",
        )
        added, updated, deleted = _apply_ops(group_id, raw)
        if updated or deleted:
            log.info("群记忆合并 %s：~%d -%d", group_id, updated, deleted)
    except Exception:  # noqa: BLE001
        log.exception("群记忆合并失败")


def _log_entries(group_id: str, since: float, limit: int = 300) -> list[dict]:
    """从群日志里取 since 之后的消息。"""
    import json as _json

    path: Path = config.GROUP_LOG_DIR / f"{group_id}.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    item = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                if item.get("ts", 0) > since:
                    out.append(item)
    except OSError:
        return []
    return out[-limit:]


def _count_since(group_id: str, since: float) -> int:
    return len(_log_entries(group_id, since, limit=100000))


async def _curate_playbook(group_id: str) -> None:
    """把上次整理之后的新消息交给模型，让它对手册做增删改。"""
    if config.MOCK_LLM:
        _summarizing.discard(group_id)
        return
    try:
        since = playbook.last_ts(group_id)
        entries = _log_entries(group_id, since)
        if not entries:
            return
        data = playbook.load(group_id)
        existing = "\n".join(
            f"id={i['id']} [{i['kind']}] {i['text']}（用过{i.get('hits', 0)}次）"
            for i in data["items"][-40:]
        ) or "（空）"
        dialog = "\n".join(
            f"{'你' if e.get('is_bot') else e.get('nick')}：{e.get('text') or '[图]'}"
            for e in entries
        )
        raw = await llm.chat(
            [
                {
                    "role": "user",
                    "content": PLAYBOOK_PROMPT.format(existing=existing, dialog=dialog),
                }
            ],
            max_tokens=400,
            temperature=0.2,
            kind="playbook",
        )
        added, updated, deleted = _apply_ops(group_id, raw)
        data = playbook.load(group_id)
        data["rounds"] = int(data.get("rounds", 0)) + 1
        data["last_ts"] = entries[-1].get("ts", since)
        playbook.save(group_id, data)

        if data["rounds"] % 10 == 0 and len(data["items"]) >= 15:
            await _merge_playbook(group_id)
        dropped = playbook.prune(group_id)
        if added or updated or deleted or dropped:
            log.info(
                "群记忆更新 %s：+%d ~%d -%d（淘汰 %d，%d 条消息）",
                group_id, added, updated, deleted, dropped, len(entries),
            )
    except Exception:  # noqa: BLE001
        log.exception("群记忆更新失败")
    finally:
        _summarizing.discard(group_id)


PERSON_SUMMARY_PROMPT = """\
下面是「{nick}」在一个 QQ 群里的发言片段。总结一下：
- 他是个什么样的人（说话风格、性格）
- 他在意什么、常聊什么话题
- 跟群里其他人的关系（看得出才写）

最多 5 条，每条一行，用 - 开头。不要客套，不要写"用户"这种词。
和旧摘要重复的就合并。

【已有摘要】
{old}

【新的发言】
{dialog}
"""


async def _maybe_summarize_person(group_id: str, user_id: str, nick: str) -> None:
    if config.MOCK_LLM or not gmemory.needs_summary(group_id, user_id):
        return
    try:
        older, keep = gmemory.split_for_summary(group_id, user_id)
        dialog = "\n".join(f"- {m['text']}" for m in older)
        text = await llm.chat(
            [
                {
                    "role": "user",
                    "content": PERSON_SUMMARY_PROMPT.format(
                        nick=nick,
                        old=gmemory.summary(group_id, user_id) or "（无）",
                        dialog=dialog,
                    ),
                }
            ],
            max_tokens=300,
            temperature=0.3,
            kind="summary",
        )
        if text.strip():
            gmemory.save_summary(group_id, user_id, text, keep)
            log.info("群记忆压缩 %s/%s：%d 条 -> 摘要", group_id, user_id, len(older))
    except Exception:  # noqa: BLE001
        log.exception("群记忆压缩失败")


def _time_text() -> str:
    from .bot import _time_hint

    return _time_hint()
