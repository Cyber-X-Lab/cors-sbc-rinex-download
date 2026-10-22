# Download CORS Data from XPOS API

Tested on ALDOT CORS Network (Alabama) which uses Leica receivers and their SBC software. The SBC software seems to have an XPOS API to which you can send requests to download data. You would obviously need to have an account with them. The download workflow is typically through their web interface. This collection of scripts and xpos client library was developed from the need of collecting large (? moderately) amount of data (several days).

## TLDR

```sh
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

./test_xpos.py                                 # offline tests, no network
./01_check_availability.py --last 4            # read-only; is 1 Hz still there?
./02_fetch.py 2026-09-30 2026-10-01            # the pull (prompts)
./03_verify.py 2026-09-30 2026-10-01          # epochs, interval, gaps, systems
```

Flags: `--station`, `--rate`, `--split-hours`, `--wait`, `--outdir`, `--yes`.
`03_verify.py` takes days (looked up under `--outdir`/`--station`) or obs file paths.

## Scripts

| scripts | role |
|---|---|
| `xpos.py` | Xpos REST client + RINEX concatenator + day long data pull logic |
| `01_check_availability.py` | probe for data availability |
| `02_fetch.py` | download data |
| `03_verify.py` | post-download checks on any RINEX-3 obs file for missing epochs |
| `.env` | `ALDOT_SBC_USER` / `ALDOT_SBC_PASS`, mode 600, git-ignored |
| `.env.example` | template for `.env` (`cp .env.example .env`, fill in, `chmod 600`) |



## Sizing

| | per hour | per 24 h |
|---|---|---|
| epochs | 3,600 | 86,400 |
| size (obs) | ~25 MB | ~660 MB |
| server time, one request | | ~7.5 min |

Scales linearly with the number of days.

AL50 at 1 Hz carries all four constellations (G=16, R=12, E=16, C=8 observation types).
