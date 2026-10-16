#!/usr/bin/env python3
"""Rebuild the "Test status" tables in README.md from local run results.

Reads every result JSON under artifacts/ (git-ignored, so this runs on the
machine that ran the tests) and rewrites the block between
<!-- STATUS:START --> and <!-- STATUS:END --> in README.md.

Run by the pre-commit hook (.githooks/pre-commit), or by hand:
    python3 scripts/update_readme_status.py
Standard library only. Without an artifacts/ folder it leaves README alone.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, "README.md")
ARTIFACTS = os.path.join(ROOT, "artifacts")
START, END = "<!-- STATUS:START -->", "<!-- STATUS:END -->"

SIZES = {
    "A": (1, 1), "B": (10, 1), "C": (1, 25), "D": (25, 1),
    "E": (5, 10), "F": (50, 1), "G": (50, 5), "H": (10, 10),
}

# (section title, file, result-file prefix, run command scenario)
SUITES = (
    ("Amazon", "test_case_amazon_final.py", "amazon_test_case_7_2_",
     "--retailer amazon --scenario test_case_amazon_final"),
    ("Walmart", "test_case_wallmart_final.py", "walmart_test_case_walmart_6_",
     "--retailer walmart --scenario test_case_wallmart_final"),
)


def _load(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) and "status" in data else None
    except Exception:  # noqa: BLE001
        return None


def _run_date(path: str) -> str:
    folder = os.path.basename(os.path.dirname(path))
    try:
        return datetime.strptime(folder, "%d-%b-%Y").strftime("%d %b %Y")
    except ValueError:
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%d %b %Y")


def _run_seconds(o: dict) -> float | None:
    if o.get("total_run_seconds"):
        return float(o["total_run_seconds"])
    markers = o.get("markers") or []
    if markers and markers[-1].get("monotonic_ms"):
        return markers[-1]["monotonic_ms"] / 1000.0
    return None


def _fmt_time(sec: float | None) -> str:
    if not sec:
        return "–"
    m, s = divmod(int(round(sec)), 60)
    return f"{m}m {s:02d}s"


def _exact(o: dict) -> bool | None:
    ex = o.get("basket_exact")
    if ex is None and int(o.get("basket_units_each") or 0) <= 1:
        ex = o.get("cart_items_exact")
    return ex


def _units(o: dict) -> str:
    total = o.get("basket_total_units")
    got = (o.get("checkout_units_after_cart_qty") or o.get("place_order_units")
           or (o.get("checkout_units") if int(o.get("basket_units_each") or 0) <= 1 else None))
    if got is None and _exact(o) and total:
        got = total
    return f"{got}/{total}" if got is not None and total else "–"


def _short_error(o: dict) -> str:
    err = (o.get("error") or "").splitlines()[0] if o.get("error") else ""
    err = re.sub(r"\s+", " ", err).replace("|", "/")
    return (err[:70] + "…") if len(err) > 70 else err


def _suite_rows(prefix: str) -> tuple[list[str], dict[str, int]]:
    runs: dict[str, list[tuple[float, str, dict]]] = {}
    for path in glob.glob(os.path.join(ARTIFACTS, "**", f"{prefix}*.json"), recursive=True):
        o = _load(path)
        if not o:
            continue
        letter = (o.get("basket_sample") or "").strip().upper()
        if letter not in SIZES:
            continue
        runs.setdefault(letter, []).append((os.path.getmtime(path), path, o))

    rows, counts = [], {"pass": 0, "partial": 0, "fail": 0, "none": 0}
    for letter, (items, units) in SIZES.items():
        size = f"{items} × {units}"
        history = sorted(runs.get(letter, []), key=lambda t: t[0])
        if not history:
            rows.append(f"| {letter} | {size} | ⚪ Not run yet | – | – | – | – |")
            counts["none"] += 1
            continue
        passes = [r for r in history if r[2].get("status") == "ok" and _exact(r[2])]
        # Older runs finished OK but predate the exactness check.
        unchecked = [r for r in history if r[2].get("status") == "ok" and _exact(r[2]) is None]
        flows = [r for r in history if r[2].get("status") == "ok"]
        best = (passes or unchecked or flows or history)[-1]
        _mt, path, o = best
        latest = history[-1]
        if passes:
            status, counts["pass"] = "✅ Pass", counts["pass"] + 1
        elif unchecked:
            status, counts["pass"] = "✅ Pass (exact not checked)", counts["pass"] + 1
        elif flows:
            status, counts["partial"] = "⚠️ Flow OK, not exact", counts["partial"] + 1
        else:
            status, counts["fail"] = "❌ Fail", counts["fail"] + 1
        ex = _exact(o)
        mark = "✅" if ex else "❌"
        exact = f"{mark} {_units(o)}" if ex is not None else "not recorded"
        lo = latest[2]
        if latest is best and lo.get("status") == "ok":
            last = "same run"
        else:
            last = (f"{_run_date(latest[1])}: "
                    + ("ok" if lo.get("status") == "ok" else f"failed — {_short_error(lo)}"))
        rows.append(
            f"| {letter} | {size} | {status} | {exact} | {_run_date(path)} | "
            f"{_fmt_time(_run_seconds(o))} | {last} |"
        )
    return rows, counts


def _instacart_rows() -> list[str]:
    files = sorted(glob.glob(os.path.join(ROOT, "grocery_latency", "scenarios", "basket_*.py")))
    rows = []
    for f in files:
        letter = os.path.basename(f)[len("basket_"):-3].upper()
        if letter not in SIZES:
            continue
        results = [o for p in glob.glob(os.path.join(ARTIFACTS, f"basket_{letter}", "**", "*.json"),
                                         recursive=True) if (o := _load(p))]
        ok = [o for o in results if o.get("status") == "ok"]
        status = "✅ Pass" if ok else ("❌ Fail" if results else "⚪ No runs recorded")
        items, units = SIZES[letter]
        rows.append(f"| {letter} | {items} × {units} | {status} | `basket_{letter}.py` |")
    return rows


def build_block() -> str:
    out = [START, "## Test status", "",
           "_Generated from local run results by `scripts/update_readme_status.py` "
           "(runs automatically on every commit). Best run per basket: an exact pass "
           "if there is one, otherwise the latest completed run._", ""]
    for title, fname, prefix, cmd in SUITES:
        rows, c = _suite_rows(prefix)
        out += [f"### {title} — `{fname}`", "",
                f"**{c['pass']} passed** · {c['partial']} flow-only · {c['fail']} failed · "
                f"{c['none']} not run  —  run: `python -m grocery_latency run {cmd} --basket-size <A-H>`",
                "",
                "| Basket | Size | Result | Exact units | Run date | Run time | Latest run |",
                "|---|---|---|---|---|---|---|", *rows, ""]
    ic = _instacart_rows()
    if ic:
        out += ["### Instacart — `basket_*.py`", "",
                "| Basket | Size | Result | File |", "|---|---|---|---|", *ic, ""]
    out.append(END)
    return "\n".join(out)


def main() -> int:
    if not os.path.isdir(ARTIFACTS):
        print("update_readme_status: no artifacts/ folder — README left unchanged")
        return 0
    with open(README, encoding="utf-8") as fh:
        text = fh.read()
    block = build_block()
    if START in text and END in text:
        new = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _m: block, text, flags=re.S)
    else:
        # First time: put the tables right after the intro paragraph.
        marker = "\n## Setup"
        idx = text.find(marker)
        new = (text[:idx] + "\n" + block + "\n" + text[idx:]) if idx >= 0 else (text + "\n" + block + "\n")
    if new != text:
        with open(README, "w", encoding="utf-8") as fh:
            fh.write(new)
        print("update_readme_status: README.md status tables updated")
    else:
        print("update_readme_status: README.md already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
