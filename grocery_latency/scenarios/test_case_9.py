"""Test Case 9 (TASK 9): Same as Test Case 8, then cancel the placed order.

Produce +×5 → Cart → record → qty +3 → TC6 checkout → Place your order →
Authorize Payment (if shown) → Back → Home → Menu → Customer Service →
order card → Track package → Track package (yellow) → Cancel items →
select all → reason "Order Created by Mistake" → Request cancellation →
View your orders → STOP recording.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from appium.webdriver.common.appiumby import AppiumBy

from grocery_latency.amazon_task1_common import scroll_down
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
from grocery_latency.scenarios.test_case_2 import _open_cart_page, _verify_five_in_cart
from grocery_latency.scenarios.test_case_3 import _mark_step, _screen_matches
from grocery_latency.scenarios.test_case_5 import (
    SEARCH_FALLBACK_QUERY,
    TARGET_ITEMS,
    _add_five_produce_or_search,
)
from grocery_latency.scenarios.test_case_6 import (
    CART_RECORDING_SECONDS,
    _tc6_checkout_edits,
)
from grocery_latency.scenarios.test_case_7 import (
    CART_QTY_INCREASE_BY,
    _increase_first_cart_item_qty,
)
from grocery_latency.scenarios.test_case_8 import (
    _tap_place_order,
    _wait_order_success,
)

TEST_CASE_NAME = "Test Case 9"
TEST_CASE_ID = "test_case_9"
# Room for place-order + authorize + CS cancel path inside one clip.
CART_RECORDING_SECONDS_TC9 = max(CART_RECORDING_SECONDS, 420)

HOME_TAB = (
    (AppiumBy.ACCESSIBILITY_ID, "homeTab"),
    (AppiumBy.ACCESSIBILITY_ID, "Home"),
    (
        AppiumBy.IOS_PREDICATE,
        'name == "homeTab" OR label == "Home" OR name == "Home"',
    ),
)

MENU_TAB = (
    (AppiumBy.ACCESSIBILITY_ID, "menuTab"),
    (AppiumBy.ACCESSIBILITY_ID, "Menu"),
    (
        AppiumBy.IOS_PREDICATE,
        'name == "menuTab" OR label == "Menu" OR name == "Menu"',
    ),
)

BACK_BUTTON = (
    (AppiumBy.ACCESSIBILITY_ID, "Back"),
    (AppiumBy.ACCESSIBILITY_ID, "nav_back_button"),
    (AppiumBy.ACCESSIBILITY_ID, "a-back-button"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label == "Back" OR name == "Back" OR name CONTAINS[c] "back" OR '
        'label CONTAINS[c] "back" OR name == "chevron.left" OR '
        'label == "Close" OR name == "Close")',
    ),
)

AUTHORIZE_PAYMENT = (
    (AppiumBy.ACCESSIBILITY_ID, "Authorize Payment Method"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Authorize Payment" OR '
        'name CONTAINS[c] "Authorize Payment" OR '
        'label CONTAINS[c] "Verify by Phone" OR '
        'name CONTAINS[c] "Verify by Phone" OR '
        'label CONTAINS[c] "Verification code" OR '
        'name CONTAINS[c] "Verification code"',
    ),
)

CUSTOMER_SERVICE = (
    (AppiumBy.ACCESSIBILITY_ID, "Customer Service"),
    (
        AppiumBy.IOS_PREDICATE,
        'label == "Customer Service" OR name == "Customer Service" OR '
        'label CONTAINS[c] "Customer Service"',
    ),
)

ORDER_CARD = (
    (AppiumBy.ACCESSIBILITY_ID, "Shipping Soon"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Shipping Soon" OR name CONTAINS[c] "Shipping Soon" OR '
        'label CONTAINS[c] "You may want to check" OR '
        'name CONTAINS[c] "You may want to check" OR '
        'label CONTAINS[c] "Arriving" OR name CONTAINS[c] "Arriving tomorrow"',
    ),
)

TRACK_PACKAGE = (
    (AppiumBy.ACCESSIBILITY_ID, "Track package"),
    (AppiumBy.ACCESSIBILITY_ID, "Track Package"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label == "Track package" OR name == "Track package" OR '
        'label == "Track Package" OR name == "Track Package")',
    ),
)

CANCEL_ITEMS = (
    (AppiumBy.ACCESSIBILITY_ID, "Cancel items"),
    (AppiumBy.ACCESSIBILITY_ID, "Cancel Items"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label == "Cancel items" OR name == "Cancel items" OR '
        'label CONTAINS[c] "Cancel items" OR name CONTAINS[c] "Cancel items")',
    ),
)

REQUEST_CANCELLATION = (
    (AppiumBy.ACCESSIBILITY_ID, "Request cancellation"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label == "Request cancellation" OR name == "Request cancellation" OR '
        'label CONTAINS[c] "Request cancellation")',
    ),
)

CANCEL_REASON_MISTAKE = (
    (
        AppiumBy.IOS_PREDICATE,
        'label == "Order Created by Mistake" OR '
        'name == "Order Created by Mistake" OR '
        'label CONTAINS[c] "Order Created by Mistake" OR '
        'label CONTAINS[c] "Ordered by Mistake" OR '
        'label CONTAINS[c] "Created by Mistake"',
    ),
)

VIEW_YOUR_ORDERS = (
    (AppiumBy.ACCESSIBILITY_ID, "View your orders"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label == "View your orders" OR name == "View your orders" OR '
        'label CONTAINS[c] "View your orders")',
    ),
)

CANCELLATION_SUCCESS = (
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Cancellation successful" OR '
        'name CONTAINS[c] "Cancellation successful" OR '
        'label CONTAINS[c] "Cancellation Summary" OR '
        'name CONTAINS[c] "Cancellation Summary"',
    ),
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _try_tap(driver, strategies, *, timeout: float = 4.0):
    try:
        el, by, value = find_first(driver, strategies, timeout=timeout)
        el.click()
        return by, value
    except Exception:  # noqa: BLE001
        return None


def _scroll_find_tap(driver, strategies, *, scrolls: int = 8, timeout: float = 2.0):
    for i in range(scrolls):
        hit = _try_tap(driver, strategies, timeout=timeout if i == 0 else 1.2)
        if hit:
            return hit
        scroll_down(driver)
        time.sleep(0.35)
    return None


def _authorize_payment_visible(driver) -> bool:
    return _screen_matches(driver, AUTHORIZE_PAYMENT, timeout=0.5)


def _tap_top_back(driver, markers: MarkerLog, outcome: dict) -> None:
    """Leave Authorize Payment / post-order sheet via top Back."""
    print("Step: tap top Back (leave place-order / authorize screen) …")
    # Prefer the top-left circular back control.
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
    except Exception:  # noqa: BLE001
        w = 390.0

    hit = _try_tap(driver, BACK_BUTTON, timeout=3.0)
    if not hit:
        # Coordinate fallback for circular back near top-left.
        print("  Back control not found by label — tapping top-left …")
        driver.execute_script("mobile: tap", {"x": int(w * 0.08), "y": 56})
        hit = ("coordinate", "top_left_back")
        time.sleep(0.8)

    by, value = hit
    markers.mark("post_order_back_tapped", by=by, value=value)
    _mark_step(outcome, "post_order_back", status="ok", by=by, value=value)
    print(f"  Back: {by} = {value!r}")
    time.sleep(0.8)

    # Extra backs if still stuck on authorize / checkout web sheet.
    for _ in range(3):
        if not _authorize_payment_visible(driver) and not _screen_matches(
            driver,
            (
                (
                    AppiumBy.IOS_PREDICATE,
                    'label CONTAINS[c] "Place Your Order" OR '
                    'name CONTAINS[c] "Place Your Order" OR '
                    'label CONTAINS[c] "Authorize Payment"',
                ),
            ),
            timeout=0.35,
        ):
            break
        print("  still on post-order sheet — Back again …")
        if not _try_tap(driver, BACK_BUTTON, timeout=1.5):
            driver.execute_script("mobile: tap", {"x": int(w * 0.08), "y": 56})
        time.sleep(0.7)


def _go_home(driver, markers: MarkerLog, outcome: dict) -> None:
    print("Step: go to Home tab …")
    hit = _try_tap(driver, HOME_TAB, timeout=4.0)
    if not hit:
        raise AssertionError("Home tab not found after leaving place-order flow")
    by, value = hit
    markers.mark("home_tab_tapped", by=by, value=value)
    _mark_step(outcome, "home_tab", status="ok", by=by, value=value)
    print(f"  Home: {by} = {value!r}")
    time.sleep(0.8)
    tap_dismissibles(driver, rounds=1)


def _open_menu_tab(driver, markers: MarkerLog, outcome: dict) -> None:
    print("Step: open Menu tab (after Cart) …")
    hit = _try_tap(driver, MENU_TAB, timeout=5.0)
    if not hit:
        raise AssertionError("Menu tab not found")
    by, value = hit
    markers.mark("menu_tab_tapped", by=by, value=value)
    _mark_step(outcome, "menu_tab", status="ok", by=by, value=value)
    print(f"  Menu: {by} = {value!r}")
    time.sleep(1.0)
    tap_dismissibles(driver, rounds=1)


def _open_customer_service(driver, markers: MarkerLog, outcome: dict) -> None:
    print('Step: scroll Menu and tap "Customer Service" …')
    hit = _scroll_find_tap(driver, CUSTOMER_SERVICE, scrolls=10, timeout=1.5)
    if not hit:
        raise AssertionError('Customer Service not found in Menu')
    by, value = hit
    markers.mark("customer_service_tapped", by=by, value=value)
    _mark_step(outcome, "customer_service", status="ok", by=by, value=value)
    print(f"  Customer Service: {by} = {value!r}")
    time.sleep(1.5)
    tap_dismissibles(driver, rounds=1)


def _tap_recent_order_card(driver, markers: MarkerLog, outcome: dict) -> None:
    print('Step: tap placed-order card ("You may want to check on…") …')
    # Prefer the Shipping Soon / recent-order card under CS hub.
    hit = _scroll_find_tap(driver, ORDER_CARD, scrolls=6, timeout=2.0)
    if not hit:
        # Fallback: first visible cell/button under "check on".
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" OR type == "XCUIElementTypeCell" OR '
                'type == "XCUIElementTypeOther"',
            )
            for el in els[:40]:
                try:
                    label = (
                        (el.get_attribute("label") or "")
                        + " "
                        + (el.get_attribute("name") or "")
                    ).lower()
                    if not any(
                        k in label
                        for k in (
                            "shipping soon",
                            "arriving",
                            "ordered",
                            "lime",
                            "check on",
                        )
                    ):
                        continue
                    r = el.rect or {}
                    y = float(r.get("y", 0))
                    if y < 120 or y > 780:
                        continue
                    el.click()
                    hit = ("heuristic", label[:60])
                    break
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            hit = None

    if not hit:
        raise AssertionError("Placed-order card not found on Customer Service")
    by, value = hit
    markers.mark("order_card_tapped", by=by, value=value)
    _mark_step(outcome, "order_card", status="ok", by=by, value=value)
    print(f"  Order card: {by} = {value!r}")
    time.sleep(1.5)


def _tap_track_package(
    driver, markers: MarkerLog, outcome: dict, *, step_key: str, prefer_yellow: bool
) -> None:
    """Tap a Track package control.

    prefer_yellow=True picks the lower/full-width yellow CTA on Check on shipping.
    """
    label = "yellow Track package" if prefer_yellow else "Track package"
    print(f'Step: tap "{label}" …')

    try:
        size = driver.get_window_size() or {}
        win_h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        win_h = 844.0

    candidates: list[tuple[float, object, float, float]] = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Track package" OR name == "Track package" OR '
            'label == "Track Package" OR name == "Track Package" OR '
            'label CONTAINS[c] "Track package")',
        )
    except Exception:  # noqa: BLE001
        els = []

    for el in els[:12]:
        try:
            r = el.rect or {}
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            h = float(r.get("height", 0))
            if y < 90 or y > win_h * 0.95 or h < 20:
                continue
            cx = float(r.get("x", 0)) + w / 2.0
            cy = y + h / 2.0
            # Yellow bottom CTA is usually wider; help list pills are narrower.
            score = y if prefer_yellow else -y
            if prefer_yellow and w > 220:
                score -= 50  # prefer full-width yellow
            if not prefer_yellow and w < 260:
                score -= 50  # prefer pill in help list
            candidates.append((score, el, cx, cy))
        except Exception:  # noqa: BLE001
            continue

    if not candidates:
        hit = _scroll_find_tap(driver, TRACK_PACKAGE, scrolls=5, timeout=1.5)
        if not hit:
            raise AssertionError(f'"{label}" button not found')
        by, value = hit
        markers.mark(step_key, by=by, value=value)
        _mark_step(outcome, step_key, status="ok", by=by, value=value)
        print(f"  {label}: {by} = {value!r}")
        time.sleep(1.2)
        return

    candidates.sort(key=lambda t: t[0])
    _score, el, cx, cy = candidates[0]
    print(f"  {label} @ ({cx:.0f},{cy:.0f})")
    try:
        el.click()
    except Exception:  # noqa: BLE001
        driver.execute_script(
            "mobile: tap", {"x": int(round(cx)), "y": int(round(cy))}
        )
    markers.mark(step_key, x=cx, y=cy, prefer_yellow=prefer_yellow)
    _mark_step(
        outcome,
        step_key,
        status="ok",
        x=cx,
        y=cy,
        prefer_yellow=prefer_yellow,
    )
    time.sleep(1.4)


def _tap_cancel_items(driver, markers: MarkerLog, outcome: dict) -> None:
    print('Step: tap "Cancel items" …')
    hit = _scroll_find_tap(driver, CANCEL_ITEMS, scrolls=8, timeout=2.0)
    if not hit:
        raise AssertionError('"Cancel items" button not found on order details')
    by, value = hit
    markers.mark("cancel_items_tapped", by=by, value=value)
    _mark_step(outcome, "cancel_items", status="ok", by=by, value=value)
    print(f"  Cancel items: {by} = {value!r}")
    time.sleep(1.5)


def _select_all_cancel_items(driver, markers: MarkerLog, outcome: dict) -> None:
    """Ensure every cancelable item checkbox is selected."""
    print("Step: select all items for cancellation …")
    selected = 0
    try:
        boxes = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(name CONTAINS[c] "checkbox" OR label CONTAINS[c] "checkbox" OR '
            'name CONTAINS[c] "Selected" OR label CONTAINS[c] "Selected" OR '
            'name CONTAINS[c] "Select" OR value == "0" OR value == "1")',
        )
    except Exception:  # noqa: BLE001
        boxes = []

    for el in boxes[:20]:
        try:
            r = el.rect or {}
            w = float(r.get("width", 0))
            h = float(r.get("height", 0))
            y = float(r.get("y", 0))
            # Item checkboxes are small squares in the cancel list.
            if w > 60 or h > 60 or w < 18 or h < 18 or y < 120 or y > 700:
                continue
            val = (el.get_attribute("value") or "").strip()
            name = (el.get_attribute("name") or "").lower()
            label = (el.get_attribute("label") or "").lower()
            already = val in {"1", "true"} or "selected" in name or "selected" in label
            if already and "unselected" not in name and "unselected" not in label:
                selected += 1
                continue
            el.click()
            selected += 1
            time.sleep(0.2)
        except Exception:  # noqa: BLE001
            continue

    # Fallback: tap Clear then re-check by tapping first few item rows.
    if selected == 0:
        print("  no checkboxes found — tapping item rows / Clear+reselect …")
        _try_tap(
            driver,
            (
                (AppiumBy.ACCESSIBILITY_ID, "Clear"),
                (
                    AppiumBy.IOS_PREDICATE,
                    'label == "Clear" OR name == "Clear"',
                ),
            ),
            timeout=1.0,
        )
        time.sleep(0.3)
        try:
            items = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND visible == 1',
            )
            for el in items[:15]:
                try:
                    r = el.rect or {}
                    x = float(r.get("x", 0))
                    y = float(r.get("y", 0))
                    w = float(r.get("width", 0))
                    h = float(r.get("height", 0))
                    if y < 160 or y > 620 or x > 80 or w > 80 or h > 80:
                        continue
                    el.click()
                    selected += 1
                    time.sleep(0.15)
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass

    markers.mark("cancel_items_selected", count=selected)
    _mark_step(outcome, "cancel_items_selected", status="ok", count=selected)
    print(f"  selected ~{selected} cancel item control(s)")
    time.sleep(0.3)


def _choose_cancel_reason_mistake(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    print('Step: choose cancel reason "Order Created by Mistake" …')
    # Open dropdown if reason picker is collapsed.
    opened = _try_tap(
        driver,
        (
            (
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND visible == 1 AND '
                '(label CONTAINS[c] "Order Created by Mistake" OR '
                'label CONTAINS[c] "Ordered by Mistake" OR '
                'label CONTAINS[c] "Select a reason" OR '
                'label CONTAINS[c] "reason" OR '
                'name CONTAINS[c] "reason")',
            ),
            (AppiumBy.ACCESSIBILITY_ID, "Order Created by Mistake"),
        ),
        timeout=2.0,
    )
    time.sleep(0.4)

    # If a picker wheel / list appeared, select the mistake reason.
    hit = _try_tap(driver, CANCEL_REASON_MISTAKE, timeout=2.5)
    if not hit:
        # Picker wheels often need tap on the visible wheel value.
        try:
            wheels = driver.find_elements(AppiumBy.CLASS_NAME, "XCUIElementTypePickerWheel")
            if wheels:
                wheels[0].send_keys("Order Created by Mistake")
                hit = ("picker", "Order Created by Mistake")
                time.sleep(0.3)
                _try_tap(
                    driver,
                    (
                        (AppiumBy.ACCESSIBILITY_ID, "Done"),
                        (
                            AppiumBy.IOS_PREDICATE,
                            'label == "Done" OR name == "Done"',
                        ),
                    ),
                    timeout=1.5,
                )
        except Exception:  # noqa: BLE001
            pass

    if not hit and opened:
        # Already showing the desired reason on the closed dropdown.
        hit = opened

    if not hit:
        raise AssertionError(
            'Could not select cancel reason "Order Created by Mistake"'
        )

    by, value = hit
    markers.mark("cancel_reason_selected", by=by, value=value)
    _mark_step(outcome, "cancel_reason", status="ok", by=by, value=value)
    print(f"  Reason: {by} = {value!r}")
    time.sleep(0.4)


def _request_cancellation(driver, markers: MarkerLog, outcome: dict) -> None:
    print('Step: tap "Request cancellation" …')
    hit = _scroll_find_tap(driver, REQUEST_CANCELLATION, scrolls=4, timeout=2.0)
    if not hit:
        raise AssertionError('"Request cancellation" button not found')
    by, value = hit
    markers.mark("request_cancellation_tapped", by=by, value=value)
    _mark_step(outcome, "request_cancellation", status="ok", by=by, value=value)
    print(f"  Request cancellation: {by} = {value!r}")
    time.sleep(2.0)


def _wait_cancellation_success(
    driver, markers: MarkerLog, outcome: dict, *, timeout: float = 25.0
) -> None:
    print("Step: wait for cancellation success …")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _screen_matches(driver, CANCELLATION_SUCCESS, timeout=0.5):
            markers.mark("cancellation_successful")
            outcome["order_cancelled"] = True
            _mark_step(outcome, "cancellation_success", status="ok")
            print("  Cancellation successful")
            return
        time.sleep(0.4)
    raise AssertionError("Cancellation successful message not detected")


def _tap_view_your_orders(driver, markers: MarkerLog, outcome: dict) -> None:
    print('Step: tap "View your orders" …')
    hit = _scroll_find_tap(driver, VIEW_YOUR_ORDERS, scrolls=3, timeout=2.5)
    if not hit:
        raise AssertionError('"View your orders" button not found after cancel')
    by, value = hit
    markers.mark("view_your_orders_tapped", by=by, value=value)
    _mark_step(outcome, "view_your_orders", status="ok", by=by, value=value)
    print(f"  View your orders: {by} = {value!r}")
    time.sleep(1.0)


def _dismiss_authorize_if_present(driver, markers: MarkerLog, outcome: dict) -> None:
    if not _authorize_payment_visible(driver):
        return
    print("Step: dismiss leftover Authorize Payment Method screen …")
    markers.mark("authorize_payment_preclear")
    _tap_top_back(driver, markers, outcome)
    time.sleep(0.5)
    _try_tap(driver, HOME_TAB, timeout=2.0)
    time.sleep(0.5)


def _clear_bloated_cart(driver, markers: MarkerLog, outcome: dict) -> None:
    """Delete leftover cart lines so Proceed/checkout can work reliably."""
    print("Step: clear leftover cart items (pre-run) …")
    try:
        _open_cart_page(driver, markers, outcome)
    except Exception as exc:  # noqa: BLE001
        print(f"  cart open for clear skipped: {exc}")
        return

    deleted = 0
    for _ in range(25):
        hit = _try_tap(
            driver,
            (
                (AppiumBy.ACCESSIBILITY_ID, "Delete"),
                (
                    AppiumBy.IOS_PREDICATE,
                    'type == "XCUIElementTypeButton" AND visible == 1 AND '
                    '(label == "Delete" OR name == "Delete")',
                ),
            ),
            timeout=1.2,
        )
        if not hit:
            # Trash icon on qty stepper (qty=1) also removes the line.
            try:
                trashes = driver.find_elements(
                    AppiumBy.IOS_PREDICATE,
                    'type == "XCUIElementTypeButton" AND visible == 1 AND '
                    '(name CONTAINS[c] "delete" OR label CONTAINS[c] "delete" OR '
                    'name CONTAINS[c] "Remove" OR label CONTAINS[c] "Remove" OR '
                    'name CONTAINS[c] "trash")',
                )
            except Exception:  # noqa: BLE001
                trashes = []
            tapped = False
            for el in trashes[:6]:
                try:
                    r = el.rect or {}
                    y = float(r.get("y", 0))
                    w = float(r.get("width", 0))
                    if y < 200 or y > 780 or w > 80:
                        continue
                    el.click()
                    deleted += 1
                    tapped = True
                    time.sleep(0.35)
                    break
                except Exception:  # noqa: BLE001
                    continue
            if not tapped:
                break
        else:
            deleted += 1
            time.sleep(0.35)
            # Confirm delete sheet if shown.
            _try_tap(
                driver,
                (
                    (AppiumBy.ACCESSIBILITY_ID, "Delete"),
                    (AppiumBy.ACCESSIBILITY_ID, "Remove"),
                    (
                        AppiumBy.IOS_PREDICATE,
                        'type == "XCUIElementTypeButton" AND visible == 1 AND '
                        '(label == "Delete" OR label == "Remove" OR label == "Yes")',
                    ),
                ),
                timeout=0.8,
            )
            time.sleep(0.25)

    markers.mark("cart_preclean_deleted", count=deleted)
    _mark_step(outcome, "cart_preclean", status="ok", deleted=deleted)
    print(f"  deleted ~{deleted} leftover cart control(s)")
    _try_tap(driver, HOME_TAB, timeout=2.5)
    time.sleep(0.6)


def _post_order_cancel_flow(driver, markers: MarkerLog, outcome: dict) -> None:
    """Authorize/back → Home → Menu → CS → track → cancel all → View orders."""
    # After Place your order Amazon often shows 3DS Authorize Payment Method.
    if _authorize_payment_visible(driver):
        markers.mark("authorize_payment_detected")
        _mark_step(outcome, "authorize_payment", status="ok", via="detected")
        print("  Authorize Payment Method screen detected")
        outcome["order_placed"] = True
        outcome["place_order"] = True

    _tap_top_back(driver, markers, outcome)
    _go_home(driver, markers, outcome)
    _open_menu_tab(driver, markers, outcome)
    _open_customer_service(driver, markers, outcome)
    _tap_recent_order_card(driver, markers, outcome)
    _tap_track_package(
        driver, markers, outcome, step_key="track_package_help", prefer_yellow=False
    )
    _tap_track_package(
        driver,
        markers,
        outcome,
        step_key="track_package_yellow",
        prefer_yellow=True,
    )
    _tap_cancel_items(driver, markers, outcome)
    _select_all_cancel_items(driver, markers, outcome)
    _choose_cancel_reason_mistake(driver, markers, outcome)
    _request_cancellation(driver, markers, outcome)
    _wait_cancellation_success(driver, markers, outcome)
    _tap_view_your_orders(driver, markers, outcome)


def run_test_case_9(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """TASK 9: TC8 place-order, then Customer Service cancel of the whole order."""
    if retailer != "amazon":
        raise ValueError(
            f"{TEST_CASE_NAME} is Amazon-only (do not invent Walmart/Instacart)."
        )

    plan = describe_session(settings, retailer, bundle_id)
    plan["scenario"] = TEST_CASE_ID
    plan["test_case"] = TEST_CASE_NAME
    plan["dry_run"] = dry_run
    plan["study_mode"] = "place_order_then_cancel"
    rec_limit = CART_RECORDING_SECONDS_TC9
    plan["recording"] = True
    plan["recording_starts_at"] = "cart_page"
    plan["recording_duration_seconds"] = rec_limit
    plan["target_items"] = TARGET_ITEMS
    plan["cart_first_qty_increase_by"] = CART_QTY_INCREASE_BY
    plan["add_strategy"] = "home_grocery_produce_plus_or_search"
    plan["place_order"] = True
    plan["cancel_order"] = True
    plan["steps"] = [
        "Start new automation session (kill prior Amazon if running)",
        "Open Amazon App (cold start)",
        "Dismiss Authorize Payment if leftover; clear bloated cart",
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
        "Select 2nd payment card → Continue",
        'Select 2nd address → "Deliver to this address"',
        'Tap "Place your order"',
        "If Authorize Payment Method appears: tap top Back → Home",
        "Open Menu tab (after Cart)",
        'Scroll to and tap "Customer Service"',
        'Tap placed-order card under "You may want to check on…"',
        'Tap "Track package" (help list)',
        'Tap yellow "Track package" on Check on shipping',
        'Tap "Cancel items"',
        'Select all items → reason "Order Created by Mistake" → Request cancellation',
        'After Cancellation successful: tap "View your orders"',
        "STOP screen recording",
    ]
    plan["notes"] = (
        f"{TEST_CASE_NAME} (TASK 9) — same as TC8 place-order, then navigate "
        "Home → Menu → Customer Service → Track package → Cancel all items "
        '(reason "Order Created by Mistake") → View your orders; stop recording after.'
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
        "order_cancelled": False,
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

        _dismiss_authorize_if_present(driver, markers, outcome)
        _clear_bloated_cart(driver, markers, outcome)

        _add_five_produce_or_search(driver, markers, outcome)
        _open_cart_page(driver, markers, outcome)

        print(
            f"START RECORDING at Cart page for {rec_limit}s "
            f"(checkout + place order + cancel run while recording) …"
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

        # Accept either thanks/CTA-gone OR Authorize Payment Method as placed.
        try:
            _wait_order_success(driver, markers, outcome, timeout=20.0)
        except AssertionError:
            if _authorize_payment_visible(driver):
                markers.mark("order_placed_confirmed", via="authorize_payment")
                outcome["order_placed"] = True
                outcome["place_order"] = True
                _mark_step(
                    outcome, "order_success", status="ok", via="authorize_payment"
                )
                print("  Order success inferred (Authorize Payment Method)")
            else:
                raise

        _post_order_cancel_flow(driver, markers, outcome)

        # Stop recording only after cancel + View your orders.
        _stop_recording(why="cancel_complete")

        time.sleep(0.2)
        markers.mark(
            "test_case_9_stopped_after_cancel",
            items=outcome.get("items_added_count"),
            cart_qty_plus=outcome.get("cart_first_qty_increased_by"),
            checkout_steps=outcome.get("checkout_steps"),
            slot=outcome.get("delivery_slot_label"),
            order_placed=outcome.get("order_placed"),
            order_cancelled=outcome.get("order_cancelled"),
        )
        print("Stopping process after order cancellation …")

        outcome["status"] = "ok"
        outcome["message"] = (
            f"{TEST_CASE_NAME} OK — added {outcome.get('items_added_count')} "
            f"via Grocery→Produce/+; cart → record → qty "
            f"+{outcome.get('cart_first_qty_increased_by')} → checkout edits → "
            f"Place your order → CS Track package → Cancel all "
            f"(Order Created by Mistake) → View your orders"
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
