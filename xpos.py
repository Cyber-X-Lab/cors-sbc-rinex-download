"""Self-contained client for the Leica Xpos REST API behind the ALDOT SBC
portal's RinexDataRequest page, plus a streaming RINEX-3 concatenator.

Authentication chain: SBC web login (credentials from ./.env) -> the RinexDataRequest
page embeds per-session tokens and the API base URL -> API calls carry them as
X-SDC-Client / X-SDC-Auth headers.

observationRate is in MILLISECONDS (the API rejects < 1000, i.e. 1 Hz is the
fastest obtainable rate).
"""

import itertools
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
SBC_BASE = "https://aldotcors.dot.state.al.us"


# ===========================================================================
# Xpos REST client
# ===========================================================================
class XposClient:
    def __init__(self, env_path=None):
        env_path = Path(env_path) if env_path else HERE / ".env"
        load_dotenv(env_path)
        user = os.environ.get("ALDOT_SBC_USER")
        pw = os.environ.get("ALDOT_SBC_PASS")
        if not (user and pw):
            sys.exit(f"STOP: ALDOT_SBC_USER / ALDOT_SBC_PASS missing from {env_path}")

        self.s = requests.Session()
        self.s.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64)"
        login_url = f"{SBC_BASE}/SBC/Account/Index?returnUrl=%2FSBC"
        r = self.s.get(login_url, timeout=20)
        m = re.search(
            r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', r.text)
        if not m:
            sys.exit("STOP: SBC login page has no verification token "
                     "(portal changed or unreachable)")
        self.s.post(login_url, data={"UserName": user, "Password": pw,
                                     "__RequestVerificationToken": m.group(1)},
                    timeout=20)

        page = self.s.get(f"{SBC_BASE}/SBC/User/Xpos/RinexDataRequest",
                          timeout=30).text
        try:
            self.api = re.search(r'XposServerUrls = \["([^"]+)"', page).group(1)
            sess = re.search(r"SBCSessionToken = '([^']+)'", page).group(1)
            api_tok = re.search(r"ApiToken = '([^']+)'", page).group(1)
        except AttributeError:
            sys.exit("STOP: SBC login failed or RinexDataRequest page changed "
                     "(no Xpos tokens found) — check credentials/subscription")
        self.s.headers.update({"X-SDC-Client": api_tok, "X-SDC-Auth": sess,
                               "Accept": "application/json"})

    def _check(self, r):
        if r.status_code != 200:
            sys.exit(f"STOP: Xpos API {r.request.method} "
                     f"{r.url.split('/API/')[-1]} -> HTTP {r.status_code}: "
                     f"{r.text[:300]}")
        return r

    def sites(self):
        return self._check(self.s.get(self.api + "sites", timeout=30)).json()

    def site_id(self, station_code):
        matches = [x for x in self.sites()
                   if x["siteCode"] == station_code.upper()]
        if not matches:
            sys.exit(f"STOP: station {station_code!r} not found in Xpos site list")
        return matches[0]["siteId"]

    def preview(self, site_id, start_iso, end_iso, rate_ms):
        """Metadata only -- queues no job and downloads nothing."""
        r = self.s.get(self.api + f"rinex/zulu/preview/{site_id}",
                       params={"startTime": start_iso, "endTime": end_iso,
                               "concatenate": "true",
                               "observationRate": rate_ms},
                       timeout=60)
        return self._check(r).json()

    def request(self, site_ids, start_iso, end_iso, rate_ms, name):
        r = self.s.post(self.api + "rinex/zulu/all",
                        params={"name": name, "startTime": start_iso,
                                "endTime": end_iso, "concatenate": "true",
                                "observationRate": rate_ms,
                                "sendNotifications": "false"},
                        json=list(site_ids), timeout=120)
        return self._check(r).json()

    def wait_finished(self, name, timeout_s=600):
        """Poll the newest request with our name until it leaves the queue.

        Default budget is 600 s, not the parent project's 180 s: that was
        sized for 30 s data (2880 epochs), and a 1 Hz window is 30x heavier.
        """
        deadline = time.monotonic() + timeout_s
        last = None
        while time.monotonic() < deadline:
            reqs = self._check(
                self.s.get(self.api + "rinexrequest", timeout=30)).json()
            mine = [r for r in reqs if r.get("name") == name]
            if mine:
                req = max(mine, key=lambda r: r["requestId"])
                if req["status"].startswith("Finished"):
                    if req["status"] != "FinishedSuccess":
                        sys.exit(f"STOP: request {req['requestId']} ended as "
                                 f"{req['status']}")
                    return req
                if req["status"] != last:
                    print(f"    request {req['requestId']}: {req['status']}...",
                          flush=True)
                    last = req["status"]
            time.sleep(3)
        sys.exit(f"STOP: request {name!r} not finished within {timeout_s}s")

    def download(self, file_id, outfile):
        outfile.parent.mkdir(parents=True, exist_ok=True)
        with self.s.get(self.api + f"downloads/rinex/victor/{file_id}",
                        stream=True, timeout=600) as r:
            self._check(r)
            nbytes = 0
            with open(outfile, "wb") as fh:
                for chunk in r.iter_content(65536):
                    fh.write(chunk)
                    nbytes += len(chunk)
        if nbytes == 0:
            sys.exit(f"STOP: download of fileId {file_id} returned 0 bytes")
        return nbytes

    def fetch(self, site_ids, start_iso, end_iso, rate_ms, name, dest_dir,
              wait_s=600):
        """One request over [start,end], download every result file into
        dest_dir; return the local file paths."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        self.request(site_ids, start_iso, end_iso, rate_ms, name)
        req = self.wait_finished(name, timeout_s=wait_s)
        detail = self._check(self.s.get(
            self.api + f"rinexrequest/{req['requestId']}", timeout=30)).json()
        files = [f for swf in detail.get("sitesWithFiles", [])
                 for f in swf["files"] if f["fileExists"]]
        if not files:
            sys.exit(f"STOP: request {req['requestId']} finished but lists no files")
        out = []
        for f in files:
            dest = dest_dir / f["fileName"]
            self.download(f["fileId"], dest)
            out.append(dest)
        return out


# ===========================================================================
# Streaming RINEX-3 concatenator
# ===========================================================================
# Keeps the first file's header through END OF HEADER, then appends every data
# record from each file in order, dropping records whose key was already
# emitted (this is what removes the epoch shared by two adjacent windows).
# Never slurps a whole file into memory.

def _read_header(fh):
    header = []
    for line in fh:
        header.append(line)
        if line[60:].strip() == "END OF HEADER":
            return header
    raise RuntimeError("no END OF HEADER — not a RINEX 3 file?")


def _make_obs_key():
    """Build a dedup key function for OBS records.

    A normal epoch line keys on (timestamp, epoch flag). But a RINEX epoch-flag
    2..5 record -- the "header information follows" blocks X-pos injects into
    every generated file -- has NO timestamp in columns 3..29:

        >                              4 78

    so every such record in every window collapses to the single key
    ("obs", "", "4"), and all but the first are dropped as duplicates. Pulling
    a day as 24 hourly windows would silently lose 23 header blocks.

    Each blank-stamp record therefore gets a unique key and is never deduped.
    Keeping a redundant header block is harmless; losing one is not.
    """
    n = itertools.count()

    def key(line):
        stamp = " ".join(line[2:29].split())
        if not stamp:
            return ("event", next(n))
        return ("obs", stamp, line[29:32].strip())

    return key


def _nav_key(line):
    return ("nav", " ".join(line[:23].split()))


def _iter_records(fh, is_start, key_of):
    block, key = [], None
    for line in fh:
        if is_start(line):
            if block:
                yield key, block
            block, key = [line], key_of(line)
        elif block:
            block.append(line)
    if block:
        yield key, block


def concat_rinex(paths, out_path, kind):
    """Concatenate `paths` (chronological) into out_path. kind 'obs'|'nav'.
    Returns (n_records_written, n_duplicates_dropped).

    Note: files are read and written in text mode, so CRLF input (which is what
    the X-pos server emits) is normalised to LF on output. Column positions are
    unaffected, so RINEX parsers are indifferent; the output is simply not
    byte-identical to what the server sent.
    """
    if kind == "obs":
        is_start, key_of = (lambda l: l.startswith(">")), _make_obs_key()
    elif kind == "nav":
        is_start, key_of = (lambda l: bool(l) and l[0].isalpha()), _nav_key
    else:
        raise ValueError(f"kind must be 'obs' or 'nav', got {kind!r}")

    paths = [Path(p) for p in paths]
    if not paths:
        raise ValueError("no input paths")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seen, written, dups = set(), 0, 0
    with open(out_path, "w") as out:
        for i, p in enumerate(paths):
            with open(p) as fh:
                header = _read_header(fh)
                if i == 0:
                    out.writelines(header)
                for key, block in _iter_records(fh, is_start, key_of):
                    if key in seen:
                        dups += 1
                        continue
                    seen.add(key)
                    out.writelines(block)
                    written += 1
    return written, dups


# ===========================================================================
# Day pulling
# ===========================================================================
def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def day_windows(day, split_hours):
    """[(start_iso, end_iso), ...] covering the full UTC day."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    if split_hours and split_hours > 0:
        edges, t = [], start
        while t < end:
            nxt = min(t + timedelta(hours=split_hours), end)
            edges.append((t, nxt))
            t = nxt
        return [(iso(s), iso(e)) for s, e in edges]
    return [(iso(start), iso(end))]


def unzip(zip_path, dest):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        z.extractall(dest)
    return [dest / n for n in names]


def count_obs_epochs(path):
    n, in_hdr = 0, True
    with open(path, errors="ignore") as fh:
        for line in fh:
            if in_hdr:
                if "END OF HEADER" in line:
                    in_hdr = False
                continue
            if line.startswith("> 20"):
                n += 1
    return n


def pull_day(client, code, sid, day, rate_ms, split_hours, out_root,
             wait_s=600):
    """Pull one full UTC day for one station into out_root/day_YYYYMMDD/<CODE>/.
    Sub-windows are unzipped and stitched per RINEX file type.
    Returns (obs_epochs, expected_epochs)."""
    site_dir = Path(out_root) / f"day_{day:%Y%m%d}" / code
    site_dir.mkdir(parents=True, exist_ok=True)
    windows = day_windows(day, split_hours)
    expected = round(86400 / (rate_ms / 1000))
    tmp = Path(tempfile.mkdtemp(prefix=f"pull-{code}-"))
    obs_epochs = 0
    try:
        by_ext = defaultdict(list)       # ".26o" -> paths in chronological order
        for i, (s_iso, e_iso) in enumerate(windows):
            name = f"cors-al50-{code}-{day:%Y%m%d}-w{i}"
            print(f"  [{i+1}/{len(windows)}] {s_iso} .. {e_iso}", flush=True)
            zips = client.fetch([sid], s_iso, e_iso, rate_ms, name,
                                tmp / f"w{i}", wait_s=wait_s)
            for z in zips:
                for p in unzip(z, tmp / f"w{i}" / "ex"):
                    by_ext[p.suffix].append(p)
        for ext, paths in sorted(by_ext.items()):
            kind = "obs" if ext.lower().endswith("o") else "nav"
            out = site_dir / paths[0].name
            written, dups = concat_rinex(paths, out, kind)
            if kind == "obs":
                obs_epochs = count_obs_epochs(out)
            tag = "" if len(paths) == 1 else f" ({len(paths)} windows, {dups} dup)"
            print(f"  -> {out.name}: {written} {kind} records{tag}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return obs_epochs, expected
