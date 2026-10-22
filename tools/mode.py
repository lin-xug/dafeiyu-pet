"""超活跃模式开关。

    python tools/mode.py                 # 看所有群
    python tools/mode.py on 123456789    # 开
    python tools/mode.py off 123456789   # 关
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import modes  # noqa: E402


def main() -> None:
    args = sys.argv[1:]
    if not args:
        data = modes.load()
        if not data:
            print("所有群都是普通模式")
        for gid in data:
            rate = modes.random_of(gid)
            mention = modes.mention_of(gid)
            bits = []
            if rate is not None:
                bits.append(f"随机 {rate:.0%}")
            if mention is not None:
                bits.append(f"提及 {mention:.0%}")
            suffix = f"（{'，'.join(bits)}）" if bits else "（默认概率）"
            print(f"{gid}: {modes.mode_of(gid)} {suffix}")
        return

    cmd = args[0]
    if cmd in ("on", "off") and len(args) >= 2:
        gid = args[1]
        if cmd == "on" and len(args) >= 3:
            def _r(token: str) -> float:
                value = float(token.rstrip("%％"))
                value = value if value <= 1 else value / 100
                return max(0.01, min(0.99, value))

            rate = _r(args[2])
            mention = _r(args[3]) if len(args) >= 4 else None
            modes.set_mode(gid, "hyper", rate, mention)
        else:
            modes.set_mode(gid, "hyper" if cmd == "on" else "normal")
        rate = modes.random_of(gid)
        mention = modes.mention_of(gid)
        bits = []
        if rate is not None:
            bits.append(f"随机 {rate:.0%}")
        if mention is not None:
            bits.append(f"提及 {mention:.0%}")
        suffix = f"（{'，'.join(bits)}）" if bits else ""
        print(f"{gid} -> {modes.mode_of(gid)}{suffix}")
    else:
        print("用法：python tools/mode.py [on|off] <群号>")


if __name__ == "__main__":
    main()
