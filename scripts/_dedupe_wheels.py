"""Keep newest wheel per distribution name; drop older wheels and sdists."""
import re, sys
from pathlib import Path
try:
    from packaging.version import parse
except ImportError:
    parse = None

def main(wd: Path) -> int:
    by = {}
    for p in wd.glob("*.whl"):
        m = re.match(r"^(.+?)-([0-9][^-]*)-", p.name)
        if not m:
            continue
        name, ver = m.group(1).lower().replace("_", "-"), m.group(2)
        v = parse(ver) if parse else ver
        prev = by.get(name)
        if prev is None or v > prev[0]:
            by[name] = (v, p)
    keep = {p.resolve() for _, p in by.values()}
    for p in wd.glob("*.whl"):
        if p.resolve() not in keep:
            print("drop old wheel", p.name)
            p.unlink()
    for p in wd.glob("*.tar.gz"):
        print("drop sdist", p.name)
        p.unlink()
    print("wheels kept:", len(keep))
    return 0

if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "build_win/cache/wheels")))
