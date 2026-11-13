"""Compare data/seed against a backup directory byte by byte (absolute paths)."""

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "data" / "seed"
RAW = ROOT / "data" / "seed_raw"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv):
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else RAW
    bad = 0
    for path in sorted(SEED.glob("*.csv")):
        other = target / path.name
        if not other.exists():
            print(f"missing in target: {path.name}")
            bad += 1
            continue
        a, b = digest(path), digest(other)
        flag = "==" if a == b else "!="
        print(f"{flag} {path.name:28} seed={a[:12]} target={b[:12]}")
        bad += a != b
    print(f"\nseed={SEED}\ntarget={target}\nmismatches={bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
