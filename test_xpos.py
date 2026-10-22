#!/usr/bin/env python3
"""Offline tests for the RINEX concatenator.

    ./venv/bin/python test_xpos.py [sample.rnx]

Defaults to the known-good 1 Hz AL50 hour if present; otherwise synthesises a
minimal RINEX-3 file so the tests still run on a clean checkout.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xpos import concat_rinex, count_obs_epochs   # noqa: E402

SAMPLE = Path("/home/minhaj/SDRDemo-Python/CORS-ALDOT/data/raw/rinex/"
              "hires_20260707T18/AL50/al50188s00.26o")

SYNTH = """\
     3.04           OBSERVATION DATA    M: MIXED            RINEX VERSION / TYPE
G    2 C1C L1C                                              SYS / # / OBS TYPES
                                                            END OF HEADER
>                              4 1
SYNTHETIC HEADER BLOCK                                      COMMENT
> 2026 07 07 18 00  0.0000000  0  1
G01  20000000.000   100000.000
> 2026 07 07 18 00  1.0000000  0  1
G01  20000001.000   100001.000
> 2026 07 07 18 00  2.0000000  0  1
G01  20000002.000   100002.000
"""


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got}"
          + ("" if ok else f", want {want}"))
    return ok


def run(src, n_obs, n_evt):
    tmp = Path(tempfile.mkdtemp(prefix="test-concat-"))
    ok = True
    print(f"\nsource: {src}  ({n_obs} obs epochs, {n_evt} event record(s))")

    print("single file is a lossless copy of all records")
    w, d = concat_rinex([src], tmp / "one", "obs")
    ok &= check("written", w, n_obs + n_evt)
    ok &= check("dups", d, 0)
    ok &= check("obs epochs", count_obs_epochs(tmp / "one"), n_obs)

    print("concatenating a file with itself dedups every observation epoch")
    w, d = concat_rinex([src, src], tmp / "self", "obs")
    ok &= check("dups", d, n_obs)
    ok &= check("obs epochs still", count_obs_epochs(tmp / "self"), n_obs)

    print("...but keeps every event record (regression: blank-stamp flag-2..5)")
    kept = sum(1 for l in open(tmp / "self")
               if l.startswith(">") and not l.startswith("> 20"))
    ok &= check("event records", kept, n_evt * 2)
    ok &= check("written", w, n_obs + n_evt * 2)
    return ok


def main():
    arg = Path(sys.argv[1]) if len(sys.argv) > 1 else SAMPLE
    if arg.exists():
        lines = [l for l in open(arg, errors="ignore") if l.startswith(">")]
        n_obs = sum(1 for l in lines if l.startswith("> 20"))
        n_evt = len(lines) - n_obs
        ok = run(arg, n_obs, n_evt)
    else:
        print(f"(sample {arg} absent — using synthetic fixture)")
        tmp = Path(tempfile.mkdtemp(prefix="test-synth-"))
        f = tmp / "synth.26o"
        f.write_text(SYNTH)
        ok = run(f, 3, 1)

    print("\n" + ("ALL PASS" if ok else "FAILURES — see above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
