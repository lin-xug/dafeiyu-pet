"""调试台：按真实链路拼出提示词，可选是否真的调用模型。

只读，不写记忆、不发消息。
"""

from __future__ import annotations

import time

from . import affinity, facts, group, llm, memory, modes, persona, stickers


def _sticker_tags() -> list[str]:
    return stickers.tag_names()


async def preview_private(user_id: str, text: str, call_model: bool = True) -> dict:
    aff = affinity.Affinity.load(user_id)
    summary = memory.load_summary(user_id)
    extra = "\n".join(
        part
        for part in (
            group._time_text(),
            persona.relation_hint(user_id),
            affinity.tone_block(aff),
            facts.block(user_id, query=text),
            ("【你记得关于这个人的事】\n" + summary) if summary else "",
        )
        if part
    )
    system = (
        persona.build_system_prompt(extra, sticker_tags=_sticker_tags())
        + "\n\n"
        + persona.private_rules()
    )
    history = memory.recent(user_id)
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": text}]

    result = {
        "kind": "private",
        "target": user_id,
        "affinity": aff.display(),
        "history": history,
        "system": system,
        "max_parts": affinity.max_parts_for(aff),
        "reply": None,
        "parts": [],
        "stickers": [],
        "error": None,
        "elapsed": 0.0,
    }
    if call_model:
        started = time.time()
        try:
            raw = await llm.chat(messages, kind="preview")
            import app.bot as bot

            parts, tags = bot.parse_reply(raw, context=text, max_parts=affinity.max_parts_for(aff))
            result.update(reply=raw, parts=parts, stickers=tags)
        except llm.LLMError as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        result["elapsed"] = round(time.time() - started, 2)
    return result


async def preview_group(group_id: str, user_id: str, nick: str, text: str, call_model: bool = True) -> dict:
    conf = group.settings(group_id)
    st = group.state_of(group_id)
    aff = affinity.Affinity.load(user_id)

    state_before = list(st.timeline)
    st.timeline.append(
        {"user_id": user_id, "nick": nick or user_id, "text": text, "has_image": False, "is_bot": False, "ts": time.time()}
    )
    name_hit = any(w in text.lower() for w in group.NAME_WORDS)
    directed = name_hit
    score = group.score_message(
        text=text,
        is_at=False,
        has_image=False,
        state=st,
        cooldown=conf["cooldown"],
        random_chance=conf["random"],
        name_ok=True,
    )
    hourly_left, daily_left = group._quota_left(st, conf)
    quiet = group._quiet_now()
    would_speak = directed or (score >= conf["threshold"] and not quiet and hourly_left > 0 and daily_left > 0)

    extra = "\n".join(
        p
        for p in (
            group._time_text(),
            persona.relation_hint(user_id) if persona.is_special(user_id) else "",
            affinity.tone_block(aff),
            facts.block(user_id, query=text),
        )
        if p
    )
    system = (
        persona.build_system_prompt(extra, sticker_tags=_sticker_tags())
        + "\n\n"
        + persona.group_rules()
    )
    kb = group._knowledge_block(conf.get("kb"))
    if kb:
        system += "\n\n" + kb

    prompt_text = (
        f"【最近的群聊】\n{group._transcript(st)}\n\n"
        f"{group._group_memory_block(group_id, query=text)}\n\n"
        f"{group._person_block(group_id, user_id, nick)}\n\n"
        f"【现在轮到你】\n（分数 {score:.2f}，{'被叫到名字' if directed else '自己判断要不要接'}）"
    )
    # 预览不留痕
    st.timeline = type(st.timeline)(state_before, maxlen=group.TL_MAX)

    result = {
        "kind": "group",
        "target": group_id,
        "affinity": aff.display(),
        "score": round(score, 2),
        "threshold": conf["threshold"],
        "directed": directed,
        "quiet": quiet,
        "quota": f"{hourly_left}/{daily_left}",
        "mode": modes.mode_of(group_id),
        "would_speak": would_speak,
        "system": system,
        "prompt": prompt_text,
        "reply": None,
        "parts": [],
        "stickers": [],
        "error": None,
        "elapsed": 0.0,
    }
    if call_model and would_speak:
        started = time.time()
        try:
            raw = await llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": prompt_text}],
                kind="preview",
            )
            import app.bot as bot

            parts, tags = bot.parse_reply(raw, context=text, max_parts=affinity.max_parts_for(aff))
            result.update(reply=raw, parts=parts, stickers=tags)
        except llm.LLMError as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        result["elapsed"] = round(time.time() - started, 2)
    return result
