import time
import unittest

from app import affinity


class AffinityTest(unittest.TestCase):
    def test_level_thresholds(self):
        cases = {0: 1, 29: 1, 30: 2, 89: 2, 90: 3, 180: 4, 300: 5, 9999: 5}
        for exp, level in cases.items():
            self.assertEqual(affinity.level_of(exp), level, exp)

    def test_change_add_and_clamp(self):
        a = affinity.Affinity(exp=25)
        applied, event = affinity.change(a, 10)
        self.assertEqual((applied, event, a.exp, a.level), (10, "levelup", 35, 2))

        a = affinity.Affinity(exp=495)
        affinity.change(a, 20)
        self.assertEqual(a.exp, 500)

        a = affinity.Affinity(exp=5)
        affinity.change(a, -10)
        self.assertEqual(a.exp, 0)

    def test_locked(self):
        a = affinity.Affinity(exp=500, locked=True)
        self.assertEqual(affinity.change(a, -50), (0, "locked"))
        self.assertEqual(a.exp, 500)

    def test_old_format(self):
        a = affinity.Affinity.from_raw({"level": 1, "exp": -9})
        self.assertEqual(a.exp, 0)
        a = affinity.Affinity.from_raw({"level": 3, "exp": 40})
        self.assertEqual((a.exp, a.level), (130, 3))
        a = affinity.Affinity.from_raw({"level": 5, "exp": 99, "locked": True})
        self.assertEqual((a.exp, a.locked), (399, True))

    def test_judge(self):
        self.assertEqual(affinity.judge_text("你这个大肥鱼"), (-2, "被叫大肥鱼"))
        self.assertEqual(affinity.judge_text("你这个大肥鱼", is_directed=True), (0, ""))
        self.assertEqual(affinity.judge_text("滚")[0], -8)
        self.assertEqual(affinity.judge_text("谢谢你")[0], 4)
        self.assertEqual(affinity.judge_text("今天天气不错"), (0, ""))

    def test_query(self):
        self.assertTrue(affinity.is_query("好感"))
        self.assertTrue(affinity.is_query("查一下我的好感度"))
        self.assertFalse(affinity.is_query("今天天气不错"))
        self.assertFalse(affinity.is_query("这个好感度机制有点复杂说实话我还没搞明白"))

    def test_display(self):
        a = affinity.Affinity(exp=90)
        self.assertIn("LV3", a.display())
        self.assertIn("熟", a.display())

    def test_mood_and_parts(self):
        a = affinity.Affinity(exp=10)
        self.assertFalse(a.mood())
        affinity.change(a, -2)
        self.assertTrue(a.mood())
        self.assertEqual(affinity.max_parts_for(affinity.Affinity(exp=0)), 2)
        self.assertEqual(affinity.max_parts_for(affinity.Affinity(exp=200)), 3)
        self.assertEqual(affinity.max_parts_for(affinity.Affinity(exp=0, locked=True)), 3)

    def test_touch_daily_bonus(self):
        uid = "unit-touch-user"
        a = affinity.Affinity(exp=10, last_seen=time.time() - 60)
        a.save(uid)
        affinity.flush_all()
        got = affinity.touch(uid)
        self.assertEqual(got.exp, 15)  # 10 + 当日首聊 5


if __name__ == "__main__":
    unittest.main()
