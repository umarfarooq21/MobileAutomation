"""Test Case Walmart 3: TC2 baskets, but qty + on Checkout Item details.

Walmart (iOS):
  1. Tap bottom tab "My Items"
  2. Fresh Produce (or All Items for large baskets)
  3. + Add once per item for basket sample A–H (qty 1 on listing)
  4. Open Cart → start screen recording
  5. Tap "Change" (delivery) → Reserve a time
  6. Tap "Change" (address) → pick 2nd saved address
  7. Toggle delivery day last↔2nd-last + time slot → tap "Reserve"
  8. Continue to checkout → Continue on Missing anything? → Checkout
  9. On Checkout: Item details → View → raise EVERY item to units_each via +
 10. Tap Done → remain on Checkout → stop recording

Does not place the order. Unit scaling is on Checkout Item details
(not on the Cart screen like Walmart TC1/TC2).
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

from appium.webdriver.common.appiumby import AppiumBy

from grocery_latency.config import Settings
from grocery_latency.driver import create_driver, describe_session
from grocery_latency.ios_helpers import (
    dump_page_source,
    find_first,
    looks_like_login_wall,
    tap_dismissibles,
)
from grocery_latency.markers import MarkerLog
from grocery_latency.recording import save_recording_bytes, start_ios_screen_recording
from grocery_latency.scenarios.test_case_walmart_1 import (
    CART_RECORDING_SECONDS,
    FRESH_PRODUCE_STRATEGIES,
    WALMART_DISMISS_LABELS,
    _apply_fast_wda_settings,
    _change_day_and_slot_then_reserve,
    _clear_cart_items,
    _dismiss_ask_sparky_if_shown,
    _mark_step,
    _open_cart,
    _scroll_down,
    _scroll_up,
    _select_second_address,
    _tap_cart_change_delivery,
    _tap_center,
    _tap_continue_to_checkout,
    _tap_fresh_produce,
    _tap_missing_anything_continue,
    _tap_my_items_tab,
    _tap_reserve_change_address,
    _wait_checkout_screen,
    _FAST,
    _MED,
)
from grocery_latency.scenarios.test_case_walmart_2 import (
    BASKET_SAMPLES,
    _add_basket_via_add_buttons,
    _tap_all_items_filter,
    resolve_basket_sample,
)

_MAX_QTY_RE = re.compile(r"max\s*(\d+)", re.I)
_CURRENT_QTY_RE = re.compile(r"current quantity is (\d+)", re.I)
_INC_ITEM_RE = re.compile(
    r"Increase quantity of (.+?), The current quantity is", re.I
)
# Coord taps only between scans. find_elements on Checkout ≈ 12s — minimize.
_QTY_TAP_GAP = 0.10
_QTY_SETTLE = 0.6
_QTY_MAX_ROUNDS = 6

TEST_CASE_NAME = "Test Case Walmart 3"
TEST_CASE_ID = "test_case_walmart_3"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _item_details_sheet_visible(driver) -> bool:
    """True when the Item details modal is open (not Checkout's section header)."""
    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    try:
        # Modal chrome: bottom Done CTA (Checkout uses Save Card, not Done).
        done = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Done" OR name == "Done")',
        )
        done_ok = False
        for el in done[:4]:
            r = el.rect or {}
            y = float(r.get("y", 0))
            ww = float(r.get("width", 0))
            if y > h * 0.70 and ww > 80:
                done_ok = True
                break
        return done_ok
    except Exception:  # noqa: BLE001
        return False


def _checkout_payment_screen_visible(driver) -> bool:
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label == "Save Card" OR name == "Save Card" OR '
            'label CONTAINS[c] "Pay with card" OR '
            'label CONTAINS[c] "Card number")',
        )
        return bool(els)
    except Exception:  # noqa: BLE001
        return False


def _dismiss_walmart_plus_checkout_splash(
    driver, markers: MarkerLog | None = None
) -> bool:
    """Dismiss Walmart+ trial sheet that covers Checkout (No thanks / Close)."""
    try:
        splash = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'name CONTAINS[c] "WalmartPlusSplash" OR '
            'label CONTAINS[c] "Confirm plan" OR '
            'label CONTAINS[c] "I agree to the terms"',
        )
    except Exception:  # noqa: BLE001
        splash = []
    if not splash:
        return False
    for by, value in (
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "No thanks" OR name == "No thanks" OR '
            'label == "No Thanks" OR name == "No Thanks")',
        ),
        (AppiumBy.ACCESSIBILITY_ID, "No thanks"),
        (AppiumBy.ACCESSIBILITY_ID, "close"),
        (AppiumBy.ACCESSIBILITY_ID, "Close"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Close" OR name == "Close" OR label == "close")',
        ),
    ):
        try:
            el, _, _ = find_first(driver, ((by, value),), timeout=1.2)
            el.click()
            if markers is not None:
                markers.mark("walmart_plus_splash_dismissed", by=by, value=value)
            print("  dismissed Walmart+ checkout splash (No thanks)")
            time.sleep(_MED)
            return True
        except TimeoutError:
            continue
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
        # "No thanks" sits under Confirm plan near the bottom.
        _tap_center(driver, w * 0.50, h * 0.88)
        time.sleep(_MED)
        if markers is not None:
            markers.mark("walmart_plus_splash_dismissed", via="coord")
        print("  dismissed Walmart+ checkout splash (coord)")
        return True
    except Exception:  # noqa: BLE001
        return False


def _tap_checkout_item_details_view(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """On Checkout, open Item details via the View link."""
    print('Step: on Checkout tap Item details "View" …')
    _dismiss_walmart_plus_checkout_splash(driver, markers)
    # Payment section is below Item details — scroll up first so View is on screen.
    for _ in range(3):
        if _checkout_payment_screen_visible(driver):
            _scroll_up(driver, percent=0.45)
            time.sleep(_FAST)
        else:
            break

    for attempt in range(4):
        _dismiss_walmart_plus_checkout_splash(driver, markers)
        strategies = (
            (AppiumBy.ACCESSIBILITY_ID, "View Item details"),
            (
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND '
                '(label == "View Item details" OR name == "View Item details" OR '
                'label CONTAINS[c] "View Item details")',
            ),
            (AppiumBy.ACCESSIBILITY_ID, "View"),
            (
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND '
                '(label == "View" OR name == "View")',
            ),
        )
        try:
            size = driver.get_window_size() or {}
            h = float(size.get("height") or 844)
            w = float(size.get("width") or 390)
        except Exception:  # noqa: BLE001
            h, w = 844.0, 390.0

        el = None
        by = value = ""
        try:
            # Prefer the right-side View next to "Item details".
            # Do not require visible==1 — splash overlays often mark it false.
            views = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND '
                '(label == "View Item details" OR name == "View Item details" OR '
                'label == "View" OR name == "View")',
            )
            picks = []
            for v in views[:10]:
                try:
                    r = v.rect or {}
                    y = float(r.get("y", 0))
                    x = float(r.get("x", 0))
                    lab = (v.get_attribute("label") or v.get_attribute("name") or "")[
                        :40
                    ]
                    if 120 < y < h * 0.82:
                        # Prefer right-side controls and explicit Item-details label.
                        score = y + (200.0 if "item details" in lab.lower() else 0.0)
                        score += x  # rightward View wins ties
                        picks.append((score, y, x, v, lab))
                except Exception:  # noqa: BLE001
                    continue
            if picks:
                picks.sort(key=lambda t: (-("item details" in t[4].lower()), -t[2], t[1]))
                _score, _y, _x, el, lab = picks[0]
                by, value = AppiumBy.IOS_PREDICATE, f"View:{lab}"
            else:
                el, by, value = find_first(driver, strategies, timeout=2.0)
        except TimeoutError:
            el = None

        if el is not None:
            try:
                el.click()
            except Exception:  # noqa: BLE001
                r = el.rect or {}
                _tap_center(
                    driver,
                    float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0,
                    float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0,
                )
            markers.mark("checkout_item_details_view", by=by, value=value)
            _mark_step(
                outcome, "checkout_item_details_view", status="ok", by=by, value=value
            )
            print(f"  Item details View: {by} = {value!r}")
            time.sleep(_MED)
            if _item_details_sheet_visible(driver):
                return
            # Wrong tap may open payment / scroll away — recover.
            if _checkout_payment_screen_visible(driver):
                print("  landed on payment — scrolling back to Item details …")
                for _ in range(4):
                    _scroll_up(driver, percent=0.45)
                    time.sleep(_FAST)
            continue

        # Hunt for View: slight down first, then up if payment shows.
        if _checkout_payment_screen_visible(driver):
            _scroll_up(driver, percent=0.40)
        else:
            _scroll_down(driver, percent=0.22)
        time.sleep(_FAST)
        print(f"  View not confirmed (attempt {attempt + 1}/4)")

    # Coord fallback near typical Item details row (upper-mid, right).
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
        for cy_frac in (0.48, 0.42, 0.55):
            _tap_center(driver, w * 0.88, h * cy_frac)
            time.sleep(_MED)
            if _item_details_sheet_visible(driver):
                markers.mark("checkout_item_details_view", via="coord", y_frac=cy_frac)
                _mark_step(
                    outcome, "checkout_item_details_view", status="ok", via="coord"
                )
                print(f"  Item details View → coord y_frac={cy_frac}")
                return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f'Could not tap Item details "View": {exc}') from exc

    raise AssertionError("Item details sheet did not open after View")


def _scroll_item_details_list(
    driver, *, direction: str = "down", percent: float = 0.14
) -> None:
    """Scroll inside Item details sheet without dismissing it.

    Keep a short drag in the sheet body only — long swipes dismiss the modal.
    """
    try:
        size = driver.get_window_size() or {}
        w = int(size.get("width") or 390)
        h = int(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        w, h = 390, 844
    # Stay in mid sheet (below title, above Done). Short travel only.
    delta = max(40, int(h * max(0.08, min(percent, 0.22))))
    mid = int(h * 0.52)
    if direction == "up":
        from_y, to_y = mid - delta // 2, mid + delta // 2
    else:
        from_y, to_y = mid + delta // 2, mid - delta // 2
    try:
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.12,
                "fromX": int(w * 0.50),
                "fromY": from_y,
                "toX": int(w * 0.50),
                "toY": to_y,
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _item_details_increment_els(
    driver, *, min_y: float | None = None, y_hi_frac: float = 0.88
) -> list[tuple[object, float, float, float]]:
    """Visible Item details qty + buttons as (el, y, cx, cy), top → bottom."""
    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
        win_w = float(size.get("width") or 390)
    except Exception:  # noqa: BLE001
        h, win_w = 844.0, 390.0
    candidates: list[tuple[object, float, float, float]] = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(name == "stepper.incrementButton" OR '
            'label CONTAINS[c] "Increase quantity")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:30]:
        try:
            r = el.rect or {}
            y = float(r.get("y", 0))
            x = float(r.get("x", 0))
            ww = float(r.get("width", 0))
            hh = float(r.get("height", 0))
            # Predicate already selects increment; skip extra name lookup (WDA snapshot).
            if y < 150 or y > h * y_hi_frac:
                continue
            if min_y is not None and y < min_y:
                continue
            if ww < 12 or hh < 12 or ww > 200:
                continue
            if x + ww / 2.0 < win_w * 0.45:
                continue
            candidates.append((el, y, x + ww / 2.0, y + hh / 2.0))
        except Exception:  # noqa: BLE001
            continue
    candidates.sort(key=lambda t: (round(t[1] / 8.0), -t[2]))
    unique: list[tuple[object, float, float, float]] = []
    for c in candidates:
        if any(abs(c[1] - u[1]) < 28 for u in unique):
            continue
        unique.append(c)
    return unique


def _item_details_increments(
    driver, *, min_y: float | None = None, y_hi_frac: float = 0.88
) -> list[tuple[float, float, float]]:
    """Visible qty + controls on Item details sheet (top → bottom) as (y,cx,cy)."""
    return [
        (y, cx, cy)
        for _el, y, cx, cy in _item_details_increment_els(
            driver, min_y=min_y, y_hi_frac=y_hi_frac
        )
    ]


def _parse_increment_label(label: str) -> tuple[str, int | None]:
    """Product key + current qty from one increment-button label."""
    key = ""
    m = _INC_ITEM_RE.search(label or "")
    if m:
        key = m.group(1).strip()[:48]
    qm = _CURRENT_QTY_RE.search(label or "")
    qty = int(qm.group(1)) if qm else None
    return key, qty


def _read_increment_label(el) -> tuple[str, int | None]:
    try:
        label = el.get_attribute("label") or ""
    except Exception:  # noqa: BLE001
        return "", None
    return _parse_increment_label(label)


def _snapshot_item_details_rows(
    driver, *, y_hi_frac: float = 0.72
) -> list[tuple[float, float, str, int | None]]:
    """One a11y scan → (cx, cy, item_key, qty) in the tappable band only.

    Keep y below ~0.72 so + is not under Done. Coord taps only afterward.
    """
    rows: list[tuple[float, float, str, int | None]] = []
    for el, _y, cx, cy in _item_details_increment_els(
        driver, y_hi_frac=y_hi_frac
    ):
        key, qty = _read_increment_label(el)
        rows.append((cx, cy, key, qty))
    return rows


def _burst_coord_plus(driver, *, cx: float, cy: float, n: int) -> int:
    """Rapid + taps at fixed coords — no element/tree lookups."""
    taps = 0
    for _ in range(max(0, n)):
        _tap_center(driver, cx, cy)
        taps += 1
        time.sleep(_QTY_TAP_GAP)
    return taps


def _qty_el_for_key(
    driver, item_key: str, *, near_y: float
) -> tuple[object, float, float, int | None] | None:
    """Targeted find for one SKU's + button → (el, cx, cy, qty)."""
    if not item_key:
        return None
    safe = item_key.replace("\\", "\\\\").replace('"', '\\"')[:40]
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            'name == "stepper.incrementButton" AND '
            f'label CONTAINS[c] "{safe}"',
        )
    except Exception:  # noqa: BLE001
        return None
    best: tuple[float, object, float, float, int | None] | None = None
    for el in els[:6]:
        try:
            r = el.rect or {}
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            ww = float(r.get("width", 0))
            hh = float(r.get("height", 0))
            if ww < 8 or hh < 8:
                continue
            cx, cy = x + ww / 2.0, y + hh / 2.0
            _key, qty = _read_increment_label(el)
            score = abs(cy - near_y)
            if best is None or score < best[0]:
                best = (score, el, cx, cy, qty)
        except Exception:  # noqa: BLE001
            continue
    if best is None:
        return None
    return best[1], best[2], best[3], best[4]


def _qty_for_key_cheap(
    driver, item_key: str, *, near_y: float
) -> tuple[float, float, int | None] | None:
    """Single targeted find for one SKU (coords + qty only)."""
    hit = _qty_el_for_key(driver, item_key, near_y=near_y)
    if hit is None:
        return None
    _el, cx, cy, qty = hit
    return cx, cy, qty


def _bring_row_into_band(
    driver,
    item_key: str,
    *,
    cx: float,
    cy: float,
    y_safe: float,
    y_top: float = 170.0,
) -> tuple[object | None, float, float, int | None]:
    """Re-locate one SKU and nudge-scroll until its + is tappable.

    Raising a line reflows the sheet, so rows below shift down under the
    footer (visible=false) where taps silently miss.
    """
    el: object | None = None
    qty: int | None = None
    try:
        h = float((driver.get_window_size() or {}).get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    for _ in range(6):
        found = _qty_el_for_key(driver, item_key, near_y=cy)
        if found is None:
            _scroll_item_details_list(driver, direction="down", percent=0.18)
            time.sleep(_FAST)
            continue
        el, cx, cy, qty = found
        # Prefer mid-band (clear of title + Done). Skip off-screen (y < 0).
        if y_top <= cy <= y_safe:
            return el, cx, cy, qty
        aim = h * 0.42
        _scroll_item_details_list(
            driver,
            direction="down" if cy > y_safe else "up",
            percent=max(0.12, min(0.22, abs(cy - aim) / h)),
        )
        time.sleep(_FAST)
    return el, cx, cy, qty


def _click_plus(el, driver, *, cx: float, cy: float) -> bool:
    """Tap the + via elementId / right-biased coords — never el.click().

    Walmart nests steppers under Remove in the a11y tree; el.click() and
    left-biased taps can activate Remove and delete the line.
    """
    try:
        eid = getattr(el, "id", None)
        if eid:
            driver.execute_script("mobile: tap", {"elementId": eid})
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        r = el.rect or {}
        x = float(r.get("x", 0))
        y = float(r.get("y", 0))
        ww = float(r.get("width", 0))
        hh = float(r.get("height", 0))
        if ww >= 8 and hh >= 8:
            # Bias right so the hit stays on + (Remove sits to the left).
            cx = x + ww * 0.72
            cy = y + hh / 2.0
    except Exception:  # noqa: BLE001
        pass
    try:
        _tap_center(driver, cx, cy)
        return True
    except Exception:  # noqa: BLE001
        return False


def _unfinished_item_keys(
    driver, *, done_keys: set[str], target_qty: int
) -> list[tuple[str, float, float, int]]:
    """All Item details SKUs still below target (incl. under Done / off-screen)."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            'name == "stepper.incrementButton"',
        )
    except Exception:  # noqa: BLE001
        return []
    out: list[tuple[str, float, float, int]] = []
    seen: set[str] = set()
    for el in els[:40]:
        try:
            key, qty = _read_increment_label(el)
            if not key or key in done_keys or key in seen:
                continue
            if qty is not None and qty >= target_qty:
                continue
            r = el.rect or {}
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            ww = float(r.get("width", 0))
            hh = float(r.get("height", 0))
            cx, cy = x + ww / 2.0, y + hh / 2.0
            seen.add(key)
            out.append((key, cx, cy, qty if qty is not None else 1))
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda t: t[2])
    return out


def _raise_one_item_details_line(
    driver,
    markers: MarkerLog,
    *,
    line_no: int,
    cx: float,
    cy: float,
    item_key: str,
    start_qty: int,
    target_qty: int,
    y_safe: float,
    relocate: bool = False,
) -> tuple[int, int]:
    """Raise one line to target via element.click() (coord taps miss after scroll).

    Walmart drops presses while syncing the cart, so verify and top up the
    shortfall. Never tap more than the verified shortfall.
    """
    current = start_qty if start_qty > 0 else 1
    taps = 0
    el: object | None = None
    if relocate and item_key:
        el, cx, cy, qty = _bring_row_into_band(
            driver, item_key, cx=cx, cy=cy, y_safe=y_safe
        )
        if qty is not None:
            current = start_qty = qty
    for _round in range(_QTY_MAX_ROUNDS):
        if current == target_qty:
            break
        if item_key:
            el, cx, cy, qty = _bring_row_into_band(
                driver, item_key, cx=cx, cy=cy, y_safe=y_safe
            )
            if qty is not None:
                current = qty
            if current == target_qty:
                break
        if current < target_qty:
            need = target_qty - current
            # Fresh element each burst — stale refs after scroll are dead.
            if el is None and item_key:
                hit = _qty_el_for_key(driver, item_key, near_y=cy)
                if hit is not None:
                    el, cx, cy, _q = hit
            for _ in range(need):
                if el is not None:
                    if not _click_plus(el, driver, cx=cx, cy=cy):
                        break
                else:
                    _tap_center(driver, cx, cy)
                taps += 1
                time.sleep(_QTY_TAP_GAP)
                # Rebind every few clicks — sheet reflows as qty grows.
                if taps % 3 == 0 and item_key:
                    hit = _qty_el_for_key(driver, item_key, near_y=cy)
                    if hit is not None:
                        el, cx, cy, qty = hit
                        if qty is not None:
                            current = qty
                        if current >= target_qty:
                            break
                        try:
                            if str(el.get_attribute("enabled")).lower() == "false":
                                break
                        except Exception:  # noqa: BLE001
                            pass
        else:
            for _ in range(current - target_qty):
                _tap_center(driver, max(20.0, cx - 86.0), cy)
                taps += 1
                time.sleep(_QTY_TAP_GAP)
        time.sleep(_QTY_SETTLE)
        verified = _qty_el_for_key(driver, item_key, near_y=cy) if item_key else None
        if verified is None:
            el = None
            continue
        el, cx, cy, qty = verified
        if qty is not None:
            current = qty

    markers.mark(
        "item_details_qty_plus_tapped",
        line=line_no,
        taps=taps,
        start_qty=start_qty,
        final_qty=current,
        item=(item_key or "")[:32] or None,
        x=cx,
        y=cy,
    )
    if current < target_qty:
        raise AssertionError(
            f"Item details line #{line_no}: qty ×{current} "
            f"< target ×{target_qty} after {taps} + tap(s)"
            + (f" [{item_key}]" if item_key else "")
        )
    if current > target_qty:
        raise AssertionError(
            f"Item details line #{line_no}: qty ×{current} "
            f"> target ×{target_qty} (could not trim)"
        )
    return start_qty, current


def _increase_item_details_qty(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    target_qty: int,
    expected_items: int | None = None,
) -> None:
    """Raise every Item details line to ``target_qty`` — fast coord bursts.

    One screen scan per visible batch; tap all in-band rows before scrolling.
    Avoids per-tap / per-row full-tree snapshots (~12s each on Checkout).
    """
    increase_by = max(0, target_qty - 1)
    if target_qty <= 1:
        print("Step: Item details qty increase skipped (units_each=1) …")
        outcome["checkout_item_details_qty_increased_by"] = 0
        outcome["checkout_item_details_lines_increased"] = 0
        markers.mark("item_details_qty_increase_skipped", reason="units_each_is_1")
        return

    end = time.monotonic() + 2.5
    while time.monotonic() < end and not _item_details_sheet_visible(driver):
        time.sleep(0.12)
    if not _item_details_sheet_visible(driver):
        raise AssertionError(
            "Item details sheet not open — cannot increase quantities"
        )

    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    # Only tap steppers clearly above Done.
    y_safe = h * 0.70

    print(
        f"Step: on Item details raise EVERY line to ×{target_qty} "
        f"(fast coord burst)"
        + (f" — {expected_items} lines expected" if expected_items else "")
        + " …"
    )

    lines_done = 0
    stagnant = 0
    done_keys: set[str] = set()
    max_rounds = max(12, (expected_items or 8) * 3)

    for round_i in range(max_rounds):
        if expected_items is not None and lines_done >= expected_items:
            break
        if not _item_details_sheet_visible(driver):
            print("  Item details sheet closed — reopening …")
            try:
                _tap_checkout_item_details_view(driver, markers, outcome)
                stagnant = 0
                time.sleep(_MED)
            except Exception as exc:  # noqa: BLE001
                print(f"  reopen failed: {exc}")
                break
            continue

        # Always take the topmost unfinished SKU (incl. under Done). Visible-only
        # snapshots skip buried rows and jump to Roma while Yellow sits at ×1 —
        # later scrolls then can't find it if Remove was hit mid-raise.
        unfinished = _unfinished_item_keys(
            driver, done_keys=done_keys, target_qty=target_qty
        )
        rows = _snapshot_item_details_rows(driver, y_hi_frac=0.88)
        for _cx, _cy, key, qty in rows:
            if key and qty is not None and qty >= target_qty:
                done_keys.add(key)

        pending: list[tuple[float, float, str, int]] = []
        if unfinished:
            key, cx, cy, start_qty = unfinished[0]
            _el, cx, cy, qty = _bring_row_into_band(
                driver, key, cx=cx, cy=cy, y_safe=y_safe
            )
            if qty is not None:
                start_qty = qty
            if qty is not None and qty >= target_qty:
                done_keys.add(key)
                continue
            if 170 <= cy <= y_safe:
                pending = [(cx, cy, key, start_qty)]

        if not pending:
            stagnant += 1
            if stagnant >= 12:
                break
            _scroll_item_details_list(
                driver,
                direction="up" if stagnant in (4, 7, 10) else "down",
                percent=0.16,
            )
            time.sleep(_FAST)
            markers.mark(
                "item_details_qty_scrolled",
                index=round_i + 1,
                lines_done=lines_done,
                reason="hunt",
            )
            continue

        stagnant = 0
        # One row per round — raising qty reflows the sheet and invalidates
        # sibling coords (rows slide under Done → silent tap misses).
        cx, cy, key, start_qty = pending[0]
        if key and key in done_keys:
            continue
        line_no = lines_done + 1
        start_qty, final_qty = _raise_one_item_details_line(
            driver,
            markers,
            line_no=line_no,
            cx=cx,
            cy=cy,
            item_key=key,
            start_qty=start_qty,
            target_qty=target_qty,
            y_safe=y_safe,
            relocate=True,
        )
        print(
            f"  item details line #{line_no}: ×{start_qty}→×{final_qty} "
            f"(target ×{target_qty})"
            + (f" [{key[:36]}]" if key else "")
            + f" @ ({cx:.0f},{cy:.0f})"
        )
        markers.mark(
            "item_details_line_qty_increased",
            line=line_no,
            start_qty=start_qty,
            final_qty=final_qty,
            target_qty=target_qty,
            item=key[:48] if key else None,
            y=cy,
        )
        lines_done += 1
        if key:
            done_keys.add(key)
        # No forced scroll — next round rescans; hunt-scroll only when pending empty.

    outcome["checkout_item_details_qty_increased_by"] = increase_by
    outcome["checkout_item_details_lines_increased"] = lines_done
    _mark_step(
        outcome,
        "checkout_item_details_qty_increase",
        status=(
            "ok"
            if expected_items is None or lines_done >= expected_items
            else "partial"
        ),
        lines=lines_done,
        increase_by=increase_by,
        target_qty=target_qty,
        expected_items=expected_items,
    )
    print(
        f"  item details qty + done: {lines_done} line(s) → ×{target_qty}"
        + (
            f" (expected {expected_items})"
            if expected_items is not None
            else ""
        )
    )
    if expected_items is not None and lines_done < expected_items:
        raise AssertionError(
            f"Only increased qty on {lines_done}/{expected_items} "
            f"Item details line(s) to ×{target_qty}"
        )


def _tap_item_details_done(driver, markers: MarkerLog, outcome: dict) -> None:
    print('Step: on Item details tap "Done" …')
    strategies = (
        (AppiumBy.ACCESSIBILITY_ID, "Done"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Done" OR name == "Done")',
        ),
    )
    try:
        el, by, value = find_first(driver, strategies, timeout=3.0)
        el.click()
    except TimeoutError:
        try:
            size = driver.get_window_size() or {}
            w = float(size.get("width") or 390)
            h = float(size.get("height") or 844)
            _tap_center(driver, w * 0.50, h * 0.92)
            by, value = "coord", "Done"
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f'Could not tap Item details "Done": {exc}') from exc
    markers.mark("item_details_done", by=by, value=value)
    _mark_step(outcome, "item_details_done", status="ok", by=by, value=value)
    print(f"  Done: {by} = {value!r}")
    time.sleep(_MED)


def run_test_case_walmart_3(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
    basket_size: str | None = None,
) -> dict:
    """Walmart TC3: basket A–H + qty + on Checkout Item details (not Cart)."""
    if retailer != "walmart":
        raise ValueError(
            f"{TEST_CASE_NAME} is Walmart-only (use --retailer walmart)."
        )

    sample = resolve_basket_sample(basket_size)
    qty_plus = max(0, sample.units_each - 1)
    # Item details qty+ for E/H/G often exceeds the shared 240s Cart cap;
    # Appium timeLimit cuts the file mid-increments. Keep recording until
    # Done → Checkout, then stop_recording_screen() saves the full clip.
    rec_limit = max(
        600,
        int(getattr(settings, "recording_time_limit_seconds", 600) or 600),
        CART_RECORDING_SECONDS,
    )
    plan = describe_session(settings, retailer, bundle_id)
    plan["scenario"] = TEST_CASE_ID
    plan["test_case"] = TEST_CASE_NAME
    plan["dry_run"] = dry_run
    plan["study_mode"] = "cancel_test"
    plan["recording"] = True
    plan["recording_starts_at"] = "cart_page"
    plan["recording_duration_seconds"] = rec_limit
    plan["basket_sample"] = sample.letter
    plan["basket_num_items"] = sample.num_items
    plan["basket_units_each"] = sample.units_each
    plan["basket_total_units"] = sample.total_units
    plan["basket_matrix"] = {
        k: {"num_items": v.num_items, "units_each": v.units_each, "total": v.total_units}
        for k, v in BASKET_SAMPLES.items()
    }
    plan["checkout_item_details_qty_increase_by"] = qty_plus
    plan["place_order"] = False
    plan["steps"] = [
        "Start new automation session (kill prior Walmart if running)",
        "Open Walmart App (cold start)",
        'Tap bottom tab "My Items"',
        "Clear existing cart items if any (so + Add is available)",
        'Tap "Fresh Produce" filter (All Items for large baskets)',
        (
            f"Basket {sample.letter}: tap blue + Add once on {sample.num_items} "
            f"item(s) (qty=1 on My Items; units set on Checkout Item details)"
        ),
        "Open Cart screen",
        f"START screen recording on Cart for {rec_limit}s",
        'Tap "Change" (delivery) on Cart → Reserve a time',
        'Tap "Change" (address) → select 2nd saved address',
        'Toggle delivery day last↔2nd-last + time slot last↔2nd-last → tap "Reserve"',
        'Tap "Continue to checkout"',
        'On "Missing anything?" tap "Continue"',
        "Land on Checkout screen",
        'Tap Item details "View"',
        (
            f"On Item details: every line qty +{qty_plus} (to ×{sample.units_each})"
            if qty_plus
            else "On Item details: skip qty bump (units_each=1)"
        ),
        'Tap "Done" → remain on Checkout → stop recording (do not place order)',
    ]
    plan["notes"] = (
        f"{TEST_CASE_NAME} — basket {sample.letter} "
        f"({sample.num_items}×{sample.units_each}={sample.total_units}): "
        "same as Walmart TC2 through Checkout, but unit scaling is on "
        "Checkout → Item details → View (not Cart). Does not place the order."
    )

    if dry_run:
        print(json.dumps({"status": "dry_run_ok", "plan": plan}, indent=2))
        return {"status": "dry_run_ok", "plan": plan}

    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    prefix = f"{retailer}_{TEST_CASE_ID}_{sample.letter}_{stamp}"
    result_path = settings.artifacts_dir / f"{prefix}.json"
    page_source_path = settings.artifacts_dir / f"{prefix}_page.xml"
    screenshot_path = settings.artifacts_dir / f"{prefix}_blocker.png"
    video_path = settings.artifacts_dir / f"{TEST_CASE_ID}_{sample.letter}_{stamp}.mp4"

    markers = MarkerLog()
    driver = None
    recording = False
    outcome: dict = {
        "status": "starting",
        "test_case": TEST_CASE_NAME,
        "plan": plan,
        "markers": markers.events,
        "selectors": {},
        "checkout_steps": {},
        "basket_sample": sample.letter,
        "basket_num_items": sample.num_items,
        "basket_units_each": sample.units_each,
        "basket_total_units": sample.total_units,
        "items_added_count": 0,
        "cart_page_opened": False,
        "recording_started": False,
        "place_order": False,
        "checkout_item_details_qty_increased_by": 0,
        "continue_to_checkout_tapped": False,
        "missing_anything_continue_tapped": False,
        "checkout_screen_reached": False,
    }

    def _dump_ui(tag: str) -> None:
        try:
            dump_page_source(driver, page_source_path)
            outcome["page_source_dump"] = str(page_source_path)
            print(f"Wrote page source ({tag}) → {page_source_path}")
        except Exception as dump_exc:  # noqa: BLE001
            outcome["page_source_dump_error"] = str(dump_exc)
        try:
            driver.get_screenshot_as_file(str(screenshot_path))
            outcome["screenshot"] = str(screenshot_path)
            print(f"Wrote screenshot ({tag}) → {screenshot_path}")
        except Exception as shot_exc:  # noqa: BLE001
            outcome["screenshot_error"] = str(shot_exc)

    try:
        print(f"Creating Appium session for {retailer} ({bundle_id}) …")
        driver = create_driver(settings, bundle_id)
        outcome["session_id"] = getattr(driver, "session_id", None)
        print(f"Session started: {outcome['session_id']}")
        _apply_fast_wda_settings(driver)

        try:
            state_before = driver.query_app_state(bundle_id)
            outcome["app_state_before_kill"] = (
                int(state_before) if state_before is not None else None
            )
            print(
                f"App state before kill: {outcome['app_state_before_kill']} "
                "(0=not installed, 1=not running, 2=background/suspended, "
                "3=background, 4=foreground)"
            )
            if outcome["app_state_before_kill"] and outcome["app_state_before_kill"] > 1:
                print(f"Terminating {bundle_id} for cold start …")
                driver.terminate_app(bundle_id)
                time.sleep(0.12)
                outcome["cold_start_terminated"] = True
            else:
                outcome["cold_start_terminated"] = False
            print(f"Activating {bundle_id} (cold start) …")
            driver.activate_app(bundle_id)
            time.sleep(0.22)
        except Exception as exc:  # noqa: BLE001
            outcome["cold_start_error"] = str(exc)
            print(f"Cold-start terminate/activate warning: {exc}")

        try:
            state = driver.query_app_state(bundle_id)
            outcome["app_state"] = int(state) if state is not None else None
            print(f"App state for {bundle_id}: {outcome['app_state']} (4=foreground)")
        except Exception as exc:  # noqa: BLE001
            outcome["app_state_error"] = str(exc)

        markers.mark("app_opened", app_state=outcome.get("app_state"))

        time.sleep(0.12)
        _dismiss_ask_sparky_if_shown(driver, markers)
        dismissed = tap_dismissibles(
            driver, labels=WALMART_DISMISS_LABELS, rounds=1
        )
        outcome["dismissed"] = dismissed
        if dismissed:
            print(f"Dismissed: {dismissed}")

        if looks_like_login_wall(driver):
            pause = max(0, settings.login_pause_seconds)
            print(f"Login wall suspected — pausing {pause}s for human assist.")
            markers.mark("login_pause_start", seconds=pause)
            time.sleep(pause)
            markers.mark("login_pause_end")
            tap_dismissibles(driver, labels=WALMART_DISMISS_LABELS, rounds=1)

        _tap_my_items_tab(driver, markers, outcome)
        _clear_cart_items(driver, markers, outcome)
        try:
            find_first(driver, FRESH_PRODUCE_STRATEGIES, timeout=1.2)
        except TimeoutError:
            _tap_my_items_tab(driver, markers, outcome)
        # Large baskets need the full My Items catalog (All Items).
        if sample.num_items >= 10:
            if not _tap_all_items_filter(driver, markers):
                try:
                    _tap_fresh_produce(driver, markers, outcome)
                except TimeoutError:
                    print("  Fresh Produce / All Items not found — continue …")
        else:
            try:
                _tap_fresh_produce(driver, markers, outcome)
            except TimeoutError:
                print("  Fresh Produce not found — falling back to All Items …")
                if not _tap_all_items_filter(driver, markers):
                    raise
        _add_basket_via_add_buttons(driver, markers, outcome, sample=sample)
        _open_cart(driver, markers, outcome)

        print(
            f"START RECORDING at Cart page for {rec_limit}s "
            f"(checkout steps run while recording) …"
        )
        try:
            start_ios_screen_recording(driver, time_limit=rec_limit)
            recording = True
            outcome["recording_started"] = True
            markers.mark(
                "recording_started_at_cart",
                duration_seconds=rec_limit,
                basket=sample.letter,
            )
            print(f"Screen recording started — recording for up to {rec_limit}s …")
        except Exception as exc:  # noqa: BLE001
            outcome["recording_started"] = False
            outcome["recording_start_error"] = str(exc)
            print(f"Screen recording failed to start: {exc}")
            raise AssertionError(
                f"Could not start screen recording on cart: {exc}"
            ) from exc

        _tap_cart_change_delivery(driver, markers, outcome)
        _tap_reserve_change_address(driver, markers, outcome)
        _select_second_address(driver, markers, outcome)
        _change_day_and_slot_then_reserve(driver, markers, outcome)
        # No cart qty bump — units are scaled on Checkout Item details.
        _tap_continue_to_checkout(driver, markers, outcome)
        _tap_missing_anything_continue(driver, markers, outcome)
        _wait_checkout_screen(driver, markers, outcome)
        _dismiss_walmart_plus_checkout_splash(driver, markers)

        _tap_checkout_item_details_view(driver, markers, outcome)
        _increase_item_details_qty(
            driver,
            markers,
            outcome,
            target_qty=sample.units_each,
            expected_items=sample.num_items,
        )
        _tap_item_details_done(driver, markers, outcome)
        _wait_checkout_screen(driver, markers, outcome)

        markers.mark(
            "test_case_walmart_3_stopped_on_checkout",
            basket=sample.letter,
            items=outcome.get("items_added_count"),
            units_each=sample.units_each,
            qty_plus=outcome.get("checkout_item_details_qty_increased_by"),
            lines=outcome.get("checkout_item_details_lines_increased"),
            checkout_steps=outcome.get("checkout_steps"),
        )
        print("Stopping process on Checkout screen (order not placed) …")

        outcome["status"] = "ok"
        outcome["message"] = (
            f"{TEST_CASE_NAME} OK — basket {sample.letter}: "
            f"{sample.num_items}×{sample.units_each}={sample.total_units} "
            f"(add {outcome.get('basket_add_elapsed_seconds')}s) → cart record → "
            f"Change delivery → Change address (2nd) → day/slot → Reserve → "
            f"Continue to checkout → Item details View → "
            f"all lines qty +{outcome.get('checkout_item_details_qty_increased_by')} "
            f"({outcome.get('checkout_item_details_lines_increased')} lines) → Done"
        )
        print(outcome["message"])
        return outcome
    except Exception as exc:  # noqa: BLE001
        outcome["status"] = "error"
        outcome["error_type"] = type(exc).__name__
        outcome["error"] = str(exc)
        print(f"{TEST_CASE_NAME} FAILED: {type(exc).__name__}: {exc}")
        if driver is not None and "page_source_dump" not in outcome:
            _dump_ui("error")
        return outcome
    finally:
        if driver is not None and recording:
            try:
                print("Stopping screen recording …")
                video_b64 = driver.stop_recording_screen()
                if video_b64:
                    save_recording_bytes(video_b64, video_path)
                    outcome["video"] = str(video_path)
                    outcome["recording_saved"] = True
                    print(f"Wrote playable recording → {video_path}")
                else:
                    outcome["recording_saved"] = False
                markers.mark("recording_stopped", saved=bool(video_b64))
            except Exception as rec_exc:  # noqa: BLE001
                outcome["recording_stop_error"] = str(rec_exc)
                print(f"Recording stop error: {rec_exc}")
        if driver is not None:
            try:
                driver.quit()
                print("Appium session stopped.")
            except Exception:  # noqa: BLE001
                pass
        try:
            result_path.write_text(
                json.dumps(outcome, indent=2, default=str), encoding="utf-8"
            )
            print(f"Wrote result → {result_path}")
        except Exception as write_exc:  # noqa: BLE001
            print(f"Result write error: {write_exc}")
