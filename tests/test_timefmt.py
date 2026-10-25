"""给模型看的时间标记：越近越细。"""

import time
import unittest
from datetime import datetime, timedelta

from app import timefmt

# 固定一个参照点，避免测试依赖真实日期
NOW = datetime(2026, 10, 25, 20, 30, 0)


def ts_of(*args) -> float:
    return datetime(*args).timestamp()


class StampTest(unittest.TestCase):
    def _stamp(self, dt: datetime) -> str:
        return timefmt.stamp(dt.timestamp(), now=NOW.timestamp())

    def test_today(self):
        self.assertEqual(self._stamp(NOW.replace(hour=9, minute=5)), "09:05")

    def test_yesterday(self):
        self.assertEqual(self._stamp(NOW - timedelta(days=1)), "昨天20:30")

    def test_this_year(self):
        self.assertEqual(self._stamp(datetime(2026, 3, 8, 14, 2)), "03-08 14:02")

    def test_older_than_this_year(self):
        self.assertEqual(self._stamp(datetime(2025, 12, 31, 23, 0)), "2025-12-31")

    def test_zero_and_bad(self):
        self.assertEqual(timefmt.stamp(None), "")
        self.assertEqual(timefmt.stamp(0), "")
        self.assertEqual(timefmt.stamp(-1), "")
        self.assertEqual(timefmt.stamp("不是数字"), "")

    def test_span(self):
        start = ts_of(2026, 10, 24, 21, 30)
        end = ts_of(2026, 10, 25, 20, 0)
        self.assertEqual(
            timefmt.span(start, end, now=NOW.timestamp()), "昨天21:30 到 20:00"
        )

    def test_span_ignores_missing(self):
        self.assertEqual(timefmt.span(None), "")


if __name__ == "__main__":
    unittest.main()
