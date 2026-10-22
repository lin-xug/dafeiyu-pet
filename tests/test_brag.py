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
