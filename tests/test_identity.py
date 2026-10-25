"""群聊里怎么区分「谁说的」。

实测数据（386 人的群）：1 个昵称被 2 个人共用，5 个人改过名，连主人都改过。
只靠昵称，模型必然把 A 说的话记到 B 头上，所以统一用「昵称#QQ后四位」。
"""

import json
import tempfile
import unittest
from pathlib import Path

from app import config, gmemory, group


class WhoTest(unittest.TestCase):
    def test_nick_plus_tail(self):
        self.assertEqual(group._who("矮脚大王", "3100034184"), "矮脚大王#4184")

    def test_same_nick_different_people(self):
        a = group._who("苍蓝残响", "107293942")
        b = group._who("苍蓝残响", "2643149875")
        self.assertNotEqual(a, b, "重名的人必须能区分开")

    def test_rename_keeps_same_tail(self):
        before = group._who("阿泽", "3100034184")
        after = group._who("矮脚大王", "3100034184")
        self.assertEqual(before.split("#")[1], after.split("#")[1], "改名后编号不能变")

    def test_empty_nick(self):
        self.assertEqual(group._who("", "3100034184"), "路人#4184")

    def test_short_or_bad_uid(self):
        self.assertEqual(group._who("小明", ""), "小明")
        self.assertEqual(group._who("小明", "abc"), "小明")


class TranscriptTest(unittest.TestCase):
    def test_two_people_same_nick_are_distinguishable(self):
        st = group.GroupState()
        st.timeline.append({"user_id": "107293942", "nick": "苍蓝残响", "text": "我抽到了"})
        st.timeline.append({"user_id": "2643149875", "nick": "苍蓝残响", "text": "我也抽到了"})
        lines = group._transcript(st).splitlines()
        self.assertEqual(lines[0], "苍蓝残响#3942：我抽到了")
        self.assertEqual(lines[1], "苍蓝残响#9875：我也抽到了")

    def test_bot_is_you(self):
        st = group.GroupState()
        st.timeline.append({"user_id": "999", "nick": "你", "text": "在呢", "is_bot": True})
        self.assertEqual(group._transcript(st), "你：在呢")

    def test_image_without_text(self):
        st = group.GroupState()
        st.timeline.append({"user_id": "1001", "nick": "甲", "text": "", "has_image": True})
        self.assertIn("（发了张图）", group._transcript(st))


class NicksHistoryTest(unittest.TestCase):
    def setUp(self):
        gmemory._cache.clear()
        gmemory._dirty.clear()

    def tearDown(self):
        gmemory._cache.clear()
        gmemory._dirty.clear()

    def test_records_name_changes(self):
        gmemory.record("10001", "3100034184", "阿泽", "在吗")
        gmemory.record("10001", "3100034184", "矮脚大王", "在")
        self.assertEqual(gmemory.nicks("10001", "3100034184"), ["阿泽", "矮脚大王"])

    def test_no_duplicate_nicks(self):
        for _ in range(3):
            gmemory.record("10001", "3100034184", "阿泽", "嗯")
        self.assertEqual(gmemory.nicks("10001", "3100034184"), ["阿泽"])

    def test_person_block_lists_old_names(self):
        gmemory.record("10001", "3100034184", "阿泽", "早")
        gmemory.record("10001", "3100034184", "矮脚大王", "早啊")
        block = group._person_block("10001", "3100034184", "矮脚大王")
        self.assertIn("矮脚大王#4184", block)
        self.assertIn("阿泽", block)
        self.assertIn("同一个人", block)


class RebuildNicksTest(unittest.TestCase):
    """老数据只有最后一个昵称，重启时从群日志把历史补回来。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._gmem = config.GMEMORY_DIR
        self._glog = config.GROUP_LOG_DIR
        config.GMEMORY_DIR = self.tmp / "gmemory"
        config.GROUP_LOG_DIR = self.tmp / "groups"
        config.GMEMORY_DIR.mkdir(parents=True)
        config.GROUP_LOG_DIR.mkdir(parents=True)
        gmemory._cache.clear()
        gmemory._dirty.clear()

    def tearDown(self):
        config.GMEMORY_DIR = self._gmem
        config.GROUP_LOG_DIR = self._glog
        gmemory._cache.clear()
        gmemory._dirty.clear()

    def _log(self, rows):
        path = config.GROUP_LOG_DIR / "10001.jsonl"
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), "utf-8"
        )

    def _memory(self, payload):
        folder = config.GMEMORY_DIR / "10001"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "3100034184.json").write_text(
            json.dumps(payload, ensure_ascii=False), "utf-8"
        )

    def test_fills_name_history(self):
        self._log(
            [
                {"user_id": "3100034184", "nick": "阿泽", "text": "早"},
                {"user_id": "3100034184", "nick": "矮脚大王", "text": "早啊"},
                {"is_bot": True, "nick": "你", "text": "嗯"},
            ]
        )
        self._memory({"nick": "矮脚大王", "messages": [], "summary": "老印象"})

        self.assertEqual(gmemory.rebuild_nicks(), 1)
        data = json.loads((config.GMEMORY_DIR / "10001" / "3100034184.json").read_text("utf-8"))
        self.assertEqual(data["nicks"], ["阿泽", "矮脚大王"])
        self.assertEqual(data["summary"], "老印象", "别的字段不许动")

    def test_marker_prevents_second_run(self):
        (config.GMEMORY_DIR / ".nicks_rebuilt").write_text("1", "utf-8")
        self._log([{"user_id": "3100034184", "nick": "阿泽", "text": "早"}])
        self._memory({"nick": "矮脚大王", "messages": []})
        self.assertEqual(gmemory.rebuild_nicks(), 0)

    def test_skips_people_without_memory_file(self):
        self._log([{"user_id": "999", "nick": "陌生人", "text": "路过"}])
        self.assertEqual(gmemory.rebuild_nicks(), 0)
        self.assertFalse((config.GMEMORY_DIR / "10001" / "999.json").exists())


if __name__ == "__main__":
    unittest.main()
