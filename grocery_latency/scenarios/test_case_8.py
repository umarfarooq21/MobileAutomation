"""Test Case 8 (TASK 8): Same as Test Case 7, then Place your order.

Produce +×5 → Cart → start recording → 1st item qty +3 → TC6 checkout
(See more slots → last slot → payment 2nd → address 2nd → Deliver) →
tap Place your order → after order success, stop screen recording.
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
from grocery_latency.scenarios.test_case_3 import (
    _mark_step,
    _screen_matches,
)
from grocery_latency.scenarios.test_case_5 import (
    SEARCH_FALLBACK_QUERY,
    TARGET_ITEMS,
    _add_five_produce_or_search,
)
from grocery_latency.scenarios.test_case_6 import (
    CART_RECORDING_SECONDS,
    _place_order_surface_visible,
    _scroll_slots_into_view,
    _tc6_checkout_edits,
)
from grocery_latency.scenarios.test_case_7 import (
    CART_QTY_INCREASE_BY,
    _increase_first_cart_item_qty,
)

TEST_CASE_NAME = "Test Case 8"
TEST_CASE_ID = "test_case_8"
# Extra headroom vs TC7 so place-order + confirmation fit inside the clip.
CART_RECORDING_SECONDS_TC8 = max(CART_RECORDING_SECONDS, 240)

# Button strategies kept for surface probes / success-adjacent UI.
PLACE_ORDER_BUTTON = (
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label == "Place your order" OR name == "Place your order" OR '
        'label == "Place order" OR name == "Place order" OR '
        'label == "Place Order" OR name == "Place Order")',
    ),
)

ORDER_SUCCESS = (
    (AppiumBy.ACCESSIBILITY_ID, "Order placed"),
    (AppiumBy.ACCESSIBILITY_ID, "Thanks"),
    (AppiumBy.ACCESSIBILITY_ID, "Thank you"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "order placed" OR name CONTAINS[c] "order placed" OR '
        'label CONTAINS[c] "Order placed" OR '
        'label CONTAINS[c] "Thanks for your order" OR '
        'name CONTAINS[c] "Thanks for your order" OR '
        'label CONTAINS[c] "Thank you for your order" OR '
        'name CONTAINS[c] "Thank you for your order" OR '
        'label CONTAINS[c] "Your order has been placed" OR '
        'name CONTAINS[c] "Your order has been placed" OR '
        'label CONTAINS[c] "We received your order" OR '
        'name CONTAINS[c] "We received your order" OR '
        'label CONTAINS[c] "You\'re all set" OR name CONTAINS[c] "You\'re all set" OR '
        'label CONTAINS[c] "Order confirmed" OR name CONTAINS[c] "Order confirmed" OR '
        'label BEGINSWITH[c] "Order #" OR name BEGINSWITH[c] "Order #" OR '
        'label CONTAINS[c] "Track your order" OR name CONTAINS[c] "Track your order"',
    ),
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _order_success_visible(driver, *, timeout: float = 0.6) -> bool:
    return _screen_matches(driver, ORDER_SUCCESS, timeout=timeout)


def _find_place_order_cta(driver) -> tuple[object, float, float, float, float] | None:
    """Return (el, cx, cy, w, h) for the sticky full-width Place your order button."""
    try:
        size = driver.get_window_size() or {}
        win_h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        win_h = 844.0

    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Place your order" OR name == "Place your order" OR '
            'label == "Place order" OR name == "Place order" OR '
            'label == "Place Order" OR name == "Place Order")',
        )
    except Exception:  # noqa: BLE001
        els = []

    # (priority, y, el, cx, cy, w, h) — prefer sticky bar under the legal blurb (~y 150–250).
    candidates: list[tuple[int, float, object, float, float, float, float]] = []
    for el in els[:8]:
        try:
            r = el.rect or {}
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            hh = float(r.get("height", 0))
            if w < 200 or hh < 28 or hh > 90:
                continue
            # Skip nav-title band (<140) and deep off-screen duplicates.
            if y < 140 or y > win_h * 0.92:
                continue
            cx = x + w / 2.0
            cy = y + hh / 2.0
            sticky = 0 if 140 <= y <= 280 else 1
            candidates.append((sticky, y, el, cx, cy, w, hh))
        except Exception:  # noqa: BLE001
            continue

    if not candidates:
        return None
    candidates.sort(key=lambda t: (t[0], t[1]))
    _prio, _y, el, cx, cy, w, hh = candidates[0]
    return el, cx, cy, w, hh


def _tap_place_order(driver, markers: MarkerLog, outcome: dict) -> None:
    """On the final Place Your Order screen, tap Place your order and confirm."""
    print('Step: tap "Place your order" …')

    # After Deliver, wait for Place Your Order surface + stable CTA.
    deadline = time.monotonic() + 14.0
    cta = None
    while time.monotonic() < deadline:
        if _place_order_surface_visible(driver):
            cta = _find_place_order_cta(driver)
            if cta is not None:
                # Stability check — rect can jump while the sticky bar settles.
                time.sleep(0.35)
                cta2 = _find_place_order_cta(driver)
                if cta2 is not None and abs(cta2[2] - cta[2]) < 12:
                    cta = cta2
                    break
        time.sleep(0.25)
    else:
        print("  Place Your Order surface not confirmed — probing for Place order CTA")
        cta = _find_place_order_cta(driver)

    hit = None
    for attempt in range(6):
        if cta is None:
            cta = _find_place_order_cta(driver)
        if cta is None:
            _scroll_slots_into_view(driver, direction="up")
            time.sleep(0.2)
            continue

        el, cx, cy, w, hh = cta
        print(
            f"  Place your order @ ({cx:.0f},{cy:.0f}) size={w:.0f}x{hh:.0f} "
            f"(attempt {attempt + 1})"
        )
        tapped = False
        try:
            el.click()
            tapped = True
        except Exception as click_exc:  # noqa: BLE001
            print(f"  element click failed ({click_exc}); coordinate tap")
        if not tapped:
            driver.execute_script(
                "mobile: tap", {"x": int(round(cx)), "y": int(round(cy))}
            )
        hit = (cx, cy, w, hh)
        time.sleep(1.5)

        if _order_success_visible(driver, timeout=0.5):
            break

        still = _find_place_order_cta(driver)
        if still is None:
            break

        # Still on checkout — second confirm tap on the (possibly moved) CTA.
        el2, cx2, cy2, w2, hh2 = still
        print(f"  Place your order still visible — confirm @ ({cx2:.0f},{cy2:.0f})")
        try:
            el2.click()
        except Exception:  # noqa: BLE001
            driver.execute_script(
                "mobile: tap", {"x": int(round(cx2)), "y": int(round(cy2))}
            )
        hit = (cx2, cy2, w2, hh2)
        markers.mark("place_order_confirmed", x=cx2, y=cy2)
        time.sleep(1.2)
        break

    if hit is None:
        _mark_step(
            outcome,
            "place_order",
            status="failed",
            reason="Place your order button not found",
        )
        raise AssertionError(
            "Could not find Place your order button on final checkout screen"
        )

    cx, cy, w, hh = hit
    markers.mark("place_order_tapped", x=cx, y=cy, w=w, h=hh)
    outcome["place_order_tapped"] = True
    _mark_step(
        outcome,
        "place_order",
        status="ok",
        x=cx,
        y=cy,
        w=w,
        h=hh,
    )
    tap_dismissibles(driver, rounds=1)


def _wait_order_success(
    driver, markers: MarkerLog, outcome: dict, *, timeout: float = 45.0
) -> None:
    """Block until Amazon shows an order-placed / thanks confirmation."""
    print("Step: wait for order success confirmation …")
    deadline = time.monotonic() + timeout
    gone_since: float | None = None
    while time.monotonic() < deadline:
        if _order_success_visible(driver, timeout=0.5):
            markers.mark("order_placed_confirmed")
            outcome["order_placed"] = True
            outcome["place_order"] = True
            _mark_step(outcome, "order_success", status="ok")
            print("  Order success confirmed")
            return
        # CTA gone for a few seconds and no longer on place-order chrome → likely placed.
        if _find_place_order_cta(driver) is None and not _place_order_surface_visible(
            driver
        ):
            if gone_since is None:
                gone_since = time.monotonic()
            elif time.monotonic() - gone_since >= 3.0:
                markers.mark("order_placed_confirmed", via="cta_gone")
                outcome["order_placed"] = True
                outcome["place_order"] = True
                _mark_step(outcome, "order_success", status="ok", via="cta_gone")
                print("  Order success inferred (Place your order CTA gone)")
                return
        else:
            gone_since = None
        tap_dismissibles(driver, rounds=1)
        time.sleep(0.4)

    _mark_step(
        outcome,
        "order_success",
        status="failed",
        reason="no order confirmation within timeout",
    )
    raise AssertionError(
        "Place your order tapped but order success confirmation was not detected"
    )


def run_test_case_8(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """TASK 8: TC7 flow + Place your order; stop recording after success."""
    if retailer != "amazon":
        raise ValueError(
            f"{TEST_CASE_NAME} is Amazon-only (do not invent Walmart/Instacart)."
        )

    plan = describe_session(settings, retailer, bundle_id)
    plan["scenario"] = TEST_CASE_ID
    plan["test_case"] = TEST_CASE_NAME
    plan["dry_run"] = dry_run
    plan["study_mode"] = "place_order_test"
    rec_limit = CART_RECORDING_SECONDS_TC8
    plan["recording"] = True
    plan["recording_starts_at"] = "cart_page"
    plan["recording_duration_seconds"] = rec_limit
    plan["target_items"] = TARGET_ITEMS
    plan["cart_first_qty_increase_by"] = CART_QTY_INCREASE_BY
    plan["add_strategy"] = "home_grocery_produce_plus_or_search"
    plan["place_order"] = True
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
        'Tap "Place your order" on the final screen',
        "After order success confirmation: STOP screen recording",
    ]
    plan["notes"] = (
        f"{TEST_CASE_NAME} (TASK 8) — same as TC7, then Place your order on the "
        "final checkout screen; stop recording only after order success."
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
    recording_stopped = False
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
        "place_order": True,
        "order_placed": False,
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

    def _stop_recording(*, why: str) -> None:
        nonlocal recording, recording_stopped
        if not recording or recording_stopped or driver is None:
            return
        try:
            print(f"Stopping screen recording ({why}) …")
            video_b64 = driver.stop_recording_screen()
            recording_stopped = True
            recording = False
            if video_b64:
                save_recording_bytes(video_b64, video_path)
                outcome["video"] = str(video_path)
                outcome["recording_saved"] = True
                print(f"Wrote playable recording → {video_path}")
            else:
                outcome["recording_saved"] = False
                print("WARNING: stop_recording_screen returned empty payload")
            markers.mark("recording_stopped", saved=bool(video_b64), why=why)
        except Exception as rec_exc:  # noqa: BLE001
            outcome["recording_stop_error"] = str(rec_exc)
            outcome["recording_saved"] = False
            recording_stopped = True
            recording = False
            print(f"Recording stop error: {rec_exc}")

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
            f"(checkout + place order run while recording) …"
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
        _tap_place_order(driver, markers, outcome)
        _wait_order_success(driver, markers, outcome, timeout=45.0)

        # Stop recording only after successful place-order confirmation.
        _stop_recording(why="order_success")

        time.sleep(0.2)
        markers.mark(
            "test_case_8_stopped_after_place_order",
            items=outcome.get("items_added_count"),
            cart_qty_plus=outcome.get("cart_first_qty_increased_by"),
            checkout_steps=outcome.get("checkout_steps"),
            slot=outcome.get("delivery_slot_label"),
            order_placed=outcome.get("order_placed"),
        )
        print("Stopping process after successful Place your order …")

        outcome["status"] = "ok"
        outcome["message"] = (
            f"{TEST_CASE_NAME} OK — added {outcome.get('items_added_count')} "
            f"via Grocery→Produce/+; cart → record → 1st item qty "
            f"+{outcome.get('cart_first_qty_increased_by')} → See more slots → "
            f"last slot → 2nd payment → Continue → 2nd address → Deliver → "
            f"Place your order (confirmed)"
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
        if recording and not recording_stopped:
            _stop_recording(why="finally")
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
