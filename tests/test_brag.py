import json

from app import brag, config, persona


def setup_function():
    config.GITHUB_URL = "https://github.com/lin-xug/dafeiyu-pet"
    config.GITHUB_BRAG_PRIVATE_DAYS = 3
    config.GITHUB_BRAG_GROUP_DAYS = 7
    p = config.DATA_DIR / "brag.json"
    if p.exists():
        p.unlink()


def test_block_has_url():
    block = persona.github_block()
    assert config.GITHUB_URL in block


def test_no_url_no_block():
    config.GITHUB_URL = ""
    assert persona.github_block() == ""
    assert brag.private_block("12345") == ""


def test_first_time_allowed_then_cooled():
    assert brag.take("p:1", 3) is True
    assert brag.take("p:1", 3) is False  # 说完就进冷却


def test_private_and_group_are_separate():
    assert brag.take("p:1", 3) is True
    assert brag.take("g:1", 7) is True
    assert brag.take("p:1", 3) is False
    assert brag.take("g:1", 7) is False


def test_private_block_consumes_slot():
    assert brag.private_block("12345") != ""
    assert brag.private_block("12345") == ""  # 第二次就没了


def test_old_timestamp_expires():
    path = config.DATA_DIR / "brag.json"
    path.write_text(json.dumps({"p:1": 0}), encoding="utf-8")
    assert brag.allowed("p:1", 3) is True


def test_default_persona_mentions_star_rule():
    # 默认文本里必须写明：不许编 star 数
    assert "star" in persona.github_block()
    assert "不许编" in persona.github_block()


# ---- 「推销一下自己」命令 ----

def test_match_basic():
    assert brag.match("推销一下自己") == "self"
    assert brag.match("自我推销") == "self"
    assert brag.match("宣传一下你的项目") == "self"
    assert brag.match("给自己打个广告") == "self"


def test_match_with_group_id():
    assert brag.match("推销一下自己 10001") == "10001"
    assert brag.match("去群 10001 推销一下自己") == "10001"


def test_match_not_a_command():
    # 随口聊天不能当成命令
    for t in ("你这个广告打得真好", "帮我看看这个宣传稿", "今天天气不错"):
        assert brag.match(t) is None


def test_match_strict_needs_self():
    # 群里严格模式：只说"打个广告"不算
    assert brag.match("打个广告") is None
    assert brag.match("打个广告", loose=True) == "self"  # 主人私聊可以偷懒


def test_force_block():
    block = brag.force_block("私聊")
    assert config.GITHUB_URL in block
    assert "star" in block
    assert "不许编" in block


def test_force_block_without_url():
    config.GITHUB_URL = ""
    assert brag.force_block() == ""


def test_cmd_cooldown():
    config.GITHUB_BRAG_CMD_COOLDOWN = 3600
    assert brag.cmd_take("g:1") is True
    assert brag.cmd_take("g:1") is False  # 一小时内不再重复
    config.GITHUB_BRAG_CMD_COOLDOWN = 0
    assert brag.cmd_take("g:1") is True  # 关掉就每次都放行


def test_loose_still_needs_imperative():
    # 主人私聊也不能随口一句话就触发
    for t in ("你这个广告打得真好", "帮我看看这个宣传稿", "刚才那个广告挺搞笑"):
        assert brag.match(t, loose=True) is None, t
    assert brag.match("宣传一下", loose=True) == "self"
    assert brag.match("帮我打个广告", loose=True) == "self"
