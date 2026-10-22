#!/usr/bin/env python3
"""Report epochs / interval / gaps / constellations for RINEX-3 obs files.

    ./03_verify.py 2026-09-30 2026-10-01             # days under --outdir
    ./03_verify.py --station AL20 --rate 30 2026-09-30
    ./03_verify.py data/day_*/AL50/*.26o             # or any obs files

A YYYY-MM-DD argument is looked up as <outdir>/day_YYYYMMDD/<station>/, the
layout 02_fetch.py writes; anything else is taken as a file path.

02_fetch.py prints its own completeness number, but that only counts `> 20`
lines. This checks what actually matters downstream: that the sampling
interval really is the requested rate, that the window stitching left no
hole at the sub-window boundaries, and which constellations are present.
Expected epoch count assumes one full UTC day per file. Stdlib only.
"""
import argparse
import collections
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def verify(path, rate):
    ts, hdr, systems = [], True, collections.Counter()
    with open(path, errors="ignore") as fh:
        for line in fh:
            if hdr:
                if line[60:].strip() == "SYS / # / OBS TYPES" and line[0].strip():
                    systems[line[0]] = int(line[1:6])
                if "END OF HEADER" in line:
                    hdr = False
                continue
            if line.startswith("> 20"):
                f = line[2:29].split()
                # monotonic pseudo-epoch; exact for consecutive-record diffs,
                # including across month and year boundaries
                ts.append(((int(f[0]) * 12 + int(f[1])) * 31 + int(f[2])) * 86400.0
                          + int(f[3]) * 3600 + int(f[4]) * 60 + float(f[5]))
    if not ts:
        print("  NO EPOCHS")
        return 1

    d = [b - a for a, b in zip(ts, ts[1:])]
    exp = round(86400 / rate)
    gaps = [(i, g) for i, g in enumerate(d) if g > rate * 1.5]
    print(f"  epochs      : {len(ts)} / {exp} expected ({100 * len(ts) / exp:.2f}%)")
    print(f"  interval    : median {statistics.median(d):.3f}s "
          f"(min {min(d):.3f} max {max(d):.3f})")
    print("  systems     : " + " ".join(f"{k}={v}" for k, v in sorted(systems.items())))
    if gaps:
        tot = sum(g - rate for _, g in gaps)
        print(f"  GAPS        : {len(gaps)} gap(s), {tot:.0f}s missing "
              f"(largest {max(g for _, g in gaps):.0f}s)")
    else:
        print("  gaps        : none")
    return 0


def verify_file(path, rate):
    """Header + stats for one obs file. Returns 0 if OK, 1 if not."""
    if not path.is_file():
        print(f"=== {path}\n  MISSING")
        return 1
    print(f"=== {path}  ({path.stat().st_size / 1048576:.1f} MB)")
    return verify(path, rate)


def verify_day(day, station="AL50", rate=1, outdir=HERE / "data"):
    """Verify one downloaded day, like the old 03_verify.sh did per day."""
    d = Path(outdir) / f"day_{day.replace('-', '')}" / station
    print(f"=== {day}  ({d}) ===")
    if not d.is_dir():
        print("  MISSING — not downloaded")
        return 1
    for f in sorted(d.iterdir()):
        print(f"  {f.name:<28} {f.stat().st_size / 1048576:10.1f} MB")
    obs = sorted(d.glob("*[oO]"))
    if not obs:
        print("  no observation file")
        return 1
    return verify(obs[0], rate)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="+",
                    help="UTC days YYYY-MM-DD and/or RINEX-3 obs files")
    ap.add_argument("--station", default="AL50")
    ap.add_argument("--rate", type=float, default=1, help="seconds (default 1)")
    ap.add_argument("--outdir", type=Path, default=HERE / "data")
    args = ap.parse_args()

    bad = 0
    for t in args.targets:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
            bad += verify_day(t, args.station, args.rate, args.outdir)
        else:
            bad += verify_file(Path(t), args.rate)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
