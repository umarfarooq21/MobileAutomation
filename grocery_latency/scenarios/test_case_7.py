"""Test Case 7 (TASK 7): Same as Test Case 6, plus cart qty +3 on first item.

Produce +×5 → Cart → start recording → increase first cart item quantity by
tapping + three times → then TC6 checkout (See more slots → last slot →
payment 2nd card → address 2nd → Deliver).

Does not place the order.
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
    looks_like_login_wall,
    tap_dismissibles,
)
from grocery_latency.markers import MarkerLog
from grocery_latency.recording import save_recording_bytes, start_ios_screen_recording
from grocery_latency.scenarios.test_case_2 import _open_cart_page, _verify_five_in_cart
from grocery_latency.scenarios.test_case_3 import _mark_step
from grocery_latency.scenarios.test_case_5 import (
    SEARCH_FALLBACK_QUERY,
    TARGET_ITEMS,
    _add_five_produce_or_search,
)
from grocery_latency.scenarios.test_case_6 import (
    CART_RECORDING_SECONDS,
    _tc6_checkout_edits,
)

TEST_CASE_NAME = "Test Case 7"
TEST_CASE_ID = "test_case_7"
CART_QTY_INCREASE_BY = 3


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _tap_center(driver, cx: float, cy: float) -> None:
    driver.execute_script(
        "mobile: tap",
        {"x": int(round(cx)), "y": int(round(cy))},
    )


def _find_first_cart_qty_plus(driver) -> tuple[float, float] | None:
    """Return (cx, cy) for the topmost cart-line quantity + control."""
    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0

    candidates: list[tuple[float, float, float, float]] = []

    # Live cart steppers: "Increase quantity by one, Quantity is 1, <item>".
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(name BEGINSWITH[c] "Increase quantity" OR '
            'label BEGINSWITH[c] "Increase quantity" OR '
            'name CONTAINS[c] "Increase quantity by one" OR '
            'label CONTAINS[c] "Increase quantity by one")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:10]:
        try:
            r = el.rect or {}
            y = float(r.get("y", 0))
            x = float(r.get("x", 0))
            w = float(r.get("width", 0))
            hh = float(r.get("height", 0))
            if y < 260 or y > h * 0.90:
                continue
            if w < 18 or hh < 18 or w > 140:
                continue
            candidates.append((y, x, x + w / 2.0, y + hh / 2.0))
        except Exception:  # noqa: BLE001
            continue

    if not candidates:
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND visible == 1 AND '
                '(label == "+" OR name == "+")',
            )
        except Exception:  # noqa: BLE001
            els = []
        for el in els[:16]:
            try:
                r = el.rect or {}
                y = float(r.get("y", 0))
                x = float(r.get("x", 0))
                w = float(r.get("width", 0))
                hh = float(r.get("height", 0))
                if y < 280 or y > h * 0.90:
                    continue
                if w < 18 or hh < 18 or w > 90 or hh > 90:
                    continue
                candidates.append((y, x, x + w / 2.0, y + hh / 2.0))
            except Exception:  # noqa: BLE001
                continue

    if not candidates:
        return None
    # First product = topmost visible stepper +.
    candidates.sort(key=lambda t: (round(t[0] / 8.0), t[1]))
    _y, _x, cx, cy = candidates[0]
    return cx, cy


def _increase_first_cart_item_qty(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    increase_by: int = CART_QTY_INCREASE_BY,
) -> None:
    """After recording starts: tap the first cart item's qty + N times (image)."""
    print(
        f"Step: on Cart, increase 1st product quantity by +{increase_by} …"
    )
    hit = _find_first_cart_qty_plus(driver)
    if hit is None:
        _mark_step(
            outcome,
            "cart_first_item_qty_plus",
            status="failed",
            reason="first cart qty + not found",
        )
        raise AssertionError(
            "Could not find the first cart item quantity + control"
        )

    cx, cy = hit
    print(f"  first cart qty + @ ({cx:.0f},{cy:.0f})")
    markers.mark("cart_first_qty_plus_found", x=cx, y=cy)
    _mark_step(
        outcome,
        "cart_first_item_qty_plus",
        status="ok",
        x=cx,
        y=cy,
        increase_by=increase_by,
    )

    # Reuse coordinates — re-finding the + each tap was ~10s/tap on cart page source.
    taps = 0
    for i in range(increase_by):
        try:
            _tap_center(driver, cx, cy)
            taps += 1
            markers.mark(
                "cart_first_qty_plus_tapped",
                index=i + 1,
                x=cx,
                y=cy,
            )
            print(f"  cart qty + tap {i + 1}/{increase_by}")
            time.sleep(0.12)
        except Exception as exc:  # noqa: BLE001
            print(f"  cart qty + tap {i + 1} failed: {exc}")
            break

    outcome["cart_first_qty_plus_taps"] = taps
    outcome["cart_first_qty_increased_by"] = taps
    _mark_step(
        outcome,
        "cart_first_item_qty_increase",
        status="ok" if taps >= increase_by else "partial",
        taps=taps,
        increase_by=increase_by,
    )
    if taps < increase_by:
        raise AssertionError(
            f"Only tapped first cart item qty + {taps}/{increase_by} times"
        )
    print(f"  first cart item quantity increased by {taps}")
    time.sleep(0.12)


def run_test_case_7(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """TASK 7: TC6 flow + cart first-item qty +3 after recording starts."""
    if retailer != "amazon":
        raise ValueError(
            f"{TEST_CASE_NAME} is Amazon-only (do not invent Walmart/Instacart)."
        )

    plan = describe_session(settings, retailer, bundle_id)
    plan["scenario"] = TEST_CASE_ID
    plan["test_case"] = TEST_CASE_NAME
    plan["dry_run"] = dry_run
    plan["study_mode"] = "cancel_test"
    rec_limit = CART_RECORDING_SECONDS
    plan["recording"] = True
    plan["recording_starts_at"] = "cart_page"
    plan["recording_duration_seconds"] = rec_limit
    plan["target_items"] = TARGET_ITEMS
    plan["cart_first_qty_increase_by"] = CART_QTY_INCREASE_BY
    plan["add_strategy"] = "home_grocery_produce_plus_or_search"
    plan["place_order"] = False
    plan["steps"] = [
        "Start new automation session (kill prior Amazon if running)",
        "Open Amazon App (cold start)",
        'Tap home "Grocery" tab',
        'Tap "Produce" aisle under search bar',
        f"Add {TARGET_ITEMS} different Produce items via listing +",
        f'If no products: search "{SEARCH_FALLBACK_QUERY}" then add {TARGET_ITEMS}',
        "Open Cart screen",
        f"START screen recording on Cart for {rec_limit}s",
        f"On Cart: increase 1st product quantity by tapping + {CART_QTY_INCREASE_BY} times",
        'Tap "Proceed to checkout"',
        'Dismiss Prime popup via close/X, Cancel, or "No thanks" if shown',
        'On Place Your Order: tap "See more delivery slots"',
        "Select the last delivery slot in the expanded grid",
        'Tap "Change payment method"',
        "Select 2nd payment card → Continue",
        'Tap "Change delivery or pickup address"',
        'Select 2nd address → "Deliver to this address"',
        "Stop screen recording (do not place order)",
    ]
    plan["notes"] = (
        f"{TEST_CASE_NAME} (TASK 7) — same as TC6, plus after recording starts "
        f"increase the first cart item qty by +{CART_QTY_INCREASE_BY}, then "
        "See more slots → last slot → payment 2nd card → address 2nd → Deliver. "
        "Does not place the order."
    )

    if dry_run:
        print(json.dumps({"status": "dry_run_ok", "plan": plan}, indent=2))
        return {"status": "dry_run_ok", "plan": plan}

    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    prefix = f"{retailer}_{TEST_CASE_ID}_{stamp}"
    result_path = settings.artifacts_dir / f"{prefix}.json"
    page_source_path = settings.artifacts_dir / f"{prefix}_page.xml"
    screenshot_path = settings.artifacts_dir / f"{prefix}_blocker.png"
    video_path = settings.artifacts_dir / f"{TEST_CASE_ID}_{stamp}.mp4"

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
        "items_added_count": 0,
        "cart_page_opened": False,
        "cart_five_items_verified": False,
        "recording_started": False,
        "proceed_checkout_tapped": False,
        "place_order": False,
        "delivery_slot_changed": False,
        "cart_first_qty_increased_by": 0,
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
                time.sleep(0.3)
                outcome["cold_start_terminated"] = True
            else:
                outcome["cold_start_terminated"] = False
            print(f"Activating {bundle_id} (cold start) …")
            driver.activate_app(bundle_id)
            time.sleep(0.5)
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

        time.sleep(0.25)
        dismissed = tap_dismissibles(driver, rounds=1)
        outcome["dismissed"] = dismissed
        if dismissed:
            print(f"Dismissed: {dismissed}")

        if looks_like_login_wall(driver):
            pause = max(0, settings.login_pause_seconds)
            print(f"Login wall suspected — pausing {pause}s for human assist.")
            markers.mark("login_pause_start", seconds=pause)
            time.sleep(pause)
            markers.mark("login_pause_end")
            tap_dismissibles(driver, rounds=1)

        _add_five_produce_or_search(driver, markers, outcome)
        _open_cart_page(driver, markers, outcome)

        print(
            f"START RECORDING at Cart page for {rec_limit}s "
            f"(checkout steps run while recording) …"
        )
        try:
            start_ios_screen_recording(driver, time_limit=rec_limit)
            recording = True
            outcome["recording_started"] = True
            markers.mark("recording_started_at_cart", duration_seconds=rec_limit)
            print(f"Screen recording started — recording for up to {rec_limit}s …")
        except Exception as exc:  # noqa: BLE001
            outcome["recording_started"] = False
            outcome["recording_start_error"] = str(exc)
            print(f"Screen recording failed to start: {exc}")
            raise AssertionError(
                f"Could not start screen recording on cart: {exc}"
            ) from exc

        _verify_five_in_cart(
            driver,
            markers,
            outcome,
            expected=TARGET_ITEMS,
            badge_before_adds=outcome.get("cart_count_before_adds"),
        )
        _increase_first_cart_item_qty(
            driver, markers, outcome, increase_by=CART_QTY_INCREASE_BY
        )
        _tc6_checkout_edits(driver, markers, outcome)

        time.sleep(0.2)
        markers.mark(
            "test_case_7_stopped_after_address",
            items=outcome.get("items_added_count"),
            cart_qty_plus=outcome.get("cart_first_qty_increased_by"),
            checkout_steps=outcome.get("checkout_steps"),
            slot=outcome.get("delivery_slot_label"),
        )
        print("Stopping process after address change (order not placed) …")

        outcome["status"] = "ok"
        outcome["message"] = (
            f"{TEST_CASE_NAME} OK — added {outcome.get('items_added_count')} "
            f"via Grocery→Produce/+; cart → record → 1st item qty "
            f"+{outcome.get('cart_first_qty_increased_by')} → See more slots → "
            f"last slot → 2nd payment → Continue → 2nd address → Deliver"
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
                    print("WARNING: stop_recording_screen returned empty payload")
                markers.mark("recording_stopped", saved=bool(video_b64))
            except Exception as rec_exc:  # noqa: BLE001
                outcome["recording_stop_error"] = str(rec_exc)
                outcome["recording_saved"] = False
                print(f"Recording stop error: {rec_exc}")
        elif recording:
            outcome["recording_saved"] = False
        if driver is not None:
            try:
                driver.quit()
                print("Appium session stopped.")
            except Exception as quit_exc:  # noqa: BLE001
                print(f"driver.quit warning: {quit_exc}")
        try:
            result_path.write_text(json.dumps(outcome, indent=2, default=str))
            print(f"Wrote {result_path}")
        except Exception as write_exc:  # noqa: BLE001
            print(f"Failed to write result JSON: {write_exc}")
