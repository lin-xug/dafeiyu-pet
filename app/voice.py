"""语音：把文字合成 QQ 语音消息（阿里云 CosyVoice）。

可选功能，默认关闭。没装 dashscope、没配 DASHSCOPE_API_KEY、或开关关掉时，
一律静默退化成"不发语音"，不影响原有文字流程。

合成结果按「音色+语速+文本」哈希缓存在 data/cache/voice/，
同一句话不会重复花钱；模型句子的重复率比想象中高。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
from pathlib import Path

from . import config, usage

log = logging.getLogger("dafeiyu")

# [[表情:高兴]]、[[沉默]]、【…】 这类标记不能念出来
MARK_RE = re.compile(r"\[\[.*?\]\]|【.*?】")
URL_RE = re.compile(r"https?://\S+")
# 只留中文、字母数字和基本标点，表情符号/零宽字符全丢掉
KEEP_RE = re.compile(r"[\u4e00-\u9fffa-zA-Z0-9，。！？、…~—,.!?%]")

CACHE_DIR = config.DATA_DIR / "cache" / "voice"
# 后台试听的临时文件：不进 bot 缓存，避免污染"这句话发过没发过"
PREVIEW_DIR = config.DATA_DIR / "cache" / "voice_preview"


def clean(text: str) -> str:
    """去掉不该念的东西：标记、链接、表情符号、多余空白。"""
    text = MARK_RE.sub("", text or "")
    text = URL_RE.sub("", text)
    text = "".join(ch for ch in text if KEEP_RE.match(ch) or ch == " ")
    return re.sub(r"\s+", " ", text).strip()


def available() -> bool:
    if not config.VOICE_ENABLED or not config.DASHSCOPE_API_KEY:
        return False
    try:
        import dashscope  # noqa: F401
    except ImportError:
        return False
    return True


def wanted(text: str) -> bool:
    """这条回复要不要主动改成语音。太长不念——费钱，也不像人说话。"""
    if not available():
        return False
    text = clean(text)
    if not text or len(text) > config.VOICE_MAX_CHARS:
        return False
    return random.random() < config.VOICE_CHANCE


# 一级：这些词出现在哪儿都算"叫她出声"
CMD_RE = re.compile(
    r"(语音|声音|出声|出个声|念|读|唱|"
    r"说句话|说一句|说点话|说点儿话|说两句|说话给我听|来一句|讲一句|"
    r"让我听听|我想听你说|听听你)"
)
# 二级：说/讲/哼/背 这种单字，只在「群里 @ 她」或「短消息」时才认
CMD_LOOSE_RE = re.compile(r"(说|讲|哼|背)")


# 问句不算命令："你会说话吗""你在说什么呢"这种不是让她念
QUESTION_HINT_RE = re.compile(r"[？?]|[吗呢嘛]$|会不会|能不能|可不可以")


def _bare(text: str) -> int:
    """去掉空白和标点后的字数，用来判断"这是不是一句短命令"。"""
    return len(re.sub(r"[\s，。！？、,.!?~—…]", "", text or ""))


def commanded(user_text: str, user_id: str = "", *, is_at: bool = False) -> bool:
    """是不是在明确叫她出声。

    `VOICE_CMD_USERS` 是**白名单**：只认名单里的人，**留空 = 谁都不认**（默认拒绝）。
    判定分两级：
      1. 强词（语音/声音/念/读/唱/说句话…）出现在哪儿都算；
      2. 单字（说/讲/哼/背）只在「群里 @ 了她」或「短消息（≤8 字）」时才算——
         否则"我跟你说个事儿"这种长句会被误判。
    """
    if not available():
        return False
    if str(user_id) not in config.VOICE_CMD_USERS:
        return False
    text = user_text or ""
    if CMD_RE.search(text):
        return True
    if QUESTION_HINT_RE.search(text.strip()):
        return False
    if (is_at or _bare(text) <= 8) and CMD_LOOSE_RE.search(text):
        return True
    return False


def should_speak(reply: str, *, user_text: str = "", user_id: str = "", is_at: bool = False) -> bool:
    """最终判定：被命令就一定念，否则按概率。合集与长度限制都在这里。"""
    if not available():
        return False
    text = clean(reply)
    if not text:
        return False
    forced = commanded(user_text, user_id, is_at=is_at)
    limit = config.VOICE_CMD_MAX_CHARS if forced else config.VOICE_MAX_CHARS
    if len(text) > limit:
        return False
    return forced or random.random() < config.VOICE_CHANCE


def _synth_sync(
    text: str,
    *,
    rate: float | None = None,
    pitch: float | None = None,
    instruct: str | None = None,
) -> bytes:
    from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

    synth = SpeechSynthesizer(
        model=config.VOICE_MODEL,
        voice=config.VOICE_NAME,
        instruction=(config.VOICE_INSTRUCT if instruct is None else instruct) or None,
        speech_rate=config.VOICE_RATE if rate is None else rate,
        pitch_rate=config.VOICE_PITCH if pitch is None else pitch,
        format=AudioFormat.MP3_22050HZ_MONO_256KBPS,
    )
    return synth.call(text)


async def synth(text: str) -> Path | None:
    """合成一段语音并返回本地 mp3；任何失败都返回 None（调用方照常发文字）。"""
    text = clean(text)
    if not text:
        return None

    key = hashlib.md5(
        f"{config.VOICE_NAME}|{config.VOICE_RATE}|{config.VOICE_PITCH}|{text}".encode()
    ).hexdigest()[:16]
    out = CACHE_DIR / f"{key}.mp3"
    if out.exists() and out.stat().st_size > 1000:
        usage.record_voice(len(text), model=config.VOICE_NAME, cached=True)
        return out

    try:
        audio = await asyncio.wait_for(asyncio.to_thread(_synth_sync, text), timeout=30)
    except Exception as exc:  # noqa: BLE001 - 语音失败不该影响回复
        log.warning("语音合成失败：%s", exc)
        return None
    if not audio:
        log.warning("语音合成返回空：%s", text)
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out.write_bytes(audio)
    usage.record_voice(len(text), model=config.VOICE_NAME)
    return out


# ---------------- 后台试听 / 预设 ----------------


def configured() -> bool:
    """能不能合成（不管 VOICE_ENABLED 开没开）——后台试听要用。"""
    if not config.DASHSCOPE_API_KEY:
        return False
    try:
        import dashscope  # noqa: F401
    except ImportError:
        return False
    return True


async def preview(
    text: str,
    *,
    rate: float | None = None,
    pitch: float | None = None,
    instruct: str | None = None,
) -> Path | None:
    """临时合成一段给后台试听：**不读也不写 bot 的缓存**，单独放 voice_preview/。

    参数留空就用当前配置。花了钱就照实记账（`kind=voice`），跟真实发送一视同仁。
    """
    text = clean(text)
    if not text or not configured():
        return None
    key = hashlib.md5(
        f"{config.VOICE_NAME}|{rate}|{pitch}|{instruct}|{text}".encode()
    ).hexdigest()[:16]
    out = PREVIEW_DIR / f"{key}.mp3"
    if out.exists() and out.stat().st_size > 1000:
        return out

    try:
        audio = await asyncio.wait_for(
            asyncio.to_thread(_synth_sync, text, rate=rate, pitch=pitch, instruct=instruct),
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("试听合成失败：%s", exc)
        return None
    if not audio:
        return None
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out.write_bytes(audio)
    usage.record_voice(len(text), model=config.VOICE_NAME)
    return out


# 预设存的是 (语速, 音高, 指令)。改 .env 三行，重启后生效。
PRESETS: dict[str, tuple[float, float, str]] = {
    "叛逆": (1.0, 1.0, ""),
    "幼态": (0.95, 1.10, "语气呆呆的，慢吞吞，像没睡醒"),
}


def current_preset() -> str | None:
    for name, (rate, pitch, instruct) in PRESETS.items():
        if (
            abs(config.VOICE_RATE - rate) < 0.001
            and abs(config.VOICE_PITCH - pitch) < 0.001
            and (config.VOICE_INSTRUCT or "") == instruct
        ):
            return name
    return None


def apply_preset(name: str) -> bool:
    """把预设写进 .env。**必须重启进程才生效**（config 是模块常量）。"""
    if name not in PRESETS:
        return False
    rate, pitch, instruct = PRESETS[name]
    values = {"VOICE_RATE": str(rate), "VOICE_PITCH": str(pitch), "VOICE_INSTRUCT": instruct}
    path = config.ROOT / ".env"
    try:
        lines, seen = [], set()
        for line in path.read_text("utf-8").splitlines():
            matched = re.match(r"^(VOICE_[A-Z_]+)=(.*)$", line)
            if matched and matched.group(1) in values:
                lines.append(f"{matched.group(1)}={values[matched.group(1)]}")
                seen.add(matched.group(1))
            else:
                lines.append(line)
        for key in values:
            if key not in seen:
                lines.append(f"{key}={values[key]}")
        path.write_text("\n".join(lines) + "\n", "utf-8")
    except OSError:
        return False
    return True


def cache_stats() -> dict:
    """缓存占了多少：条数 / 体积。清缓存前给个心里有数。"""
    count = size = 0
    try:
        for path in CACHE_DIR.glob("*.mp3"):
            count += 1
            size += path.stat().st_size
    except OSError:
        pass
    return {"count": count, "size": size}


def clear_cache() -> int:
    removed = 0
    try:
        for path in CACHE_DIR.glob("*.mp3"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    except OSError:
        pass
    return removed
