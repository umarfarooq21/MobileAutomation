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


def _tap_checkout_item_details_view(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """On Checkout, open Item details via the View link."""
    print('Step: on Checkout tap Item details "View" …')
    # Payment section is below Item details — scroll up first so View is on screen.
    for _ in range(3):
        if _checkout_payment_screen_visible(driver):
            _scroll_up(driver, percent=0.45)
            time.sleep(_FAST)
        else:
            break

    for attempt in range(4):
        strategies = (
            (AppiumBy.ACCESSIBILITY_ID, "View Item details"),
            (
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND visible == 1 AND '
                '(label == "View Item details" OR name == "View Item details" OR '
                'label CONTAINS[c] "View Item details")',
            ),
            (AppiumBy.ACCESSIBILITY_ID, "View"),
            (
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND visible == 1 AND '
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
            views = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND visible == 1 AND '
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


def _scroll_item_details_list(driver, *, direction: str = "down") -> None:
    """Scroll inside Item details sheet without dismissing it.

    Global ``_scroll_up`` swipes downward from mid-screen and closes the modal.
    Keep the gesture in the sheet body (below the title, above Done).
    """
    try:
        size = driver.get_window_size() or {}
        w = int(size.get("width") or 390)
        h = int(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        w, h = 390, 844
    # Sheet list band roughly y 0.28–0.78.
    if direction == "up":
        from_y, to_y = int(h * 0.38), int(h * 0.62)
    else:
        from_y, to_y = int(h * 0.62), int(h * 0.38)
    try:
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.16,
                "fromX": int(w * 0.50),
                "fromY": from_y,
                "toX": int(w * 0.50),
                "toY": to_y,
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _item_details_increments(
    driver, *, min_y: float | None = None, y_hi_frac: float = 0.88
) -> list[tuple[float, float, float]]:
    """Visible qty + controls on Item details sheet (top → bottom)."""
    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
        win_w = float(size.get("width") or 390)
    except Exception:  # noqa: BLE001
        h, win_w = 844.0, 390.0
    candidates: list[tuple[float, float, float]] = []
    preds = (
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(name == "stepper.incrementButton" OR name CONTAINS[c] "increment" OR '
        'label == "+" OR name == "+")',
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label CONTAINS[c] "Increase" OR name CONTAINS[c] "Increase")',
    )
    for pred in preds:
        try:
            els = driver.find_elements(AppiumBy.IOS_PREDICATE, pred)
        except Exception:  # noqa: BLE001
            els = []
        for el in els[:24]:
            try:
                r = el.rect or {}
                y = float(r.get("y", 0))
                x = float(r.get("x", 0))
                ww = float(r.get("width", 0))
                hh = float(r.get("height", 0))
                name = (el.get_attribute("name") or "").lower()
                label = (el.get_attribute("label") or "").lower()
                if "decrement" in name or "decrease" in name or "remove" in label:
                    continue
                # Sheet body — avoid sticky Done footer / title.
                if y < 150 or y > h * y_hi_frac:
                    continue
                if min_y is not None and y < min_y:
                    continue
                if ww < 12 or hh < 12 or ww > 200:
                    continue
                # Prefer right-side steppers.
                if x + ww / 2.0 < win_w * 0.45:
                    continue
                candidates.append((y, x + ww / 2.0, y + hh / 2.0))
            except Exception:  # noqa: BLE001
                continue
        if candidates:
            break
    candidates.sort(key=lambda t: (round(t[0] / 8.0), -t[1]))
    unique: list[tuple[float, float, float]] = []
    for c in candidates:
        if any(abs(c[0] - u[0]) < 28 for u in unique):
            continue
        unique.append(c)
    return unique


def _increase_item_details_qty(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    increase_by: int,
    expected_items: int | None = None,
) -> None:
    """On Item details sheet, raise EVERY line to units_each via qty +."""
    if increase_by <= 0:
        print("Step: Item details qty increase skipped (units_each=1) …")
        outcome["checkout_item_details_qty_increased_by"] = 0
        outcome["checkout_item_details_lines_increased"] = 0
        markers.mark("item_details_qty_increase_skipped", reason="units_each_is_1")
        return

    # Wait briefly for sheet chrome after View.
    end = time.monotonic() + 2.5
    while time.monotonic() < end and not _item_details_sheet_visible(driver):
        time.sleep(0.15)
    if not _item_details_sheet_visible(driver):
        raise AssertionError(
            "Item details sheet not open — cannot increase quantities"
        )

    print(
        f"Step: on Item details increase EVERY line by +{increase_by} "
        f"(to units_each={increase_by + 1})"
        + (f" — {expected_items} lines expected" if expected_items else "")
        + " …"
    )

    lines_done = 0
    stagnant = 0
    last_cy: float | None = None
    max_rounds = max(24, (expected_items or 8) * 6)

    for round_i in range(max_rounds):
        if expected_items is not None and lines_done >= expected_items:
            break
        if not _item_details_sheet_visible(driver):
            print("  Item details sheet closed unexpectedly — reopening …")
            try:
                _tap_checkout_item_details_view(driver, markers, outcome)
            except Exception as exc:  # noqa: BLE001
                print(f"  reopen failed: {exc}")
                break
            last_cy = None
            continue

        min_y = (last_cy + 36.0) if last_cy is not None else None
        hits = _item_details_increments(driver, min_y=min_y, y_hi_frac=0.88)
        if not hits:
            hits = _item_details_increments(driver, y_hi_frac=0.90)
            if hits and last_cy is not None and abs(hits[0][2] - last_cy) < 36:
                hits = []

        if not hits:
            stagnant += 1
            if stagnant >= 10:
                break
            # Sheet-safe scroll only — never global _scroll_up (dismisses modal).
            if stagnant in (3, 6, 9):
                _scroll_item_details_list(driver, direction="up")
            else:
                _scroll_item_details_list(driver, direction="down")
            time.sleep(_FAST)
            markers.mark(
                "item_details_qty_scrolled",
                index=round_i + 1,
                lines_done=lines_done,
                reason="hunt",
            )
            continue

        stagnant = 0
        _y, cx, cy = hits[0]
        taps = 0
        for i in range(increase_by):
            try:
                if i == 0 or i % 3 == 0:
                    near = _item_details_increments(
                        driver, min_y=(cy - 40.0), y_hi_frac=0.90
                    )
                    if near:
                        near.sort(key=lambda t: abs(t[2] - cy))
                        _y, cx, cy = near[0]
                _tap_center(driver, cx, cy)
                taps += 1
                markers.mark(
                    "item_details_qty_plus_tapped",
                    line=lines_done + 1,
                    index=i + 1,
                    x=cx,
                    y=cy,
                )
                time.sleep(0.08)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"  item details line #{lines_done + 1} "
                    f"qty + {i + 1} failed: {exc}"
                )
                break

        if taps < increase_by:
            print(
                f"  item details line #{lines_done + 1} qty +"
                f"{taps}/{increase_by} (partial)"
            )
        else:
            print(
                f"  item details line #{lines_done + 1} qty +"
                f"{taps}/{increase_by} @ ({cx:.0f},{cy:.0f})"
            )
        markers.mark(
            "item_details_line_qty_increased",
            line=lines_done + 1,
            taps=taps,
            expected=increase_by,
            y=cy,
        )
        if taps == 0:
            stagnant += 1
            _scroll_item_details_list(driver, direction="down")
            time.sleep(_FAST)
            continue
        lines_done += 1
        last_cy = cy
        # Nudge so the next line's stepper clears into view.
        _scroll_item_details_list(driver, direction="down")
        time.sleep(_FAST)

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
        expected_items=expected_items,
    )
    print(
        f"  item details qty + done: {lines_done} line(s) × +{increase_by}"
        + (
            f" (expected {expected_items})"
            if expected_items is not None
            else ""
        )
    )
    if expected_items is not None and lines_done < expected_items:
        raise AssertionError(
            f"Only increased qty on {lines_done}/{expected_items} "
            f"Item details line(s) by +{increase_by}"
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
    rec_limit = CART_RECORDING_SECONDS
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

        _tap_checkout_item_details_view(driver, markers, outcome)
        _increase_item_details_qty(
            driver,
            markers,
            outcome,
            increase_by=qty_plus,
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
