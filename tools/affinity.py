"""好感度管理：查看、修改、重置。

    python tools/affinity.py                       列出所有人
    python tools/affinity.py 123456789             看某人
    python tools/affinity.py 123456789 set 120     直接设经验值
    python tools/affinity.py 123456789 add 20      加 20 经验
    python tools/affinity.py 123456789 lock        锁满
    python tools/affinity.py 123456789 unlock
    python tools/affinity.py 123456789 reset
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import affinity, config  # noqa: E402


def main() -> None:
    args = sys.argv[1:]
    if not args:
        files = sorted(config.AFFINITY_DIR.glob("*.json"))
        if not files:
            print("还没有任何好感度记录")
            return
        for path in files:
            a = affinity.Affinity.load(path.stem)
            print(f"{path.stem:14} {a.display()}")
        return

    uid = args[0]
    a = affinity.Affinity.load(uid)
    if len(args) == 1:
        print(f"{uid}: {a.display()}  锁死={a.locked}  经验={a.exp}/{affinity.MAX_EXP}")
        return

    cmd = args[1]
    if cmd == "reset":
        a = affinity.Affinity()
    elif cmd == "lock":
        a.exp, a.locked = affinity.MAX_EXP, True
    elif cmd == "unlock":
        a.locked = False
    elif cmd == "set" and len(args) >= 3:
        a.exp = max(0, min(affinity.MAX_EXP, int(args[2])))
    elif cmd == "add" and len(args) >= 3:
        affinity.change(a, int(args[2]))
    else:
        print("用法：python tools/affinity.py <qq> [set <exp> | add <n> | lock | unlock | reset]")
        return
    a.save(uid)
    affinity.flush_all()
    print(f"{uid} -> {a.display()}")


if __name__ == "__main__":
    main()
