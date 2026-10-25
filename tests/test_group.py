import json
import tempfile
import unittest
from pathlib import Path

from app import config, group


class ScoreTest(unittest.TestCase):
    def test_name_hit(self):
        st = group.GroupState()
        self.assertGreaterEqual(group.score_message(text="小鲸鱼在吗", is_at=False, has_image=False, state=st), 0.85)

    def test_name_ok_false(self):
        st = group.GroupState()
        # random_chance 必须显式给 0：默认值有概率给「主动插话」加分，断言 0.0 会偶发失败
        self.assertEqual(
            group.score_message(
                text="小鲸鱼", is_at=False, has_image=False, state=st, name_ok=False, random_chance=0
            ),
            0.0,
        )

    def test_question_and_image(self):
        st = group.GroupState()
        score = group.score_message(text="这关怎么打", is_at=False, has_image=True, state=st)
        self.assertGreaterEqual(score, 0.75)

    def test_cooldown_penalty(self):
        st = group.GroupState()
        st.last_spoke = __import__("time").time()
        score = group.score_message(text="这关怎么打", is_at=False, has_image=False, state=st, cooldown=90)
        self.assertLess(score, 0.5)

    def test_at_shortcut(self):
        st = group.GroupState()
        self.assertEqual(group.score_message(text="", is_at=True, has_image=False, state=st), 1.0)


class IsAtTest(unittest.TestCase):
    def test_cq_string(self):
        self.assertTrue(group._is_at({"raw_message": "[CQ:at,qq=123] 在吗"}, "123"))

    def test_array_segment(self):
        ev = {"message": [{"type": "at", "data": {"qq": "123"}}]}
        self.assertTrue(group._is_at(ev, "123"))

    def test_plain(self):
        self.assertFalse(group._is_at({"raw_message": "在吗"}, "123"))


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "groups.json"
        self.tmp.write_text(json.dumps({"default": {"threshold": 0.7}, "1": {"hourly": 9}}), "utf-8")
        self.old = config.GROUP_CONFIG_PATH
        config.GROUP_CONFIG_PATH = self.tmp
        group._override_cache = None

    def tearDown(self):
        config.GROUP_CONFIG_PATH = self.old
        group._override_cache = None

    def test_override(self):
        self.assertEqual(group.settings("1")["hourly"], 9)
        self.assertEqual(group.settings("2")["threshold"], 0.7)


if __name__ == "__main__":
    unittest.main()
