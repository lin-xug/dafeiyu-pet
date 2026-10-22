import unittest

from app import bot, modes


class SplitTest(unittest.TestCase):
    def test_split_limits(self):
        parts = bot.split_reply("一\n二\n三\n四")
        self.assertLessEqual(len(parts), 3)
        self.assertEqual(parts[0], "一")

    def test_split_strips_period(self):
        self.assertEqual(bot.split_reply("你好。")[0], "你好")

    def test_parse_reply_sticker(self):
        parts, tags = bot.parse_reply("在\n[[表情:高兴]]", context="哈哈")
        self.assertEqual(parts, ["在"])
        self.assertEqual(tags, ["高兴"])

    def test_make_typo_changes(self):
        changed = sum(1 for _ in range(50) if bot.make_typo("今天真的有点累了") != "今天真的有点累了")
        self.assertGreater(changed, 5)

    def test_typing_delay_bounds(self):
        for n in (2, 10, 40, 200):
            delay = bot.typing_delay("字" * n)
            self.assertGreaterEqual(delay, 0.3)
            self.assertLessEqual(delay, 4.2)

    def test_fallback_cooldown(self):
        bot._fallback_sent.clear()
        first = bot.fallback_line("unit:1")
        second = bot.fallback_line("unit:1")
        self.assertTrue(first)
        self.assertIsNone(second)


class ModesTest(unittest.TestCase):
    def tearDown(self):
        modes.set_mode("unit-gid", "normal")

    def test_rate(self):
        self.assertIn("10%", modes.handle_command("超活跃 10", "unit-gid"))
        self.assertEqual(modes.random_of("unit-gid"), 0.1)

    def test_two_numbers(self):
        modes.handle_command("超活跃 10 50", "unit-gid")
        self.assertEqual(modes.random_of("unit-gid"), 0.1)
        self.assertAlmostEqual(modes.mention_of("unit-gid"), 0.5)

    def test_group_targeted(self):
        msg = modes.handle_command("普通模式 123456789", None)
        self.assertIn("123456789", msg)
        self.assertEqual(modes.mode_of("123456789"), "normal")

    def test_unknown(self):
        self.assertIsNone(modes.handle_command("今天天气不错", "unit-gid"))

    def test_silent_and_wake(self):
        msg = modes.handle_command("关闭模式", "unit-gid")
        self.assertIn("闭嘴", msg)
        self.assertTrue(modes.is_silent("unit-gid"))

        # 静音时查模式要能看出是关闭模式
        self.assertIn("关闭模式", modes.handle_command("当前模式", "unit-gid"))

        msg = modes.handle_command("打开模式", "unit-gid")
        self.assertIn("普通模式", msg)
        self.assertFalse(modes.is_silent("unit-gid"))

    def test_silent_by_group_number(self):
        modes.handle_command("关闭模式 555555", None)
        self.assertTrue(modes.is_silent("555555"))
        modes.handle_command("打开模式 555555", None)
        self.assertFalse(modes.is_silent("555555"))

    def test_silent_not_counted_as_hyper(self):
        modes.handle_command("关闭模式", "unit-gid")
        self.assertFalse(modes.is_hyper("unit-gid"))

    def test_three_modes_are_peers(self):
        """关闭 / 超活跃 / 普通 三者同级，任意两个都能直接互切。"""
        modes.handle_command("闭嘴", "unit-gid")
        self.assertTrue(modes.is_silent("unit-gid"))

        modes.handle_command("超活跃 10", "unit-gid")
        self.assertTrue(modes.is_hyper("unit-gid"))
        self.assertFalse(modes.is_silent("unit-gid"))

        modes.handle_command("闭嘴", "unit-gid")
        self.assertTrue(modes.is_silent("unit-gid"))
        self.assertFalse(modes.is_hyper("unit-gid"))

        modes.handle_command("普通模式", "unit-gid")
        self.assertEqual(modes.mode_of("unit-gid"), "normal")

    def test_shut_up_with_group_number(self):
        msg = modes.handle_command("闭嘴 666666", None)
        self.assertIn("666666", msg)
        self.assertTrue(modes.is_silent("666666"))
        modes.set_mode("666666", "normal")


if __name__ == "__main__":
    unittest.main()
