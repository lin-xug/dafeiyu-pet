"""配置：优先读环境变量，其次读项目根目录的 .env。零额外依赖。"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """极简 .env 解析：KEY=VALUE，支持 # 注释和成对引号。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # 真实环境变量优先，.env 只做兜底
        os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, ""))
    except ValueError:
        return default


# ---- 模型接口（任何 OpenAI 兼容端点都能用）----
API_BASE = os.getenv("API_BASE", "https://api.deepseek.com/v1").rstrip("/")
API_KEY = os.getenv("API_KEY", "")
MODEL = os.getenv("MODEL", "deepseek-chat")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.9"))
MAX_TOKENS = _int("MAX_TOKENS", 160)
LLM_TIMEOUT = _int("LLM_TIMEOUT", 60)
# 连不上就别干等 60 秒
LLM_CONNECT_TIMEOUT = _int("LLM_CONNECT_TIMEOUT", 10)
# 模型挂掉时回一句短的，别装死
LLM_FALLBACK_ENABLED = _bool("LLM_FALLBACK_ENABLED", True)
LLM_FALLBACK_COOLDOWN = _int("LLM_FALLBACK_COOLDOWN", 120)

# ---- OneBot / NapCat ----
ONEBOT_URL = os.getenv("ONEBOT_URL", "http://127.0.0.1:3000").rstrip("/")
# NapCat「HTTP 服务器」里设的 token，留空表示没设
ONEBOT_TOKEN = os.getenv("ONEBOT_TOKEN", "")

# ---- 运行方式 ----
HOST = os.getenv("HOST", "127.0.0.1")
PORT = _int("PORT", 8000)
# 不回真 QQ，只把要发的内容写进日志（本地调试用）
DRY_RUN = _bool("DRY_RUN", False)
# 不调模型，返回假的回复（测试整条链路用）
MOCK_LLM = _bool("MOCK_LLM", False)

# ---- 拟人化 ----
# 打错字再撤回的概率
TYPO_CHANCE = float(os.getenv("TYPO_CHANCE", "0.10"))
# 模型没主动发时，按情绪兜底发表情的概率
STICKER_CHANCE = float(os.getenv("STICKER_CHANCE", "0.60"))
# 同一个会话里两张表情之间至少隔多少秒
STICKER_COOLDOWN = _int("STICKER_COOLDOWN", 120)

# ---- 群聊 ----
# 只在这些群里说话（逗号分隔的群号），留空 = 所有群都管
GROUPS = {g.strip() for g in os.getenv("GROUPS", "").split(",") if g.strip()}
# 每群单独配置（覆盖 .env 里的默认值）
GROUP_CONFIG_PATH = ROOT / "groups.json"
# 主动开口的阈值，越高越沉默
GROUP_THRESHOLD = float(os.getenv("GROUP_THRESHOLD", "0.70"))
# 少量主动：每条消息的随机加成概率
GROUP_RANDOM_CHANCE = float(os.getenv("GROUP_RANDOM_CHANCE", "0.03"))
# 触发"突然想说话"时加的分数，要能单独把一条普通消息顶过阈值
GROUP_RANDOM_BONUS = float(os.getenv("GROUP_RANDOM_BONUS", "0.75"))
GROUP_HOURLY_LIMIT = _int("GROUP_HOURLY_LIMIT", 2)
GROUP_DAILY_LIMIT = _int("GROUP_DAILY_LIMIT", 15)
# 说完话多少秒内不再开口
GROUP_COOLDOWN = _int("GROUP_COOLDOWN", 90)
# 被 @ / 被点名时用的宽松额度
GROUP_DIRECT_HOURLY = _int("GROUP_DIRECT_HOURLY", 10000)
GROUP_DIRECT_COOLDOWN = _int("GROUP_DIRECT_COOLDOWN", 2)
# 被叫到名字（不是 @）时的回复概率，1.0 = 必回
GROUP_MENTION_RATE = float(os.getenv("GROUP_MENTION_RATE", "1.0"))
# 深夜静音区间（只影响主动开口，@ 照回）
GROUP_QUIET_START = _int("GROUP_QUIET_START", 1)
GROUP_QUIET_END = _int("GROUP_QUIET_END", 8)

# ---- 语音（阿里云 CosyVoice，可选）----
# 总开关，关着的时候一切照旧，连 dashscope 都不导入
VOICE_ENABLED = _bool("VOICE_ENABLED", False)
# 私聊回复有多大概率主动改成语音（0 = 从不主动，只等你命令）
VOICE_CHANCE = float(os.getenv("VOICE_CHANCE", "0.0"))
# 谁能命令她出声：逗号分隔的 QQ，留空 = 谁都行
VOICE_CMD_USERS = {u.strip() for u in os.getenv("VOICE_CMD_USERS", "").split(",") if u.strip()}
# 被命令时允许念多长（比平时的 40 字宽松些）
VOICE_CMD_MAX_CHARS = _int("VOICE_CMD_MAX_CHARS", 120)
VOICE_MODEL = os.getenv("VOICE_MODEL", "cosyvoice-v3-flash")
# longxian_v3 = 龙仙，豪放可爱女 12 岁（不支持 Instruct，所以 VOICE_INSTRUCT 留空）
VOICE_NAME = os.getenv("VOICE_NAME", "longxian_v3")
# Instruct 是固定模板，乱写会被拒绝；不支持 Instruct 的音色必须留空
VOICE_INSTRUCT = os.getenv("VOICE_INSTRUCT", "")
# 语速，1.0 = 原速；调快一点更活泼
VOICE_RATE = float(os.getenv("VOICE_RATE", "1.05"))
# 音高倍数，1.0 = 原声；低于 1 更沉、更有"懒得理你"的感觉
VOICE_PITCH = float(os.getenv("VOICE_PITCH", "0.94"))
# 超过这个字数就不念了（费钱，而且不像人说话）
VOICE_MAX_CHARS = _int("VOICE_MAX_CHARS", 40)
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")

# ---- 好感度 ----
AFFINITY_ENABLED = _bool("AFFINITY_ENABLED", True)
# 多久不说话算冷落（小时）
AFFINITY_DECAY_HOURS = _int("AFFINITY_DECAY_HOURS", 720)  # 30 天没说话才掉
AFFINITY_DECAY_PENALTY = _int("AFFINITY_DECAY_PENALTY", 10)
# 普通聊天加好感的冷却（秒）
AFFINITY_GAIN_COOLDOWN = _int("AFFINITY_GAIN_COOLDOWN", 300)

# ---- 群记忆手册 ----
# 每次注入几条
PLAYBOOK_INJECT = _int("PLAYBOOK_INJECT", 12)

# ---- 用量与价格（元 / 百万 token，自己按实际价改）----
# ⚠️ 这三个是**假设单价**，不是从账单读来的（DeepSeek 没有用量接口，只有余额）。
# 实际走的是 deepseek-flash（`MODEL=deepseek-chat` 是别名）。
# 输入分两种价：命中上下文缓存（`prompt_cache_hit_tokens`）比未命中便宜很多。
# 不分这两档，花费会高估 3 倍以上——实测过。
PRICE_CACHE_HIT = float(os.getenv("PRICE_CACHE_HIT", "0.5"))
PRICE_INPUT = float(os.getenv("PRICE_INPUT", "2"))
PRICE_OUTPUT = float(os.getenv("PRICE_OUTPUT", "8"))
# CosyVoice 单价（元/万字符）：v3.5-flash 是 0.8，v3-flash 是 1.0
VOICE_PRICE = float(os.getenv("VOICE_PRICE", "0.8"))

# ---- 账单 / 用量 ----
# 谁能查余额和用量；默认跟语音白名单同一批人（只有主人能看账）
ADMIN_USERS = {
    u.strip()
    for u in os.getenv("ADMIN_USERS", os.getenv("VOICE_CMD_USERS", "")).split(",")
    if u.strip()
}
# 阿里云 AccessKey：**只用来查账户余额**。DashScope 的 sk- key 查不了账单
# （api/v1/billing/* 这类路径全是 404，别信网上那些示例）
ALIYUN_AK_ID = os.getenv("ALIYUN_ACCESS_KEY_ID", "")
ALIYUN_AK_SECRET = os.getenv("ALIYUN_ACCESS_KEY_SECRET", "")

# ---- 事实库 ----
# 同一个人攒多少句话抽一次事实
FACTS_TRIGGER = _int("FACTS_TRIGGER", 20)
# 每次注入几条
FACTS_INJECT = _int("FACTS_INJECT", 5)
# 临时事实多少天后过期
FACTS_EXPIRE_DAYS = _int("FACTS_EXPIRE_DAYS", 30)

# ---- 群长期记忆 ----
# 攒多少条消息压一次群摘要
GROUP_SUMMARY_TRIGGER = _int("GROUP_SUMMARY_TRIGGER", 60)
# 摘要最多留多少字
GROUP_SUMMARY_MAX = _int("GROUP_SUMMARY_MAX", 500)

# ---- 超活跃模式（手动开启，按群生效）----
HYPER_RANDOM = float(os.getenv("HYPER_RANDOM", "0.35"))
HYPER_COOLDOWN = _int("HYPER_COOLDOWN", 10)
HYPER_HOURLY = _int("HYPER_HOURLY", 120)
HYPER_DAILY = _int("HYPER_DAILY", 1000)

# ---- 好友申请 / 入群申请 ----
# 谁能收到申请通知并用命令处理；默认跟账单白名单同一批人
OWNER_IDS = {
    u.strip()
    for u in os.getenv("OWNER_IDS", os.getenv("OWNER_ID", "")).split(",")
    if u.strip()
} or ADMIN_USERS
# 申请事件里的 flag 有效期（秒），过期就只能让对方重新申请
REQUEST_TTL = _int("REQUEST_TTL", 1800)

# ---- GitHub 人设（可选）----
# 填了才会把「我知道自己被开源」这段注入人格，并把链接交给它
GITHUB_URL = os.getenv("GITHUB_URL", "").strip()
# 求赞限频：私聊隔多少天才能提一次，群里隔多少天才能提一次
GITHUB_BRAG_PRIVATE_DAYS = _int("GITHUB_BRAG_PRIVATE_DAYS", 3)
GITHUB_BRAG_GROUP_DAYS = _int("GITHUB_BRAG_GROUP_DAYS", 7)
# 主人下命令「推销一下自己」时的防刷屏间隔（秒）。
# 默认 0 = 不限制：主人喊一次就执行一次，绝不出现「刚推销过了」。
# 嫌吵可以设成 60 之类；自动求赞走另外的 GITHUB_BRAG_*_DAYS，跟这个无关。
GITHUB_BRAG_CMD_COOLDOWN = _int("GITHUB_BRAG_CMD_COOLDOWN", 0)

# ---- 收图 ----
# 收到图片时自动存进 杂图/（只对特殊关系的人生效）
COLLECT_IMAGES = _bool("COLLECT_IMAGES", True)

# ---- 记忆 ----
# 记住最近多少轮（一问一答算一轮）
HISTORY_TURNS = _int("HISTORY_TURNS", 12)
# 长期摘要开关
SUMMARY_ENABLED = _bool("SUMMARY_ENABLED", True)
# 原话攒到多少条就压缩一次
SUMMARY_TRIGGER = _int("SUMMARY_TRIGGER", 32)
# 压缩后保留多少条原话
SUMMARY_KEEP = _int("SUMMARY_KEEP", 16)

# ---- 数据目录 ----
# 记忆存哪：默认放到用户文档目录，删掉项目也不会丢
DATA_DIR = Path(os.getenv("DATA_DIR", str(Path.home() / "文档" / "大肥鱼")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
GROUP_LOG_DIR = DATA_DIR / "groups"
KB_DIR = ROOT / "kb"
AFFINITY_DIR = DATA_DIR / "affinity"
GMEMORY_DIR = DATA_DIR / "gmemory"
FACTS_DIR = DATA_DIR / "facts"
USAGE_DIR = DATA_DIR / "usage"
HISTORY_DIR = DATA_DIR / "history"
LOG_DIR = DATA_DIR / "logs"
for _d in (DATA_DIR, HISTORY_DIR, LOG_DIR, GROUP_LOG_DIR, AFFINITY_DIR, GMEMORY_DIR, FACTS_DIR, USAGE_DIR):
    _d.mkdir(parents=True, exist_ok=True)
