"""账单：账户余额 + 用量汇总。

余额有两个来源，能力不一样，别混：
  - **DeepSeek**：`GET {API_BASE}/user/balance`，用现有的 API_KEY 就能查。✅ 能用
  - **阿里云百炼**：只能走费用中心 BSS OpenAPI（`QueryAccountBalance`），
    必须 AccessKey ID/Secret + HMAC-SHA1 签名。DashScope 的 sk- key 查不了，
    网上那些 `api/v1/billing/usage` 的示例都是假的（实测 404）。
    没配 AccessKey 时这一行会显示"查不了"，不影响 DeepSeek 那条。

用量走本地账本（app/usage.py），不依赖任何云接口。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from . import config, usage

log = logging.getLogger("dafeiyu")

BALANCE_WORDS = (
    "余额",
    "查余额",
    "余额查询",
    "账户余额",
    "还有多少钱",
    "还剩多少钱",
    "钱够吗",
    "钱还够吗",
    "没钱了",
)
USAGE_WORDS = (
    "用量",
    "查用量",
    "用量查询",
    "花费",
    "花销",
    "账单",
    "消耗",
    "花了多少",
    "用掉多少",
    "用了多少",
    "成本",
    "花多少钱",
)

# @大肥鱼 这种前缀要先剥掉，否则"@大肥鱼 余额"会被当成一句话而不是命令
AT_RE = re.compile(r"[@＠]\s*\S+")
JUNK_RE = re.compile(r"[\s，。！？、,.!?~—…:：]")


def _norm(text: str) -> str:
    return JUNK_RE.sub("", AT_RE.sub(" ", text or ""))


def kind_of(text: str) -> str | None:
    """判断意图：'balance' / 'usage' / 'all' / None。

    要求**整体是个短命令**（剥掉 @ 和标点后 ≤8 字），
    否则"我余额不足了你说咋办"这种正常聊天会被劫持。
    """
    word = _norm(text)
    if not word or len(word) > 8:
        return None
    hit_balance = any(w in word for w in BALANCE_WORDS)
    hit_usage = any(w in word for w in USAGE_WORDS)
    if hit_balance and hit_usage:
        return "all"
    if hit_balance:
        return "balance"
    if hit_usage:
        return "usage"
    return None


def allowed(user_id: str) -> bool:
    """只有主人能看账。名单在 ADMIN_USERS（默认跟语音白名单同一批人）。"""
    return str(user_id) in config.ADMIN_USERS


# ---------------- 余额 ----------------


def _money(value) -> str:
    try:
        return f"¥{float(value):.2f}"
    except (TypeError, ValueError):
        return "¥?"


def _cost(value) -> str:
    """便宜到分级：一条语音两厘钱，显示 ¥0.00 会让人以为没花钱。"""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "¥?"
    if 0 < amount < 0.01:
        return f"¥{amount:.3f}"
    return f"¥{amount:.2f}"


async def deepseek_balance() -> dict | None:
    if not config.API_KEY:
        return None
    url = config.API_BASE.rstrip("/") + "/user/balance"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {config.API_KEY}"})
        if resp.status_code >= 400:
            log.warning("查 DeepSeek 余额失败：HTTP %s %s", resp.status_code, resp.text[:120])
            return None
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("查 DeepSeek 余额失败：%s", exc)
        return None
    _snapshot(_cny_of(data))
    return data


# ---------------- 余额快照：用两次余额差反推"官网真实扣费" ----------------
#
# 余额是平台给的**真实数字**，但平台没有用量接口（`/user/usage`、`/dashboard/billing/*` 全是 404）。
# 所以换个办法：每次查到余额就存一条快照，相邻两条的差额就是这段时间真实花掉的钱。
# 这比按单价估算准得多——估算是拿 token × 假设单价，账本里没有的东西（重试、其他程序用同一 key）都看不见。

def _balance_log():
    return config.USAGE_DIR / "balance.jsonl"


def _cny_of(payload: dict | None) -> float | None:
    for info in (payload or {}).get("balance_infos") or []:
        if (info.get("currency") or "CNY") == "CNY":
            try:
                return float(info.get("total_balance"))
            except (TypeError, ValueError):
                return None
    return None


def _snapshot(cny: float | None) -> None:
    if cny is None:
        return
    try:
        config.USAGE_DIR.mkdir(parents=True, exist_ok=True)
        with _balance_log().open("a", encoding="utf-8") as fh:
            fh.write(f'{{"t": {time.time():.3f}, "cny": {cny:.4f}}}\n')
    except OSError:
        pass


def balance_delta() -> dict:
    """余额变化：spent=真花掉的钱，topup=充值（余额变多）。

    基准点的选法很关键——平台**不是按次实时扣费**，而是隔一阵结算一批，
    所以"一分钟内两次查询"的差额永远是 0。这里优先拿「今天之前的最后一条」当基准，
    没有才退化成「今天第一条」；样本太短时由调用方提示"再等等"。
    """
    path = _balance_log()
    if not path.exists():
        return {"ok": False, "reason": "还没有余额快照"}
    rows: list[tuple[datetime, float]] = []
    try:
        for line in path.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append((datetime.fromtimestamp(float(item.get("t") or 0)), float(item.get("cny") or 0)))
    except OSError:
        return {"ok": False, "reason": "读不了快照"}
    if len(rows) < 2:
        return {"ok": False, "reason": f"只有 {len(rows)} 次快照"}
    rows.sort(key=lambda x: x[0])

    today = datetime.now().date()
    base = 0
    for index, (moment, _) in enumerate(rows):
        if moment.date() < today:
            base = index  # 今天之前的最后一条
    if base >= len(rows) - 1:
        base = 0

    spent = topup = 0.0
    prev = rows[base][1]
    for _, value in rows[base + 1 :]:
        change = value - prev
        if change < 0:
            spent += -change
        elif change > 0:
            topup += change
        prev = value
    start, end = rows[base][0], rows[-1][0]
    minutes = int((end - start).total_seconds() // 60)
    return {
        "ok": True,
        "spent": round(spent, 4),
        "topup": round(topup, 4),
        "first": rows[base][1],
        "last": rows[-1][1],
        "span": f"{start:%m-%d %H:%M}→{end:%H:%M}" if start.date() != end.date() else f"{start:%H:%M}→{end:%H:%M}",
        "minutes": minutes,
        "count": len(rows),
        "cross_day": start.date() < today,
        "start_ts": start.timestamp(),
        "end_ts": end.timestamp(),
    }


def _pe(value) -> str:
    """阿里云要求的 percent-encode：只有 ~ 不编码。"""
    return quote(str(value), safe="~")


def _aliyun_sign(params: dict, secret: str) -> str:
    canonical = "&".join(f"{_pe(k)}={_pe(params[k])}" for k in sorted(params))
    to_sign = "GET&%2F&" + _pe(canonical)
    digest = hmac.new(f"{secret}&".encode("utf-8"), to_sign.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def aliyun_balance() -> dict | None:
    """查阿里云账户可用额度。没配 AccessKey 直接返回 None。"""
    ak, secret = config.ALIYUN_AK_ID, config.ALIYUN_AK_SECRET
    if not ak or not secret:
        return None
    params = {
        "Action": "QueryAccountBalance",
        "Version": "2017-12-14",
        "Format": "JSON",
        "AccessKeyId": ak,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0",
        "SignatureNonce": uuid.uuid4().hex,
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    params["Signature"] = _aliyun_sign(params, secret)
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get("https://business.aliyuncs.com/", params=params)
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("查阿里云余额失败：%s", exc)
        return None
    if resp.status_code >= 400 or data.get("Code"):
        log.warning("查阿里云余额失败：%s", str(data)[:200])
        return None
    return data.get("Data") or None


# ---------------- 文案 ----------------


def _wan(tokens: int) -> str:
    if tokens >= 10_000:
        return f"{tokens / 10_000:.1f} 万"
    return str(tokens)


def format_balance(deepseek: dict | None, aliyun: dict | None) -> str:
    lines = ["余额"]
    if deepseek:
        infos = deepseek.get("balance_infos") or []
        if infos:
            parts = []
            for info in infos[:2]:
                total = info.get("total_balance")
                unit = "¥" if (info.get("currency") or "CNY") == "CNY" else f"{info.get('currency')} "
                text = f"{unit}{total}" if unit == "¥" else f"{unit}{total}"
                parts.append(text)
            first = infos[0]
            lines.append(f"· DeepSeek：{'／'.join(parts)}")
            lines.append(
                f"  充值 {first.get('topped_up_balance')} ／ 赠送 {first.get('granted_balance')}"
            )
        else:
            lines.append("· DeepSeek：接口没返回余额")
    else:
        lines.append("· DeepSeek：查不到（网络或 key 有问题）")

    if aliyun:
        lines.append(
            f"· 阿里云百炼：{_money(aliyun.get('AvailableAmount'))}"
            f"（现金 {_money(aliyun.get('AvailableCashAmount'))}"
            f" ／ 信控 {_money(aliyun.get('CreditAmount'))}）"
        )
    elif config.ALIYUN_AK_ID:
        lines.append("· 阿里云百炼：查不到（AccessKey 无效或没权限）")
    else:
        lines.append("· 阿里云百炼：查不了，需要配 AccessKey（见 HANDOVER.md）")
    return "\n".join(lines)


def format_usage() -> str:
    day = usage.summary(1)
    week = usage.summary(7)
    total = usage.summary(0)

    lines = ["用量 · 今天"]
    if day["calls"]:
        lines.append(f"· 对话 {day['calls']} 次 · {_wan(day['total'])} token · {_cost(day['chat_cost'])}")
        if day["hit"] or day["hit_prompt"]:
            lines.append(
                f"  缓存命中 {day['cache_rate']:.0%}"
                f"（新记录 {_wan(day['hit'])} / {_wan(day['hit_prompt'])} 输入，按 1/4 价算）"
            )
    if day["voice_calls"]:
        extra = f"（另有 {day['voice_cached']} 段命中缓存，不花钱）" if day["voice_cached"] else ""
        lines.append(
            f"· 语音 {day['voice_calls']} 段 · {day['chars']} 字 · {_cost(day['voice_cost'])}{extra}"
        )
    elif day["voice_cached"]:
        lines.append(f"· 语音命中缓存 {day['voice_cached']} 段 · ¥0")
    if not day["calls"] and not day["voice_calls"] and not day["voice_cached"]:
        lines.append("· 今天还没动")
    lines.append(f"合计 {_cost(day['cost'])}")
    lines.append(
        f"近 7 天 {_cost(week['cost'])} ｜ 累计 {_cost(total['cost'])}"
        f"（{total['calls']} 次对话 · {total['voice_calls']} 段语音）"
    )

    # 对账：本地是"估算"，余额差才是"官网真实扣费"
    delta = balance_delta()
    if delta["ok"] and delta["minutes"] < 10:
        lines.append(
            f"对账：余额样本只有 {delta['minutes']} 分钟，平台按结算周期扣费，隔一阵再看更准"
        )
    elif delta["ok"]:
        lines.append(f"官网口径 {delta['span']} 扣了 {_cost(delta['spent'])}（结算有延迟，偏低）")
        if delta["topup"]:
            lines.append(f"  （期间充值 +{_cost(delta['topup'])}）")
        # 必须拿**同一时间窗**的本地花费来比，否则一个算全天下、一个算一截，永远对不上
        same = usage.cost_between(delta["start_ts"], delta["end_ts"])
        gap = same["chat_cost"] - delta["spent"]
        if delta["spent"] <= 0:
            lines.append("  （这段时间余额没动，说明还没结算）")
        elif abs(gap) < 0.02:
            lines.append(f"同区间本地估算 {_cost(same['chat_cost'])} · 对得上")
        else:
            legacy = "（这段里还有老记录没记缓存标记，会偏高）" if same["hit_prompt"] < same["prompt"] else ""
            lines.append(
                f"同区间本地估算 {_cost(same['chat_cost'])} · 差 {_cost(abs(gap))}"
                f"（{'高' if gap > 0 else '低'}估，PRICE_* 是假设值）{legacy}"
            )
        if delta["spent"] > 0 and same["total"] > 0:
            implied = delta["spent"] / (same["total"] / 1_000_000)
            lines.append(
                f"反推综合单价 ≈ ¥{implied:.2f}/百万 token"
                f"（现在配的是 in {config.PRICE_INPUT:g} / out {config.PRICE_OUTPUT:g}）"
            )
    else:
        lines.append(f"对账：{delta['reason']}，再查一次余额就能算差额")
    return "\n".join(lines)


async def handle(text: str, user_id: str) -> str | None:
    """命令入口。不是命令、或不是主人，返回 None（调用方照常走聊天）。"""
    kind = kind_of(text)
    if not kind:
        return None
    if not allowed(user_id):
        return None
    if kind == "usage":
        await deepseek_balance()  # 顺手刷一次余额快照，这样"用量"里能对账
        return format_usage()
    if kind == "balance":
        return format_balance(await deepseek_balance(), aliyun_balance())
    # both
    return format_balance(await deepseek_balance(), aliyun_balance()) + "\n\n" + format_usage()
