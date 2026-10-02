"""Test Case 1: Groceries → Shop groceries → tap + → Cart → record 1 min."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

from appium.webdriver.common.appiumby import AppiumBy

from grocery_latency.amazon_search import (
    SEARCH_TAP_STRATEGIES,
    SEARCH_TYPE_STRATEGIES,
    field_contains_query,
    type_into_search,
)
from grocery_latency.config import Settings
from grocery_latency.driver import create_driver, describe_session
from grocery_latency.ios_helpers import (
    dump_page_source,
    element_textish,
    find_first,
    looks_like_login_wall,
    tap_dismissibles,
)
from grocery_latency.markers import MarkerLog
from grocery_latency.recording import save_recording_bytes, start_ios_screen_recording

TEST_CASE_NAME = "Test Case 1"
TEST_CASE_ID = "test_case_1"
SEARCH_QUERY = "Groceries"
# Record Cart screen for exactly this many seconds, then stop.
CART_RECORDING_SECONDS = 60

GROCERIES_SUGGESTION_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "groceries"),
    (AppiumBy.ACCESSIBILITY_ID, "Groceries"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(name == "groceries" OR label == "groceries" OR '
        'name == "Groceries" OR label == "Groceries")',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeButton[`name == "groceries" OR label == "groceries" OR '
        'name == "Groceries" OR label == "Groceries"`]',
    ),
)

SHOP_GROCERIES_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Shop groceries on Amazon"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Shop groceries on Amazon" OR '
        'name CONTAINS[c] "Shop groceries on Amazon"',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeButton[`label CONTAINS[c] "Shop groceries on Amazon" OR '
        'name CONTAINS[c] "Shop groceries on Amazon"`]',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeStaticText[`label CONTAINS[c] "Shop groceries on Amazon" OR '
        'name CONTAINS[c] "Shop groceries on Amazon"`]',
    ),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Shop groceries" OR name CONTAINS[c] "Shop groceries"',
    ),
    # Soft alternatives seen on live SERP when Same-Day grocery storefront is unavailable.
    (AppiumBy.ACCESSIBILITY_ID, "Pantry staples"),
    (AppiumBy.ACCESSIBILITY_ID, "Budget-friendly deals"),
    (
        AppiumBy.IOS_PREDICATE,
        'label == "Pantry staples" OR name == "Pantry staples" OR '
        'label == "Budget-friendly deals" OR name == "Budget-friendly deals"',
    ),
)

ADD_TO_CART_BUTTON = (
    # One exact accessibility id + one predicate — avoid multi-strategy find_elements scans.
    (AppiumBy.ACCESSIBILITY_ID, "Add To Cart"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(label == "Add To Cart" OR name == "Add To Cart" OR '
        'label == "Add to cart" OR name == "Add to cart" OR '
        'label == "Add to Cart" OR name == "Add to Cart" OR '
        'name == "+" OR label == "+")',
    ),
)

CART_TAB_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "cartTab"),
    (
        AppiumBy.IOS_PREDICATE,
        'name == "cartTab" OR label CONTAINS[c] "Cart"',
    ),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(label CONTAINS[c] "Cart" OR name CONTAINS[c] "Cart")',
    ),
    (AppiumBy.ACCESSIBILITY_ID, "Go to cart"),
    (AppiumBy.ACCESSIBILITY_ID, "View cart"),
)

PROCEED_CHECKOUT = (
    (AppiumBy.ACCESSIBILITY_ID, "Proceed to checkout"),
    (AppiumBy.ACCESSIBILITY_ID, "Proceed to Checkout"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(label CONTAINS[c] "Proceed to checkout" OR '
        'name CONTAINS[c] "Proceed to checkout")',
    ),
)

DELIVER_TO_THIS_ADDRESS = (
    (AppiumBy.ACCESSIBILITY_ID, "Deliver to this address"),
    (AppiumBy.ACCESSIBILITY_ID, "Use this address"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(label == "Deliver to this address" OR '
        'name == "Deliver to this address" OR '
        'label CONTAINS[c] "Deliver to this address" OR '
        'label == "Use this address" OR '
        'name == "Use this address" OR '
        'label CONTAINS[c] "Use this address")',
    ),
)

# Place Your Order quantity stepper (+)
QTY_PLUS = (
    (AppiumBy.ACCESSIBILITY_ID, "Increase item quantity"),
    (AppiumBy.ACCESSIBILITY_ID, "Increase quantity"),
    (AppiumBy.ACCESSIBILITY_ID, "+"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(label == "Increase item quantity" OR name == "Increase item quantity" OR '
        'label CONTAINS[c] "Increase item quantity" OR '
        'name == "+" OR label == "+" OR '
        'label CONTAINS[c] "Increase quantity" OR '
        'name CONTAINS[c] "Increase quantity")',
    ),
)

QUANTITY_INCREASE_BY = 3

ADDED_CONFIRM_NEEDLES = (
    "added to cart",
    "added to basket",
    "1 in cart",
    "cart 1",
    "go to cart",
    "view cart",
    "cart subtotal",
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _scroll_down(driver, percent: float = 0.55) -> None:
    try:
        driver.execute_script(
            "mobile: swipe",
            {"direction": "up", "percent": percent},
        )
    except Exception:  # noqa: BLE001
        try:
            size = driver.get_window_size()
            w, h = size["width"], size["height"]
            driver.execute_script(
                "mobile: dragFromToForDuration",
                {
                    "duration": 0.4,
                    "fromX": w // 2,
                    "fromY": int(h * 0.72),
                    "toX": w // 2,
                    "toY": int(h * 0.28),
                },
            )
        except Exception:  # noqa: BLE001
            pass


def _tap_search_and_type(driver, markers: MarkerLog, outcome: dict, query: str) -> None:
    search_el, tap_by, tap_value = find_first(
        driver, SEARCH_TAP_STRATEGIES, timeout=12.0
    )
    outcome.setdefault("selectors", {})["search_tap"] = {"by": tap_by, "value": tap_value}
    print(f"Search tap: {tap_by} = {tap_value!r}")
    search_el.click()
    markers.mark("search_tapped", by=tap_by, value=tap_value, query=query)
    time.sleep(0.4)

    type_el, type_by, type_value = find_first(
        driver, SEARCH_TYPE_STRATEGIES, timeout=10.0
    )
    outcome["selectors"]["search_type"] = {"by": type_by, "value": type_value}
    print(f"Search type: {type_by} = {type_value!r}")
    type_into_search(driver, type_el, query)
    time.sleep(0.5)
    markers.mark("query_typed", query=query)

    ok, evidence = field_contains_query(driver, query)
    outcome["query_confirmed"] = ok
    outcome["query_evidence"] = evidence
    if not ok:
        raise AssertionError(f"Could not confirm typed query {query!r}")


def _cart_badge_count(driver) -> int | None:
    """Read cart count from cartTab only — never scan every 'Cart' label on SERP."""
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "cartTab"),
        (
            AppiumBy.IOS_PREDICATE,
            'name == "cartTab" OR (type == "XCUIElementTypeButton" AND '
            'label BEGINSWITH[c] "Cart")',
        ),
    ):
        try:
            el = driver.find_element(by, value)
        except Exception:  # noqa: BLE001
            continue
        try:
            blob = element_textish(el)
            m = re.search(r"Cart\s*(\d+)", blob, re.I)
            if m:
                return int(m.group(1))
            m = re.search(r"(\d+)", blob)
            if m:
                return int(m.group(1))
        except Exception:  # noqa: BLE001
            continue
    return None


def _looks_added(driver, before_count: int | None) -> bool:
    after = _cart_badge_count(driver)
    if before_count is not None and after is not None and after > before_count:
        return True
    if before_count is None and after is not None and after > 0:
        return True
    return False


def _wait_added(driver, before_count: int | None, timeout: float = 2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if _looks_added(driver, before_count):
            return True
        time.sleep(0.25)
    return _looks_added(driver, before_count)


def _try_add_any_item(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    source: str,
    max_scrolls: int = 3,
) -> dict | None:
    """Scroll SERP and tap the first visible Add to cart via find_first (fast)."""
    before = _cart_badge_count(driver)
    outcome["cart_count_before"] = before

    for scroll_i in range(max_scrolls):
        try:
            add_el, by, value = find_first(
                driver,
                ADD_TO_CART_BUTTON,
                timeout=4.0 if scroll_i == 0 else 1.5,
            )
        except TimeoutError:
            _scroll_down(driver)
            time.sleep(0.3)
            markers.mark("results_scrolled", source=source, index=scroll_i)
            continue

        blob = ""
        try:
            blob = element_textish(add_el)
        except Exception:  # noqa: BLE001
            blob = value
        print(f"Any-item add candidate: {by}={value!r} text={blob[:120]!r}")
        outcome["selectors"]["add_to_cart"] = {"by": by, "value": value}
        add_el.click()
        markers.mark(
            "add_to_cart_tapped",
            source=source,
            by=by,
            value=value,
            text=blob[:200],
        )
        time.sleep(0.4)
        # Skip full dismiss scan (very slow on SERP); one cheap cartTab check.
        confirmed = _wait_added(driver, before, timeout=2.0)
        after = _cart_badge_count(driver)
        outcome["cart_count_after"] = after
        outcome["add_confirmed_in_ui"] = confirmed
        outcome["item_added_to_cart"] = True
        markers.mark(
            "item_added_to_cart",
            confirmed=confirmed,
            cart_before=before,
            cart_after=after,
            source=source,
        )
        return {
            "text": blob[:500],
            "source": source,
            "add_by": by,
            "add_value": value,
            "confirmed": confirmed,
        }

    return None


def _open_cart_page(driver, markers: MarkerLog, outcome: dict) -> None:
    print("Step: open Cart screen …")
    el, by, value = find_first(driver, CART_TAB_STRATEGIES, timeout=6.0)
    outcome.setdefault("selectors", {})["cart_tab"] = {"by": by, "value": value}
    print(f"Cart: {by} = {value!r}")
    el.click()
    markers.mark("cart_opened", by=by, value=value)
    time.sleep(0.5)
    outcome["cart_page_opened"] = True


def _try_tap(driver, strategies, timeout: float = 4.0):
    try:
        el, by, value = find_first(driver, strategies, timeout=timeout)
        el.click()
        return by, value
    except TimeoutError:
        return None


def _scroll_find_tap(driver, strategies, *, scrolls: int = 3, timeout: float = 2.5):
    for i in range(scrolls):
        hit = _try_tap(driver, strategies, timeout=timeout if i == 0 else 1.0)
        if hit:
            return hit
        _scroll_down(driver)
        time.sleep(0.25)
    return None


def _tap_proceed_to_checkout(driver, markers: MarkerLog, outcome: dict) -> None:
    print('Step: tap "Proceed to checkout" …')
    hit = _scroll_find_tap(driver, PROCEED_CHECKOUT, scrolls=3)
    if not hit:
        raise AssertionError('Proceed to checkout button not found on Cart')
    by, value = hit
    outcome.setdefault("selectors", {})["proceed_checkout"] = {"by": by, "value": value}
    markers.mark("proceed_checkout", by=by, value=value)
    outcome["proceed_checkout_tapped"] = True
    time.sleep(0.6)


def _tap_second_address_radio(driver, markers: MarkerLog, outcome: dict) -> None:
    """On 'Select a delivery address', tap the 2nd address radio (top of that row)."""
    print("Step: tap second address radio button …")
    time.sleep(0.3)

    # Prefer native radio buttons; fall back to buttons that look like radios.
    radios = []
    for predicate in (
        'type == "XCUIElementTypeRadioButton"',
        'type == "XCUIElementTypeButton" AND '
        '(name CONTAINS[c] "radio" OR label CONTAINS[c] "radio" OR '
        'name CONTAINS[c] "unselected" OR label CONTAINS[c] "unselected" OR '
        'name CONTAINS[c] "selected" OR label CONTAINS[c] "selected")',
        'type == "XCUIElementTypeOther" AND '
        '(name CONTAINS[c] "radio" OR label CONTAINS[c] "radio")',
    ):
        try:
            els = driver.find_elements(AppiumBy.IOS_PREDICATE, predicate)
        except Exception:  # noqa: BLE001
            continue
        for el in els:
            try:
                if not el.is_displayed():
                    continue
                y = el.location.get("y", 0)
                if y < 80:
                    continue
                radios.append((y, el))
            except Exception:  # noqa: BLE001
                continue
        if len(radios) >= 2:
            break

    if len(radios) >= 2:
        radios.sort(key=lambda t: t[0])
        target = radios[1][1]  # second from top
        print(f"Tapping 2nd radio (of {len(radios)} visible) …")
        target.click()
        markers.mark("second_address_radio_tapped", radio_count=len(radios))
        outcome["second_address_radio_tapped"] = True
        time.sleep(0.6)
        tap_dismissibles(driver)
        return

    # Fallback: tap the second address text block (often selects its radio).
    print("Radio buttons not found — tapping 2nd address text block …")
    address_els = []
    try:
        candidates = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'label CONTAINS[c] "Pakistan" OR name CONTAINS[c] "Pakistan" OR '
            'label CONTAINS[c] "Phone number" OR name CONTAINS[c] "Phone number" OR '
            'label CONTAINS[c] "Karachi" OR name CONTAINS[c] "Karachi"',
        )
    except Exception:  # noqa: BLE001
        candidates = []
    for el in candidates:
        try:
            if not el.is_displayed():
                continue
            blob = element_textish(el)
            if len(blob) < 20:
                continue
            y = el.location.get("y", 0)
            address_els.append((y, el, blob[:120]))
        except Exception:  # noqa: BLE001
            continue
    if len(address_els) < 2:
        raise AssertionError(
            f"Could not find 2nd delivery address radio/row "
            f"(radios={len(radios)}, address_rows={len(address_els)})"
        )
    address_els.sort(key=lambda t: t[0])
    _y, el, blob = address_els[1]
    print(f"Tapping 2nd address row: {blob!r}")
    el.click()
    markers.mark("second_address_row_tapped", text=blob)
    outcome["second_address_radio_tapped"] = True
    time.sleep(0.6)
    tap_dismissibles(driver)


def _tap_deliver_to_this_address(driver, markers: MarkerLog, outcome: dict) -> None:
    print('Step: tap "Deliver to this address" / "Use this address" …')
    hit = _scroll_find_tap(driver, DELIVER_TO_THIS_ADDRESS, scrolls=3)
    if not hit:
        raise AssertionError(
            '"Deliver to this address" / "Use this address" button not found'
        )
    by, value = hit
    outcome.setdefault("selectors", {})["deliver_to_this_address"] = {
        "by": by,
        "value": value,
    }
    markers.mark("deliver_to_this_address", by=by, value=value)
    outcome["deliver_to_this_address_tapped"] = True
    time.sleep(0.6)


def _find_qty_plus_button(driver, *, require_displayed: bool = True):
    """Return (el, by, value) for Increase item quantity — prefer single find_element."""
    for by, value in QTY_PLUS[:2]:
        try:
            el = driver.find_element(by, value)
            if require_displayed and not el.is_displayed():
                continue
            y = el.location.get("y", 0)
            if y < 100:
                continue
            return el, by, value
        except Exception:  # noqa: BLE001
            continue
    # Fallback: predicate scan (slower).
    by, value = QTY_PLUS[-1]
    try:
        els = driver.find_elements(by, value)
    except Exception:  # noqa: BLE001
        return None, None, None
    candidates = []
    for el in els:
        try:
            if require_displayed and not el.is_displayed():
                continue
            y = el.location.get("y", 0)
            if y < 100:
                continue
            candidates.append((y, el))
        except Exception:  # noqa: BLE001
            continue
    if candidates:
        candidates.sort(key=lambda t: t[0])
        return candidates[0][1], by, value
    return None, None, None


def _increase_quantity_on_place_order(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    increase_by: int = QUANTITY_INCREASE_BY,
) -> None:
    """On Place Your Order, tap quantity + N times via coordinates (fast)."""
    print(f"Step: on Place Your Order, increase quantity by {increase_by} …")
    time.sleep(0.4)
    try:
        find_first(
            driver,
            (
                (AppiumBy.ACCESSIBILITY_ID, "Place Your Order"),
                (
                    AppiumBy.IOS_PREDICATE,
                    'label CONTAINS[c] "Place Your Order" OR name CONTAINS[c] "Place Your Order"',
                ),
            ),
            timeout=3.0,
        )
        markers.mark("place_order_screen_detected")
        outcome["place_order_screen"] = True
    except TimeoutError:
        outcome["place_order_screen"] = False

    plus_el, plus_by, plus_value = None, None, None
    for scroll_i in range(5):
        plus_el, plus_by, plus_value = _find_qty_plus_button(driver)
        if plus_el is not None:
            markers.mark("qty_plus_visible", scroll_index=scroll_i)
            break
        print(f"  scrolling for quantity + (pass {scroll_i + 1}) …")
        _scroll_down(driver, percent=0.4)
        time.sleep(0.35)

    if plus_el is None:
        raise AssertionError("Quantity + control not found on Place Your Order screen")

    outcome.setdefault("selectors", {})["qty_plus"] = {
        "by": plus_by,
        "value": plus_value,
    }
    print(f"Quantity +: {plus_by} = {plus_value!r}")

    # Tap same screen point N times — avoids re-querying the hierarchy each tap.
    try:
        loc = plus_el.location
        size = plus_el.size
        tap_x = int(loc.get("x", 0) + (size.get("width", 0) or 0) / 2)
        tap_y = int(loc.get("y", 0) + (size.get("height", 0) or 0) / 2)
    except Exception:  # noqa: BLE001
        tap_x = tap_y = None

    taps = 0
    for i in range(increase_by):
        try:
            if tap_x is not None and tap_y is not None:
                driver.execute_script(
                    "mobile: tap",
                    {"x": tap_x, "y": tap_y},
                )
            else:
                el, by, value = _find_qty_plus_button(driver)
                if el is None:
                    print(f"  quantity + control missing before tap {i + 1}")
                    break
                el.click()
                plus_by, plus_value = by, value
            taps += 1
            markers.mark(
                "quantity_plus_tapped",
                index=i + 1,
                by=plus_by,
                value=plus_value,
            )
            print(f"  quantity + tap {i + 1}/{increase_by}")
            time.sleep(0.35)
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"  quantity + tap {i + 1} click failed: {exc}")

        try:
            el2, _, _ = _find_qty_plus_button(driver)
            if el2 is None:
                break
            loc = el2.location
            size = el2.size
            x = int(loc.get("x", 0) + size.get("width", 0) / 2)
            y = int(loc.get("y", 0) + size.get("height", 0) / 2)
            driver.execute_script("mobile: tap", {"x": x, "y": y})
            taps += 1
            markers.mark("quantity_plus_tapped", index=i + 1, via="coordinate")
            print(f"  quantity + tap {i + 1}/{increase_by} (coordinate)")
            time.sleep(0.7)
        except Exception as exc2:  # noqa: BLE001
            print(f"  quantity + retry failed: {exc2}")
            break

    outcome["quantity_plus_taps"] = taps
    outcome["quantity_increased_by"] = taps
    if taps < increase_by:
        raise AssertionError(
            f"Only tapped quantity + {taps}/{increase_by} times on Place Your Order"
        )
    markers.mark("quantity_increased", taps=taps)
    print(f"Quantity increased by {taps} — stopping process.")


def run_test_case_1(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Test Case 1: Open Amazon → Groceries → + add → Cart → record 1 min."""
    if retailer != "amazon":
        raise ValueError(
            f"{TEST_CASE_NAME} is Amazon-only (do not invent Walmart/Instacart)."
        )

    rec_limit = CART_RECORDING_SECONDS

    plan = describe_session(settings, retailer, bundle_id)
    plan["scenario"] = TEST_CASE_ID
    plan["test_case"] = TEST_CASE_NAME
    plan["dry_run"] = dry_run
    plan["study_mode"] = "cancel_test"
    plan["recording"] = True
    plan["recording_starts_at"] = "cart_page"
    plan["recording_duration_seconds"] = rec_limit
    plan["steps"] = [
        f"{TEST_CASE_NAME}: Open Amazon App",
        f'Tap Search Bar and type "{SEARCH_QUERY}"',
        'Tap "groceries" suggestion',
        'Tap "Shop groceries on Amazon"',
        "Find any item and tap + to add to cart",
        "Go to Cart screen",
        f"START screen recording on Cart for {rec_limit}s",
        'Tap "Proceed to checkout"',
        "Tap second delivery-address radio button",
        'Tap "Deliver to this address"',
        f"On Place Your Order, increase quantity by {QUANTITY_INCREASE_BY}",
        "Stop recording and quit session",
    ]
    plan["notes"] = (
        f"{TEST_CASE_NAME} — add grocery item via +, open Cart, start recording, "
        "proceed to checkout, select 2nd address, deliver to this address, "
        f"increase quantity by {QUANTITY_INCREASE_BY} on Place Your Order, then stop."
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
        "item_added_to_cart": False,
        "cart_page_opened": False,
        "recording_started": False,
        "proceed_checkout_tapped": False,
        "second_address_radio_tapped": False,
        "deliver_to_this_address_tapped": False,
        "quantity_increased_by": 0,
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
                time.sleep(0.4)
                outcome["cold_start_terminated"] = True
            else:
                outcome["cold_start_terminated"] = False
            print(f"Activating {bundle_id} (cold start) …")
            driver.activate_app(bundle_id)
            time.sleep(1.0)
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

        time.sleep(0.8)
        dismissed = tap_dismissibles(driver)
        outcome["dismissed"] = dismissed
        if dismissed:
            print(f"Dismissed: {dismissed}")

        if looks_like_login_wall(driver):
            pause = max(0, settings.login_pause_seconds)
            print(f"Login wall suspected — pausing {pause}s for human assist.")
            markers.mark("login_pause_start", seconds=pause)
            time.sleep(pause)
            markers.mark("login_pause_end")
            tap_dismissibles(driver)

        print("Step: tap search + type Groceries …")
        _tap_search_and_type(driver, markers, outcome, SEARCH_QUERY)
        markers.mark("groceries_typed", query=SEARCH_QUERY)

        print("Step: tap Groceries suggestion …")
        time.sleep(0.35)
        sug_el, sug_by, sug_value = find_first(
            driver, GROCERIES_SUGGESTION_STRATEGIES, timeout=8.0
        )
        outcome["selectors"]["groceries_suggestion"] = {
            "by": sug_by,
            "value": sug_value,
        }
        print(f"Suggestion: {sug_by} = {sug_value!r}")
        sug_el.click()
        markers.mark("groceries_suggestion_tapped", by=sug_by, value=sug_value)
        time.sleep(0.4)

        print('Step: tap "Shop groceries on Amazon" (soft) …')
        shop_hit = False
        try:
            shop_el, shop_by, shop_value = find_first(
                driver, SHOP_GROCERIES_STRATEGIES, timeout=5.0
            )
            outcome["selectors"]["shop_groceries"] = {
                "by": shop_by,
                "value": shop_value,
            }
            print(f"Shop groceries: {shop_by} = {shop_value!r}")
            shop_el.click()
            markers.mark("shop_groceries_tapped", by=shop_by, value=shop_value)
            shop_hit = True
            time.sleep(0.8)
        except TimeoutError:
            outcome["shop_groceries_skipped"] = True
            outcome["shop_groceries_skip_reason"] = (
                "Shop groceries on Amazon not found; continuing on search results."
            )
            markers.mark("shop_groceries_skipped")
            print(outcome["shop_groceries_skip_reason"])

        outcome["shop_groceries_tapped"] = shop_hit

        print("Step: find any item and tap + / Add to cart …")
        chosen = _try_add_any_item(
            driver, markers, outcome, source="groceries_serp", max_scrolls=4
        )
        if not chosen:
            _dump_ui("no_add_button")
            raise AssertionError(
                "Could not find any + / Add to cart control on groceries results"
            )

        outcome["chosen_item"] = chosen
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

        _tap_proceed_to_checkout(driver, markers, outcome)
        _tap_second_address_radio(driver, markers, outcome)
        _tap_deliver_to_this_address(driver, markers, outcome)
        _increase_quantity_on_place_order(
            driver, markers, outcome, increase_by=QUANTITY_INCREASE_BY
        )

        markers.mark(
            "test_case_1_stopped_after_qty",
            quantity_increased_by=outcome.get("quantity_increased_by"),
        )
        print("Stopping process after quantity increase …")

        outcome["status"] = "ok"
        outcome["message"] = (
            f"{TEST_CASE_NAME} OK — cart → record → checkout → 2nd address → "
            f"Deliver to this address → qty +{outcome.get('quantity_increased_by')} "
            f"(source={chosen.get('source')!r})"
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


# Backward-compatible alias (old CLI name).
run_select_grocery_under_4 = run_test_case_1
