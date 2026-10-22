"""账单 / 用量：命令识别、权限、花费计算。"""

from __future__ import annotations

import unittest

from app import billing, config, usage

OWNER = "123456789"


class BillingCase(unittest.TestCase):
    def setUp(self):
        self._admin = set(config.ADMIN_USERS)
        config.ADMIN_USERS = {OWNER}
        self._price = config.VOICE_PRICE
        config.VOICE_PRICE = 0.8

    def tearDown(self):
        config.ADMIN_USERS = self._admin
        config.VOICE_PRICE = self._price


class TestKind(BillingCase):
    def test_balance_words(self):
        for text in ("余额", "查余额", "余额查询", "账户余额", "还剩多少钱"):
            self.assertEqual(billing.kind_of(text), "balance", text)

    def test_usage_words(self):
        for text in ("用量", "查用量", "花费", "账单", "消耗", "花了多少"):
            self.assertEqual(billing.kind_of(text), "usage", text)

    def test_at_prefix_stripped(self):
        self.assertEqual(billing.kind_of("@大肥鱼 余额"), "balance")
        self.assertEqual(billing.kind_of("@大肥鱼 查用量"), "usage")

    def test_normal_chat_not_hijacked(self):
        for text in ("我余额不足了你说咋办", "你花了多少钱买这个", "今天天气不错", "好感"):
            self.assertIsNone(billing.kind_of(text), text)

    def test_both(self):
        self.assertEqual(billing.kind_of("余额和用量"), "all")


class TestPermission(BillingCase):
    def test_owner_allowed(self):
        self.assertTrue(billing.allowed(OWNER))

    def test_other_denied(self):
        self.assertFalse(billing.allowed("999999"))

    def test_empty_whitelist_denies_all(self):
        config.ADMIN_USERS = set()
        self.assertFalse(billing.allowed(OWNER))


class TestCost(BillingCase):
    def test_voice_chars_billed(self):
        before = usage.summary(1)
        usage.record_voice(100, model="unit")
        after = usage.summary(1)
        self.assertEqual(after["voice_calls"], before["voice_calls"] + 1)
        self.assertEqual(after["chars"], before["chars"] + 100)
        # 100 字 = 0.01 万字符 × 0.8 元 = 0.008 元
        self.assertAlmostEqual(after["voice_cost"] - before["voice_cost"], 0.008, places=4)

    def test_chat_and_voice_summed(self):
        before = usage.summary(1)
        usage.record(1_000_000, 0, model="unit", kind="unit")
        usage.record_voice(10_000, model="unit")
        after = usage.summary(1)
        delta = after["cost"] - before["cost"]
        # 1M 输入 token = PRICE_INPUT 元；1 万字符 = VOICE_PRICE 元
        self.assertAlmostEqual(delta, config.PRICE_INPUT + config.VOICE_PRICE, places=3)

    def test_cache_hit_free(self):
        before = usage.summary(1)
        usage.record_voice(500, model="unit", cached=True)
        after = usage.summary(1)
        self.assertEqual(after["voice_cached"], before["voice_cached"] + 1)
        self.assertEqual(after["chars"], before["chars"])
        self.assertEqual(after["cost"], before["cost"])

    def test_cache_hit_priced_cheaper(self):
        before = usage.summary(1)
        usage.record(1_000_000, 0, model="unit", kind="unit", cached_tokens=1_000_000)
        after = usage.summary(1)
        self.assertAlmostEqual(
            after["cost"] - before["cost"], config.PRICE_CACHE_HIT, places=3
        )

    def test_mixed_hit_and_miss(self):
        before = usage.summary(1)
        # 一半命中：1M 里 50 万走命中价、50 万走未命中价
        usage.record(1_000_000, 0, model="unit", kind="unit", cached_tokens=500_000)
        after = usage.summary(1)
        expect = config.PRICE_CACHE_HIT * 0.5 + config.PRICE_INPUT * 0.5
        self.assertAlmostEqual(after["cost"] - before["cost"], expect, places=3)

    def test_old_row_without_h_counts_as_miss(self):
        before = usage.summary(1)
        usage.record(1_000_000, 0, model="unit", kind="unit")
        after = usage.summary(1)
        self.assertAlmostEqual(after["cost"] - before["cost"], config.PRICE_INPUT, places=3)

    def test_hit_clamped_to_prompt(self):
        before = usage.summary(1)
        usage.record(100, 0, model="unit", kind="unit", cached_tokens=9999)
        after = usage.summary(1)
        self.assertLessEqual(after["hit"] - before["hit"], 100)

    def test_zero_not_recorded(self):
        before = usage.summary(1)["voice_calls"]
        usage.record_voice(0)
        self.assertEqual(usage.summary(1)["voice_calls"], before)


class TestReconcile(BillingCase):
    """余额快照对账：本地估算 vs 官网真实扣费。"""

    def _write(self, points):
        import json
        import time as _time

        path = config.USAGE_DIR / "balance.jsonl"
        now = _time.time()
        lines = []
        for offset, cny in points:
            lines.append(json.dumps({"t": now + offset, "cny": cny}))
        path.write_text("\n".join(lines) + "\n")

    def test_spent_from_balance_drop(self):
        self._write([(-60, 17.0), (-30, 16.6), (0, 16.5)])
        d = billing.balance_delta()
        self.assertTrue(d["ok"])
        self.assertAlmostEqual(d["spent"], 0.5, places=3)
        self.assertEqual(d["topup"], 0)

    def test_topup_not_counted_as_spend(self):
        self._write([(-90, 16.0), (-60, 15.4), (-30, 25.4), (0, 25.0)])
        d = billing.balance_delta()
        self.assertAlmostEqual(d["spent"], 1.0, places=3)
        self.assertAlmostEqual(d["topup"], 10.0, places=3)

    def test_single_snapshot_no_delta(self):
        self._write([(0, 16.5)])
        self.assertFalse(billing.balance_delta()["ok"])


class TestFormat(BillingCase):
    def test_balance_without_aliyun(self):
        ds = {"balance_infos": [{"currency": "CNY", "total_balance": "17.25",
                                "topped_up_balance": "17.25", "granted_balance": "0.00"}]}
        old = config.ALIYUN_AK_ID
        config.ALIYUN_AK_ID = ""
        try:
            text = billing.format_balance(ds, None)
        finally:
            config.ALIYUN_AK_ID = old
        self.assertIn("17.25", text)
        self.assertIn("查不了", text)

    def test_balance_failure_text(self):
        self.assertIn("查不到", billing.format_balance(None, None))

    def test_usage_contains_money(self):
        text = billing.format_usage()
        self.assertIn("¥", text)
        self.assertIn("今天", text)


if __name__ == "__main__":
    unittest.main()
