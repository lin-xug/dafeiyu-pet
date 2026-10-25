"""群白名单的热加载：改 .env 里的 GROUPS 不用重启服务。"""

import os
import tempfile
import unittest
from pathlib import Path

from app import config


class GroupsHotReloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._root = config.ROOT
        self._cache = dict(config._GROUPS_CACHE)
        config.ROOT = self.tmp
        config._GROUPS_CACHE.update({"mtime": -1.0, "checked": 0.0, "value": {"1"}})

    def tearDown(self):
        config.ROOT = self._root
        config._GROUPS_CACHE.clear()
        config._GROUPS_CACHE.update(self._cache)

    def _read(self) -> set:
        config._GROUPS_CACHE["checked"] = 0.0  # 绕开 1 秒节流，否则读到的是缓存
        return config.groups()

    def _write(self, text: str, mtime: int) -> Path:
        path = self.tmp / ".env"
        path.write_text(text, "utf-8")
        os.utime(path, (mtime, mtime))
        return path

    def test_no_env_keeps_startup_value(self):
        # 没有 .env 时不能把白名单清空，否则机器人会对所有群闭嘴
        self.assertEqual(self._read(), {"1"})

    def test_reads_from_env_file(self):
        self._write("GROUPS=100,200\n", 1000)
        self.assertEqual(self._read(), {"100", "200"})

    def test_reload_without_restart(self):
        self._write("GROUPS=100\n", 1000)
        self.assertEqual(self._read(), {"100"})
        self._write("GROUPS=100,300\n", 2000)
        self.assertEqual(self._read(), {"100", "300"})

    def test_empty_means_nobody(self):
        # 明确写空就是"谁都不理"，这是合法配置，要尊重
        self._write("GROUPS=\n", 1000)
        self.assertEqual(self._read(), set())

    def test_other_keys_ignored(self):
        self._write("# 注释\nGROUPS=777\nOTHER=1\n", 1000)
        self.assertEqual(self._read(), {"777"})


class SetGroupsTest(unittest.TestCase):
    """后台的白名单开关会调 set_groups 写回 .env。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._root = config.ROOT
        self._cache = dict(config._GROUPS_CACHE)
        config.ROOT = self.tmp
        config._GROUPS_CACHE.update({"mtime": -1.0, "checked": 0.0, "value": {"1"}})

    def tearDown(self):
        config.ROOT = self._root
        config._GROUPS_CACHE.clear()
        config._GROUPS_CACHE.update(self._cache)

    def _read(self) -> set:
        config._GROUPS_CACHE["checked"] = 0.0
        return config.groups()

    def test_write_and_reload(self):
        self.assertTrue(config.set_groups({"100", "200"}))
        self.assertEqual((self.tmp / ".env").read_text("utf-8").strip(), "GROUPS=100,200")
        self.assertEqual(self._read(), {"100", "200"})

    def test_rewrite_replaces_existing_line(self):
        (self.tmp / ".env").write_text("GROUPS=1,2\n", "utf-8")
        config.set_groups({"3"})
        text = (self.tmp / ".env").read_text("utf-8").strip()
        self.assertEqual(text, "GROUPS=3")

    def test_keeps_other_lines(self):
        (self.tmp / ".env").write_text("API_KEY=abc\nGROUPS=1\nMODE=hyper\n", "utf-8")
        config.set_groups({"9"})
        text = (self.tmp / ".env").read_text("utf-8")
        self.assertIn("API_KEY=abc", text)
        self.assertIn("MODE=hyper", text)
        self.assertIn("GROUPS=9", text)
        self.assertNotIn("GROUPS=1\n", text)

    def test_appends_when_missing(self):
        (self.tmp / ".env").write_text("API_KEY=abc\n", "utf-8")
        config.set_groups({"5"})
        self.assertIn("GROUPS=5", (self.tmp / ".env").read_text("utf-8"))

    def test_clear_all(self):
        self.assertTrue(config.set_groups(set()))
        self.assertEqual(self._read(), set())


if __name__ == "__main__":
    unittest.main()
