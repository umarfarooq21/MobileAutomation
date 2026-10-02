"""Test Case 2 (TASK 2): Home → Grocery tab → tap + on products → cart ending.

US home: tap home "Grocery" pill → Produce aisle → yellow + ×5 → Cart →
record → same ending as Test Case 1 (checkout → 2nd address → deliver → qty +N).
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
    element_textish,
    find_first,
    looks_like_login_wall,
    tap_dismissibles,
)
from grocery_latency.markers import MarkerLog
from grocery_latency.recording import save_recording_bytes, start_ios_screen_recording
from grocery_latency.scenarios.test_case_1 import (
    QUANTITY_INCREASE_BY,
    _scroll_find_tap,
    _tap_deliver_to_this_address,
    _tap_proceed_to_checkout,
)

TEST_CASE_NAME = "Test Case 2"
TEST_CASE_ID = "test_case_2"
TARGET_ITEMS = 5
CART_RECORDING_SECONDS = 45

GROCERY_HOME_TAB_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Grocery"),
    (AppiumBy.ACCESSIBILITY_ID, "grocery"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(label == "Grocery" OR name == "Grocery" OR '
        'label == "grocery" OR name == "grocery")',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeButton[`label == "Grocery" OR name == "Grocery"`]',
    ),
    (
        AppiumBy.IOS_PREDICATE,
        'label == "Grocery" OR name == "Grocery" OR '
        'label CONTAINS[c] "Grocery" OR name CONTAINS[c] "Grocery"',
    ),
)

PRODUCE_TAB_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Produce"),
    (
        AppiumBy.IOS_PREDICATE,
        'label == "Produce" OR name == "Produce"',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeButton[`label == "Produce" OR name == "Produce"`]',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeStaticText[`label == "Produce"`]',
    ),
)

GROCERY_PLUS_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "+"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(name == "+" OR label == "+" OR '
        'label CONTAINS[c] "Add" OR name CONTAINS[c] "Add to")',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeButton[`name == "+" OR label == "+"`]',
    ),
)

RUFUS_CLOSE_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Close Rufus"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Close Rufus" OR name CONTAINS[c] "Close Rufus" OR '
        'label == "Dismiss Rufus" OR name == "Dismiss Rufus"',
    ),
)

CART_TAB_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "cartTab"),
    (
        AppiumBy.IOS_PREDICATE,
        'name == "cartTab" OR label BEGINSWITH[c] "Cart"',
    ),
    (AppiumBy.ACCESSIBILITY_ID, "Go to cart"),
    (AppiumBy.ACCESSIBILITY_ID, "View cart"),
)

NO_THANKS_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "No thanks"),
    (AppiumBy.ACCESSIBILITY_ID, "No Thanks"),
    (
        AppiumBy.IOS_PREDICATE,
        'label == "No thanks" OR name == "No thanks" OR '
        'label == "No Thanks" OR name == "No Thanks" OR '
        'label CONTAINS[c] "No thanks" OR name CONTAINS[c] "No thanks"',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeButton[`label == "No thanks" OR label == "No Thanks"`]',
    ),
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _cart_badge_count(driver) -> int | None:
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "cartTab"),
        (
            AppiumBy.IOS_PREDICATE,
            'name == "cartTab" OR (type == "XCUIElementTypeButton" AND '
            'label BEGINSWITH[c] "Cart")',
        ),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label CONTAINS[c] "Cart" OR name CONTAINS[c] "Cart" OR '
            'label CONTAINS[c] "bag" OR name CONTAINS[c] "bag" OR '
            'label CONTAINS[c] "Basket" OR name CONTAINS[c] "Basket")',
        ),
    ):
        try:
            el = driver.find_element(by, value)
        except Exception:  # noqa: BLE001
            continue
        try:
            blob = element_textish(el)
            m = re.search(r"Cart\s*(\d+)", blob, re.I) or re.search(r"(\d+)", blob)
            if m:
                return int(m.group(1))
        except Exception:  # noqa: BLE001
            continue
    return None


def _wait_badge_up(driver, before: int | None, timeout: float = 2.5) -> tuple[bool, int | None]:
    end = time.monotonic() + timeout
    after = None
    while time.monotonic() < end:
        after = _cart_badge_count(driver)
        if before is not None and after is not None and after > before:
            return True, after
        if before is None and after is not None and after > 0:
            return True, after
        time.sleep(0.2)
    after = _cart_badge_count(driver)
    ok = (
        (before is not None and after is not None and after > before)
        or (before is None and after is not None and after > 0)
    )
    return ok, after


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
                    "duration": 0.35,
                    "fromX": w // 2,
                    "fromY": int(h * 0.72),
                    "toX": w // 2,
                    "toY": int(h * 0.28),
                },
            )
        except Exception:  # noqa: BLE001
            pass


def _add_fingerprint(el) -> str | None:
    """Stable-enough id for a + button so we don't re-tap the same control."""
    try:
        r = el.rect or {}
        y = int(round(float(r.get("y", 0)) / 8.0) * 8)
        x = int(round(float(r.get("x", 0)) / 8.0) * 8)
        return f"{x}:{y}"
    except Exception:  # noqa: BLE001
        return None


def _try_tap_first(
    driver,
    strategies,
    *,
    timeout: float = 4.0,
    scrolls: int = 0,
) -> tuple[str, str] | None:
    """Tap first matching control; retry once on stale. Returns (by, value)."""
    for i in range(max(1, scrolls + 1)):
        try:
            el, by, value = find_first(
                driver, strategies, timeout=timeout if i == 0 else 1.5
            )
            try:
                el.click()
            except Exception:  # noqa: BLE001
                el2, by, value = find_first(driver, strategies, timeout=2.0)
                try:
                    el2.click()
                except Exception:  # noqa: BLE001
                    loc = el2.location
                    size = el2.size
                    x = int(loc.get("x", 0) + (size.get("width", 0) or 0) / 2)
                    y = int(loc.get("y", 0) + (size.get("height", 0) or 0) / 2)
                    driver.execute_script("mobile: tap", {"x": x, "y": y})
            return by, value
        except TimeoutError:
            if i < scrolls:
                _scroll_down(driver)
                time.sleep(0.3)
    return None


def _dismiss_rufus(driver, markers: MarkerLog | None = None) -> None:
    """Close Rufus overlay if present (short timeout)."""
    hit = _try_tap_first(driver, RUFUS_CLOSE_STRATEGIES, timeout=0.6, scrolls=0)
    if hit:
        print(f"  Dismissed Rufus/overlay: {hit[0]} = {hit[1]!r}")
        if markers is not None:
            markers.mark("rufus_closed", by=hit[0], value=hit[1])
        time.sleep(0.25)


def _dismiss_grocery_search_keyboard(driver, markers: MarkerLog | None = None) -> None:
    """Leave 'Search in Groceries' overlay so product + buttons stay tappable."""
    try:
        driver.hide_keyboard()
    except Exception:  # noqa: BLE001
        pass
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "Cancel"),
        (AppiumBy.ACCESSIBILITY_ID, "cancel"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(label == "Cancel" OR name == "Cancel" OR label == "Done")',
        ),
    ):
        try:
            el = driver.find_element(by, value)
            if el.is_displayed():
                el.click()
                if markers is not None:
                    markers.mark("grocery_search_dismissed", by=by, value=value)
                time.sleep(0.35)
                return
        except Exception:  # noqa: BLE001
            continue
    hit = _try_tap_first(driver, PRODUCE_TAB_STRATEGIES, timeout=1.0, scrolls=0)
    if hit and markers is not None:
        markers.mark("grocery_search_dismissed_via_produce", by=hit[0], value=hit[1])
        time.sleep(0.35)


def _open_grocery_tab_from_home(driver, markers: MarkerLog, outcome: dict) -> None:
    """US home: tap the Grocery category pill (no search box)."""
    print('Step: tap home "Grocery" tab …')
    hit = _try_tap_first(driver, GROCERY_HOME_TAB_STRATEGIES, timeout=8.0, scrolls=1)
    if not hit:
        raise AssertionError('Home "Grocery" tab not found')
    by, value = hit
    outcome.setdefault("selectors", {})["grocery_home_tab"] = {"by": by, "value": value}
    markers.mark("grocery_home_tab_tapped", by=by, value=value)
    print(f"  Grocery tab: {by} = {value!r}")
    time.sleep(1.2)
    _dismiss_rufus(driver, markers)
    _dismiss_grocery_search_keyboard(driver, markers)

    produce = _try_tap_first(driver, PRODUCE_TAB_STRATEGIES, timeout=3.0, scrolls=0)
    if produce:
        outcome["selectors"]["produce_tab"] = {
            "by": produce[0],
            "value": produce[1],
        }
        markers.mark("produce_tab_tapped", by=produce[0], value=produce[1])
        print(f"  Produce: {produce[0]} = {produce[1]!r}")
        time.sleep(0.7)
        _dismiss_grocery_search_keyboard(driver, markers)
    else:
        markers.mark("produce_tab_skipped")
        print("  Produce tab not tapped (already on grocery grid or missing)")


def _collect_grocery_plus(driver, used: set[str]) -> list[tuple]:
    """Visible yellow + buttons on grocery product cards (rect only)."""
    candidates: list[tuple] = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(name == "+" OR label == "+" OR '
            'label BEGINSWITH[c] "Add" OR name BEGINSWITH[c] "Add")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:12]:
        try:
            r = el.rect or {}
            y = float(r.get("y", 0))
            x = float(r.get("x", 0))
            h = float(r.get("height", 0))
            if h <= 0 or y < 160 or y > 780:
                continue
            fp = f"{int(round(x / 8.0) * 8)}:{int(round(y / 8.0) * 8)}"
            if fp in used:
                continue
            candidates.append((el, AppiumBy.IOS_PREDICATE, "+", fp, y))
        except Exception:  # noqa: BLE001
            continue
    candidates.sort(key=lambda c: (c[4], c[3]))
    return candidates[:8]


def _qty_chip_visible_near(driver, y_target: float) -> bool:
    """True if a quantity chip appeared near a tapped + (badge may be hidden)."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "1" OR name == "1" OR label == "2" OR name == "2" OR '
            'label == "3" OR name == "3" OR '
            'label CONTAINS[c] "Increase" OR name CONTAINS[c] "Increase" OR '
            'label CONTAINS[c] "quantity" OR name CONTAINS[c] "quantity")',
        )
    except Exception:  # noqa: BLE001
        return False
    for el in els[:20]:
        try:
            y = float((el.rect or {}).get("y", -999))
            if abs(y - y_target) < 90:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _add_n_via_grocery_plus(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    target: int,
    max_scrolls: int = 6,
) -> list[dict]:
    """Tap distinct grocery-storefront yellow + buttons until `target` confirms."""
    print(f"Step: add {target} items via grocery yellow + …")
    _dismiss_grocery_search_keyboard(driver, markers)
    start_badge = _cart_badge_count(driver)
    outcome["cart_count_before_adds"] = start_badge
    outcome["asin_add_strategy"] = "home_grocery_tab_plus"
    markers.mark("grocery_plus_add_start", target=target, badge=start_badge)

    used: set[str] = set()
    added: list[dict] = []
    scrolls = 0

    while len(added) < target and scrolls <= max_scrolls:
        _dismiss_grocery_search_keyboard(driver, markers)
        before = _cart_badge_count(driver)
        batch = _collect_grocery_plus(driver, used)
        if not batch:
            try:
                el, by, value = find_first(driver, GROCERY_PLUS_STRATEGIES, timeout=1.5)
                fp = _add_fingerprint(el) or f"plus:{len(added)}"
                if fp not in used:
                    r = el.rect or {}
                    y = float(r.get("y", 0))
                    if 160 <= y <= 780:
                        batch = [(el, by, value, fp, y)]
            except TimeoutError:
                pass
        if not batch:
            _scroll_down(driver, percent=0.55)
            scrolls += 1
            markers.mark("grocery_grid_scrolled", index=scrolls)
            time.sleep(0.2)
            continue

        el, by, value, fp, y = batch[0]
        print(
            f"  + candidate #{len(added) + 1}/{target}: "
            f"{by}={value!r} y={y:.0f} fp={fp}"
        )
        try:
            el.click()
        except Exception as exc:  # noqa: BLE001
            if fp:
                used.add(fp)
            print(f"  + tap failed: {exc}")
            continue

        if fp:
            used.add(fp)
        markers.mark(
            "grocery_plus_tapped",
            index=len(added) + 1,
            by=by,
            value=value,
            fingerprint=fp,
            y=y,
        )
        time.sleep(0.45)

        ok, after = _wait_badge_up(driver, before, timeout=1.8)
        confirm_via = "badge"
        if not ok:
            if _qty_chip_visible_near(driver, y):
                ok = True
                after = (before + 1) if before is not None else (len(added) + 1)
                confirm_via = "qty_chip"
                print(f"  confirmed via qty chip near y={y:.0f}")
        if not ok:
            print(f"  no badge bump {before}→{after}; try next")
            markers.mark(
                "add_no_badge_increase",
                index=len(added) + 1,
                cart_before=before,
                cart_after=after,
            )
            continue

        entry = {
            "index": len(added) + 1,
            "source": "grocery_tab_plus",
            "add_path": "plus",
            "add_by": by,
            "add_value": value,
            "fingerprint": fp,
            "confirmed": True,
            "confirm_via": confirm_via,
            "cart_before": before,
            "cart_after": after,
        }
        added.append(entry)
        markers.mark(
            "item_added_to_cart",
            index=entry["index"],
            confirmed=True,
            cart_before=before,
            cart_after=after,
            fingerprint=fp,
            path="plus",
            confirm_via=confirm_via,
        )
        print(
            f"  added #{entry['index']}/{target} via + "
            f"badge {before}→{after} ({confirm_via})"
        )

    outcome["items_added"] = list(added)
    outcome["items_added_count"] = len(added)
    end_badge = _cart_badge_count(driver)
    outcome["cart_count_after_adds"] = end_badge
    markers.mark(
        "grocery_plus_add_done",
        added=len(added),
        target=target,
        badge_before=start_badge,
        badge_after=end_badge,
        scrolls=scrolls,
    )
    if len(added) < target:
        raise AssertionError(
            f"Only added {len(added)}/{target} grocery + items "
            f"(scrolls={scrolls}, badge {start_badge}→{end_badge})"
        )
    return added


def _open_cart_page(driver, markers: MarkerLog, outcome: dict) -> None:
    print("Step: open Cart screen …")
    try:
        el, by, value = find_first(driver, CART_TAB_STRATEGIES, timeout=2.5)
    except TimeoutError:
        print("  cartTab not on grocery UI — closing grocery storefront …")
        closed = _try_tap_first(
            driver,
            (
                (AppiumBy.ACCESSIBILITY_ID, "Close"),
                (AppiumBy.ACCESSIBILITY_ID, "close"),
                (
                    AppiumBy.IOS_PREDICATE,
                    'type == "XCUIElementTypeButton" AND '
                    '(label == "Close" OR name == "Close" OR '
                    'label == "Done" OR name == "Done" OR '
                    'name == "xmark" OR label CONTAINS[c] "close")',
                ),
                (
                    AppiumBy.IOS_CLASS_CHAIN,
                    '**/XCUIElementTypeButton[`label == "Close" OR name == "Close"`]',
                ),
            ),
            timeout=3.0,
            scrolls=0,
        )
        if closed:
            markers.mark("grocery_storefront_closed", by=closed[0], value=closed[1])
            print(f"  Closed grocery: {closed[0]} = {closed[1]!r}")
            time.sleep(0.8)
        el, by, value = find_first(driver, CART_TAB_STRATEGIES, timeout=8.0)

    outcome.setdefault("selectors", {})["cart_tab"] = {"by": by, "value": value}
    print(f"Cart: {by} = {value!r}")
    el.click()
    markers.mark("cart_opened", by=by, value=value)
    time.sleep(0.55)
    outcome["cart_page_opened"] = True


def _verify_five_in_cart(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    expected: int = TARGET_ITEMS,
    badge_before_adds: int | None,
) -> None:
    print(f"Step: verify {expected} items in cart …")
    time.sleep(0.3)
    badge = _cart_badge_count(driver)
    outcome["cart_verify_badge"] = badge
    outcome["cart_verify_line_items"] = None

    delta_ok = False
    if badge_before_adds is not None and badge is not None:
        delta_ok = (badge - badge_before_adds) >= expected
    badge_ok = badge is not None and badge >= expected
    items_ok = outcome.get("items_added_count", 0) >= expected
    verified = delta_ok or (badge_ok and items_ok) or (
        items_ok
        and all(bool(it.get("confirmed")) for it in (outcome.get("items_added") or []))
    )

    outcome["cart_five_items_verified"] = bool(verified)
    markers.mark(
        "cart_verified",
        expected=expected,
        badge=badge,
        verified=bool(verified),
    )
    print(
        f"  cart verify: badge={badge} before_adds={badge_before_adds} "
        f"verified={verified}"
    )
    if not verified:
        raise AssertionError(
            f"Cart verification failed — expected {expected} items "
            f"(badge={badge}, before={badge_before_adds})"
        )


def _on_place_order_screen(driver) -> bool:
    try:
        find_first(
            driver,
            (
                (AppiumBy.ACCESSIBILITY_ID, "Place Your Order"),
                (
                    AppiumBy.IOS_PREDICATE,
                    'label CONTAINS[c] "Place Your Order" OR '
                    'name CONTAINS[c] "Place Your Order" OR '
                    'label CONTAINS[c] "Place your order"',
                ),
            ),
            timeout=1.2,
        )
        return True
    except TimeoutError:
        return False


def _open_change_delivery_address(driver, markers: MarkerLog, outcome: dict) -> bool:
    """From Place Your Order, open the address picker."""
    print('Step: tap "Change delivery address" …')
    strategies = (
        (
            AppiumBy.IOS_CLASS_CHAIN,
            '**/XCUIElementTypeStaticText[`label == "Change delivery address"`]',
        ),
        (
            AppiumBy.IOS_CLASS_CHAIN,
            '**/XCUIElementTypeLink[`label == "Change delivery address"`]',
        ),
        (AppiumBy.ACCESSIBILITY_ID, "Change delivery address"),
    )

    for attempt in range(3):
        hit = None
        try:
            el, by, value = find_first(driver, strategies, timeout=2.0)
            el.click()
            hit = (by, value)
        except TimeoutError:
            hit = _scroll_find_tap(driver, strategies, scrolls=2, timeout=1.2)

        if not hit:
            continue

        by, value = hit
        outcome.setdefault("selectors", {})["change_delivery_address"] = {
            "by": by,
            "value": value,
        }
        markers.mark(
            "change_delivery_address", by=by, value=value, attempt=attempt
        )
        outcome["change_delivery_address_tapped"] = True
        print(f"  Change address: {by} = {value!r} (attempt {attempt + 1})")
        time.sleep(0.7)

        if not _on_place_order_screen(driver):
            return True
        try:
            find_first(
                driver,
                (
                    (AppiumBy.ACCESSIBILITY_ID, "Deliver to this address"),
                    (AppiumBy.ACCESSIBILITY_ID, "Use this address"),
                    (
                        AppiumBy.IOS_PREDICATE,
                        'label CONTAINS[c] "Select a delivery address" OR '
                        'label CONTAINS[c] "Select a billing address" OR '
                        'label CONTAINS[c] "Deliver to this address" OR '
                        'label CONTAINS[c] "Use this address"',
                    ),
                ),
                timeout=1.2,
            )
            return True
        except TimeoutError:
            print("  still on Place Your Order — coordinate retry …")
            try:
                el, _, _ = find_first(driver, strategies[:1], timeout=1.5)
                loc, size = el.location, el.size
                x = int(loc.get("x", 0) + (size.get("width", 0) or 0) / 2)
                y = int(loc.get("y", 0) + (size.get("height", 0) or 0) / 2)
                driver.execute_script("mobile: tap", {"x": x, "y": y})
                markers.mark("change_delivery_address_coordinate", x=x, y=y)
                time.sleep(0.8)
                if not _on_place_order_screen(driver):
                    return True
            except Exception as exc:  # noqa: BLE001
                print(f"  coordinate tap failed: {exc}")
            continue
    return False


def _tc2_find_qty_plus(driver):
    """Accessibility-id only — never scan find_elements (that was ~2 min)."""
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "Increase item quantity"),
        (AppiumBy.ACCESSIBILITY_ID, "Increase quantity"),
    ):
        try:
            el = driver.find_element(by, value)
            return el, by, value
        except Exception:  # noqa: BLE001
            continue
    return None, None, None


def _tc2_increase_quantity_fast(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    increase_by: int = QUANTITY_INCREASE_BY,
) -> None:
    """Flick to line items then tap qty + via coordinates."""
    print(f"Step: on Place Your Order, increase quantity by {increase_by} (fast) …")
    time.sleep(0.25)
    markers.mark("place_order_screen_detected")
    outcome["place_order_screen"] = True

    try:
        win_h = int(driver.get_window_size().get("height", 800))
    except Exception:  # noqa: BLE001
        win_h = 800

    plus_el = plus_by = plus_value = None
    for scroll_i in range(8):
        plus_el, plus_by, plus_value = _tc2_find_qty_plus(driver)
        if plus_el is not None:
            try:
                y = float(plus_el.location.get("y", 0))
            except Exception:  # noqa: BLE001
                y = 0.0
            if 60 < y < win_h - 20:
                markers.mark("qty_plus_visible", scroll_index=scroll_i, y=y)
                break
            print(f"  qty + off-screen y={y:.0f} — flicking …")
        else:
            print(f"  scrolling for quantity + (pass {scroll_i + 1}) …")
        _scroll_down(driver, percent=0.72)
        time.sleep(0.12)
        plus_el = None

    if plus_el is None:
        plus_el, plus_by, plus_value = _tc2_find_qty_plus(driver)
    if plus_el is None:
        raise AssertionError("Quantity + control not found on Place Your Order screen")

    outcome.setdefault("selectors", {})["qty_plus"] = {
        "by": plus_by,
        "value": plus_value,
    }
    print(f"Quantity +: {plus_by} = {plus_value!r}")

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
                driver.execute_script("mobile: tap", {"x": tap_x, "y": tap_y})
            else:
                el, by, value = _tc2_find_qty_plus(driver)
                if el is None:
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
            time.sleep(0.28)
        except Exception as exc:  # noqa: BLE001
            print(f"  quantity + tap {i + 1} failed: {exc}")
            break

    outcome["quantity_plus_taps"] = taps
    outcome["quantity_increased_by"] = taps
    if taps < increase_by:
        raise AssertionError(
            f"Only tapped quantity + {taps}/{increase_by} times on Place Your Order"
        )
    markers.mark("quantity_increased", taps=taps)
    print(f"Quantity increased by {taps} — stopping process.")


def _tc2_tap_second_address(driver, markers: MarkerLog, outcome: dict) -> None:
    """Select 2nd delivery address; broader fallbacks than TC1 Pakistan-only text."""
    print("Step: tap second address radio button …")
    time.sleep(0.25)

    radios: list[tuple[float, object]] = []
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
        target = radios[1][1]
        print(f"Tapping 2nd radio (of {len(radios)} visible) …")
        target.click()
        markers.mark("second_address_radio_tapped", radio_count=len(radios))
        outcome["second_address_radio_tapped"] = True
        time.sleep(0.6)
        return

    print("Radio buttons not found — tapping 2nd address text block …")
    address_els: list[tuple[float, object, str]] = []
    try:
        candidates = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'label CONTAINS[c] "Pakistan" OR name CONTAINS[c] "Pakistan" OR '
            'label CONTAINS[c] "Phone number" OR name CONTAINS[c] "Phone number" OR '
            'label CONTAINS[c] "Karachi" OR name CONTAINS[c] "Karachi" OR '
            'label CONTAINS[c] "Sindh" OR name CONTAINS[c] "Sindh" OR '
            'label CONTAINS[c] "House" OR name CONTAINS[c] "House" OR '
            'label CONTAINS[c] "Street" OR name CONTAINS[c] "Street" OR '
            'label CONTAINS[c] "Avenue" OR name CONTAINS[c] "Avenue"',
        )
    except Exception:  # noqa: BLE001
        candidates = []
    for el in candidates:
        try:
            if not el.is_displayed():
                continue
            blob = element_textish(el)
            if len(blob) < 12:
                continue
            if "delivering to" in blob.lower():
                continue
            y = el.location.get("y", 0)
            address_els.append((y, el, blob[:120]))
        except Exception:  # noqa: BLE001
            continue

    address_els.sort(key=lambda t: t[0])
    deduped: list[tuple[float, object, str]] = []
    for row in address_els:
        if deduped and abs(row[0] - deduped[-1][0]) < 24:
            continue
        deduped.append(row)

    if len(deduped) >= 2:
        _y, el, blob = deduped[1]
        print(f"Tapping 2nd address row: {blob!r}")
        el.click()
        markers.mark("second_address_row_tapped", text=blob)
        outcome["second_address_radio_tapped"] = True
        time.sleep(0.6)
        return

    if len(deduped) == 1:
        _y, el, blob = deduped[0]
        print(f"Only 1 address row visible — tapping it: {blob!r}")
        el.click()
        markers.mark("single_address_row_tapped", text=blob)
        outcome["second_address_radio_tapped"] = False
        outcome["single_address_fallback"] = True
        time.sleep(0.6)
        return

    raise AssertionError(
        f"Could not find 2nd delivery address radio/row "
        f"(radios={len(radios)}, address_rows={len(deduped)})"
    )


def _dismiss_prime_upsell(driver, markers: MarkerLog, outcome: dict) -> None:
    """Dismiss Amazon Prime trial interstitial if it blocks Place Your Order."""
    hit = _scroll_find_tap(driver, NO_THANKS_STRATEGIES, scrolls=1, timeout=1.0)
    if hit:
        outcome.setdefault("selectors", {})["prime_no_thanks"] = {
            "by": hit[0],
            "value": hit[1],
        }
        markers.mark("prime_upsell_dismissed", by=hit[0], value=hit[1])
        print(f"  Dismissed Prime upsell: {hit[0]} = {hit[1]!r}")
        time.sleep(0.45)


def _tc1_style_checkout_ending(driver, markers: MarkerLog, outcome: dict) -> None:
    """Match Test Case 1 ending: checkout → 2nd address → deliver → qty +N."""
    _tap_proceed_to_checkout(driver, markers, outcome)
    time.sleep(0.6)

    if _on_place_order_screen(driver):
        markers.mark("place_order_before_address_change")
        outcome["place_order_before_address_change"] = True
        print("  Place Your Order detected — opening Change delivery address …")
        if not _open_change_delivery_address(driver, markers, outcome):
            raise AssertionError(
                "On Place Your Order but could not open Change delivery address"
            )

    _tc2_tap_second_address(driver, markers, outcome)
    _tap_deliver_to_this_address(driver, markers, outcome)
    time.sleep(0.45)
    _dismiss_prime_upsell(driver, markers, outcome)
    _tc2_increase_quantity_fast(
        driver, markers, outcome, increase_by=QUANTITY_INCREASE_BY
    )


def run_test_case_2(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """TASK 2 / Test Case 2: home Grocery tab → + adds → TC1 cart checkout ending."""
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
    plan["add_strategy"] = "home_grocery_tab_plus"
    plan["steps"] = [
        "Start new automation session (kill prior Amazon if running)",
        "Open Amazon App (cold start, US home)",
        'Tap home "Grocery" tab (no search)',
        "Open Produce aisle if needed",
        f"Tap yellow + on {TARGET_ITEMS} grocery products",
        "Open Cart screen",
        f"START screen recording on Cart for {rec_limit}s",
        f"Verify all {TARGET_ITEMS} items added (while recording)",
        'Tap "Proceed to checkout"',
        'If on Place Your Order: tap "Change delivery address"',
        "Select 2nd delivery address radio",
        'Tap "Deliver to this address" / "Use this address"',
        f"On Place Your Order, tap quantity + {QUANTITY_INCREASE_BY} times",
        "Stop recording and quit session",
    ]
    plan["notes"] = (
        f"{TEST_CASE_NAME} (TASK 2) — US home Grocery tab → Produce → "
        f"yellow + ×{TARGET_ITEMS}; then same cart ending as Test Case 1 "
        f"(record → checkout → 2nd address → deliver → qty +{QUANTITY_INCREASE_BY})."
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
        "items_added_count": 0,
        "cart_page_opened": False,
        "cart_five_items_verified": False,
        "recording_started": False,
        "proceed_checkout_tapped": False,
        "second_address_radio_tapped": False,
        "deliver_to_this_address_tapped": False,
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

        time.sleep(0.35)
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

        _open_grocery_tab_from_home(driver, markers, outcome)
        _add_n_via_grocery_plus(
            driver, markers, outcome, target=TARGET_ITEMS, max_scrolls=6
        )
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
        _tc1_style_checkout_ending(driver, markers, outcome)

        markers.mark(
            "test_case_2_stopped_after_qty",
            items=outcome.get("items_added_count"),
            quantity_increased_by=outcome.get("quantity_increased_by"),
        )
        print("Stopping process after quantity increase …")

        outcome["status"] = "ok"
        outcome["message"] = (
            f"{TEST_CASE_NAME} OK — added {outcome.get('items_added_count')} "
            f"via home Grocery tab +; "
            f"cart → record → checkout → 2nd address → deliver → "
            f"qty +{outcome.get('quantity_increased_by')}"
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
                print(f"driver.quit warning: {quit_exc}")
        try:
            result_path.write_text(json.dumps(outcome, indent=2, default=str))
            print(f"Wrote {result_path}")
        except Exception as write_exc:  # noqa: BLE001
            print(f"Failed to write result JSON: {write_exc}")
