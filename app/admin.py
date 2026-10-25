"""管理后台：总览、群管理、好感度、调试台。

只绑定 127.0.0.1，给本机用。所有写操作直接改现有 JSON，不引入数据库。
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import (
    affinity,
    aliases,
    billing,
    config,
    facts,
    gmemory,
    group,
    memory,
    modes,
    persona,
    playbook,
    preview,
    stickers,
    usage,
    voice,
)

ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(ROOT / "web" / "templates"))
router = APIRouter(prefix="/admin")


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def _napcat() -> dict:
    try:
        headers = {"Authorization": f"Bearer {config.ONEBOT_TOKEN}"} if config.ONEBOT_TOKEN else {}
        with httpx.Client(timeout=3) as c:
            r = c.get(f"{config.ONEBOT_URL}/get_login_info", headers=headers)
        data = r.json().get("data") or {}
        return {"ok": bool(data), "uin": data.get("user_id"), "nick": data.get("nickname")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def _napcat_groups() -> list[str]:
    """机器人当前在的全部群。拿不到就返回空，调用方自己兜底。"""
    try:
        headers = {"Authorization": f"Bearer {config.ONEBOT_TOKEN}"} if config.ONEBOT_TOKEN else {}
        with httpx.Client(timeout=4) as c:
            r = c.get(f"{config.ONEBOT_URL}/get_group_list", headers=headers)
        return [str(g.get("group_id")) for g in (r.json().get("data") or []) if g.get("group_id")]
    except Exception:  # noqa: BLE001
        return []


def _model_ping() -> dict:
    started = time.time()
    try:
        with httpx.Client(timeout=httpx.Timeout(8, connect=4)) as c:
            r = c.get(
                f"{config.API_BASE}/models",
                headers={"Authorization": f"Bearer {config.API_KEY}"},
            )
        return {"ok": r.status_code < 500, "code": r.status_code, "ms": round((time.time() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__, "ms": round((time.time() - started) * 1000)}


def _today_stats(text: str) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    stats = {"received": 0, "replied": 0, "group_spoke": 0, "errors": 0, "model_fail": 0}
    for line in text.splitlines():
        if not line.startswith(today):
            continue
        if "收到 " in line:
            stats["received"] += 1
        if "已发送给" in line:
            stats["replied"] += 1
        if "群里说了" in line:
            stats["group_spoke"] += 1
        if " ERROR " in line:
            stats["errors"] += 1
        if "模型请求失败" in line:
            stats["model_fail"] += 1
    return stats


def _recent_events(text: str, limit: int = 40) -> list[dict]:
    rows = []
    for line in text.splitlines()[-400:]:
        if " | " not in line:
            continue
        stamp, _, msg = line.partition(" | ")
        level = "INFO"
        for candidate in ("ERROR", "WARNING", "INFO"):
            if f" {candidate} " in stamp:
                level = candidate
                break
        rows.append({"time": stamp[:19], "level": level, "msg": msg[:200]})
    return rows[-limit:][::-1]


def _read_log() -> str:
    path = config.LOG_DIR / "bot.log"
    try:
        return path.read_text("utf-8", errors="replace")
    except OSError:
        return ""


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request):
    log_text = _read_log()
    groups_info = _group_rows()
    people = _people_rows()
    payload = await billing.deepseek_balance()
    delta = billing.balance_delta()
    same = usage.cost_between(delta["start_ts"], delta["end_ts"]) if delta.get("ok") else None
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "nav": "dashboard",
            "napcat": _napcat(),
            "model": _model_ping(),
            "health": {
                "model": config.MODEL,
                "groups": len(config.groups()),
                "groups_total": len(groups_info),
            },
            "stats": _today_stats(log_text),
            "events": _recent_events(log_text),
            "groups": groups_info,
            "people_count": len(people),
            "top_people": people[:5],
            "usage": usage.today(),
            "balance": billing._cny_of(payload) if payload else None,
            "delta": delta,
            "same": same,
            "voice_on": config.VOICE_ENABLED and voice.configured(),
        },
    )


# ---------------- 群 ----------------


def _group_rows() -> list[dict]:
    names: dict[str, dict] = {}
    try:
        headers = {"Authorization": f"Bearer {config.ONEBOT_TOKEN}"} if config.ONEBOT_TOKEN else {}
        with httpx.Client(timeout=4) as c:
            r = c.get(f"{config.ONEBOT_URL}/get_group_list", headers=headers)
        for g in (r.json().get("data") or []):
            names[str(g.get("group_id"))] = {
                "name": g.get("group_name"),
                "count": g.get("member_count"),
            }
    except Exception:  # noqa: BLE001
        pass

    rows = []
    allowed = config.groups()
    for gid in sorted(allowed | set(names)):
        conf = group.settings(gid)
        info = names.get(gid, {})
        rows.append(
            {
                "id": gid,
                "name": info.get("name") or "(不在群列表)",
                "members": info.get("count"),
                "enabled": gid in allowed,
                "mode": modes.mode_of(gid),
                "threshold": conf["threshold"],
                "random": conf["random"],
                "cooldown": conf["cooldown"],
                "hourly": conf["hourly"],
                "daily": conf["daily"],
            }
        )
    return rows


@router.get("/groups", response_class=HTMLResponse)
async def groups_page(request: Request, ok: str = ""):
    rows = _group_rows()
    return templates.TemplateResponse(
        request,
        "groups.html",
        {
            "nav": "groups",
            "groups": rows,
            "ok": ok,
            "enabled_count": sum(1 for r in rows if r["enabled"]),
            "total": len(rows),
        },
    )


@router.get("/groups/{gid}", response_class=HTMLResponse)
async def group_detail(request: Request, gid: str, ok: str = ""):
    conf = group.settings(gid)
    items = playbook.active(gid)
    log_path = config.GROUP_LOG_DIR / f"{gid}.jsonl"
    tail = []
    try:
        lines = log_path.read_text("utf-8", errors="replace").splitlines()[-60:]
        for line in lines[::-1]:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            tail.append(
                {
                    "time": _fmt_ts(item.get("ts")),
                    "who": "你" if item.get("is_bot") else (item.get("nick") or item.get("user_id")),
                    "text": (item.get("text") or "[图]")[:80],
                }
            )
    except OSError:
        pass
    return templates.TemplateResponse(
        request,
        "group_detail.html",
        {
            "nav": "groups",
            "gid": gid,
            "conf": conf,
            "ok": ok,
            "enabled": gid in config.groups(),
            "group_summary": gmemory.group_summary(gid),
            "mode": modes.mode_of(gid),
            "items": items,
            "tail": tail,
            "random_pct": round((modes.random_of(gid) or config.HYPER_RANDOM) * 100),
            "mention_pct": round((modes.mention_of(gid) or config.GROUP_MENTION_RATE) * 100),
        },
    )


@router.post("/groups/{gid}/settings")
async def save_group_settings(
    gid: str,
    threshold: float = Form(...),
    random_pct: float = Form(...),
    cooldown: int = Form(...),
    hourly: int = Form(...),
    daily: int = Form(...),
    direct_cooldown: int = Form(...),
    kb: str = Form(""),
):
    path = config.GROUP_CONFIG_PATH
    data = json.loads(path.read_text("utf-8")) if path.exists() else {}
    slot = data.setdefault(gid, {})
    slot.update(
        {
            "threshold": round(max(0.0, min(1.0, threshold)), 2),
            "random": round(max(0.0, min(1.0, random_pct / 100)), 3),
            "cooldown": max(0, cooldown),
            "hourly": max(0, hourly),
            "daily": max(0, daily),
            "direct_cooldown": max(0, direct_cooldown),
        }
    )
    if kb.strip():
        slot["kb"] = kb.strip()
    else:
        slot.pop("kb", None)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return RedirectResponse(f"/admin/groups/{gid}", status_code=303)


@router.post("/groups/{gid}/mode")
async def save_group_mode(
    gid: str,
    mode: str = Form(...),
    random_pct: float = Form(0),
    mention_pct: float = Form(100),
):
    if mode == "normal":
        modes.set_mode(gid, "normal")
    elif mode == "silent":
        modes.set_mode(gid, "silent")
    else:
        modes.set_mode(
            gid, "hyper", max(0.01, min(0.99, random_pct / 100)), max(0.01, min(1.0, mention_pct / 100))
        )
    return RedirectResponse(f"/admin/groups/{gid}", status_code=303)


@router.post("/groups/{gid}/playbook")
async def playbook_action(
    gid: str,
    action: str = Form(...),
    kind: str = Form("事"),
    text: str = Form(""),
    item_id: int = Form(0),
):
    if action == "add" and text.strip():
        playbook.add(gid, kind, text)
    elif action == "update" and item_id:
        playbook.update(gid, item_id, text)
    elif action == "delete" and item_id:
        playbook.delete(gid, item_id)
    return RedirectResponse(f"/admin/groups/{gid}", status_code=303)


@router.post("/groups/{gid}/whitelist")
async def toggle_whitelist(gid: str, back: str = Form("list")):
    """开关这个群的白名单：写回 .env，下一次判断就是新的（不用重启）。"""
    allowed = set(config.groups())
    if gid in allowed:
        allowed.discard(gid)
    else:
        allowed.add(gid)
    config.set_groups(allowed)
    target = f"/admin/groups/{gid}" if back == "detail" else "/admin/groups"
    return RedirectResponse(f"{target}?ok=whitelist", status_code=303)


@router.post("/groups/whitelist/all")
async def whitelist_all(mode: str = Form("on")):
    """一键全开 / 全关。全开拿 NapCat 的群列表当全集。"""
    if mode == "off":
        config.set_groups(set())
    else:
        config.set_groups(set(_napcat_groups()) or set(config.groups()))
    return RedirectResponse("/admin/groups?ok=whitelist", status_code=303)


@router.post("/groups/{gid}/summary")
async def save_group_summary(gid: str, text: str = Form("")):
    """手改「这段时间群里发生了什么」。改完自动整理时照常接着写。"""
    gmemory.save_group_summary(gid, text)
    return RedirectResponse(f"/admin/groups/{gid}?ok=summary", status_code=303)


# ---------------- 人 ----------------

def _people_rows() -> list[dict]:
    marked = aliases.names()
    nicks = aliases.nick_map()
    rows = []
    for path in config.AFFINITY_DIR.glob("*.json"):
        qq = path.stem
        a = affinity.Affinity.load(qq)
        alias = marked.get(qq, "")
        nick = nicks.get(qq, "")
        rows.append(
            {
                "qq": qq,
                "alias": alias,
                "nick": nick,
                "name": alias or nick,
                "level": a.level,
                "tier": a.tier,
                "exp": a.exp,
                "label": a.label(),
                "locked": a.locked,
                "last_seen": _fmt_ts(a.last_seen),
                "special": persona.is_special(qq),
            }
        )
    rows.sort(key=lambda r: (-r["exp"], r["name"] or r["qq"]))
    return rows


@router.get("/people", response_class=HTMLResponse)
async def people_page(request: Request, q: str = "", ok: str = ""):
    rows = _people_rows()
    total = len(rows)
    keyword = (q or "").strip().lower()
    if keyword:
        rows = [
            r
            for r in rows
            if keyword in r["qq"]
            or keyword in (r["name"] or "").lower()
            or keyword in (r["alias"] or "").lower()
            or keyword in (r["nick"] or "").lower()
        ]
    return templates.TemplateResponse(
        request,
        "people.html",
        {"nav": "people", "people": rows, "q": q, "ok": ok, "total": total},
    )


@router.get("/people/{qq}", response_class=HTMLResponse)
async def person_page(request: Request, qq: str, ok: str = ""):
    a = affinity.Affinity.load(qq)
    d = facts.load(qq)
    summary = memory.load_summary(qq)
    hist = memory.recent(qq, turns=6)
    group_mem = []
    for path in config.GMEMORY_DIR.glob("*/" + qq + ".json"):
        gid = path.parent.name
        group_mem.append(
            {
                "gid": gid,
                "nick": gmemory._read(gid, qq).get("nick", ""),
                "recent": gmemory.recent(gid, qq, 5),
                "summary": gmemory.summary(gid, qq),
            }
        )
    alias = aliases.get(qq)
    nick = aliases.nick_map().get(str(qq), "")
    return templates.TemplateResponse(
        request,
        "person.html",
        {
            "nav": "people",
            "qq": qq,
            "a": a,
            "alias": alias,
            "nick": nick,
            "name": alias or nick,
            "ok": ok,
            "label": a.display(),
            "facts": d,
            "summary": summary,
            "hist": hist,
            "group_mem": group_mem,
            "special": persona.relation_of(qq).get("prompt", ""),
        },
    )


@router.post("/people/{qq}/alias")
async def alias_action(qq: str, name: str = Form(""), back: str = Form("")):
    aliases.set_alias(qq, name)
    target = f"/admin/people/{qq}" if back == "person" else "/admin/people"
    return RedirectResponse(f"{target}?ok=alias", status_code=303)


@router.post("/people/{qq}/affinity")
async def affinity_action(
    qq: str,
    action: str = Form(...),
    value: int = Form(0),
):
    a = affinity.Affinity.load(qq)
    if action == "add":
        affinity.change(a, value)
    elif action == "set":
        a.exp = max(0, min(affinity.MAX_EXP, value))
    elif action == "lock":
        a.exp, a.locked = affinity.MAX_EXP, True
    elif action == "unlock":
        a.locked = False
    elif action == "reset":
        a.exp, a.locked, a.last_offense = 0, False, 0.0
    a.save(qq)
    affinity.flush_all()
    return RedirectResponse(f"/admin/people/{qq}", status_code=303)


# ---------------- 记忆（后台上手改） ----------------


@router.post("/people/{qq}/summary")
async def save_person_summary(qq: str, text: str = Form("")):
    """私聊摘要是塞进提示词的那段"我印象里的他"，手改立刻生效。"""
    memory.save_summary(qq, text)
    return RedirectResponse(f"/admin/people/{qq}?ok=summary", status_code=303)


@router.post("/people/{qq}/facts")
async def facts_action(
    qq: str,
    action: str = Form(...),
    text: str = Form(""),
    kind: str = Form("事"),
    index: int = Form(-1),
):
    if action == "add" and text.strip():
        facts.add(qq, text, kind, source="admin")
    elif action == "edit":
        facts.edit(qq, index, text)
    elif action == "delete":
        facts.remove(qq, index)
    return RedirectResponse(f"/admin/people/{qq}?ok=facts", status_code=303)


@router.post("/people/{qq}/group_summary")
async def save_group_impression(qq: str, gid: str = Form(...), text: str = Form("")):
    """某个群里"我对这个人的印象"。保留原话不动，只换摘要。"""
    keep = gmemory._read(gid, qq).get("messages", [])
    gmemory.save_summary(gid, qq, text, keep)
    return RedirectResponse(f"/admin/people/{qq}?ok=memory", status_code=303)


@router.post("/people/{qq}/history")
async def history_action(qq: str, action: str = Form(...)):
    if action == "clear":
        memory.replace(qq, [])
    return RedirectResponse(f"/admin/people/{qq}?ok=history", status_code=303)


# ---------------- 记忆总览 ----------------


@router.get("/memory", response_class=HTMLResponse)
async def memory_page(request: Request):
    """把所有群记忆、人物记忆摊在一页上，点进去就能改。"""
    names = {r["id"]: r for r in _group_rows()}

    groups = []
    for gid in sorted(config.groups(), key=lambda g: names.get(g, {}).get("members") or 0, reverse=True):
        data = gmemory.load_group(gid)
        row = names.get(gid, {})
        groups.append(
            {
                "gid": gid,
                "name": row.get("name") or "(不在群列表)",
                "members": row.get("members"),
                "summary": (data.get("summary") or "").strip(),
                "updated": _fmt_ts(data.get("updated_at")),
                "items": len(playbook.load(gid)["items"]),
            }
        )

    nicks = aliases.nick_map()
    people = []
    for path in config.AFFINITY_DIR.glob("*.json"):
        qq = path.stem
        summary = memory.load_summary(qq)
        n_facts = len(facts.load(qq))
        if not summary and not n_facts:
            continue
        people.append(
            {
                "qq": qq,
                "name": aliases.get(qq) or nicks.get(qq) or qq,
                "summary": summary,
                "facts": n_facts,
                "exp": affinity.Affinity.load(qq).exp,
            }
        )
    people.sort(key=lambda r: (-r["facts"], -r["exp"], r["name"]))

    return templates.TemplateResponse(
        request,
        "memory.html",
        {
            "nav": "memory",
            "groups": groups,
            "people": people,
            "total_facts": sum(p["facts"] for p in people),
        },
    )


# ---------------- 调试台 ----------------


@router.get("/debug", response_class=HTMLResponse)
async def debug_page(request: Request):
    return templates.TemplateResponse(
        request,
        "debug.html",
        {"nav": "debug", "groups": sorted(config.groups()), "result": None, "form": {}},
    )


@router.post("/debug", response_class=HTMLResponse)
async def debug_run(
    request: Request,
    kind: str = Form("group"),
    group_id: str = Form(""),
    user_id: str = Form(""),
    nick: str = Form("测试"),
    text: str = Form("在吗"),
    call_model: str = Form(""),
):
    allowed = config.groups()
    call = bool(call_model)
    if kind == "private":
        result = await preview.preview_private(user_id, text, call_model=call)
    else:
        result = await preview.preview_group(group_id or next(iter(allowed), ""), user_id, nick, text, call_model=call)
    return templates.TemplateResponse(
        request,
        "debug.html",
        {
            "nav": "debug",
            "groups": sorted(allowed),
            "result": result,
            "form": {"kind": kind, "group_id": group_id, "user_id": user_id, "nick": nick, "text": text, "call_model": call},
        },
    )


# ---------------- 人格文本 ----------------

PERSONA_LABELS = {
    "persona": "人设正文",
    "examples": "正面示例",
    "style": "禁止项",
    "output": "输出格式",
    "group_rules": "群聊规则",
    "private_rules": "私聊规则",
    "github": "GitHub 人设",
}

_KB_NAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fff-]{1,40}$")


def _safe_kb_name(name: str) -> str:
    """知识库文件名：只要一段名字，不许带路径，防目录穿越。"""
    stem = Path(name.strip()).stem
    if not _KB_NAME_RE.match(stem):
        return ""
    return f"{stem}.md"


def _snapshot(name: str, text: str) -> None:
    hist = persona.PERSONA_DIR / "history"
    hist.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (hist / f"{name}-{stamp}.md").write_text(text, "utf-8")
    old = sorted(hist.glob(f"{name}-*.md"))
    for path in old[:-20]:
        path.unlink(missing_ok=True)


@router.get("/persona", response_class=HTMLResponse)
async def persona_page(request: Request, ok: str = ""):
    sections = [
        {"key": key, "label": label, "text": persona.get(key), "chars": len(persona.get(key))}
        for key, label in PERSONA_LABELS.items()
    ]
    blocks = []
    for path in sorted(config.KB_DIR.glob("*.md")) if config.KB_DIR.exists() else []:
        try:
            text = path.read_text("utf-8")
        except OSError:
            continue
        blocks.append({"name": path.name, "stem": path.stem, "text": text, "chars": len(text)})
    return templates.TemplateResponse(
        request,
        "persona.html",
        {
            "nav": "persona",
            "sections": sections,
            "dir": str(persona.PERSONA_DIR),
            "kb_blocks": blocks,
            "kb_dir": str(config.KB_DIR),
            "ok": ok,
        },
    )


@router.post("/kb/{name}")
async def save_kb(name: str, text: str = Form("")):
    safe = _safe_kb_name(name)
    if not safe:
        return RedirectResponse("/admin/persona?ok=kb-bad", status_code=303)
    config.KB_DIR.mkdir(parents=True, exist_ok=True)
    (config.KB_DIR / safe).write_text(text.rstrip() + "\n", "utf-8")
    return RedirectResponse("/admin/persona?ok=kb", status_code=303)


@router.post("/kb")
async def kb_action(action: str = Form(...), name: str = Form(""), text: str = Form("")):
    safe = _safe_kb_name(name)
    if not safe:
        return RedirectResponse("/admin/persona?ok=kb-bad", status_code=303)
    path = config.KB_DIR / safe
    if action == "create":
        config.KB_DIR.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(text.rstrip() + "\n" if text.strip() else "", "utf-8")
    elif action == "delete":
        path.unlink(missing_ok=True)
    return RedirectResponse("/admin/persona?ok=kb", status_code=303)


@router.post("/persona/{name}")
async def save_persona(name: str, text: str = Form(...)):
    if name not in PERSONA_LABELS:
        return RedirectResponse("/admin/persona", status_code=303)
    old = persona.get(name)
    _snapshot(name, old)
    persona.set_text(name, text)
    return RedirectResponse("/admin/persona", status_code=303)


# ---------------- 按人定制 ----------------


@router.get("/relations", response_class=HTMLResponse)
async def relations_page(request: Request):
    rows = []
    for qq, entry in persona.relations().items():
        rows.append(
            {
                "qq": qq,
                "name": entry.get("name", ""),
                "prompt": entry.get("prompt", ""),
                "enabled": entry.get("enabled", True),
                "system": entry.get("system", False),
                "chars": len(entry.get("prompt", "")),
            }
        )
    rows.sort(key=lambda r: (not r["system"], r["qq"]))
    return templates.TemplateResponse(
        request, "relations.html", {"nav": "relations", "rows": rows, "limit": 800}
    )


@router.post("/relations")
async def relations_action(
    action: str = Form(...),
    qq: str = Form(""),
    name: str = Form(""),
    prompt: str = Form(""),
    enabled: str = Form(""),
):
    data = persona.relations()
    qq = qq.strip()
    if action == "delete" and qq:
        entry = data.get(qq) or {}
        if not entry.get("system"):
            data.pop(qq, None)
            persona.save_relations(data)
    elif action in ("add", "update") and qq:
        text = prompt.strip()[:800]
        data[qq] = {
            "name": name.strip() or data.get(qq, {}).get("name", ""),
            "prompt": text,
            "enabled": bool(enabled),
            "system": data.get(qq, {}).get("system", False),
        }
        persona.save_relations(data)
    return RedirectResponse("/admin/relations", status_code=303)


# ---------------- 语音 ----------------


@router.get("/voice", response_class=HTMLResponse)
async def voice_page(request: Request, clip: str = "", ok: str = ""):
    return templates.TemplateResponse(
        request,
        "voice.html",
        {
            "nav": "voice",
            "enabled": config.VOICE_ENABLED,
            "configured": voice.configured(),
            "model": config.VOICE_MODEL,
            "voice_name": config.VOICE_NAME,
            "rate": config.VOICE_RATE,
            "pitch": config.VOICE_PITCH,
            "instruct": config.VOICE_INSTRUCT,
            "presets": voice.PRESETS,
            "current": voice.current_preset(),
            "cache": voice.cache_stats(),
            "clip": clip,
            "ok": ok,
            "recent": [
                {
                    "when": _fmt_ts(row.get("t")),
                    "chars": row.get("chars", 0),
                    "model": str(row.get("model") or ""),
                }
                for row in usage.recent("voice", 8)
            ],
            "deals": usage.today(),
            "chance": config.VOICE_CHANCE,
            "users": sorted(config.VOICE_CMD_USERS),
            "max_chars": config.VOICE_MAX_CHARS,
            "cmd_chars": config.VOICE_CMD_MAX_CHARS,
        },
    )


@router.post("/voice/preview")
async def voice_preview(
    text: str = Form(...),
    rate: float = Form(0),
    pitch: float = Form(0),
    instruct: str = Form(""),
):
    path = await voice.preview(
        text[:120],
        rate=rate or None,
        pitch=pitch or None,
        instruct=instruct.strip() or None,
    )
    if path is None:
        return RedirectResponse("/admin/voice?ok=fail", status_code=303)
    return RedirectResponse(f"/admin/voice?clip={path.name}", status_code=303)


@router.get("/voice/clip/{name}")
async def voice_clip(name: str):
    from fastapi.responses import FileResponse

    if "/" in name or ".." in name:
        return RedirectResponse("/admin/voice", status_code=303)
    path = voice.PREVIEW_DIR / name
    if not path.is_file():
        return RedirectResponse("/admin/voice", status_code=303)
    return FileResponse(path, media_type="audio/mpeg")


@router.post("/voice/preset")
async def voice_preset(name: str = Form(...)):
    ok = "preset" if voice.apply_preset(name) else "fail"
    return RedirectResponse(f"/admin/voice?ok={ok}", status_code=303)


@router.post("/voice/cache/clear")
async def voice_cache_clear():
    voice.clear_cache()
    return RedirectResponse("/admin/voice?ok=cleared", status_code=303)


# ---------------- 表情 ----------------


def _tag_index() -> dict[str, list[str]]:
    path = stickers.STICKER_DIR / "tags.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in data.items() if isinstance(v, list)}


def _save_tag_index(index: dict[str, list[str]]) -> None:
    path = stickers.STICKER_DIR / "tags.json"
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2), "utf-8")


@router.get("/stickers", response_class=HTMLResponse)
async def stickers_page(request: Request):
    index = _tag_index()
    rows = []
    for path in sorted(stickers.STICKER_DIR.iterdir()):
        if not path.is_file() or path.suffix.lower() not in stickers.EXTS:
            continue
        tags = [tag for tag, names in index.items() if path.name in names]
        rows.append({"name": path.name, "tags": tags, "kb": round(path.stat().st_size / 1024)})
    return templates.TemplateResponse(
        request,
        "stickers.html",
        {"nav": "stickers", "rows": rows, "tags": sorted(index), "dir": str(stickers.STICKER_DIR)},
    )


@router.get("/stickers/file/{name}")
async def sticker_file(name: str):
    from fastapi.responses import FileResponse

    if "/" in name or ".." in name:
        return RedirectResponse("/admin/stickers", status_code=303)
    path = stickers.STICKER_DIR / name
    if not path.is_file():
        return RedirectResponse("/admin/stickers", status_code=303)
    return FileResponse(path)


@router.post("/stickers/upload")
async def sticker_upload(  # noqa: B008 - FastAPI 的标准写法
    tag: str = Form(""), file: UploadFile = File(...)
):
    raw = await file.read()
    saved = stickers.save_incoming(raw)
    if saved and tag.strip():
        index = _tag_index()
        index.setdefault(tag.strip(), []).append(saved.name)
        _save_tag_index(index)
    return RedirectResponse("/admin/stickers", status_code=303)


@router.post("/stickers/tag")
async def sticker_tag(name: str = Form(...), tag: str = Form("")):
    index = _tag_index()
    for names in index.values():
        if name in names:
            names.remove(name)
    index = {k: v for k, v in index.items() if v}
    if tag.strip():
        index.setdefault(tag.strip(), []).append(name)
    _save_tag_index(index)
    return RedirectResponse("/admin/stickers", status_code=303)


@router.post("/stickers/delete")
async def sticker_delete(name: str = Form(...)):
    if "/" in name or ".." in name:
        return RedirectResponse("/admin/stickers", status_code=303)
    index = _tag_index()
    for names in index.values():
        if name in names:
            names.remove(name)
    _save_tag_index({k: v for k, v in index.items() if v})
    (stickers.STICKER_DIR / name).unlink(missing_ok=True)
    return RedirectResponse("/admin/stickers", status_code=303)


# ---------------- 备份 ----------------


@router.get("/backup", response_class=HTMLResponse)
async def backup_page(request: Request):
    from . import backup

    backup.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted(backup.BACKUP_DIR.glob("dafeiyu-*.tar.gz"), reverse=True):
        rows.append(
            {
                "name": path.name,
                "kb": round(path.stat().st_size / 1024),
                "time": _fmt_ts(path.stat().st_mtime),
            }
        )
    return templates.TemplateResponse(
        request, "backup.html", {"nav": "backup", "rows": rows, "dir": str(backup.BACKUP_DIR)}
    )


@router.post("/backup/create")
async def backup_create():
    from . import backup

    backup.create_backup(force=True)
    return RedirectResponse("/admin/backup", status_code=303)


@router.post("/backup/restore")
async def backup_restore(name: str = Form(...), confirm: str = Form("")):
    import tarfile

    from . import backup

    if confirm.strip() != "恢复":
        return RedirectResponse("/admin/backup", status_code=303)
    target = backup.BACKUP_DIR / name
    if not target.is_file() or ".." in name:
        return RedirectResponse("/admin/backup", status_code=303)
    backup.create_backup()  # 恢复前先留一份
    with tarfile.open(target) as tar:
        tar.extractall(config.DATA_DIR)  # noqa: S202 - 只允许恢复自己生成的备份
    return RedirectResponse("/admin/backup", status_code=303)
