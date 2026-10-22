import unittest

from app import usage


class UsageTest(unittest.TestCase):
    def test_record_and_today(self):
        before = usage.today()
        usage.record(100, 50, model="test", kind="unit")
        after = usage.today()
        self.assertEqual(after["calls"], before["calls"] + 1)
        self.assertGreaterEqual(after["total"], before["total"] + 150)
        self.assertIn("unit", after["by_kind"])


if __name__ == "__main__":
    unittest.main()
