"""知识库索引：命中关键词才给这一条消息挂判断依据。"""

import json
import tempfile
import unittest
from pathlib import Path

from app import config, kbindex


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._kb = config.KB_DIR
        config.KB_DIR = self.tmp

    def tearDown(self):
        config.KB_DIR = self._kb

    def test_default_used_when_no_index(self):
        body = kbindex.block("叫爸爸")
        self.assertIn("认亲", body)
        self.assertIn("[[表情:认亲]]", body)

    def test_hits_on_trigger_word(self):
        for text in ("我妈今天做饭", "我女儿三岁", "他想跟我认亲", "爷爷来了"):
            self.assertTrue(kbindex.block(text), f"没命中：{text}")

    def test_misses_without_trigger(self):
        for text in ("今天天气不错", "这关怎么打", "", "打游戏吗"):
            self.assertEqual(kbindex.block(text), "", f"不该命中：{text}")

    def test_reads_markdown_file(self):
        (self.tmp / "index.json").write_text(
            json.dumps({"测试": {"trigger": ["鲸鱼"], "file": "鲸鱼.md"}}, ensure_ascii=False),
            "utf-8",
        )
        (self.tmp / "鲸鱼.md").write_text("这是鲸鱼专用说明", "utf-8")
        self.assertEqual(kbindex.block("小鲸鱼在吗"), "这是鲸鱼专用说明")

    def test_falls_back_to_inline_text(self):
        (self.tmp / "index.json").write_text(
            json.dumps({"x": {"trigger": ["暗号"], "text": "内联说明"}}, ensure_ascii=False),
            "utf-8",
        )
        self.assertEqual(kbindex.block("说个暗号"), "内联说明")

    def test_broken_index_falls_back_to_default(self):
        (self.tmp / "index.json").write_text("{ 这不是 json", "utf-8")
        self.assertIn("认亲", kbindex.block("叫爸爸"))

    def test_missing_file_does_not_crash(self):
        (self.tmp / "index.json").write_text(
            json.dumps({"x": {"trigger": ["暗号"], "file": "不存在.md"}}, ensure_ascii=False),
            "utf-8",
        )
        self.assertEqual(kbindex.block("说个暗号"), "", "文件没了应该安静跳过")


if __name__ == "__main__":
    unittest.main()
