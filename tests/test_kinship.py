"""认亲识别：关键词粗筛 + 单句精判。"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app import config, kinship, llm


class HitTest(unittest.TestCase):
    def test_strong_words(self):
        for text in ("叫爸爸", "我是你爹", "乖女儿", "来认个亲", "叫声爷爷", "一家人啊"):
            self.assertTrue(kinship.hit(text), f"强词没命中：{text}")

    def test_weak_needs_context(self):
        # 「姐姐」天天有人说，单看不能筛；带上动作才算
        self.assertFalse(kinship.hit("姐姐今天好漂亮"))
        self.assertTrue(kinship.hit("叫姐姐"))
        self.assertTrue(kinship.hit("喊哥"))

    def test_irrelevant(self):
        for text in ("今天天气不错", "这关怎么打", "", "打游戏吗"):
            self.assertFalse(kinship.hit(text), f"不该命中：{text}")


class JudgeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._switch = config.KINSHIP_JUDGE
        config.KINSHIP_JUDGE = True

    def tearDown(self):
        config.KINSHIP_JUDGE = self._switch

    async def test_yes(self):
        with patch.object(llm, "chat", AsyncMock(return_value="是")):
            self.assertIs(await kinship.judge("叫爸爸"), True)

    async def test_no(self):
        with patch.object(llm, "chat", AsyncMock(return_value="否")):
            self.assertIs(await kinship.judge("我妈今天做饭"), False)

    async def test_failure_returns_none(self):
        async def boom(*args, **kwargs):
            raise llm.LLMError("网络炸了")

        with patch.object(llm, "chat", boom):
            self.assertIsNone(await kinship.judge("叫爸爸"), "失败要当「没判成」，交给兜底")

    async def test_timeout_returns_none(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(5)
            return "是"

        with patch.object(llm, "chat", slow):
            self.assertIsNone(await kinship.judge("叫爸爸", timeout=0.05))

    async def test_switch_off_skips_call(self):
        config.KINSHIP_JUDGE = False
        with patch.object(llm, "chat", AsyncMock(side_effect=AssertionError("不该调用"))):
            self.assertIsNone(await kinship.judge("叫爸爸"))


if __name__ == "__main__":
    unittest.main()
