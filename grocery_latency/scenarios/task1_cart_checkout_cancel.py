"""TASK 1 continuation: cart → record → checkout → place order → Path A cancel."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from appium.webdriver.common.appiumby import AppiumBy

from grocery_latency.amazon_task1_common import (
    add_master_asin_to_cart,
    cold_start_amazon,
    dismiss_and_login_pause,
    open_cart_page,
    scroll_down,
)
from grocery_latency.config import Settings
from grocery_latency.driver import create_driver, describe_session
from grocery_latency.ios_helpers import (
    dump_page_source,
    element_textish,
    find_first,
    tap_dismissibles,
)
from grocery_latency.markers import MarkerLog
from grocery_latency.recording import save_recording_bytes, start_ios_screen_recording

PROCEED_CHECKOUT = (
    (AppiumBy.ACCESSIBILITY_ID, "Proceed to checkout"),
    (AppiumBy.ACCESSIBILITY_ID, "Proceed to Checkout"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(label CONTAINS[c] "Proceed to checkout" OR '
        'name CONTAINS[c] "Proceed to checkout" OR '
        'label CONTAINS[c] "Checkout" OR name CONTAINS[c] "Checkout")',
    ),
)

PLACE_ORDER = (
    (AppiumBy.ACCESSIBILITY_ID, "Place your order"),
    (AppiumBy.ACCESSIBILITY_ID, "Place order"),
    (AppiumBy.ACCESSIBILITY_ID, "Place Order"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(label CONTAINS[c] "Place your order" OR name CONTAINS[c] "Place your order" OR '
        'label == "Place order" OR name == "Place order" OR '
        'label CONTAINS[c] "Place Order")',
    ),
)

PAYMENT_OPEN = (
    (AppiumBy.ACCESSIBILITY_ID, "Change payment method"),
    (AppiumBy.ACCESSIBILITY_ID, "Payment method"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Payment" OR name CONTAINS[c] "Payment" OR '
        'label CONTAINS[c] "Visa" OR label CONTAINS[c] "Mastercard" OR '
        'label CONTAINS[c] "ending in"',
    ),
)

ADDRESS_OPEN = (
    (AppiumBy.ACCESSIBILITY_ID, "Change delivery address"),
    (AppiumBy.ACCESSIBILITY_ID, "Deliver to"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Deliver to" OR name CONTAINS[c] "Deliver to" OR '
        'label CONTAINS[c] "Delivery address" OR name CONTAINS[c] "Delivery address" OR '
        'label CONTAINS[c] "Change address"',
    ),
)

SLOT_OPEN = (
    (AppiumBy.ACCESSIBILITY_ID, "Delivery time"),
    (AppiumBy.ACCESSIBILITY_ID, "Choose a delivery time"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "delivery time" OR name CONTAINS[c] "delivery time" OR '
        'label CONTAINS[c] "Delivery slot" OR label CONTAINS[c] "Schedule" OR '
        'label CONTAINS[c] "Arriving"',
    ),
)

CS_ENTRY = (
    (AppiumBy.ACCESSIBILITY_ID, "Customer Service"),
    (AppiumBy.ACCESSIBILITY_ID, "Help"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Customer Service" OR name CONTAINS[c] "Customer Service" OR '
        'label == "Help" OR name == "Help"',
    ),
)

TRACK_ORDER = (
    (AppiumBy.ACCESSIBILITY_ID, "Track package"),
    (AppiumBy.ACCESSIBILITY_ID, "Track your package"),
    (AppiumBy.ACCESSIBILITY_ID, "Where is my stuff"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Track" OR name CONTAINS[c] "Track package" OR '
        'label CONTAINS[c] "Where is my stuff"',
    ),
)

YOUR_ORDERS = (
    (AppiumBy.ACCESSIBILITY_ID, "Your Orders"),
    (AppiumBy.ACCESSIBILITY_ID, "Orders"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Your Orders" OR name CONTAINS[c] "Your Orders" OR '
        'label == "Orders" OR name == "Orders"',
    ),
)

VIEW_EDIT_ORDER = (
    (AppiumBy.ACCESSIBILITY_ID, "View order details"),
    (AppiumBy.ACCESSIBILITY_ID, "View or edit order"),
    (AppiumBy.ACCESSIBILITY_ID, "Order details"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "View or edit" OR name CONTAINS[c] "View or edit" OR '
        'label CONTAINS[c] "View order" OR label CONTAINS[c] "Order details"',
    ),
)

CANCEL_ITEMS = (
    (AppiumBy.ACCESSIBILITY_ID, "Cancel items"),
    (AppiumBy.ACCESSIBILITY_ID, "Cancel Items"),
    (AppiumBy.ACCESSIBILITY_ID, "Cancel order"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(label CONTAINS[c] "Cancel items" OR name CONTAINS[c] "Cancel items" OR '
        'label CONTAINS[c] "Cancel order" OR label CONTAINS[c] "Cancel item")',
    ),
)

CANCEL_REASON = (
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(label CONTAINS[c] "Ordered by mistake" OR '
        'label CONTAINS[c] "No longer needed" OR '
        'label CONTAINS[c] "Found better price" OR '
        'label CONTAINS[c] "Other")',
    ),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeStaticText" AND '
        '(label CONTAINS[c] "Ordered by mistake" OR '
        'label CONTAINS[c] "No longer needed" OR '
        'label CONTAINS[c] "Other")',
    ),
)

CANCEL_CONFIRM = (
    (AppiumBy.ACCESSIBILITY_ID, "Cancel item"),
    (AppiumBy.ACCESSIBILITY_ID, "Cancel items"),
    (AppiumBy.ACCESSIBILITY_ID, "Confirm"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(label CONTAINS[c] "Cancel item" OR label == "Confirm" OR '
        'label CONTAINS[c] "Yes" OR name CONTAINS[c] "Cancel item")',
    ),
)

QTY_PLUS = (
    (AppiumBy.ACCESSIBILITY_ID, "+"),
    (AppiumBy.ACCESSIBILITY_ID, "Increase quantity"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(name == "+" OR label == "+" OR label CONTAINS[c] "Increase" OR '
        'name CONTAINS[c] "stepper increment" OR label CONTAINS[c] "Add one")',
    ),
)

MENU_TAB = (
    (AppiumBy.ACCESSIBILITY_ID, "menuTab"),
    (AppiumBy.ACCESSIBILITY_ID, "Menu"),
    (
        AppiumBy.IOS_PREDICATE,
        'name == "menuTab" OR label CONTAINS[c] "Menu"',
    ),
)

ME_TAB = (
    (AppiumBy.ACCESSIBILITY_ID, "meTab"),
    (AppiumBy.ACCESSIBILITY_ID, "Your Amazon"),
    (
        AppiumBy.IOS_PREDICATE,
        'name == "meTab" OR label CONTAINS[c] "Your Amazon" OR label CONTAINS[c] "Account"',
    ),
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _tap_first(driver, strategies, timeout: float = 12.0):
    el, by, value = find_first(driver, strategies, timeout=timeout)
    el.click()
    return by, value


def _try_tap(driver, strategies, timeout: float = 8.0):
    try:
        return _tap_first(driver, strategies, timeout=timeout)
    except TimeoutError:
        return None


def _mark_step(outcome: dict, key: str, *, status: str, **extra) -> None:
    outcome.setdefault("checkout_steps", {})[key] = {"status": status, **extra}


def _scroll_find_tap(driver, strategies, *, scrolls: int = 5, timeout: float = 4.0):
    for i in range(scrolls):
        hit = _try_tap(driver, strategies, timeout=timeout if i == 0 else 2.0)
        if hit:
            return hit
        scroll_down(driver)
        time.sleep(0.8)
    return None


def step_proceed_checkout(driver, markers: MarkerLog, outcome: dict) -> None:
    print("Step 4: Proceed to checkout …")
    hit = _scroll_find_tap(driver, PROCEED_CHECKOUT, scrolls=6)
    if not hit:
        raise AssertionError("Proceed to checkout not found on cart/checkout page")
    by, value = hit
    outcome["selectors"]["proceed_checkout"] = {"by": by, "value": value}
    markers.mark("proceed_checkout", by=by, value=value)
    _mark_step(outcome, "proceed_checkout", status="ok", by=by, value=value)
    time.sleep(3.0)
    tap_dismissibles(driver)


def step_raise_quantities(
    driver, markers: MarkerLog, outcome: dict, assigned_qty: int
) -> None:
    print(f"Step 5: Raise each item quantity to {assigned_qty} …")
    raised = 0
    # Tap quantity + until we reach assigned qty (best-effort across visible steppers).
    for round_i in range(max(1, assigned_qty)):
        plus_buttons = []
        for by, value in QTY_PLUS:
            try:
                for el in driver.find_elements(by, value):
                    try:
                        if el.is_displayed():
                            plus_buttons.append(el)
                    except Exception:  # noqa: BLE001
                        continue
                if plus_buttons:
                    break
            except Exception:  # noqa: BLE001
                continue
        if not plus_buttons:
            break
        for el in plus_buttons:
            try:
                el.click()
                raised += 1
                time.sleep(0.6)
            except Exception:  # noqa: BLE001
                continue
        markers.mark("quantity_plus_round", round=round_i, taps=len(plus_buttons))
    outcome["quantity_plus_taps"] = raised
    if raised == 0:
        _mark_step(
            outcome,
            "raise_quantity",
            status="skipped",
            reason="No quantity + controls found",
            assigned_quantity=assigned_qty,
        )
        markers.mark("raise_quantity_skipped")
    else:
        _mark_step(
            outcome,
            "raise_quantity",
            status="ok",
            taps=raised,
            assigned_quantity=assigned_qty,
        )
        markers.mark("raise_quantity_done", taps=raised, assigned_quantity=assigned_qty)
    time.sleep(1.0)


def _switch_alternate_option(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    step_key: str,
    open_strategies,
    option_predicate: str,
) -> None:
    """Open a picker and tap a second/alternate saved option if available."""
    open_hit = _scroll_find_tap(driver, open_strategies, scrolls=5)
    if not open_hit:
        _mark_step(
            outcome,
            step_key,
            status="skipped",
            reason=f"{step_key} entry control not found",
        )
        markers.mark(f"{step_key}_skipped", reason="entry_missing")
        return
    by, value = open_hit
    markers.mark(f"{step_key}_opened", by=by, value=value)
    time.sleep(1.5)

    options = []
    try:
        options = [
            el
            for el in driver.find_elements(AppiumBy.IOS_PREDICATE, option_predicate)
            if el.is_displayed()
        ]
    except Exception:  # noqa: BLE001
        options = []

    if len(options) < 2:
        # Try selecting the first non-selected row, or Done/close.
        if options:
            try:
                options[0].click()
                markers.mark(f"{step_key}_selected_only_option")
                _mark_step(
                    outcome,
                    step_key,
                    status="skipped",
                    reason="Only one saved option visible; tapped it / no alternate",
                )
            except Exception:  # noqa: BLE001
                _mark_step(
                    outcome,
                    step_key,
                    status="skipped",
                    reason="Could not select alternate option",
                )
        else:
            _mark_step(
                outcome,
                step_key,
                status="skipped",
                reason="No alternate options listed",
            )
            markers.mark(f"{step_key}_skipped", reason="no_options")
        # Dismiss picker if still open
        _try_tap(
            driver,
            (
                (AppiumBy.ACCESSIBILITY_ID, "Done"),
                (AppiumBy.ACCESSIBILITY_ID, "Close"),
                (AppiumBy.ACCESSIBILITY_ID, "Cancel"),
                (
                    AppiumBy.IOS_PREDICATE,
                    'label == "Done" OR label == "Close" OR label == "Cancel"',
                ),
            ),
            timeout=3.0,
        )
        time.sleep(1.0)
        return

    # Prefer second option as "different" from current.
    try:
        options[1].click()
        markers.mark(f"{step_key}_alternate_selected", index=1)
        _mark_step(outcome, step_key, status="ok", selected_index=1)
    except Exception as exc:  # noqa: BLE001
        _mark_step(outcome, step_key, status="skipped", reason=str(exc))
        markers.mark(f"{step_key}_failed", error=str(exc))

    _try_tap(
        driver,
        (
            (AppiumBy.ACCESSIBILITY_ID, "Done"),
            (AppiumBy.ACCESSIBILITY_ID, "Continue"),
            (AppiumBy.ACCESSIBILITY_ID, "Use this address"),
            (AppiumBy.ACCESSIBILITY_ID, "Select"),
            (
                AppiumBy.IOS_PREDICATE,
                'label == "Done" OR label == "Continue" OR '
                'label CONTAINS[c] "Use this" OR label == "Select"',
            ),
        ),
        timeout=4.0,
    )
    time.sleep(1.5)
    tap_dismissibles(driver)


def step_update_payment(driver, markers: MarkerLog, outcome: dict) -> None:
    print("Step 6: Update payment method …")
    _switch_alternate_option(
        driver,
        markers,
        outcome,
        step_key="update_payment",
        open_strategies=PAYMENT_OPEN,
        option_predicate=(
            'type == "XCUIElementTypeButton" OR type == "XCUIElementTypeCell" OR '
            'type == "XCUIElementTypeStaticText"'
        ),
    )


def step_update_address(driver, markers: MarkerLog, outcome: dict) -> None:
    print("Step 7: Update address …")
    _switch_alternate_option(
        driver,
        markers,
        outcome,
        step_key="update_address",
        open_strategies=ADDRESS_OPEN,
        option_predicate=(
            'type == "XCUIElementTypeButton" OR type == "XCUIElementTypeCell" OR '
            'type == "XCUIElementTypeStaticText"'
        ),
    )


def step_choose_delivery_slot(driver, markers: MarkerLog, outcome: dict) -> None:
    print("Step 8: Choose a different delivery slot …")
    _switch_alternate_option(
        driver,
        markers,
        outcome,
        step_key="delivery_slot",
        open_strategies=SLOT_OPEN,
        option_predicate=(
            'type == "XCUIElementTypeButton" OR type == "XCUIElementTypeCell" OR '
            'type == "XCUIElementTypeStaticText"'
        ),
    )


def step_place_order(driver, markers: MarkerLog, outcome: dict) -> None:
    print("Step 9: Place your order …")
    hit = _scroll_find_tap(driver, PLACE_ORDER, scrolls=8, timeout=5.0)
    if not hit:
        raise AssertionError("Place your order button not found")
    by, value = hit
    outcome["selectors"]["place_order"] = {"by": by, "value": value}
    markers.mark("place_order_tapped", by=by, value=value)
    time.sleep(2.0)
    # Confirm sheet if present
    confirm = _try_tap(driver, PLACE_ORDER, timeout=5.0)
    if confirm:
        markers.mark("place_order_confirmed", by=confirm[0], value=confirm[1])
    time.sleep(4.0)
    tap_dismissibles(driver)
    _mark_step(outcome, "place_order", status="ok", by=by, value=value)
    outcome["order_placed"] = True


def path_a_cancel(driver, markers: MarkerLog, outcome: dict) -> None:
    """CS → Track → Orders → View/edit → Cancel items (any reason)."""
    print("Path A: CS and cancellation …")

    # Prefer Menu / Your Amazon to reach CS / Orders.
    if not _try_tap(driver, MENU_TAB, timeout=5.0):
        _try_tap(driver, ME_TAB, timeout=5.0)
    time.sleep(1.5)
    tap_dismissibles(driver)

    print("Path A step 1: Go to CS page …")
    cs = _scroll_find_tap(driver, CS_ENTRY, scrolls=6)
    if cs:
        markers.mark("cs_opened", by=cs[0], value=cs[1])
        _mark_step(outcome, "cs_page", status="ok", by=cs[0], value=cs[1])
        time.sleep(2.0)
    else:
        _mark_step(outcome, "cs_page", status="skipped", reason="CS entry not found")
        markers.mark("cs_skipped")

    print("Path A step 2: Track your order …")
    track = _scroll_find_tap(driver, TRACK_ORDER, scrolls=5)
    if track:
        markers.mark("track_order", by=track[0], value=track[1])
        _mark_step(outcome, "track_order", status="ok", by=track[0], value=track[1])
        time.sleep(2.5)
    else:
        _mark_step(outcome, "track_order", status="skipped", reason="Track not found")
        markers.mark("track_skipped")

    print("Path A step 3: Go to Orders …")
    # Back out if needed, then open Your Orders from account/menu.
    _try_tap(
        driver,
        (
            (AppiumBy.ACCESSIBILITY_ID, "Back"),
            (AppiumBy.ACCESSIBILITY_ID, "nav_back_button"),
            (AppiumBy.IOS_PREDICATE, 'label == "Back" OR name CONTAINS[c] "back"'),
        ),
        timeout=3.0,
    )
    if not _try_tap(driver, MENU_TAB, timeout=4.0):
        _try_tap(driver, ME_TAB, timeout=4.0)
    time.sleep(1.0)
    orders = _scroll_find_tap(driver, YOUR_ORDERS, scrolls=6)
    if not orders:
        raise AssertionError("Your Orders not found for Path A cancel")
    markers.mark("orders_opened", by=orders[0], value=orders[1])
    _mark_step(outcome, "orders", status="ok", by=orders[0], value=orders[1])
    time.sleep(2.5)

    print("Path A step 4: View or edit order …")
    view = _scroll_find_tap(driver, VIEW_EDIT_ORDER, scrolls=6)
    if view:
        markers.mark("view_edit_order", by=view[0], value=view[1])
        _mark_step(outcome, "view_edit_order", status="ok", by=view[0], value=view[1])
        time.sleep(2.5)
    else:
        # Tap first order card / button containing "Ordered" or order number.
        try:
            cards = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'label CONTAINS[c] "Ordered" OR label CONTAINS[c] "Order #" OR '
                'name CONTAINS[c] "Order"',
            )
            for el in cards:
                if el.is_displayed():
                    el.click()
                    markers.mark("order_card_tapped")
                    _mark_step(outcome, "view_edit_order", status="ok", via="order_card")
                    time.sleep(2.5)
                    break
            else:
                raise AssertionError("Could not open order details")
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"View/edit order failed: {exc}") from exc

    print("Path A step 5: Cancel items …")
    cancel = _scroll_find_tap(driver, CANCEL_ITEMS, scrolls=8)
    if not cancel:
        raise AssertionError("Cancel items control not found")
    markers.mark("cancel_items_tapped", by=cancel[0], value=cancel[1])
    time.sleep(2.0)

    reason = _scroll_find_tap(driver, CANCEL_REASON, scrolls=5)
    if reason:
        markers.mark("cancel_reason_selected", by=reason[0], value=reason[1])
    else:
        # Tap any visible reason-like button.
        try:
            for el in driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" OR type == "XCUIElementTypeStaticText"',
            ):
                blob = element_textish(el).lower()
                if any(
                    k in blob
                    for k in ("mistake", "needed", "price", "other", "reason")
                ):
                    el.click()
                    markers.mark("cancel_reason_heuristic", text=blob[:120])
                    break
        except Exception:  # noqa: BLE001
            pass
    time.sleep(1.0)

    conf = _scroll_find_tap(driver, CANCEL_CONFIRM, scrolls=5)
    if not conf:
        # Sometimes the same Cancel items button confirms.
        conf = _try_tap(driver, CANCEL_ITEMS, timeout=5.0)
    if not conf:
        raise AssertionError("Could not confirm cancel items")
    markers.mark("cancel_confirmed", by=conf[0], value=conf[1])
    _mark_step(outcome, "cancel_items", status="ok", by=conf[0], value=conf[1])
    outcome["items_cancelled"] = True
    time.sleep(2.0)
    tap_dismissibles(driver)


def run_task1_cart_checkout_cancel(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Under-$6 add → Cart → record → checkout → place order → Path A cancel."""
    if retailer != "amazon":
        raise ValueError("task1_cart_checkout_cancel is Amazon-only.")

    assigned_qty = settings.assigned_quantity
    rec_limit = settings.recording_time_limit_seconds

    plan = describe_session(settings, retailer, bundle_id)
    plan["scenario"] = "task1_cart_checkout_cancel"
    plan["dry_run"] = dry_run
    plan["study_mode"] = "cancel_test"
    plan["recording"] = True
    plan["recording_starts_at"] = "cart_page"
    plan["master_asins"] = list(settings.amazon_asins)
    plan["assigned_quantity"] = assigned_qty
    plan["recording_time_limit_seconds"] = rec_limit
    plan["post_purchase"] = "path_a_cs_cancellation"
    plan["steps"] = [
        "Cold-start Amazon → open master ASIN PDP → add to cart",
        "Navigate to Cart page",
        "START screen recording on Cart page",
        "4) Proceed to checkout",
        f"5) Raise each item to assigned quantity ({assigned_qty})",
        "6) Update payment method (alternate saved method if available)",
        "7) Update address (alternate saved address if available)",
        "8) Choose a different delivery slot if available",
        "9) Place your order",
        "Path A: CS → Track → Orders → View/edit → Cancel items",
        "Stop recording and quit session",
    ]
    plan["notes"] = (
        "Recording starts at Cart only. Places a real order then cancels (Path A). "
        "Path B returns not included (requires delivered order). "
        "Item selection uses pre-built master ASIN list (deep link) — no grocery search. "
        "Payment/address/slot switch among saved options only — never invent card data."
    )

    if dry_run:
        print(json.dumps({"status": "dry_run_ok", "plan": plan}, indent=2))
        return {"status": "dry_run_ok", "plan": plan}

    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    prefix = f"{retailer}_task1_cart_checkout_cancel_{stamp}"
    result_path = settings.artifacts_dir / f"{prefix}.json"
    page_source_path = settings.artifacts_dir / f"{prefix}_page.xml"
    screenshot_path = settings.artifacts_dir / f"{prefix}_blocker.png"
    video_path = settings.artifacts_dir / f"{prefix}.mp4"

    markers = MarkerLog()
    driver = None
    recording = False
    outcome: dict = {
        "status": "starting",
        "plan": plan,
        "markers": markers.events,
        "selectors": {},
        "checkout_steps": {},
        "item_added_to_cart": False,
        "recording_started": False,
        "order_placed": False,
        "items_cancelled": False,
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

        cold_start_amazon(driver, bundle_id, markers, outcome)
        dismiss_and_login_pause(driver, settings, markers, outcome)
        add_master_asin_to_cart(
            driver,
            bundle_id,
            settings.amazon_asins,
            markers,
            outcome,
            skip_if_cart_nonempty=settings.skip_add_if_cart_nonempty,
            cart_add_url_only=settings.cart_add_url_only,
        )

        open_cart_page(driver, markers, outcome)

        print(
            f"START RECORDING at Cart page "
            f"(timeLimit={rec_limit}s, everything from here on video) …"
        )
        try:
            start_ios_screen_recording(driver, time_limit=rec_limit)
            recording = True
            outcome["recording_started"] = True
            markers.mark("recording_started_at_cart", time_limit=rec_limit)
            print("Screen recording started.")
        except Exception as exc:  # noqa: BLE001
            outcome["recording_started"] = False
            outcome["recording_start_error"] = str(exc)
            print(f"Screen recording failed to start: {exc}")
            raise AssertionError(f"Could not start screen recording on cart: {exc}") from exc

        step_proceed_checkout(driver, markers, outcome)
        step_raise_quantities(driver, markers, outcome, assigned_qty)
        step_update_payment(driver, markers, outcome)
        step_update_address(driver, markers, outcome)
        step_choose_delivery_slot(driver, markers, outcome)
        step_place_order(driver, markers, outcome)
        path_a_cancel(driver, markers, outcome)

        outcome["status"] = "ok"
        outcome["message"] = (
            "TASK 1 cart/checkout/cancel OK — master ASIN → cart (recorded) → "
            "checkout → place order → Path A cancel"
        )
        print(outcome["message"])
        return outcome
    except Exception as exc:  # noqa: BLE001
        outcome["status"] = "error"
        outcome["error_type"] = type(exc).__name__
        outcome["error"] = str(exc)
        print(f"LIVE RUN FAILED: {type(exc).__name__}: {exc}")
        if driver is not None and "page_source_dump" not in outcome:
            _dump_ui("error")
        return outcome
    finally:
        if driver is not None and recording:
            try:
                video_b64 = driver.stop_recording_screen()
                if video_b64:
                    save_recording_bytes(video_b64, video_path)
                    outcome["video"] = str(video_path)
                markers.mark("recording_stopped")
            except Exception as rec_exc:  # noqa: BLE001
                outcome["recording_stop_error"] = str(rec_exc)
                print(f"Recording stop error: {rec_exc}")
        if driver is not None:
            try:
                driver.quit()
                print("Appium session stopped.")
            except Exception as quit_exc:  # noqa: BLE001
                outcome["quit_error"] = str(quit_exc)
        outcome["markers"] = markers.events
        result_path.write_text(json.dumps(outcome, indent=2), encoding="utf-8")
        print(f"Wrote {result_path}")
