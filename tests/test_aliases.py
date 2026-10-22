"""QQ 备注：存取、长度、乱码修复、昵称兜底。"""

from __future__ import annotations

import json
import unittest

from app import aliases, config


class AliasCase(unittest.TestCase):
    def setUp(self):
        self._backup = aliases.FILE.read_text("utf-8") if aliases.FILE.exists() else None
        aliases.save({})
        # 昵称缓存是模块级的，测试之间要清掉
        aliases._NICKS = (0.0, {})

    def tearDown(self):
        if self._backup is None:
            aliases.FILE.unlink(missing_ok=True)
        else:
            aliases.FILE.write_text(self._backup, "utf-8")
        aliases._NICKS = (0.0, {})


class TestStore(AliasCase):
    def test_set_and_get(self):
        aliases.set_alias("123", "胖头鱼")
        self.assertEqual(aliases.get("123"), "胖头鱼")
        self.assertEqual(aliases.names(), {"123": "胖头鱼"})

    def test_overwrite(self):
        aliases.set_alias("123", "旧")
        aliases.set_alias("123", "新")
        self.assertEqual(aliases.get("123"), "新")

    def test_empty_deletes(self):
        aliases.set_alias("123", "胖头鱼")
        aliases.set_alias("123", "   ")
        self.assertEqual(aliases.get("123"), "")

    def test_trim_and_limit(self):
        aliases.set_alias(" 123 ", "  一   二  ")
        self.assertEqual(aliases.get("123"), "一 二")
        long_name = "字" * 50
        aliases.set_alias("456", long_name)
        self.assertEqual(len(aliases.get("456")), 32)

    def test_mojibake_repaired(self):
        # curl -d 不带编码时，中文会被按 cp1252 误解码
        aliases.set_alias("123", "æµ‹è¯•å¤‡æ³¨")
        self.assertEqual(aliases.get("123"), "测试备注")

    def test_display_empty_when_unknown(self):
        self.assertEqual(aliases.display("999"), "")


class TestNick(AliasCase):
    def _write_mem(self, gid: str, qq: str, nick: str, messages=None):
        path = config.GMEMORY_DIR / gid / f"{qq}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"nick": nick, "messages": messages or []}, ensure_ascii=False), "utf-8"
        )

    def test_nick_from_gmemory(self):
        self._write_mem("111", "123", "苍蓝残响")
        aliases._NICKS = (0.0, {})
        self.assertEqual(aliases.nick_map().get("123"), "苍蓝残响")

    def test_alias_beats_nick(self):
        self._write_mem("111", "123", "苍蓝残响")
        aliases._NICKS = (0.0, {})
        self.assertEqual(aliases.display("123"), "苍蓝残响")
        aliases.set_alias("123", "表哥")
        self.assertEqual(aliases.display("123"), "表哥")

    def test_escaped_nick(self):
        self._write_mem("111", "123", 'a"b\\c')
        aliases._NICKS = (0.0, {})
        self.assertEqual(aliases.nick_map().get("123"), 'a"b\\c')


if __name__ == "__main__":
    unittest.main()
