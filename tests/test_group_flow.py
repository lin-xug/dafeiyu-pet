"""群聊主流程的回归测试。

这里只盯几个曾经坏过的地方，不重复测单点逻辑：
- 「推销一下自己」被日配额拦住（喊了不说话）
- 光 @ 不说话时一声不吭
- 被 @ 的回复偷偷消耗日配额，把配额顶成负数
"""

import asyncio
import time
import unittest

from app import config, group, llm, onebot, persona


def _event(text: str = "", *, at: bool = True, user: str = "10001",
           self_id: str = "999", group_id: str = "10001") -> dict:
    segs = []
    if at:
        segs.append({"type": "at", "data": {"qq": self_id}})
    if text:
        segs.append({"type": "text", "data": {"text": text}})
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": group_id,
        "user_id": user,
        "self_id": self_id,
        "message": segs,
        "raw_message": "",
        "sender": {"nickname": "主人", "card": "主人"},
    }


class GroupFlowTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._send = onebot.send_group
        self._chat = llm.chat
        self._special = persona.is_special
        self._url = config.GITHUB_URL
        self._groups = config.GROUPS
        self._cooldown = config.GITHUB_BRAG_CMD_COOLDOWN

        async def fake_send(group_id, text, at_user=None):
            self.sent.append((group_id, text))
            return 1

        async def fake_chat(messages, **kwargs):
            return "我的代码在 GitHub 上，有空来看看呗"

        onebot.send_group = fake_send
        llm.chat = fake_chat
        persona.is_special = lambda uid: True
        config.GITHUB_URL = "https://example.com/repo"
        config.GROUPS = {"10001"}
        config.GITHUB_BRAG_CMD_COOLDOWN = 0
        group._states.clear()
        # 同一次测试会话共用一个 DATA_DIR，上一个用例写进日志的回复会被 state_of
        # 恢复成 last_spoke，把 subsequent 用例卡在 2 秒直呼冷却里。这里直接塞个干净的。
        group._states["10001"] = group.GroupState()

    def tearDown(self):
        onebot.send_group = self._send
        llm.chat = self._chat
        persona.is_special = self._special
        config.GITHUB_URL = self._url
        config.GROUPS = self._groups
        config.GITHUB_BRAG_CMD_COOLDOWN = self._cooldown
        group._states.clear()

    def test_brag_command_ignores_exhausted_quota(self):
        # 日配额早就爆了、而且一秒前刚说过话——主人喊了也必须说
        st = group.state_of("10001")
        st.day_count = 999
        st.last_spoke = time.time()
        asyncio.run(group.handle(_event("推销一下自己")))
        self.assertTrue(self.sent, "喊了推销却一句话没说")
        self.assertIn("GitHub", self.sent[0][1])

    def test_brag_command_without_self_word(self):
        # 主人 @ 它说「推销一下」（没点名"自己"）也要认
        asyncio.run(group.handle(_event("推销一下")))
        self.assertTrue(self.sent)

    def test_bare_at_gets_a_reply(self):
        # 只 @ 不说话：得应一声，不能当没听见
        asyncio.run(group.handle(_event("")))
        self.assertTrue(self.sent, "光 @ 不说话时没有任何回应")

    def test_directed_reply_does_not_eat_daily_quota(self):
        # 被 @ 的回复不受日配额限制，也就不能偷偷把 day_count 加满
        st = group.state_of("10001")
        st.day_count = 0
        asyncio.run(group.handle(_event("在干嘛")))
        self.assertTrue(self.sent)
        self.assertEqual(st.day_count, 0)

    def test_silent_mode_stays_silent(self):
        # 关闭模式照旧谁都不回，@ 也不回
        from app import modes

        modes.set_mode("10001", "silent")
        try:
            asyncio.run(group.handle(_event("在吗")))
            self.assertFalse(self.sent)
        finally:
            modes.set_mode("10001", "normal")


if __name__ == "__main__":
    unittest.main()
