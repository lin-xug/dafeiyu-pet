import unittest

from app import config, voice


class VoiceCleanTest(unittest.TestCase):
    def test_strip_marks_and_urls(self):
        raw = "哼 才不是呢[[表情:生气]] https://example.com/x 你看【补充】"
        self.assertEqual(voice.clean(raw), "哼 才不是呢 你看")

    def test_drop_emoji_and_keep_punctuation(self):
        self.assertEqual(voice.clean("好耶🎉🎉！"), "好耶！")

    def test_collapse_blank(self):
        self.assertEqual(voice.clean("  在   吗  "), "在 吗")

    def test_empty(self):
        self.assertEqual(voice.clean("[[沉默]]"), "")


class VoiceWantedTest(unittest.TestCase):
    def setUp(self):
        self._enabled = config.VOICE_ENABLED
        self._key = config.DASHSCOPE_API_KEY
        self._chance = config.VOICE_CHANCE
        self._max = config.VOICE_MAX_CHARS

    def tearDown(self):
        config.VOICE_ENABLED = self._enabled
        config.DASHSCOPE_API_KEY = self._key
        config.VOICE_CHANCE = self._chance
        config.VOICE_MAX_CHARS = self._max

    def test_disabled_by_switch(self):
        config.VOICE_ENABLED = False
        config.DASHSCOPE_API_KEY = "sk-test"
        config.VOICE_CHANCE = 1.0
        self.assertFalse(voice.available())
        self.assertFalse(voice.wanted("在呢"))

    def test_disabled_without_key(self):
        config.VOICE_ENABLED = True
        config.DASHSCOPE_API_KEY = ""
        self.assertFalse(voice.available())

    def test_too_long_not_spoken(self):
        config.VOICE_ENABLED = True
        config.DASHSCOPE_API_KEY = "sk-test"
        config.VOICE_CHANCE = 1.0
        config.VOICE_MAX_CHARS = 10
        self.assertFalse(voice.wanted("这句话其实挺长的超过十个字了吧"))

    def test_only_marks_not_spoken(self):
        config.VOICE_ENABLED = True
        config.DASHSCOPE_API_KEY = "sk-test"
        config.VOICE_CHANCE = 1.0
        self.assertFalse(voice.wanted("[[表情:高兴]]"))

    def test_short_text_can_be_spoken(self):
        config.VOICE_ENABLED = True
        config.DASHSCOPE_API_KEY = "sk-test"
        config.VOICE_CHANCE = 1.0
        config.VOICE_MAX_CHARS = 40
        self.assertTrue(voice.wanted("在呢 干嘛呀"))


class VoiceCommandTest(unittest.TestCase):
    def setUp(self):
        self._enabled = config.VOICE_ENABLED
        self._key = config.DASHSCOPE_API_KEY
        self._chance = config.VOICE_CHANCE
        self._users = config.VOICE_CMD_USERS
        self._max = config.VOICE_MAX_CHARS
        self._cmd_max = config.VOICE_CMD_MAX_CHARS
        config.VOICE_ENABLED = True
        config.DASHSCOPE_API_KEY = "sk-test"
        config.VOICE_CHANCE = 0.0
        config.VOICE_CMD_USERS = {"10001"}
        config.VOICE_MAX_CHARS = 40
        config.VOICE_CMD_MAX_CHARS = 120

    def tearDown(self):
        config.VOICE_ENABLED = self._enabled
        config.DASHSCOPE_API_KEY = self._key
        config.VOICE_CHANCE = self._chance
        config.VOICE_CMD_USERS = self._users
        config.VOICE_MAX_CHARS = self._max
        config.VOICE_CMD_MAX_CHARS = self._cmd_max

    def test_command_phrases(self):
        for text in ("说句话我听", "用语音说", "念出来", "让我听听你的声音", "出个声"):
            self.assertTrue(voice.commanded(text, "10001"), text)

    def test_bare_word_in_short_message(self):
        # 短消息里单说一个"说/念"就是命令
        for text in ("说", "你说", "说说", "念"):
            self.assertTrue(voice.commanded(text, "10001"), text)

    def test_bare_word_in_long_message_ignored(self):
        self.assertFalse(voice.commanded("我跟你说个事儿你别急着反驳我", "10001"))

    def test_at_makes_short_loose_work_in_group(self):
        self.assertTrue(voice.commanded("你说啥", "10001", is_at=True))

    def test_question_is_not_a_command(self):
        for text in ("你会说话吗", "你在说什么呢", "你说啥？"):
            self.assertFalse(voice.commanded(text, "10001"), text)

    def test_voice_word_works_anywhere(self):
        # "语音"现在是强词，出现在哪儿都算
        self.assertTrue(voice.commanded("我是谁，语音", "10001"))
        self.assertTrue(voice.commanded("语音", "10001"))

    def test_plain_chat_not_command(self):
        for text in ("你在干嘛", "你会说话吗", "今天好累"):
            self.assertFalse(voice.commanded(text, "10001"), text)

    def test_other_user_cannot_command(self):
        self.assertFalse(voice.commanded("说句话我听", "999999"))

    def test_empty_list_denies_everyone(self):
        # 白名单为空 = 谁都不认（默认拒绝，加人靠 .env 显式列出）
        config.VOICE_CMD_USERS = set()
        self.assertFalse(voice.commanded("说句话我听", "999999"))
        self.assertFalse(voice.commanded("说句话我听", "10001"))

    def test_commanded_beats_zero_chance(self):
        self.assertTrue(voice.should_speak("在呢", user_text="说句话我听", user_id="10001"))

    def test_no_command_no_voice(self):
        self.assertFalse(voice.should_speak("在呢", user_text="在吗", user_id="10001"))

    def test_command_allows_longer_reply(self):
        long_reply = "这句话有点长" * 6  # 36 字，超过平时 40 的限制内
        self.assertFalse(voice.should_speak(long_reply, user_text="在吗", user_id="10001"))
        self.assertTrue(voice.should_speak(long_reply, user_text="说句话", user_id="10001"))


class VoiceIPv4Test(unittest.TestCase):
    """阿里云同时有 AAAA 记录，而很多网络有 IPv6 地址却没有 IPv6 路由。

    Python 会先试 IPv6 并卡到超时，dashscope 不等回退直接报
    「websocket connection could not established within 5s」，语音就哑了。
    """

    def setUp(self):
        import socket

        self.socket = socket
        self.calls = []
        self._real = socket.getaddrinfo

        def spy(host, port, family=0, *args, **kwargs):
            self.calls.append((host, family))
            return [("fake", "fake", "fake", "fake", ("1.2.3.4", 443))]

        self.spy = spy
        socket.getaddrinfo = spy

    def tearDown(self):
        self.socket.getaddrinfo = self._real

    def test_dashscope_forced_to_ipv4(self):
        with voice._ipv4_only():
            self.socket.getaddrinfo("dashscope.aliyuncs.com", 443, 0, self.socket.SOCK_STREAM)
        self.assertEqual(self.calls, [("dashscope.aliyuncs.com", self.socket.AF_INET)])

    def test_other_hosts_untouched(self):
        with voice._ipv4_only():
            self.socket.getaddrinfo("api.deepseek.com", 443, 0, self.socket.SOCK_STREAM)
        self.assertEqual(self.calls, [("api.deepseek.com", 0)])

    def test_explicit_family_respected(self):
        # 调用方明确要 IPv6 时不该被改写
        with voice._ipv4_only():
            self.socket.getaddrinfo(
                "dashscope.aliyuncs.com", 443, self.socket.AF_INET6, self.socket.SOCK_STREAM
            )
        self.assertEqual(self.calls[0][1], self.socket.AF_INET6)

    def test_restores_on_exit(self):
        with voice._ipv4_only():
            pass
        self.assertIs(self.socket.getaddrinfo, self.spy)


if __name__ == "__main__":
    unittest.main()
