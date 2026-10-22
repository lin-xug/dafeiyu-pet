"""调模型。用裸 httpx 打 OpenAI 兼容接口，换端点只要改 .env。"""

from __future__ import annotations

import asyncio
import logging

import httpx

from . import config, usage

log = logging.getLogger("dafeiyu")


class LLMError(RuntimeError):
    """模型调用失败（网络、鉴权、格式都算）。"""


MOCK_REPLIES = [
    "草||这我真没想过",
    "确实||有点离谱",
    "我哪知道",
    "啊这",
    "不至于吧",
    "乐||就这",
    "你问我我问谁",
]


_mock_index = 0


def _mock_reply(messages: list[dict]) -> str:
    """轮着来：保证每次测试看到的话都不一样，顺便测分条。"""
    global _mock_index
    reply = MOCK_REPLIES[_mock_index % len(MOCK_REPLIES)]
    _mock_index += 1
    return reply


async def chat(
    messages: list[dict],
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    kind: str = "chat",
) -> str:
    if config.MOCK_LLM:
        await asyncio.sleep(0.3)
        return _mock_reply(messages)

    if not config.API_KEY:
        raise LLMError("没有配置 API_KEY，去 .env 里填")

    payload = {
        "model": config.MODEL,
        "messages": messages,
        "temperature": config.TEMPERATURE if temperature is None else temperature,
        "max_tokens": config.MAX_TOKENS if max_tokens is None else max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {config.API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{config.API_BASE}/chat/completions"

    last_error: Exception | None = None
    timeout = httpx.Timeout(config.LLM_TIMEOUT, connect=config.LLM_CONNECT_TIMEOUT)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            try:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code >= 400:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                detail = data.get("usage") or {}
                cached = int(
                    detail.get("prompt_cache_hit_tokens")
                    or (detail.get("prompt_tokens_details") or {}).get("cached_tokens")
                    or 0
                )
                usage.record(
                    int(detail.get("prompt_tokens") or 0),
                    int(detail.get("completion_tokens") or 0),
                    model=str(data.get("model") or config.MODEL),
                    kind=kind,
                    cached_tokens=cached,
                )
                return str(content).strip()
            except (httpx.HTTPError, LLMError, KeyError, IndexError, TypeError) as exc:
                last_error = exc
                log.warning(
                    "模型请求失败（第 %d 次）：%s: %s",
                    attempt + 1,
                    type(exc).__name__,
                    exc or "(无错误信息)",
                )
                await asyncio.sleep(1.0)

    raise LLMError(str(last_error) if last_error else "未知错误")
