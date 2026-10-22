"""统计群聊活跃度，并按公式给出建议配额。

    python tools/group_stats.py            # 只看建议
    python tools/group_stats.py --write    # 写进 groups.json

公式：
    H = clamp(ceil(R × p), 1, 6)
    D = clamp(H × 8, 8, 60)
R 是真人每小时发言条数，p 是目标存在感占比（默认 3%）。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402

LOG_DIR = config.GROUP_LOG_DIR
CONFIG_PATH = ROOT / "groups.json"

# 小群给高一点存在感，大群压低
def target_ratio(members: int | None) -> float:
    if members is None:
        return 0.03
    if members <= 10:
        return 0.05
    if members <= 60:
        return 0.03
    return 0.02


def suggest(rate: float, ratio: float) -> tuple[int, int]:
    hourly = max(1, min(6, math.ceil(rate * ratio)))
    daily = max(8, min(60, hourly * 8))
    return hourly, daily


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--members", type=int, default=None, help="群人数，用来定占比")
    args = parser.parse_args()

    files = sorted(LOG_DIR.glob("*.jsonl"))
    if not files:
        print("还没有群聊日志，先让机器人跑一会儿")
        return

    config = {}
    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text("utf-8"))
        except json.JSONDecodeError:
            config = {}

    for path in files:
        rows = []
        for line in path.read_text("utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not item.get("is_bot"):
                rows.append(item)

        if not rows:
            print(f"{path.stem}: 没有真人消息")
            continue

        stamps = [r["ts"] for r in rows if "ts" in r]
        span_hours = max((max(stamps) - min(stamps)) / 3600, 0.5)
        rate = len(rows) / span_hours
        people = Counter(r.get("user_id") for r in rows)

        ratio = target_ratio(args.members)
        hourly, daily = suggest(rate, ratio)

        print(f"\n群 {path.stem}")
        print(f"  真人消息 {len(rows)} 条，跨度 {span_hours:.1f} 小时，约 {rate:.1f} 条/小时")
        print(f"  发言人数 {len(people)}，最活跃 {(people.most_common(1)[0][0] if people else '-')}")
        print(f"  建议：每小时 {hourly} 条，每天 {daily} 条（按占比 {ratio:.0%}）")

        if args.write:
            slot = config.setdefault(path.stem, {})
            slot["hourly"] = hourly
            slot["daily"] = daily

    if args.write:
        CONFIG_PATH.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n已写入 {CONFIG_PATH}")


if __name__ == "__main__":
    main()
