#!/usr/bin/env python3
"""
Update the website leaderboard from three upstream sources:
  - CodaLab (old):  https://competitions.codalab.org/competitions/24122#results
  - CodaLab (new):  https://codalab.lisn.upsaclay.fr/competitions/420#results
  - CodaBench:      https://www.codabench.org/competitions/13955/#/results-tab

Design goals:
  - Write a single, sorted "combined" leaderboard JSON consumed by index.html.
  - Cache the two CodaLab leaderboards on disk (they no longer change).
  - Refresh only CodaBench on a schedule (monthly via GitHub Actions).

Notes:
  - CodaBench's CSV export endpoint may return HTTP 403 on public competitions. In that case (or when configured),
    we scrape the public Results tab using Playwright.
"""

import argparse
import csv
import datetime as _dt
import io
import json
import os
import re
import sys
import ssl
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


CODALAB_OLD = ("codalab-old", "https://competitions.codalab.org", 24122)
CODALAB_NEW = ("codalab-new", "https://codalab.lisn.upsaclay.fr", 420)
CODABENCH = ("codabench", "https://www.codabench.org", 13955)

# "results/<id>/data" endpoints (CSV exports) for the Official Test leaderboards.
# These are stable as long as the competition results page keeps the same result set id.
CODALAB_OLD_OFFICIAL_RESULTS_ID = 40019
CODALAB_NEW_OFFICIAL_RESULTS_ID = 563

# CodaBench exposes a direct CSV export endpoint parameterized by phase.
# Default comes from the user-provided results-tab phase id.
CODABENCH_DEFAULT_PHASE_ID = 23177

RESULT_URLS = {
    "codalab-old": "https://competitions.codalab.org/competitions/24122#results",
    "codalab-new": "https://codalab.lisn.upsaclay.fr/competitions/420#results",
    "codabench": "https://www.codabench.org/competitions/13955/#/results-tab",
}


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _write_js_global(path: Path, global_name: str, data: Any) -> None:
    """
    Write a JS file that assigns the leaderboard payload to window.<global_name>.
    This allows the site to work even when index.html is opened via file:// (no fetch()).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    js = "window.{name} = {payload};\n".format(name=global_name, payload=payload)
    path.write_text(js, encoding="utf-8")


def _is_placeholder_payload(payload: Dict[str, Any]) -> bool:
    fetched_at = str(payload.get("fetched_at") or "")
    note = str(payload.get("note") or "")
    rows = payload.get("rows") or []
    if fetched_at.startswith("1970-01-01"):
        return True
    if "placeholder" in note.casefold():
        return True
    # Treat empty rows as placeholder for our use-case (these competitions are known to have results).
    if not rows:
        return True
    return False


def _http_get(url: str, timeout: int = 45, insecure: bool = False) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "msnews.github.io leaderboard updater (https://msnews.github.io/)",
            "Accept": "*/*",
        },
        method="GET",
    )
    context = ssl._create_unverified_context() if insecure else None
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        return resp.read()


def _http_get_with_headers(
    url: str, headers: Dict[str, str], timeout: int = 45, insecure: bool = False
) -> bytes:
    req_headers = {
        "User-Agent": "msnews.github.io leaderboard updater (https://msnews.github.io/)",
        "Accept": "*/*",
    }
    for k, v in (headers or {}).items():
        if v is None:
            continue
        req_headers[str(k)] = str(v)
    req = urllib.request.Request(url, headers=req_headers, method="GET")
    context = ssl._create_unverified_context() if insecure else None
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        return resp.read()


def _fetch_json(url: str, insecure: bool = False) -> Any:
    raw = _http_get(url, insecure=insecure)
    return json.loads(raw.decode("utf-8"))


def _norm_key(s: str) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).strip().casefold())


def _find_column(headers: List[str], candidates: Iterable[str]) -> Optional[str]:
    """
    Find a column in `headers` that matches any candidate (exact or substring, case-insensitive).
    Returns the actual header name (as present in the CSV) or None.
    """
    headers = [h for h in headers if h is not None]
    norm_headers = {h: _norm_key(h) for h in headers}
    cand_norm = [_norm_key(c) for c in candidates]
    # exact match first
    for h, nh in norm_headers.items():
        if nh in cand_norm:
            return h
    # substring match
    for h, nh in norm_headers.items():
        for c in cand_norm:
            if c and c in nh:
                return h
    return None


def _parse_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in {"na", "n/a", "none", "null", "-"}:
        return None
    s = s.replace(",", "")
    m = re.search(r"[-+]?\d+(\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _parse_date_any(v: Any) -> Optional[_dt.datetime]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    # ISO-like first (supports "YYYY-MM-DD" and "YYYY-MM-DD HH:MM:SS[.ffffff][+TZ]")
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass

    # Common formats seen in leaderboards / exports
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%b %d, %Y",
        "%b. %d, %Y",
        "%B %d, %Y",
    ):
        try:
            return _dt.datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def _month_abbr_with_dot(dt: _dt.datetime) -> str:
    # Match the existing site style (e.g., "Oct. 05, 2021"). Prefer "Sept." over "Sep."
    m = dt.strftime("%b")
    if m == "Sep":
        m = "Sept"
    return m + "."


def _format_date_display(dt: Optional[_dt.datetime]) -> Optional[str]:
    if not dt:
        return None
    return "{} {:02d}, {:04d}".format(_month_abbr_with_dot(dt), dt.day, dt.year)


def _date_iso(dt: Optional[_dt.datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.date().isoformat()


def _metric_sort_key(row: Dict[str, Any]) -> Tuple[float, float, float, float, str]:
    def val(k: str) -> float:
        v = row.get(k)
        return float(v) if isinstance(v, (int, float)) else float("-inf")

    return (val("auc"), val("mrr"), val("ndcg5"), val("ndcg10"), str(row.get("team") or ""))


def _codabench_fetch_leaderboard_csv(
    base_url: str, competition_id: int, phase_id: int, insecure: bool
) -> bytes:
    """
    Fetch a leaderboard CSV from CodaBench.

    User-confirmed endpoint pattern:
      /api/comptitions/<id>/results.csv?phase=<phase_id>

    Note the apparent typo "comptitions" (no 'e') — we try both spellings to be robust.
    """
    base = base_url.rstrip("/")
    candidates = [
        "{}/api/comptitions/{}/results.csv?phase={}".format(base, competition_id, phase_id),
        "{}/api/competitions/{}/results.csv?phase={}".format(base, competition_id, phase_id),
    ]
    # Optional auth (some exports return 403 without login). Provide via env vars / GitHub secrets.
    bearer = os.environ.get("CODABENCH_BEARER_TOKEN", "").strip()
    token = os.environ.get("CODABENCH_TOKEN", "").strip()
    cookie = os.environ.get("CODABENCH_COOKIE", "").strip()
    extra_headers: Dict[str, str] = {"Accept": "text/csv,*/*;q=0.8", "Referer": RESULT_URLS["codabench"]}
    if bearer:
        extra_headers["Authorization"] = "Bearer {}".format(bearer)
    elif token:
        # Some deployments accept "Token <...>".
        extra_headers["Authorization"] = "Token {}".format(token)
    if cookie:
        extra_headers["Cookie"] = cookie

    last_err = None
    for url in candidates:
        try:
            return _http_get_with_headers(url, extra_headers, insecure=insecure)
        except urllib.error.HTTPError as e:
            last_err = e
            # Try next candidate on 404.
            if e.code == 404:
                continue
            raise
        except Exception as e:
            last_err = e
    raise RuntimeError("CodaBench results.csv fetch failed: {}".format(last_err))


def _codabench_parse_table(headers: List[str], rows_2d: List[List[str]]) -> List[Dict[str, Any]]:
    if not headers or not rows_2d:
        return []

    team_col = _find_column(headers, ["team", "team name", "participant", "user", "username"])
    date_col = _find_column(headers, ["date of last entry", "last entry", "submission date", "submitted", "date"])
    auc_col = _find_column(headers, ["auc"])
    mrr_col = _find_column(headers, ["mrr"])
    ndcg5_col = _find_column(headers, ["ndcg@5", "ndcg5", "ndcg 5"])
    ndcg10_col = _find_column(headers, ["ndcg@10", "ndcg10", "ndcg 10"])

    index = {h: i for i, h in enumerate(headers) if h is not None}

    def cell(row: List[str], col: Optional[str]) -> Optional[str]:
        if not col:
            return None
        i = index.get(col)
        if i is None or i < 0 or i >= len(row):
            return None
        return row[i]

    parsed: List[Dict[str, Any]] = []
    for row in rows_2d:
        team = (cell(row, team_col) or "").strip()
        if not team:
            continue
        dt = _parse_date_any(cell(row, date_col))
        parsed.append(
            {
                "team": team,
                "auc": _parse_float(cell(row, auc_col)),
                "mrr": _parse_float(cell(row, mrr_col)),
                "ndcg5": _parse_float(cell(row, ndcg5_col)),
                "ndcg10": _parse_float(cell(row, ndcg10_col)),
                "date_iso": _date_iso(dt),
                "date_display": _format_date_display(dt),
            }
        )
    return parsed


def _codabench_scrape_results_tab(base_url: str, competition_id: int, insecure: bool) -> List[Dict[str, Any]]:
    """
    Scrape the public Results tab using Playwright and parse the visible table.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "Playwright is required for --codabench-method=scrape. Install with: pip install playwright && python -m playwright install chromium"
        ) from e

    url = "{}/competitions/{}/#/results-tab".format(base_url.rstrip("/"), competition_id)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=bool(insecure))
        page = context.new_page()
        page.goto(url, wait_until="networkidle", timeout=120000)

        # Try to find a table that contains the expected metric columns.
        try:
            page.wait_for_function(
                "() => Array.from(document.querySelectorAll('table thead')).some(t => (t.innerText||'').toUpperCase().includes('AUC'))",
                timeout=60000,
            )
        except Exception:
            page.wait_for_timeout(2000)

        tables = page.eval_on_selector_all(
            "table",
            "els => els.map(t => ({"
            "headers: Array.from(t.querySelectorAll('thead th')).map(th => (th.innerText||'').trim()),"
            "rows: Array.from(t.querySelectorAll('tbody tr')).map(tr => Array.from(tr.querySelectorAll('td')).map(td => (td.innerText||'').trim()))"
            "}))",
        )
        best = None
        best_score = -1
        for t in tables or []:
            hdrs = t.get("headers") or []
            norm = [_norm_key(h) for h in hdrs]
            score = 0
            for key in ("auc", "mrr", "ndcg@5", "ndcg@10"):
                kn = _norm_key(key)
                if any(kn in h for h in norm):
                    score += 1
            if score > best_score and (t.get("rows") or []):
                best_score = score
                best = t
        if not best:
            raise RuntimeError("Could not locate a results table on the page.")

        headers = [h for h in (best.get("headers") or [])]
        rows_2d = [r for r in (best.get("rows") or []) if isinstance(r, list)]
        browser.close()
        return _codabench_parse_table(headers, rows_2d)


def _codabench_parse_rows(csv_text: str) -> List[Dict[str, Any]]:
    f = io.StringIO(csv_text)
    reader = csv.DictReader(f)
    if not reader.fieldnames:
        return []
    headers = list(reader.fieldnames)

    team_col = _find_column(headers, ["team", "team name", "participant", "user", "username"])
    date_col = _find_column(headers, ["date of last entry", "last entry", "date"])
    auc_col = _find_column(headers, ["auc"])
    mrr_col = _find_column(headers, ["mrr"])
    ndcg5_col = _find_column(headers, ["ndcg@5", "ndcg5", "ndcg 5"])
    ndcg10_col = _find_column(headers, ["ndcg@10", "ndcg10", "ndcg 10"])

    rows: List[Dict[str, Any]] = []
    for r in reader:
        team = (r.get(team_col) if team_col else None) or ""
        team = str(team).strip()
        if not team:
            continue

        dt = _parse_date_any(r.get(date_col)) if date_col else None
        rows.append(
            {
                "team": team,
                "auc": _parse_float(r.get(auc_col)) if auc_col else None,
                "mrr": _parse_float(r.get(mrr_col)) if mrr_col else None,
                "ndcg5": _parse_float(r.get(ndcg5_col)) if ndcg5_col else None,
                "ndcg10": _parse_float(r.get(ndcg10_col)) if ndcg10_col else None,
                "date_iso": _date_iso(dt),
                "date_display": _format_date_display(dt),
            }
        )
    return rows


def _codabench_load_or_fetch(
    cache_path: Path,
    refresh: bool,
    base_url: str,
    competition_id: int,
    insecure: bool,
    method: str,
) -> Dict[str, Any]:
    if cache_path.exists() and not refresh:
        cached = json.loads(_read_text(cache_path))
        if _is_placeholder_payload(cached):
            print("WARN: codabench cache is placeholder/empty; run with --refresh-codabench to fetch.", file=sys.stderr)
        return cached

    # Phase id is stored in cache if present; otherwise use the default.
    phase_id = CODABENCH_DEFAULT_PHASE_ID
    if cache_path.exists():
        try:
            cached = json.loads(_read_text(cache_path))
            phase_id = int(cached.get("phase_id") or phase_id)
        except Exception:
            pass

    method = (method or "scrape").strip().lower()
    if method not in {"scrape", "csv", "auto"}:
        raise ValueError("Invalid --codabench-method: {!r} (expected scrape|csv|auto)".format(method))

    def _fetch_rows() -> Tuple[str, List[Dict[str, Any]]]:
        # Default: scrape (CSV export is known to return 403 for this competition).
        if method == "scrape":
            return ("scrape", _codabench_scrape_results_tab(base_url, competition_id, insecure=insecure))
        if method == "csv":
            raw = _codabench_fetch_leaderboard_csv(base_url, competition_id, phase_id=phase_id, insecure=insecure)
            csv_text = _maybe_unzip_to_csv_text(raw)
            return ("csv", _parse_generic_leaderboard_rows(csv_text))

        # auto: try CSV first (supports auth via env vars); fall back to scraping on 403.
        try:
            raw = _codabench_fetch_leaderboard_csv(base_url, competition_id, phase_id=phase_id, insecure=insecure)
            csv_text = _maybe_unzip_to_csv_text(raw)
            return ("csv", _parse_generic_leaderboard_rows(csv_text))
        except urllib.error.HTTPError as e:
            if e.code == 403:
                return ("scrape", _codabench_scrape_results_tab(base_url, competition_id, insecure=insecure))
            raise

    try:
        codabench_method_used, rows = _fetch_rows()
    except Exception as e:
        # If refresh fails but we have a non-placeholder cache, keep the last good snapshot.
        if cache_path.exists():
            cached = json.loads(_read_text(cache_path))
            if not _is_placeholder_payload(cached):
                print("WARN: codabench refresh failed ({}); using cached snapshot at {}".format(e, cache_path), file=sys.stderr)
                return cached
        raise

    payload = {
        "source": "codabench",
        "competition_id": competition_id,
        "base_url": base_url,
        "results_url": RESULT_URLS["codabench"],
        "phase_id": phase_id,
        "method": codabench_method_used,
        "fetched_at": _utc_now_iso(),
        "rows": rows,
    }
    _write_json(cache_path, payload)
    return payload


def _codalab_try_fetch_phases(base_url: str, competition_id: int) -> List[Dict[str, Any]]:
    # CodaLab instances differ slightly in URL paths; try both common patterns.
    candidates = [
        "{}/api/competition/{}/phases/".format(base_url.rstrip("/"), competition_id),
        "{}/api/competitions/{}/phases/".format(base_url.rstrip("/"), competition_id),
    ]
    last_err = None
    for url in candidates:
        try:
            data = _fetch_json(url)
            # Common patterns: list, or {"results":[...]}.
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                if isinstance(data.get("results"), list):
                    return data["results"]
                if isinstance(data.get("phases"), list):
                    return data["phases"]
        except Exception as e:
            last_err = e
    raise RuntimeError("Failed to fetch CodaLab phases for {}: {}".format(competition_id, last_err))


def _codalab_pick_phase(phases: List[Dict[str, Any]], phase_regex: str) -> Dict[str, Any]:
    rx = re.compile(phase_regex)
    for p in phases:
        name = " ".join(
            str(p.get(k) or "")
            for k in ("label", "name", "title", "description")
        ).strip()
        if rx.search(name):
            return p
    # Fall back to the last phase (often the "final"/official one)
    return phases[-1] if phases else {}


def _codalab_try_fetch_leaderboard_data(base_url: str, competition_id: int, phase_id: Any) -> Dict[str, Any]:
    candidates = [
        "{}/api/competition/{}/phases/{}/leaderboard/data".format(base_url.rstrip("/"), competition_id, phase_id),
        "{}/api/competitions/{}/phases/{}/leaderboard/data".format(base_url.rstrip("/"), competition_id, phase_id),
    ]
    last_err = None
    for url in candidates:
        try:
            data = _fetch_json(url)
            if isinstance(data, dict) and "scores" in data and "headers" in data:
                return data
        except Exception as e:
            last_err = e
    raise RuntimeError("Failed to fetch CodaLab leaderboard data: {}".format(last_err))


def _decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _maybe_unzip_to_csv_text(data: bytes) -> str:
    bio = io.BytesIO(data)
    if zipfile.is_zipfile(bio):
        bio.seek(0)
        with zipfile.ZipFile(bio) as zf:
            csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csv_names:
                # Fallback: take the first file.
                names = zf.namelist()
                if not names:
                    raise RuntimeError("ZIP is empty.")
                return _decode_text(zf.read(names[0]))
            return _decode_text(zf.read(csv_names[0]))
    return _decode_text(data)


def _csv_dict_rows(csv_text: str) -> List[Dict[str, str]]:
    # Try to sniff delimiter to support both comma and tab exports.
    sample = csv_text[:4096]
    dialect = None
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", "\t", ";"])
    except Exception:
        dialect = csv.excel
    f = io.StringIO(csv_text)
    reader = csv.DictReader(f, dialect=dialect)
    return [r for r in reader]


def _parse_generic_leaderboard_rows(csv_text: str) -> List[Dict[str, Any]]:
    dict_rows = _csv_dict_rows(csv_text)
    if not dict_rows:
        return []
    headers = [h for h in dict_rows[0].keys() if h is not None]

    team_col = _find_column(headers, ["team", "team name", "participant", "user", "username"])
    date_col = _find_column(headers, ["date of last entry", "last entry", "submission date", "submitted", "date"])
    auc_col = _find_column(headers, ["auc"])
    mrr_col = _find_column(headers, ["mrr"])
    ndcg5_col = _find_column(headers, ["ndcg@5", "ndcg5", "ndcg 5"])
    ndcg10_col = _find_column(headers, ["ndcg@10", "ndcg10", "ndcg 10"])

    rows: List[Dict[str, Any]] = []
    for r in dict_rows:
        team = (r.get(team_col) if team_col else None) or ""
        team = str(team).strip()
        if not team:
            continue
        date_raw = (r.get(date_col) if date_col else None)
        date_raw_s = str(date_raw).strip() if date_raw is not None else ""
        dt = _parse_date_any(date_raw_s) if date_raw_s else None
        rows.append(
            {
                "team": team,
                "auc": _parse_float(r.get(auc_col)) if auc_col else None,
                "mrr": _parse_float(r.get(mrr_col)) if mrr_col else None,
                "ndcg5": _parse_float(r.get(ndcg5_col)) if ndcg5_col else None,
                "ndcg10": _parse_float(r.get(ndcg10_col)) if ndcg10_col else None,
                "date_iso": _date_iso(dt),
                "date_raw": date_raw_s or None,
                "date_display": _format_date_display(dt) if dt else (date_raw_s or None),
            }
        )
    return rows


def _codalab_fetch_results_csv(
    base_url: str, competition_id: int, results_id: int, insecure: bool = False
) -> bytes:
    url = "{}/competitions/{}/results/{}/data".format(base_url.rstrip("/"), competition_id, results_id)
    return _http_get(url, insecure=insecure)


def _codalab_load_from_local_csv(cache_path: Path, source_name: str, csv_path: Path) -> Dict[str, Any]:
    raw = csv_path.read_bytes()
    csv_text = _maybe_unzip_to_csv_text(raw)
    rows = _parse_generic_leaderboard_rows(csv_text)
    payload = {
        "source": source_name,
        "competition_id": CODALAB_OLD[2] if source_name == "codalab-old" else CODALAB_NEW[2],
        "base_url": CODALAB_OLD[1] if source_name == "codalab-old" else CODALAB_NEW[1],
        "results_url": RESULT_URLS[source_name],
        "fetched_at": _utc_now_iso(),
        "note": "Loaded from local CSV: {}".format(str(csv_path)),
        "rows": rows,
    }
    _write_json(cache_path, payload)
    return payload


def _codalab_load_or_fetch_from_results_csv(
    cache_path: Path,
    refresh: bool,
    base_url: str,
    competition_id: int,
    results_id: int,
    source_name: str,
    insecure: bool,
) -> Dict[str, Any]:
    if cache_path.exists() and not refresh:
        cached = json.loads(_read_text(cache_path))
        if _is_placeholder_payload(cached):
            print(
                "WARN: {} cache is placeholder/empty; run with --refresh-codalab (and --insecure if needed) to fetch.".format(
                    source_name
                ),
                file=sys.stderr,
            )
        return cached

    if not refresh and not cache_path.exists():
        raise RuntimeError(
            "Cache missing for {} ({}); run with --refresh-codalab to fetch once.".format(source_name, cache_path)
        )

    raw = _codalab_fetch_results_csv(base_url, competition_id, results_id, insecure=insecure)
    csv_text = _maybe_unzip_to_csv_text(raw)
    rows = _parse_generic_leaderboard_rows(csv_text)
    payload = {
        "source": source_name,
        "competition_id": competition_id,
        "base_url": base_url,
        "results_url": RESULT_URLS[source_name],
        "results_id": results_id,
        "fetched_at": _utc_now_iso(),
        "rows": rows,
    }
    _write_json(cache_path, payload)
    return payload


def _codalab_parse_rows(leaderboard: Dict[str, Any]) -> List[Dict[str, Any]]:
    headers = leaderboard.get("headers") or []
    scores = leaderboard.get("scores") or []

    # Map header labels -> index in values[]
    labels = [str(h.get("label") or h.get("name") or "").strip() for h in headers]
    norm = [_norm_key(x) for x in labels]

    def idx_for(cands: Iterable[str]) -> Optional[int]:
        cn = [_norm_key(c) for c in cands]
        for i, n in enumerate(norm):
            if n in cn:
                return i
        for i, n in enumerate(norm):
            for c in cn:
                if c and c in n:
                    return i
        return None

    auc_i = idx_for(["auc"])
    mrr_i = idx_for(["mrr"])
    ndcg5_i = idx_for(["ndcg@5", "ndcg5"])
    ndcg10_i = idx_for(["ndcg@10", "ndcg10"])

    rows: List[Dict[str, Any]] = []
    for s in scores:
        entry = None
        if isinstance(s, list) and len(s) >= 2:
            entry = s[1]
        elif isinstance(s, dict):
            entry = s
        if not isinstance(entry, dict):
            continue

        team = (
            entry.get("team_name")
            or entry.get("team")
            or entry.get("username")
            or entry.get("user_name")
            or ""
        )
        team = str(team).strip()
        if not team:
            continue

        values = entry.get("values") or []
        # values items are commonly {"val": "..."}.
        def val_at(i: Optional[int]) -> Optional[float]:
            if i is None:
                return None
            if i < 0 or i >= len(values):
                return None
            v = values[i]
            if isinstance(v, dict) and "val" in v:
                return _parse_float(v.get("val"))
            return _parse_float(v)

        dt = _parse_date_any(
            entry.get("submitted_at")
            or entry.get("submission_date")
            or entry.get("date")
        )

        rows.append(
            {
                "team": team,
                "auc": val_at(auc_i),
                "mrr": val_at(mrr_i),
                "ndcg5": val_at(ndcg5_i),
                "ndcg10": val_at(ndcg10_i),
                "date_iso": _date_iso(dt),
                "date_display": _format_date_display(dt),
            }
        )
    return rows


def _codalab_load_or_fetch(
    cache_path: Path,
    refresh: bool,
    base_url: str,
    competition_id: int,
    phase_regex: str,
    source_name: str,
) -> Dict[str, Any]:
    if cache_path.exists() and not refresh:
        cached = json.loads(_read_text(cache_path))
        if not _is_placeholder_payload(cached):
            return cached
        # Cache exists but is a placeholder/empty snapshot; try fetching once to initialize.
        try:
            return _codalab_load_or_fetch(
                cache_path,
                refresh=True,
                base_url=base_url,
                competition_id=competition_id,
                phase_regex=phase_regex,
                source_name=source_name,
            )
        except Exception as e:
            print("WARN: {} init refresh failed; using cached placeholder: {}".format(source_name, e), file=sys.stderr)
            return cached

    if not refresh and not cache_path.exists():
        raise RuntimeError(
            "Cache missing for {} ({}); run with --refresh-codalab to fetch once.".format(
                source_name, cache_path
            )
        )

    phases = _codalab_try_fetch_phases(base_url, competition_id)
    phase = _codalab_pick_phase(phases, phase_regex)
    phase_id = phase.get("id") or phase.get("pk") or phase.get("phase_id")
    if phase_id is None:
        raise RuntimeError("Could not determine CodaLab phase id for competition {}".format(competition_id))

    lb = _codalab_try_fetch_leaderboard_data(base_url, competition_id, phase_id)
    payload = {
        "source": source_name,
        "competition_id": competition_id,
        "base_url": base_url,
        "results_url": RESULT_URLS[source_name],
        "phase": {"id": phase_id, "raw": phase},
        "fetched_at": _utc_now_iso(),
        "rows": _codalab_parse_rows(lb),
    }
    _write_json(cache_path, payload)
    return payload


def _bootstrap_rows_from_index_static_table(index_path: Path) -> List[Dict[str, Any]]:
    """
    One-time bootstrap: extract the currently hard-coded leaderboard rows in index.html.
    This is best-effort and only intended to seed a cache when upstream CodaLab fetch
    isn't available from the local environment.
    """
    html = _read_text(index_path)
    m = re.search(
        r"<h1 id=\"leaderboard\">[\s\S]*?<table[^>]*performanceTable[^>]*>([\s\S]*?)</table>",
        html,
    )
    if not m:
        return []

    table_inner = m.group(1)
    row_html = re.findall(r"<tr class='leaderboardline(?:mask)?'>[\s\S]*?</tr>", table_inner)
    rows: List[Dict[str, Any]] = []

    def strip_tags(s: str) -> str:
        s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
        s = re.sub(r"<[^>]+>", "", s)
        return re.sub(r"\s+", " ", s).strip()

    for rh in row_html:
        # Extract <td> contents
        tds = re.findall(r"<td[^>]*>([\s\S]*?)</td>", rh)
        if len(tds) < 6:
            continue
        rank_text = strip_tags(tds[0])
        # rank cell includes rank and date; rank is first token
        rank_parts = rank_text.split()
        if not rank_parts:
            continue
        team = strip_tags(tds[1])
        auc = _parse_float(strip_tags(tds[2]))
        mrr = _parse_float(strip_tags(tds[3]))
        ndcg5 = _parse_float(strip_tags(tds[4]))
        ndcg10 = _parse_float(strip_tags(tds[5]))
        # Preserve the exact date string as it appears in the legacy HTML.
        date_display = None
        ms = re.search(r"<span[^>]*class=\"date label label-default\"[^>]*>([\s\S]*?)</span>", tds[0])
        if ms:
            date_display = strip_tags(ms.group(1)) or None

        rows.append(
            {
                "team": team,
                "auc": auc,
                "mrr": mrr,
                "ndcg5": ndcg5,
                "ndcg10": ndcg10,
                "date_iso": None,
                "date_raw": date_display,
                "date_display": date_display,
            }
        )
    return rows


def _combine_sources(source_payloads: List[Dict[str, Any]]) -> Dict[str, Any]:
    combined_rows: List[Dict[str, Any]] = []
    sources_meta: List[Dict[str, Any]] = []
    for payload in source_payloads:
        source = payload.get("source") or "unknown"
        comp_id = payload.get("competition_id")
        results_url = payload.get("results_url") or RESULT_URLS.get(source)
        rows = payload.get("rows") or []
        if rows:
            sources_meta.append(
                {
                    "source": source,
                    "competition_id": comp_id,
                    "results_url": results_url,
                    "fetched_at": payload.get("fetched_at"),
                }
            )
        for r in rows:
            row = dict(r)
            row["source"] = source
            row["competition_id"] = comp_id
            row["results_url"] = results_url
            combined_rows.append(row)

    combined_rows.sort(key=_metric_sort_key, reverse=True)
    for i, r in enumerate(combined_rows, 1):
        r["rank"] = i

    # "Date of Last Entry" in a merged table is ambiguous; keep the per-row date fields and
    # expose a global update timestamp as well.
    return {
        "generated_at": _utc_now_iso(),
        "sources": sources_meta,
        "rows": combined_rows,
    }


def _render_leaderboard_rows_html(rows: List[Dict[str, Any]]) -> str:
    """
    Render <tr> rows matching the legacy index.html leaderboard UI.
    Keep markup structure identical: classes, <p>, <span class="date ...">, and <b> metrics.
    """

    def fmt_metric(v: Any) -> str:
        try:
            n = float(v)
        except Exception:
            return ""
        return "{:.4f}".format(n)

    out: List[str] = []
    for i, r in enumerate(rows, 1):
        cls = "leaderboardline" if (i % 2 == 1) else "leaderboardlinemask"
        rank = r.get("rank") or i
        date_text = r.get("date_display") or r.get("date_raw") or ""
        team = r.get("team") or ""

        auc = fmt_metric(r.get("auc"))
        mrr = fmt_metric(r.get("mrr"))
        ndcg5 = fmt_metric(r.get("ndcg5"))
        ndcg10 = fmt_metric(r.get("ndcg10"))

        # Match the legacy indentation & line breaks as closely as possible.
        out.append("                            <tr class='{}'>".format(cls))
        out.append("                                <td>")
        out.append("                                    <p class=\"word-break2\">")
        out.append("                                        {}".format(rank))
        out.append("                                    </p><span class=\"date label label-default\">{}</span>".format(date_text))
        out.append("                                </td>")
        out.append("                                <td class=\"word-break\">")
        out.append("                                    {}".format(team))
        out.append("                                </td>")
        out.append("                                <td class=\"word-break\">")
        out.append("                                    <b>{}</b>".format(auc))
        out.append("                                </td>")
        out.append("                                <td class=\"word-break\">")
        out.append("                                    <b>{}</b>".format(mrr))
        out.append("                                </td>")
        out.append("                                <td class=\"word-break\">")
        out.append("                                    <b>{}</b>".format(ndcg5))
        out.append("                                </td>")
        out.append("                                <td class=\"word-break\">")
        out.append("                                    <b>{}</b>".format(ndcg10))
        out.append("                                </td>")
        out.append("                            </tr>")
    return "\n".join(out) + ("\n" if out else "")


def _update_index_html_leaderboard(index_path: Path, combined: Dict[str, Any]) -> None:
    html = _read_text(index_path)
    anchor = html.find('<h1 id="leaderboard">')
    if anchor == -1:
        raise RuntimeError("Could not find leaderboard section in index.html")

    # Find the leaderboard table within the leaderboard section.
    table_start = html.find('<table class="table performanceTable"', anchor)
    if table_start == -1:
        raise RuntimeError("Could not find leaderboard table in index.html")
    # Replace from the beginning of the line to avoid doubling indentation.
    line_start = html.rfind("\n", 0, table_start)
    table_replace_start = (line_start + 1) if line_start != -1 else table_start
    table_end = html.find("</table>", table_start)
    if table_end == -1:
        raise RuntimeError("Could not find end of leaderboard table in index.html")
    table_end += len("</table>")

    rows = combined.get("rows") or []
    rows_html = _render_leaderboard_rows_html(rows)
    header_html = "\n".join(
        [
            "                        <table class=\"table performanceTable\">",
            "                            <tr class='leaderboardhead'>",
                "                                <th>",
                    "                                    Rank",
                "                                </th>",
                "                                <th>",
                    "                                    Team",
                "                                </th>",
                "                                <th>",
                    "                                    AUC",
                "                                </th>",
                "                                <th>",
                    "                                    MRR",
                "                                </th>",
                "                                <th>",
                    "                                    nDCG@5",
                "                                </th>",
                "                                <th>",
                    "                                    nDCG@10",
                "                                </th>",
            "                            </tr>",
        ]
    )
    new_table_html = header_html + "\n" + rows_html + "                        </table>"

    html2 = html[:table_replace_start] + new_table_html + html[table_end:]

    # Remove any legacy JS we previously injected right after the table (leaderboard.js + inline renderer).
    html2 = re.sub(
        r"\s*<script\s+src=\"\./assets/data/leaderboard\.js\"></script>\s*<script[\s\S]*?</script>",
        "",
        html2,
        count=1,
    )

    index_path.write_text(html2, encoding="utf-8")


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="assets/data/leaderboard.json", help="Combined leaderboard JSON output path.")
    ap.add_argument(
        "--output-js",
        default="",
        help="Optional JS output that assigns the payload to window.MIND_LEADERBOARD.",
    )
    ap.add_argument(
        "--write-index",
        default="",
        help="If set, rewrite the Leaderboard table in this index.html to match the legacy UI markup.",
    )
    ap.add_argument(
        "--cache-dir",
        default="assets/data/leaderboard_sources",
        help="Directory for per-source cached snapshots.",
    )
    ap.add_argument(
        "--phase-regex",
        default=r"(?i)official\\s*test|official",
        help="Regex used to select the CodaLab phase (default targets Official Test).",
    )
    ap.add_argument(
        "--codalab-old-results-id",
        type=int,
        default=CODALAB_OLD_OFFICIAL_RESULTS_ID,
        help="CodaLab legacy results id for Official Test (used with /results/<id>/data).",
    )
    ap.add_argument(
        "--codalab-new-results-id",
        type=int,
        default=CODALAB_NEW_OFFICIAL_RESULTS_ID,
        help="CodaLab new-site results id for Official Test (used with /results/<id>/data).",
    )
    ap.add_argument(
        "--codalab-old-csv",
        default="",
        help="Optional path to a locally downloaded CodaLab legacy results CSV (avoids network/TLS issues).",
    )
    ap.add_argument(
        "--codalab-new-csv",
        default="",
        help="Optional path to a locally downloaded CodaLab new-site results CSV (avoids network/TLS issues).",
    )
    ap.add_argument(
        "--codabench-phase-id",
        type=int,
        default=CODABENCH_DEFAULT_PHASE_ID,
        help="CodaBench phase id for results.csv export (default targets the results-tab phase).",
    )
    ap.add_argument(
        "--codabench-method",
        default="scrape",
        choices=("scrape", "csv", "auto"),
        help="How to fetch CodaBench results. 'scrape' uses Playwright on the public Results tab (recommended).",
    )
    ap.add_argument(
        "--refresh-codalab",
        action="store_true",
        help="Refetch CodaLab NEW-SITE leaderboard even if a cache exists (legacy is treated as frozen).",
    )
    ap.add_argument(
        "--refresh-codalab-old",
        action="store_true",
        help="Refetch CodaLab legacy leaderboard via /results/<id>/data (normally avoided to preserve the archived snapshot).",
    )
    ap.add_argument(
        "--refresh-codabench",
        action="store_true",
        help="Refetch CodaBench leaderboard even if a cache exists.",
    )
    ap.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification for HTTP requests (use only if your network injects a self-signed proxy cert).",
    )
    ap.add_argument(
        "--bootstrap-codalab-old-from-index",
        default="",
        help="If provided and CodaLab-old cache is missing, seed it by parsing the current index.html table.",
    )
    args = ap.parse_args(argv)

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    codalab_old_cache = cache_dir / "codalab-old_24122_official-test.json"
    codalab_new_cache = cache_dir / "codalab-new_420_official-test.json"
    codabench_cache = cache_dir / "codabench_13955.csv-export.json"

    payloads: List[Dict[str, Any]] = []

    # CodaLab (old) - cache-first; optionally bootstrap from existing HTML.
    if args.bootstrap_codalab_old_from_index:
        idx = Path(args.bootstrap_codalab_old_from_index)
        rows = _bootstrap_rows_from_index_static_table(idx)
        if rows:
            _write_json(
                codalab_old_cache,
                {
                    "source": "codalab-old",
                    "competition_id": CODALAB_OLD[2],
                    "base_url": CODALAB_OLD[1],
                    "results_url": RESULT_URLS["codalab-old"],
                    "phase": {"id": None, "raw": {"bootstrap": True, "note": "Seeded from index.html static table"}},
                    "fetched_at": _utc_now_iso(),
                    "rows": rows,
                },
            )

    try:
        if args.codalab_old_csv:
            payloads.append(_codalab_load_from_local_csv(codalab_old_cache, "codalab-old", Path(args.codalab_old_csv)))
        else:
            payloads.append(
                _codalab_load_or_fetch_from_results_csv(
                    codalab_old_cache,
                    refresh=bool(args.refresh_codalab_old),
                    base_url=CODALAB_OLD[1],
                    competition_id=CODALAB_OLD[2],
                    results_id=args.codalab_old_results_id,
                    source_name="codalab-old",
                    insecure=args.insecure,
                )
            )
    except Exception as e:
        print("WARN: codalab-old unavailable: {}".format(e), file=sys.stderr)

    # CodaLab (new) - cache-first.
    try:
        if args.codalab_new_csv:
            payloads.append(_codalab_load_from_local_csv(codalab_new_cache, "codalab-new", Path(args.codalab_new_csv)))
        else:
            payloads.append(
                _codalab_load_or_fetch_from_results_csv(
                    codalab_new_cache,
                    refresh=bool(args.refresh_codalab),
                    base_url=CODALAB_NEW[1],
                    competition_id=CODALAB_NEW[2],
                    results_id=args.codalab_new_results_id,
                    source_name="codalab-new",
                    insecure=args.insecure,
                )
            )
    except Exception as e:
        print("WARN: codalab-new unavailable: {}".format(e), file=sys.stderr)

    # CodaBench - refreshable.
    try:
        # Keep phase_id in cache in case it changes; allow override from CLI as well.
        if codabench_cache.exists():
            try:
                cached = json.loads(_read_text(codabench_cache))
                cached["phase_id"] = int(args.codabench_phase_id)
                _write_json(codabench_cache, cached)
            except Exception:
                pass
        payloads.append(
            _codabench_load_or_fetch(
                codabench_cache,
                refresh=args.refresh_codabench,
                base_url=CODABENCH[1],
                competition_id=CODABENCH[2],
                insecure=args.insecure,
                method=args.codabench_method,
            )
        )
    except urllib.error.HTTPError as e:
        if e.code == 403:
            print(
                "ERROR: codabench HTTP 403 (Forbidden). The CSV export endpoint may require authentication.\n"
                "Use --codabench-method=scrape (default) or set env vars CODABENCH_BEARER_TOKEN and/or CODABENCH_COOKIE\n"
                "(for GitHub Actions: repository secrets CODABENCH_BEARER_TOKEN / CODABENCH_COOKIE).",
                file=sys.stderr,
            )
        else:
            print("ERROR: codabench HTTP error {} while fetching leaderboard CSV export".format(e.code), file=sys.stderr)
        raise
    except Exception as e:
        print("ERROR: codabench unavailable: {}".format(e), file=sys.stderr)
        raise

    combined = _combine_sources(payloads)
    out_path = Path(args.output)
    _write_json(out_path, combined)
    if args.output_js:
        _write_js_global(Path(args.output_js), "MIND_LEADERBOARD", combined)
    if args.write_index:
        _update_index_html_leaderboard(Path(args.write_index), combined)
    print("Wrote {}".format(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
