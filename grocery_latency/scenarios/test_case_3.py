"""Test Case 3 (TASK 3): Grocery → Household → + adds → payment + address edits.

US home: Grocery tab → Household aisle → yellow + ×5 (≤ $5 only) → Cart → record →
Proceed to checkout → No thanks → Change payment (2nd card) → Continue →
Change delivery address (2nd) → Deliver to this address → stop recording.

Does not place the order.
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
    _tap_deliver_to_this_address,
)
from grocery_latency.scenarios.test_case_2 import (
    GROCERY_HOME_TAB_STRATEGIES,
    GROCERY_PLUS_STRATEGIES,
    NO_THANKS_STRATEGIES,
    _add_fingerprint,
    _cart_badge_count,
    _collect_grocery_plus,
    _dismiss_rufus,
    _open_cart_page,
    _qty_chip_visible_near,
    _scroll_down,
    _tc2_tap_second_address,
    _try_tap_first,
    _verify_five_in_cart,
    _wait_badge_up,
)

TEST_CASE_NAME = "Test Case 3"
TEST_CASE_ID = "test_case_3"
TARGET_ITEMS = 5
MAX_PRICE_USD = 5.0
# Checkout edits usually finish well under this once payment/address are fast.
CART_RECORDING_SECONDS = 90

_PRICE_RE = re.compile(r"\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)")

FILTER_OPEN_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Filter"),
    (AppiumBy.ACCESSIBILITY_ID, "Filters"),
    (AppiumBy.ACCESSIBILITY_ID, "All Filters"),
    (
        AppiumBy.IOS_PREDICATE,
        'label == "Filter" OR name == "Filter" OR '
        'label == "Filters" OR name == "Filters" OR '
        'label CONTAINS[c] "All Filters" OR name CONTAINS[c] "All Filters"',
    ),
)

PRICE_SECTION_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Price & Deals"),
    (AppiumBy.ACCESSIBILITY_ID, "Price"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Price & Deals" OR name CONTAINS[c] "Price & Deals" OR '
        'label == "Price" OR name == "Price"',
    ),
)

PRICE_0_TO_5_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Up to $5"),
    (AppiumBy.ACCESSIBILITY_ID, "Under $5"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Up to $5" OR name CONTAINS[c] "Up to $5" OR '
        'label CONTAINS[c] "Under $5" OR name CONTAINS[c] "Under $5" OR '
        'label CONTAINS[c] "$0 to $5" OR label CONTAINS[c] "$0-$5" OR '
        'label CONTAINS[c] "0 to $5"',
    ),
)

FILTER_APPLY_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Show results"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '((label BEGINSWITH[c] "Show" AND label CONTAINS[c] "result") OR '
        '(name BEGINSWITH[c] "Show" AND name CONTAINS[c] "result"))',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeButton[`label BEGINSWITH[c] "Show" AND '
        'label CONTAINS[c] "result"`]',
    ),
    (AppiumBy.ACCESSIBILITY_ID, "See results"),
    (AppiumBy.ACCESSIBILITY_ID, "Apply"),
)


def _parse_usd_prices(blob: str) -> list[float]:
    out: list[float] = []
    for m in _PRICE_RE.finditer(blob or ""):
        try:
            out.append(float(m.group(1).replace(",", "")))
        except ValueError:
            continue
    return out


def _refresh_viewport_prices(driver) -> list[tuple[float, float, float]]:
    """Return (x, y, shelf_price) for visible $ labels (skip unit-price lines)."""
    rows: list[tuple[float, float, float]] = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND '
            '(label CONTAINS "$" OR name CONTAINS "$")',
        )
    except Exception:  # noqa: BLE001
        return rows
    for el in els[:35]:
        try:
            label = (el.get_attribute("label") or "") + " " + (
                el.get_attribute("name") or ""
            )
            low = label.lower()
            # Unit prices: "$0.24 / load", "$0.10 per oz"
            if "/" in label or " per " in low or "/oz" in low or "/load" in low:
                continue
            prices = _parse_usd_prices(label)
            if not prices:
                continue
            r = el.rect or {}
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            if y < 140 or y > 820:
                continue
            rows.append((x, y, max(prices)))
        except Exception:  # noqa: BLE001
            continue
    return rows


def _card_price_near_plus(
    plus_el,
    y_target: float,
    viewport_prices: list[tuple[float, float, float]],
) -> float | None:
    """Nearest shelf price on the same card as a yellow + (left of the +)."""
    own = _parse_usd_prices(element_textish(plus_el))
    if own:
        return max(own)

    try:
        px = float((plus_el.rect or {}).get("x", 0))
    except Exception:  # noqa: BLE001
        px = 0.0

    cands: list[tuple[float, float]] = []
    for x, y, price in viewport_prices:
        if abs(y - y_target) > 110:
            continue
        if x > px + 24:
            continue
        dist = abs(y - y_target) + abs(x - px) * 0.25
        cands.append((dist, price))
    if not cands:
        return None
    cands.sort(key=lambda t: t[0])
    return cands[0][1]


def _drag_price_slider_under_max(driver, markers: MarkerLog, outcome: dict) -> bool:
    """Best-effort drag of price-range max handle toward under-$5."""
    print("  Fallback: drag price slider toward under $5 …")
    try:
        size = driver.get_window_size()
        w, h = int(size["width"]), int(size["height"])
        from_x = int(w * 0.78)
        to_x = int(w * 0.52)
        y = int(h * 0.38)
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.55,
                "fromX": from_x,
                "fromY": y,
                "toX": to_x,
                "toY": y,
            },
        )
        markers.mark("price_slider_dragged", from_x=from_x, to_x=to_x, y=y)
        print(f"  Slider drag: ({from_x},{y}) → ({to_x},{y})")
        time.sleep(0.45)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  Slider drag failed: {exc}")
        return False


def _apply_under_5_filter(driver, markers: MarkerLog, outcome: dict) -> bool:
    """Filters → Price & Deals → Up to $5 → Show results (best-effort)."""
    print("Step: apply Filters → Price & Deals → Up to $5 …")
    hit = _try_tap_first(driver, FILTER_OPEN_STRATEGIES, timeout=2.5, scrolls=1)
    if not hit:
        print("  Filter button not found — will rely on per-card ≤$5 check")
        markers.mark("price_filter_skipped", reason="no_filter_button")
        outcome["price_filter_applied"] = False
        return False
    markers.mark("filter_opened", by=hit[0], value=hit[1])
    print(f"  Filter: {hit[0]} = {hit[1]!r}")
    time.sleep(0.55)

    price_hit = _try_tap_first(
        driver, PRICE_SECTION_STRATEGIES, timeout=2.5, scrolls=2
    )
    if not price_hit:
        print("  Price & Deals not found — dismissing filter sheet")
        _fast_tap(
            driver,
            (
                (AppiumBy.ACCESSIBILITY_ID, "Close"),
                (AppiumBy.ACCESSIBILITY_ID, "Cancel"),
            ),
            timeout=0.8,
        )
        markers.mark("price_filter_skipped", reason="no_price_section")
        outcome["price_filter_applied"] = False
        return False
    markers.mark("price_section_tapped", by=price_hit[0], value=price_hit[1])
    print(f"  Price section: {price_hit[0]} = {price_hit[1]!r}")
    time.sleep(0.4)

    preset = _try_tap_first(driver, PRICE_0_TO_5_STRATEGIES, timeout=2.5, scrolls=3)
    if preset:
        markers.mark("price_0_to_5_tapped", by=preset[0], value=preset[1])
        outcome["price_filter_method"] = "up_to_5_preset"
        print(f"  Price preset: {preset[0]} = {preset[1]!r}")
    elif _drag_price_slider_under_max(driver, markers, outcome):
        outcome["price_filter_method"] = "slider_drag_under_5"
    else:
        print("  Up to $5 control not found — dismissing filter sheet")
        _fast_tap(
            driver,
            (
                (AppiumBy.ACCESSIBILITY_ID, "Close"),
                (AppiumBy.ACCESSIBILITY_ID, "Cancel"),
            ),
            timeout=0.8,
        )
        markers.mark("price_filter_skipped", reason="no_up_to_5")
        outcome["price_filter_applied"] = False
        return False

    apply_hit = _try_tap_first(
        driver, FILTER_APPLY_STRATEGIES, timeout=3.0, scrolls=0
    )
    if not apply_hit:
        print('  "Show results" not found after price filter')
        markers.mark("price_filter_skipped", reason="no_show_results")
        outcome["price_filter_applied"] = False
        return False
    markers.mark("filter_show_results", by=apply_hit[0], value=apply_hit[1])
    print(f"  Show results: {apply_hit[0]} = {apply_hit[1]!r}")
    outcome["price_filter_applied"] = True
    time.sleep(1.0)
    _dismiss_overlay_fast(driver)
    return True

HOUSEHOLD_TAB_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Household"),
    (
        AppiumBy.IOS_PREDICATE,
        'label == "Household" OR name == "Household"',
    ),
)

PAYMENT_CHANGE = (
    (AppiumBy.ACCESSIBILITY_ID, "Change payment method"),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeStaticText[`label == "Change payment method"`]',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeLink[`label == "Change payment method"`]',
    ),
    (
        AppiumBy.IOS_PREDICATE,
        'label == "Change payment method" OR name == "Change payment method" OR '
        'label CONTAINS[c] "Change payment" OR name CONTAINS[c] "Change payment"',
    ),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Paying with" OR name CONTAINS[c] "Paying with"',
    ),
)

PROCEED_CHECKOUT_TC3 = (
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(label BEGINSWITH[c] "Proceed to checkout" OR '
        'name BEGINSWITH[c] "Proceed to checkout" OR '
        'label CONTAINS[c] "Proceed to checkout" OR '
        'name CONTAINS[c] "Proceed to checkout")',
    ),
    (AppiumBy.ACCESSIBILITY_ID, "Proceed to checkout"),
)

CONTINUE_BTN = (
    (AppiumBy.ACCESSIBILITY_ID, "Continue"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(label == "Continue" OR name == "Continue")',
    ),
)

# Prefer StaticText/Link — accessibility id often finds a non-opening hit.
ADDRESS_CHANGE = (
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeStaticText[`label == "Change delivery address"`]',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeLink[`label == "Change delivery address"`]',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeStaticText[`label CONTAINS[c] "Change delivery"`]',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeLink[`label CONTAINS[c] "Change delivery"`]',
    ),
    (AppiumBy.ACCESSIBILITY_ID, "Change delivery address"),
    (AppiumBy.ACCESSIBILITY_ID, "Change delivery or pickup address"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Change delivery or pickup" OR '
        'name CONTAINS[c] "Change delivery or pickup" OR '
        'label == "Change delivery address" OR name == "Change delivery address" OR '
        'label CONTAINS[c] "Change delivery address"',
    ),
)

USE_THIS_ADDRESS = (
    (AppiumBy.ACCESSIBILITY_ID, "Use this address"),
    (AppiumBy.ACCESSIBILITY_ID, "Deliver to this address"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(label == "Use this address" OR name == "Use this address" OR '
        'label CONTAINS[c] "Deliver to this address")',
    ),
)

SCREEN_PAYMENT = (
    (AppiumBy.ACCESSIBILITY_ID, "Select a payment method"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Select a payment method" OR '
        'name CONTAINS[c] "Select a payment method"',
    ),
)

SCREEN_ADDRESS = (
    (AppiumBy.ACCESSIBILITY_ID, "Select a billing address"),
    (AppiumBy.ACCESSIBILITY_ID, "Select a delivery address"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Select a billing address" OR '
        'name CONTAINS[c] "Select a billing address" OR '
        'label CONTAINS[c] "Select a delivery address" OR '
        'name CONTAINS[c] "Select a delivery address" OR '
        'label CONTAINS[c] "All addresses"',
    ),
)

RELOAD_PAGE = (
    (AppiumBy.ACCESSIBILITY_ID, "Reload the page"),
    (
        AppiumBy.IOS_PREDICATE,
        'label == "Reload the page" OR name == "Reload the page" OR '
        'label CONTAINS[c] "Reload the page"',
    ),
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _mark_step(outcome: dict, key: str, *, status: str, **extra) -> None:
    outcome.setdefault("checkout_steps", {})[key] = {"status": status, **extra}


def _dismiss_overlay_fast(driver) -> None:
    """Hide grocery search keyboard without tapping Produce (keeps Household)."""
    try:
        driver.hide_keyboard()
    except Exception:  # noqa: BLE001
        pass
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "Cancel"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(label == "Cancel" OR name == "Cancel")',
        ),
    ):
        try:
            el = driver.find_element(by, value)
            if el.is_displayed():
                el.click()
                time.sleep(0.15)
                return
        except Exception:  # noqa: BLE001
            continue


def _swipe_aisle_rail_left(driver) -> None:
    """Household often sits off-screen to the right of Produce/Dairy."""
    try:
        size = driver.get_window_size()
        w, h = int(size["width"]), int(size["height"])
        y = max(140, min(220, int(h * 0.22)))
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.22,
                "fromX": int(w * 0.88),
                "fromY": y,
                "toX": int(w * 0.12),
                "toY": y,
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _open_grocery_household(driver, markers: MarkerLog, outcome: dict) -> None:
    """Tap home Grocery, then Household aisle under the search bar."""
    print('Step: tap home "Grocery" tab …')
    hit = _try_tap_first(driver, GROCERY_HOME_TAB_STRATEGIES, timeout=3.5, scrolls=1)
    if not hit:
        raise AssertionError('Home "Grocery" tab not found')
    by, value = hit
    outcome.setdefault("selectors", {})["grocery_home_tab"] = {"by": by, "value": value}
    markers.mark("grocery_home_tab_tapped", by=by, value=value)
    print(f"  Grocery tab: {by} = {value!r}")
    time.sleep(0.4)
    _dismiss_rufus(driver, markers)
    _dismiss_overlay_fast(driver)

    print('Step: tap "Household" aisle …')
    household = _try_tap_first(driver, HOUSEHOLD_TAB_STRATEGIES, timeout=0.6, scrolls=0)
    if not household:
        for swipe_i in range(4):
            _swipe_aisle_rail_left(driver)
            markers.mark("household_aisle_swiped", index=swipe_i + 1)
            household = _try_tap_first(
                driver, HOUSEHOLD_TAB_STRATEGIES, timeout=0.45, scrolls=0
            )
            if household:
                break

    if not household:
        raise AssertionError('Grocery "Household" tab not found under search bar')

    outcome["selectors"]["household_tab"] = {
        "by": household[0],
        "value": household[1],
    }
    markers.mark("household_tab_tapped", by=household[0], value=household[1])
    print(f"  Household: {household[0]} = {household[1]!r}")
    time.sleep(0.45)
    _dismiss_overlay_fast(driver)
    _wait_household_products_ready(driver, markers, outcome, attempts=3)


def _wait_household_products_ready(
    driver, markers: MarkerLog, outcome: dict, *, attempts: int = 3
) -> None:
    """Household sometimes shows 'shelves are restocking' — Reload until + appears."""
    for i in range(attempts):
        # Empty / restocking interstitial.
        reload_hit = _fast_tap(driver, RELOAD_PAGE, timeout=0.5)
        if reload_hit:
            markers.mark(
                "household_reload_tapped",
                by=reload_hit[0],
                value=reload_hit[1],
                attempt=i + 1,
            )
            print(f"  Household restocking — tapped Reload ({i + 1}/{attempts})")
            time.sleep(1.0)
            # Re-tap Household in case reload reset the aisle.
            _try_tap_first(driver, HOUSEHOLD_TAB_STRATEGIES, timeout=0.7, scrolls=0)
            time.sleep(0.45)

        batch = _collect_grocery_plus(driver, set())
        if batch:
            markers.mark("household_products_ready", count=len(batch), attempt=i + 1)
            outcome["household_products_ready"] = True
            return
        try:
            find_first(driver, GROCERY_PLUS_STRATEGIES, timeout=0.8)
            markers.mark("household_products_ready", via="plus_strategy", attempt=i + 1)
            outcome["household_products_ready"] = True
            return
        except TimeoutError:
            print(f"  waiting for Household products ({i + 1}/{attempts}) …")
            time.sleep(0.55)

    outcome["household_products_ready"] = False
    markers.mark("household_products_not_ready")
    print("  WARNING: Household products still not visible — will try adds anyway")


def _add_n_household_fast(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    target: int,
    max_scrolls: int = 5,
) -> list[dict]:
    """Tap distinct Household yellow + buttons priced at or under $5 only."""
    print(
        f"Step: add {target} Household items via yellow + "
        f"(only ≤ ${MAX_PRICE_USD:g}) …"
    )
    _dismiss_overlay_fast(driver)
    start_badge = _cart_badge_count(driver)
    outcome["cart_count_before_adds"] = start_badge
    outcome["asin_add_strategy"] = "home_grocery_household_plus_fast"
    outcome["max_price_usd"] = MAX_PRICE_USD
    markers.mark(
        "household_plus_add_start",
        target=target,
        badge=start_badge,
        max_price_usd=MAX_PRICE_USD,
    )

    used: set[str] = set()
    added: list[dict] = []
    scrolls = 0
    skipped_over_budget = 0
    skipped_no_price = 0

    while len(added) < target and scrolls <= max_scrolls:
        batch = _collect_grocery_plus(driver, used)
        if not batch:
            try:
                el, by, value = find_first(driver, GROCERY_PLUS_STRATEGIES, timeout=0.7)
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
            markers.mark("household_grid_scrolled", index=scrolls)
            time.sleep(0.08)
            continue

        # One viewport price scan per screen — avoid ~30s/card find_elements.
        viewport_prices = _refresh_viewport_prices(driver)

        # Drain the visible batch before another find_elements / scroll.
        added_before = len(added)
        for el, by, value, fp, y in batch:
            if len(added) >= target:
                break
            print(
                f"  + candidate #{len(added) + 1}/{target}: "
                f"{by}={value!r} y={y:.0f} fp={fp}"
            )

            price = _card_price_near_plus(el, y, viewport_prices)
            if price is None:
                skipped_no_price += 1
                if fp:
                    used.add(fp)
                markers.mark(
                    "add_skipped_no_price",
                    fingerprint=fp,
                    y=y,
                )
                print("  skip — could not read card price (need ≤ $5)")
                continue
            if price > MAX_PRICE_USD:
                skipped_over_budget += 1
                if fp:
                    used.add(fp)
                markers.mark(
                    "add_skipped_over_budget",
                    fingerprint=fp,
                    price_usd=price,
                    max_price_usd=MAX_PRICE_USD,
                    y=y,
                )
                print(f"  skip — ${price:.2f} > ${MAX_PRICE_USD:g}")
                continue

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
                "household_plus_tapped",
                index=len(added) + 1,
                by=by,
                value=value,
                fingerprint=fp,
                y=y,
                price_usd=price,
            )
            print(f"  tapping + at ${price:.2f} …")
            time.sleep(0.14)

            ok = _qty_chip_visible_near(driver, y)
            confirm_via = "qty_chip"
            after: int | None = len(added) + 1 if ok else None
            if not ok:
                # Short badge poll only — long waits burned ~4–7s per dead +.
                ok, after = _wait_badge_up(driver, None, timeout=0.35)
                confirm_via = "badge"
            if not ok:
                print("  no confirm; try next")
                markers.mark(
                    "add_no_badge_increase",
                    index=len(added) + 1,
                    cart_before=None,
                    cart_after=after,
                    price_usd=price,
                )
                continue

            entry = {
                "index": len(added) + 1,
                "source": "grocery_household_plus",
                "add_path": "plus",
                "add_by": by,
                "add_value": value,
                "fingerprint": fp,
                "confirmed": True,
                "confirm_via": confirm_via,
                "price_usd": price,
                "cart_before": None,
                "cart_after": after,
            }
            added.append(entry)
            markers.mark(
                "item_added_to_cart",
                index=entry["index"],
                confirmed=True,
                cart_before=None,
                cart_after=after,
                fingerprint=fp,
                path="plus",
                confirm_via=confirm_via,
                price_usd=price,
            )
            print(
                f"  added #{entry['index']}/{target} via + "
                f"${price:.2f} ({confirm_via})"
            )

        # If this screen produced no adds (all over-budget / no-price / dead +),
        # always scroll — fingerprints can regenerate and spin forever otherwise.
        if len(added) == added_before and len(added) < target:
            _scroll_down(driver, percent=0.55)
            scrolls += 1
            markers.mark("household_grid_scrolled", index=scrolls, reason="no_add_on_screen")
            time.sleep(0.08)

    outcome["items_added"] = list(added)
    outcome["items_added_count"] = len(added)
    outcome["skipped_over_budget"] = skipped_over_budget
    outcome["skipped_no_price"] = skipped_no_price
    end_badge = _cart_badge_count(driver)
    outcome["cart_count_after_adds"] = end_badge
    markers.mark(
        "household_plus_add_done",
        added=len(added),
        target=target,
        badge_before=start_badge,
        badge_after=end_badge,
        scrolls=scrolls,
        skipped_over_budget=skipped_over_budget,
        skipped_no_price=skipped_no_price,
    )
    if len(added) < target:
        raise AssertionError(
            f"Only added {len(added)}/{target} Household + items ≤ ${MAX_PRICE_USD:g} "
            f"(scrolls={scrolls}, skipped_over=${skipped_over_budget}, "
            f"skipped_no_price={skipped_no_price}, badge {start_badge}→{end_badge})"
        )
    return added


def _fast_tap(driver, strategies, *, timeout: float = 1.5) -> tuple[str, str] | None:
    try:
        el, by, value = find_first(driver, strategies, timeout=timeout)
        el.click()
        return by, value
    except TimeoutError:
        return None
    except Exception:  # noqa: BLE001
        try:
            el2, by, value = find_first(driver, strategies, timeout=0.8)
            loc, size = el2.location, el2.size
            x = int(loc.get("x", 0) + (size.get("width", 0) or 0) / 2)
            y = int(loc.get("y", 0) + (size.get("height", 0) or 0) / 2)
            driver.execute_script("mobile: tap", {"x": x, "y": y})
            return by, value
        except Exception:  # noqa: BLE001
            return None


def _fast_scroll_find_tap(
    driver, strategies, *, scrolls: int = 3, timeout: float = 1.5
) -> tuple[str, str] | None:
    for i in range(scrolls + 1):
        hit = _fast_tap(driver, strategies, timeout=timeout if i == 0 else 0.9)
        if hit:
            return hit
        if i < scrolls:
            _scroll_down(driver, percent=0.55)
            time.sleep(0.15)
    return None


def _screen_matches(driver, strategies, *, timeout: float = 0.7) -> bool:
    try:
        find_first(driver, strategies, timeout=timeout)
        return True
    except TimeoutError:
        return False


def _place_order_visible(driver, *, timeout: float = 0.4) -> bool:
    """True only for native Place Your Order control — not the webview page title."""
    try:
        find_first(
            driver,
            (
                (AppiumBy.ACCESSIBILITY_ID, "Place Your Order"),
                (
                    AppiumBy.IOS_PREDICATE,
                    'label == "Place Your Order" OR name == "Place Your Order"',
                ),
            ),
            timeout=timeout,
        )
        return True
    except TimeoutError:
        return False


def _checkout_webview_visible(driver) -> bool:
    """Amazon sometimes opens a web checkout sheet whose title contains Place Your Order."""
    return _screen_matches(
        driver,
        (
            (
                AppiumBy.IOS_PREDICATE,
                'name CONTAINS[c] "Amazon.com Checkout" OR '
                'label CONTAINS[c] "Amazon.com Checkout"',
            ),
        ),
        timeout=0.3,
    )


def _detect_checkout_screen(driver) -> str:
    """Return current checkout surface for adaptive branching."""
    # Prefer cheap accessibility checks first — Place Your Order page source is huge
    # and predicate scans here regularly cost tens of seconds.
    if _place_order_visible(driver, timeout=0.35):
        return "place_order"
    if _screen_matches(driver, NO_THANKS_STRATEGIES, timeout=0.3):
        return "no_thanks"
    if _screen_matches(
        driver,
        ((AppiumBy.ACCESSIBILITY_ID, "Select a payment method"),),
        timeout=0.35,
    ):
        return "payment"
    if _screen_matches(
        driver,
        (
            (AppiumBy.ACCESSIBILITY_ID, "Select a billing address"),
            (AppiumBy.ACCESSIBILITY_ID, "Select a delivery address"),
        ),
        timeout=0.35,
    ):
        return "address"
    if _still_on_cart(driver):
        return "cart"
    if _screen_matches(driver, SCREEN_PAYMENT, timeout=0.35):
        return "payment"
    if _screen_matches(driver, CONTINUE_BTN, timeout=0.3) and _screen_matches(
        driver,
        (
            (
                AppiumBy.IOS_PREDICATE,
                'label CONTAINS[c] "Visa" OR label CONTAINS[c] "Mastercard" OR '
                'label CONTAINS[c] "MasterCard" OR label CONTAINS[c] "ending in"',
            ),
        ),
        timeout=0.3,
    ):
        return "payment"
    if _screen_matches(driver, SCREEN_ADDRESS, timeout=0.35):
        return "address"
    if _screen_matches(driver, USE_THIS_ADDRESS, timeout=0.3):
        return "address"
    if _screen_matches(
        driver,
        ((AppiumBy.ACCESSIBILITY_ID, "Change payment method"),),
        timeout=0.3,
    ):
        return "place_order"
    return "unknown"


def _wait_after_payment(driver, *, timeout: float = 16.0) -> str:
    """After Continue, poll cheaply for the next checkout surface."""
    end = time.monotonic() + timeout
    nudged = False
    while time.monotonic() < end:
        # Cheapest signals first — avoid full multi-strategy detects here.
        try:
            find_first(
                driver,
                ((AppiumBy.ACCESSIBILITY_ID, "Place Your Order"),),
                timeout=0.25,
            )
            return "place_order"
        except TimeoutError:
            pass
        if _screen_matches(driver, NO_THANKS_STRATEGIES, timeout=0.25):
            return "no_thanks"
        if _screen_matches(driver, SCREEN_ADDRESS, timeout=0.25):
            return "address"
        if _screen_matches(driver, USE_THIS_ADDRESS, timeout=0.25):
            return "address"
        if not nudged:
            if _fast_tap(driver, CONTINUE_BTN, timeout=0.4):
                nudged = True
                time.sleep(0.3)
                continue
            nudged = True
        time.sleep(0.35)
    # One final broader detect.
    return _detect_checkout_screen(driver)


def _open_address_from_place_order(driver, markers: MarkerLog, outcome: dict) -> bool:
    """Open address picker via the visible Change delivery address link (fast path)."""
    print('Step: tap "Change delivery address" …')

    try:
        size = driver.get_window_size() or {}
        win_h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        win_h = 844.0

    def _on_address_picker() -> bool:
        return _screen_matches(
            driver,
            (
                (AppiumBy.ACCESSIBILITY_ID, "Select a billing address"),
                (AppiumBy.ACCESSIBILITY_ID, "Select a delivery address"),
                (AppiumBy.ACCESSIBILITY_ID, "Deliver to this address"),
                (AppiumBy.ACCESSIBILITY_ID, "Use this address"),
                (
                    AppiumBy.IOS_PREDICATE,
                    'label CONTAINS[c] "Select a delivery address" OR '
                    'name CONTAINS[c] "Select a delivery address" OR '
                    'label CONTAINS[c] "Select a billing address" OR '
                    'name CONTAINS[c] "Select a billing address" OR '
                    'label CONTAINS[c] "All addresses" OR '
                    'name CONTAINS[c] "All addresses"',
                ),
            ),
            timeout=0.4,
        )

    def _swipe_toward_address(*, direction: str) -> None:
        # After expanding slots the address block is often above the fold.
        mid_x = 195
        if direction == "up":
            driver.execute_script(
                "mobile: swipe",
                {"direction": "up", "velocity": 800},
            )
        else:
            driver.execute_script(
                "mobile: dragFromToForDuration",
                {
                    "duration": 0.25,
                    "fromX": mid_x,
                    "fromY": int(win_h * 0.35),
                    "toX": mid_x,
                    "toY": int(win_h * 0.75),
                },
            )
        time.sleep(0.2)

    def _onscreen(y: float) -> bool:
        return 70.0 <= y <= win_h * 0.90

    def _tap_xy(x: float, y: float, *, via: str) -> bool:
        if not _onscreen(y):
            print(
                f"  skip off-screen Change delivery tap @ ({x:.0f},{y:.0f}) via={via}"
            )
            return False
        print(f"  Change delivery coordinate tap @ ({x:.0f},{y:.0f}) via={via}")
        driver.execute_script(
            "mobile: tap", {"x": int(round(x)), "y": int(round(y))}
        )
        time.sleep(0.75)
        if _on_address_picker():
            markers.mark("change_delivery_address", via=via, x=x, y=y)
            outcome["change_delivery_address_tapped"] = True
            _mark_step(
                outcome,
                "change_delivery_address",
                status="ok",
                via=via,
                x=x,
                y=y,
            )
            print(f"  Change address: opened ({via})")
            return True
        print(f"  tap via {via} did not open address picker — retrying")
        return False

    def _try_el(el, *, via: str) -> bool:
        try:
            r = el.rect or {}
            x = float(r.get("x", 0)) + float(r.get("width", 100)) / 2.0
            y = float(r.get("y", 0)) + float(r.get("height", 20)) / 2.0
        except Exception:  # noqa: BLE001
            x, y = 100.0, 300.0
        if not _onscreen(y):
            # Slot expand leaves this link above/below the viewport — nudge then re-read.
            print(f"  Change delivery off-screen y={y:.0f}; scrolling into view …")
            if y < 70:
                _swipe_toward_address(direction="down")  # content moves down = finger up
            else:
                _swipe_toward_address(direction="up")
            try:
                r = el.rect or {}
                x = float(r.get("x", 0)) + float(r.get("width", 100)) / 2.0
                y = float(r.get("y", 0)) + float(r.get("height", 20)) / 2.0
            except Exception:  # noqa: BLE001
                pass
            if not _onscreen(y):
                return False
        # Prefer coordinate tap — Link nodes are often accessible=false in checkout webview.
        if _tap_xy(x, y, via=via):
            return True
        try:
            el.click()
            time.sleep(0.75)
            if _on_address_picker():
                markers.mark("change_delivery_address", via=f"{via}_click")
                outcome["change_delivery_address_tapped"] = True
                _mark_step(
                    outcome,
                    "change_delivery_address",
                    status="ok",
                    via=f"{via}_click",
                )
                print(f"  Change address: opened ({via}_click)")
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    # 0) Slot expand often leaves address above the fold — scroll up first.
    for _ in range(2):
        try:
            el = driver.find_element(
                AppiumBy.ACCESSIBILITY_ID, "Change delivery address"
            )
            r = el.rect or {}
            y = float(r.get("y", 0))
            if _onscreen(y):
                break
            if y < 70:
                _swipe_toward_address(direction="down")
            else:
                _swipe_toward_address(direction="up")
        except Exception:  # noqa: BLE001
            _swipe_toward_address(direction="down")
            break

    # 1) Fast accessibility / class-chain hits (avoid full-tree predicate scans).
    fast_strategies = (
        (AppiumBy.ACCESSIBILITY_ID, "Change delivery address"),
        (AppiumBy.ACCESSIBILITY_ID, "Change delivery or pickup address"),
        (
            AppiumBy.IOS_CLASS_CHAIN,
            '**/XCUIElementTypeStaticText[`label == "Change delivery address"`]',
        ),
        (
            AppiumBy.IOS_CLASS_CHAIN,
            '**/XCUIElementTypeLink[`label == "Change delivery address"`]',
        ),
    )
    for by, value in fast_strategies:
        try:
            el = driver.find_element(by, value)
            if _try_el(el, via=f"fast:{value}"):
                return True
        except Exception:  # noqa: BLE001
            continue

    # 2) mobile:scroll to the exact label, then tap.
    try:
        driver.execute_script(
            "mobile: scroll",
            {
                "direction": "up",
                "predicateString": 'label == "Change delivery address"',
            },
        )
        time.sleep(0.2)
    except Exception:  # noqa: BLE001
        pass
    for by, value in fast_strategies[:2]:
        try:
            el = driver.find_element(by, value)
            if _try_el(el, via=f"scrolled:{value}"):
                return True
        except Exception:  # noqa: BLE001
            continue

    # 3) Anchor off "Delivering to …" — Change delivery address sits just below.
    try:
        delivering = driver.find_element(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeStaticText" AND '
            'label BEGINSWITH[c] "Delivering to"',
        )
        r = delivering.rect or {}
        y = float(r.get("y", 0)) + float(r.get("height", 24)) + 55.0
        x = 100.0
        if _tap_xy(x, y, via="below_delivering_to"):
            return True
    except Exception:  # noqa: BLE001
        pass

    # 4) Last resort: known iPhone 390pt coords — only if place-order address cues exist.
    if _screen_matches(
        driver,
        (
            (AppiumBy.ACCESSIBILITY_ID, "Change delivery address"),
            (
                AppiumBy.IOS_PREDICATE,
                'label BEGINSWITH[c] "Delivering to" OR '
                'label CONTAINS[c] "See more delivery slots"',
            ),
        ),
        timeout=0.3,
    ) and _tap_xy(103.0, 304.0, via="layout_coords"):
        return True

    _mark_step(
        outcome,
        "change_delivery_address",
        status="failed",
        reason="Change delivery address not opened within budget",
    )
    markers.mark("change_delivery_address_failed")
    print("  Change delivery address not opened")
    return False



def _tap_no_thanks(driver, markers: MarkerLog, outcome: dict) -> None:
    """Dismiss Prime Shipping trial interstitial if present.

    Prefer the popup's top-right X (close) or Cancel; fall back to No thanks.
    """
    print('Step: dismiss Prime popup (close / Cancel / No thanks) …')

    # 1) Top-right X on the Prime sheet (label/name "close", ~y 150–400, right side).
    try:
        size = driver.get_window_size() or {}
        mid_x = float(size.get("width") or 390) * 0.55
        for el in driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "close" OR name == "close" OR '
            'name == "xmark" OR label == "xmark" OR '
            'label == "Close" OR name == "Close")',
        )[:8]:
            try:
                if not el.is_displayed():
                    continue
                r = el.rect or {}
                x = float(r.get("x", 0))
                y = float(r.get("y", 0))
                if y < 120 or y > 520 or x < mid_x:
                    continue
                el.click()
                markers.mark("prime_popup_dismissed", via="close", x=x, y=y)
                _mark_step(outcome, "prime_popup_dismiss", status="ok", via="close")
                outcome["prime_popup_dismissed"] = True
                outcome["no_thanks_tapped"] = True
                print(f"  Prime popup: tapped close/X @ ({x:.0f},{y:.0f})")
                time.sleep(0.55)
                return
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass

    # 2) Cancel on the Prime sheet body only — never the y≈51 nav Cancel (exits checkout).
    try:
        el = driver.find_element(AppiumBy.ACCESSIBILITY_ID, "Cancel")
        if el.is_displayed():
            y = float((el.rect or {}).get("y", 0))
            if 120 <= y <= 560:
                el.click()
                markers.mark("prime_popup_dismissed", via="cancel", y=y)
                _mark_step(outcome, "prime_popup_dismiss", status="ok", via="cancel")
                outcome["prime_popup_dismissed"] = True
                outcome["no_thanks_tapped"] = True
                print(f"  Prime popup: tapped Cancel @ y={y:.0f}")
                time.sleep(0.55)
                return
    except Exception:  # noqa: BLE001
        pass

    # 3) Classic No thanks button.
    hit = _fast_scroll_find_tap(driver, NO_THANKS_STRATEGIES, scrolls=1, timeout=1.2)
    if not hit:
        _mark_step(outcome, "no_thanks", status="skipped", reason="No thanks not found")
        markers.mark("no_thanks_skipped")
        print("  Prime dismiss controls not found — skipping")
        return
    by, value = hit
    outcome.setdefault("selectors", {})["no_thanks"] = {"by": by, "value": value}
    markers.mark("no_thanks_tapped", by=by, value=value)
    _mark_step(outcome, "no_thanks", status="ok", by=by, value=value)
    outcome["no_thanks_tapped"] = True
    print(f"  No thanks: {by} = {value!r}")
    time.sleep(0.6)


def _payment_row_selected(png: bytes, scale: float, row_y: float) -> bool | None:
    """Amazon marks the chosen card with a light-blue tile (white otherwise)
    — the a11y tree gives both rows the same value. Sample under the card
    icon (x≈60 pt, ~70 pt below the card name)."""
    try:
        import io as _io

        from PIL import Image as _Image

        img = _Image.open(_io.BytesIO(png)).convert("RGB")
        x0, y0 = int(52 * scale), int((row_y + 62) * scale)
        px = [img.getpixel((x, y)) for x in range(x0, x0 + int(16 * scale), 3)
              for y in range(y0, y0 + int(16 * scale), 3)]
        r = sum(p[0] for p in px) / len(px)
        b = sum(p[2] for p in px) / len(px)
        return (b - r) > 6 and r < 248
    except Exception:  # noqa: BLE001
        return None


def _select_second_payment_card(driver, markers: MarkerLog, outcome: dict) -> None:
    """On Select a Payment Method: switch to the saved card that is NOT the
    one currently selected (light-blue tile)."""
    print("Step: select the other saved payment card …")
    time.sleep(0.35)

    # Saved cards are buttons like "Your Bank •••• 1234 …" / "Mastercard ••••
    # 5678 …" — the bank name is not always a card brand, so match the digits.
    rows: list[tuple[float, float, str]] = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(label CONTAINS "••••" OR label CONTAINS[c] "ending in")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:10]:
        try:
            blob = (el.get_attribute("label") or "").strip()
            low = blob.lower()
            # Never a card: Affirm, gift cards and card OFFERS ("Get a $50
            # Amazon Gift Card upon approval for Amazon Visa" opens the Visa
            # application page).
            if any(k in low for k in ("affirm", "ineligible", "gift card",
                                      "upon approval", "apply", "add new",
                                      "promo", "offer")):
                continue
            r = el.rect or {}
            y = float(r.get("y", 0))
            if y < 100:
                continue
            rows.append((y, float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0, blob))
        except Exception:  # noqa: BLE001
            continue
    rows.sort(key=lambda t: t[0])
    deduped: list[tuple[float, float, str]] = []
    for row in rows:
        if deduped and abs(row[0] - deduped[-1][0]) < 28:
            continue
        deduped.append(row)
    if len(deduped) < 2:
        raise AssertionError(
            f"Could not find 2nd payment card (card_rows={len(deduped)}, radios=0)"
        )

    pick = None
    try:
        png = driver.get_screenshot_as_png()
        w = float((driver.get_window_size() or {}).get("width") or 390)
        from PIL import Image as _Image
        import io as _io

        scale = _Image.open(_io.BytesIO(png)).size[0] / w
        sel = [_payment_row_selected(png, scale, y) for y, _x, _b in deduped]
        print(f"  saved cards: {[b[:28] for _y, _x, b in deduped]} selected={sel}")
        if sel.count(True) == 1:
            pick = next(row for row, on in zip(deduped, sel) if not on)
    except Exception as exc:  # noqa: BLE001
        print(f"  selected-card check failed: {exc}")
    if pick is None:
        pick = deduped[1]  # can't tell which is selected: the 2nd card
    y, x, blob = pick
    print(f"  Tapping payment card: {blob[:60]!r}")
    _tap_xy_card = getattr(driver, "execute_script")
    _tap_xy_card("mobile: tap", {"x": x, "y": y + 10})
    markers.mark("second_payment_card_tapped", text=blob[:80])
    _mark_step(outcome, "second_payment_card", status="ok", text=blob[:80])
    outcome["second_payment_card_tapped"] = True
    outcome["payment_card_selected"] = blob[:80]
    time.sleep(0.6)


def _tap_payment_continue(driver) -> bool:
    """The picker has two "Continue" buttons: one sticky under the nav bar
    (often half hidden) and one at the bottom. Tap whichever shows enough."""
    try:
        h = float((driver.get_window_size() or {}).get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    for _ in range(3):
        try:
            btns = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND (name == "Continue" OR label == "Continue")',
            )
        except Exception:  # noqa: BLE001
            btns = []
        for el in btns[:4]:
            try:
                r = el.rect or {}
                top = max(float(r.get("y", 0)), 104.0)
                bot = min(float(r.get("y", 0)) + float(r.get("height", 0)), h - 40)
                if bot - top >= 12:
                    driver.execute_script("mobile: tap", {
                        "x": float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0,
                        "y": (top + bot) / 2.0,
                    })
                    return True
            except Exception:  # noqa: BLE001
                continue
        # Neither on screen: scroll back to the top (sticky Continue).
        try:
            driver.execute_script("mobile: dragFromToWithVelocity", {
                "fromX": 195, "fromY": h * 0.35, "toX": 195, "toY": h * 0.85,
                "velocity": 900, "pressDuration": 0.1, "holdDuration": 0.1,
            })
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.6)
    return False


def _handle_payment_screen(driver, markers: MarkerLog, outcome: dict) -> None:
    """Already on payment picker: select 2nd card → Continue."""
    print("  Screen: payment method picker")
    markers.mark("screen_payment_detected")
    outcome["change_payment_tapped"] = True  # opened by checkout flow itself
    _mark_step(outcome, "change_payment", status="ok", via="auto_screen")
    _select_second_payment_card(driver, markers, outcome)

    print('Step: tap "Continue" …')
    if _tap_payment_continue(driver):
        cont = ("position", "Continue")
    else:
        cont = _fast_scroll_find_tap(driver, CONTINUE_BTN, scrolls=1, timeout=1.5)
    if not cont:
        raise AssertionError('"Continue" not found on payment method screen')
    markers.mark("payment_continue_tapped", by=cont[0], value=cont[1])
    _mark_step(outcome, "payment_continue", status="ok", by=cont[0], value=cont[1])
    outcome["payment_continue_tapped"] = True
    print(f"  Continue: {cont[0]} = {cont[1]!r}")
    time.sleep(0.35)


def _handle_address_screen(driver, markers: MarkerLog, outcome: dict) -> None:
    """Already on address picker: select 2nd → Use/Deliver this address."""
    print("  Screen: address picker")
    markers.mark("screen_address_detected")
    outcome["change_delivery_address_tapped"] = True
    _mark_step(outcome, "change_delivery_address", status="ok", via="auto_screen")
    _tc2_tap_second_address(driver, markers, outcome)

    print('Step: tap "Use this address" / "Deliver to this address" …')
    hit = _fast_scroll_find_tap(driver, USE_THIS_ADDRESS, scrolls=1, timeout=1.5)
    if not hit:
        _tap_deliver_to_this_address(driver, markers, outcome)
    else:
        markers.mark("deliver_to_this_address", by=hit[0], value=hit[1])
        outcome["deliver_to_this_address_tapped"] = True
        _mark_step(outcome, "deliver_address", status="ok", by=hit[0], value=hit[1])
        print(f"  Address confirm: {hit[0]} = {hit[1]!r}")
        time.sleep(0.5)


def _tap_point(driver, x: float, y: float) -> None:
    """Plain coordinate tap (no element lookup)."""
    driver.execute_script("mobile: tap", {"x": int(round(x)), "y": int(round(y))})


_CHANGE_PAY_LABEL = "Change payment method"
_PLACE_ORDER_CLEARANCE_PT = 90  # same clearance as the Place Your Order qty raise
_CHANGE_PAY_MAX_TAPS = 2


def _change_pay_rects(driver) -> list[tuple[float, float, float, float]]:
    """(x, y, w, h) of every "Change payment method" link the page reports."""
    out: list[tuple[float, float, float, float]] = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            f'label == "{_CHANGE_PAY_LABEL}" OR name == "{_CHANGE_PAY_LABEL}"',
        )
    except Exception:  # noqa: BLE001
        return out
    for el in els[:6]:
        try:
            r = el.rect or {}
            box = (float(r.get("x", 0)), float(r.get("y", 0)),
                   float(r.get("width", 0)), float(r.get("height", 0)))
        except Exception:  # noqa: BLE001
            continue
        if box[2] > 0 and not any(abs(box[1] - o[1]) < 6 for o in out):
            out.append(box)
    out.sort(key=lambda b: b[1])
    return out


def _place_order_button_ys(driver) -> list[float]:
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(label ==[c] "Place your order" OR name ==[c] "Place your order")',
        )
    except Exception:  # noqa: BLE001
        return []
    ys = []
    for el in els[:4]:
        try:
            r = el.rect or {}
            ys.append(float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0)
        except Exception:  # noqa: BLE001
            continue
    return ys


def _screen_confirms_link(driver, cx: float, cy: float, w: float):
    """Live-screenshot check at the tap point → (ok, reason).

    ok only if blue link text is really drawn there (the page's reported
    position is fresh) and no yellow "Place your order" button is drawn
    within the clearance band above/below it."""
    try:
        import io as _io

        from PIL import Image as _Image

        png = driver.get_screenshot_as_png()
        img = _Image.open(_io.BytesIO(png)).convert("RGB")
        win_w = float((driver.get_window_size() or {}).get("width") or 390)
        k = img.size[0] / win_w
    except Exception as exc:  # noqa: BLE001
        return False, f"screenshot failed: {exc}"
    W, H = img.size

    def _count(x0, y0, x1, y1, test) -> int:
        n = 0
        for yy in range(max(0, int(y0 * k)), min(H, int(y1 * k)), 3):
            for xx in range(max(0, int(x0 * k)), min(W, int(x1 * k)), 3):
                if test(img.getpixel((xx, yy))):
                    n += 1
        return n

    def blue(p):
        r, g, b = p
        return b > 120 and r < 110 and (b - r) > 60

    def yellow(p):
        r, g, b = p
        return r > 225 and g > 180 and b < 110

    if _count(cx - w / 2, cy - 8, cx + w / 2, cy + 8, blue) < 6:
        return False, "no link text drawn at the reported position (stale)"
    band = _PLACE_ORDER_CLEARANCE_PT
    if _count(10, cy - band, win_w - 10, cy + band, yellow) > 25:
        return False, "Place your order button within clearance"
    return True, ""


def _open_payment_from_place_order(driver, markers: MarkerLog, outcome: dict) -> bool:
    """From Place Your Order, open Change payment method — only by a
    coordinate tap at a position confirmed fresh (stable across two reads,
    link text visible in a live screenshot) and at least
    _PLACE_ORDER_CLEARANCE_PT away from "Place your order" (page-reported
    AND on screen). On big pages the tree's positions can be stale and
    el.click() tapped elsewhere (19 Oct 2026, basket F looped 8×)."""
    print('Step: tap "Change payment method" (if on Place Your Order) …')
    taps = int(outcome.get("change_payment_taps") or 0)
    if taps >= _CHANGE_PAY_MAX_TAPS:
        print(f"  payment page did not open after {taps} tap(s) — skipping payment change")
        _mark_step(outcome, "change_payment", status="skipped",
                   reason="payment page did not open after safe taps")
        outcome["payment_change_skipped"] = "payment page did not open"
        markers.mark("change_payment_skipped", reason="max_taps")
        return False
    try:
        h = float((driver.get_window_size() or {}).get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    deadline = time.monotonic() + 10.0
    reason = "link not found"
    while time.monotonic() < deadline:
        first = _change_pay_rects(driver)
        time.sleep(0.6)
        second = _change_pay_rects(driver)
        pick = None
        for b in second:
            cy = b[1] + b[3] / 2.0
            if not (120 <= cy <= h * 0.85):
                continue
            # Fresh 1: the same position on two separate reads.
            if not any(abs(b[1] - a[1]) <= 3 and abs(b[0] - a[0]) <= 3 for a in first):
                reason = "position not stable"
                continue
            pick = b
            break
        if pick is None:
            if not second:
                reason = "link not found"
            _scroll_down(driver, percent=0.30)
            time.sleep(0.5)
            continue
        x, y, w, hh = pick
        cx, cy = x + min(w, 200.0) / 2.0, y + hh / 2.0
        # Clearance 1: page-reported "Place your order" buttons.
        if any(abs(cy - py) < _PLACE_ORDER_CLEARANCE_PT for py in _place_order_button_ys(driver)):
            reason = "too close to Place your order (page)"
            _scroll_down(driver, percent=0.25)
            time.sleep(0.5)
            continue
        # Fresh 2 + clearance 2: what is actually drawn on screen.
        ok, why = _screen_confirms_link(driver, cx, cy, min(w, 200.0))
        if not ok:
            reason = why
            if "clearance" in why:
                _scroll_down(driver, percent=0.25)
            time.sleep(0.6)
            continue
        _tap_point(driver, cx, cy)
        outcome["change_payment_taps"] = taps + 1
        markers.mark("change_payment_tapped", via="confirmed_coordinate", x=cx, y=cy)
        _mark_step(outcome, "change_payment", status="ok", via="confirmed_coordinate")
        outcome["change_payment_tapped"] = True
        print(f"  Change payment: confirmed tap @ ({cx:.0f},{cy:.0f}) — fresh + clear of Place your order")
        time.sleep(0.6)
        return True
    _mark_step(outcome, "change_payment", status="skipped", reason=reason)
    markers.mark("change_payment_skipped", reason=reason[:80])
    outcome["payment_change_skipped"] = reason
    print(f"  Change payment not tapped — {reason}; skipping (no unsafe tap)")
    return False


def _still_on_cart(driver) -> bool:
    """Cheap cart check — avoid full checkout-tree scans on huge cart pages."""
    try:
        find_first(
            driver,
            (
                (
                    AppiumBy.IOS_PREDICATE,
                    'type == "XCUIElementTypeButton" AND visible == 1 AND '
                    '(label BEGINSWITH[c] "Proceed to checkout" OR '
                    'name BEGINSWITH[c] "Proceed to checkout")',
                ),
            ),
            timeout=0.55,
        )
        return True
    except TimeoutError:
        return False


def _left_cart_for_checkout(driver) -> bool:
    """True when Proceed is gone OR a checkout / authorize surface is visible."""
    if _screen_matches(
        driver,
        (
            (AppiumBy.ACCESSIBILITY_ID, "Continue"),
            (AppiumBy.ACCESSIBILITY_ID, "No thanks"),
            (AppiumBy.ACCESSIBILITY_ID, "Place your order"),
            (AppiumBy.ACCESSIBILITY_ID, "Place Your Order"),
            (AppiumBy.ACCESSIBILITY_ID, "Authorize Payment Method"),
            (
                AppiumBy.IOS_PREDICATE,
                'label BEGINSWITH[c] "Select a Payment" OR '
                'name BEGINSWITH[c] "Select a Payment" OR '
                'label == "Paying with" OR name == "Paying with" OR '
                'label CONTAINS[c] "Authorize Payment" OR '
                'name CONTAINS[c] "Authorize Payment" OR '
                'label CONTAINS[c] "Verify by Phone"',
            ),
        ),
        timeout=0.4,
    ):
        return True
    return not _still_on_cart(driver)


def _tap_proceed_until_left_cart(driver, markers: MarkerLog, outcome: dict) -> None:
    """Tap Proceed to checkout and verify we leave the cart screen."""
    print('Step: tap "Proceed to checkout" …')

    def _scroll_cart_to_top() -> None:
        try:
            driver.execute_script(
                "mobile: swipe", {"direction": "down", "percent": 0.65}
            )
        except Exception:  # noqa: BLE001
            try:
                driver.execute_script(
                    "mobile: dragFromToForDuration",
                    {
                        "duration": 0.18,
                        "fromX": 195,
                        "fromY": 300,
                        "toX": 195,
                        "toY": 580,
                    },
                )
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.25)

    _scroll_cart_to_top()

    for attempt in range(4):
        try:
            el, by, value = find_first(
                driver,
                (
                    (
                        AppiumBy.IOS_PREDICATE,
                        'type == "XCUIElementTypeButton" AND visible == 1 AND '
                        '(label BEGINSWITH[c] "Proceed to checkout" OR '
                        'name BEGINSWITH[c] "Proceed to checkout" OR '
                        'label CONTAINS[c] "Proceed to checkout" OR '
                        'name CONTAINS[c] "Proceed to checkout")',
                    ),
                    (AppiumBy.ACCESSIBILITY_ID, "Proceed to checkout"),
                ),
                timeout=3.0,
            )
        except TimeoutError as exc:
            if attempt < 3:
                print("  Proceed not visible — scrolling cart to top …")
                _scroll_cart_to_top()
                continue
            raise AssertionError("Proceed to checkout not found on cart") from exc

        r = {}
        try:
            r = el.rect or {}
        except Exception:  # noqa: BLE001
            r = {}
        cx = float(r.get("x", 15)) + float(r.get("width", 360)) / 2.0
        cy = float(r.get("y", 178)) + float(r.get("height", 42)) / 2.0
        if cy < 80 or cy > 780:
            print(f"  Proceed off-screen y={cy:.0f} — scrolling to top …")
            _scroll_cart_to_top()
            continue

        print(f"  Proceed tap @ ({cx:.0f},{cy:.0f}) attempt={attempt + 1}")
        # One activation only. Prefer coordinate tap — more reliable on cart webviews.
        try:
            driver.execute_script(
                "mobile: tap",
                {"x": int(round(cx)), "y": int(round(cy))},
            )
        except Exception:  # noqa: BLE001
            try:
                el.click()
            except Exception as click_exc:  # noqa: BLE001
                print(f"  Proceed activation failed: {click_exc}")

        markers.mark(
            "proceed_checkout",
            by=by,
            value=value,
            attempt=attempt + 1,
            x=cx,
            y=cy,
        )
        outcome["proceed_checkout_tapped"] = True
        outcome.setdefault("selectors", {})["proceed_checkout"] = {
            "by": by,
            "value": value,
        }
        print(f"  Proceed: {by} = {value!r} (attempt {attempt + 1})")

        time.sleep(2.0)
        for _ in range(10):
            if _left_cart_for_checkout(driver):
                return
            time.sleep(0.5)
        print("  still on cart after Proceed — retrying …")
        _scroll_cart_to_top()
        time.sleep(0.2)

    if not _left_cart_for_checkout(driver):
        raise AssertionError("Proceed to checkout tapped but still on Cart screen")


def _wait_checkout_entry(driver, markers: MarkerLog, *, timeout: float = 12.0) -> str:
    """After Proceed, wait for a real checkout surface (prefer payment over webview title)."""
    end = time.monotonic() + timeout
    saw_webview = False
    while time.monotonic() < end:
        screen = _detect_checkout_screen(driver)
        if screen in ("payment", "no_thanks", "address", "place_order"):
            return screen
        if _checkout_webview_visible(driver):
            if not saw_webview:
                markers.mark("checkout_webview_seen")
                print("  checkout webview sheet visible — waiting briefly for native UI …")
                saw_webview = True
            time.sleep(0.5)
            continue
        time.sleep(0.3)
    if saw_webview and _checkout_webview_visible(driver):
        return "webview"
    return _detect_checkout_screen(driver)


def _tc3_checkout_edits(driver, markers: MarkerLog, outcome: dict) -> None:
    """Screen-driven checkout: only act on screens that appear; skip the rest.

    After Proceed, Amazon may land on:
      - No thanks (Prime)
      - payment card picker
      - billing/delivery address picker
      - Place Your Order
    Handle whichever is visible; do not require every intermediate step.
    """
    _tap_proceed_until_left_cart(driver, markers, outcome)
    time.sleep(0.25)

    payment_done = False
    address_done = False

    # Don't trust the first frame — webview title can contain "Place Your Order".
    first = _wait_checkout_entry(driver, markers, timeout=12.0)
    markers.mark("checkout_screen", screen=first, round=0, via="entry_wait")
    print(f"  checkout entry surface: {first}")
    if first == "webview":
        raise AssertionError(
            "Proceed opened Amazon webview checkout (not native grocery payment/"
            "Place Your Order). Clear the main Amazon cart and retry — a bloated "
            "cart often forces this inaccessible webview sheet."
        )

    for round_i in range(8):
        screen = first if round_i == 0 else _detect_checkout_screen(driver)
        if round_i > 0:
            markers.mark("checkout_screen", screen=screen, round=round_i + 1)
            print(f"  checkout screen detect: {screen} (round {round_i + 1})")
        else:
            print(f"  checkout screen detect: {screen} (round 1)")

        if screen == "no_thanks":
            _tap_no_thanks(driver, markers, outcome)
            time.sleep(0.3)
            continue

        if screen == "payment":
            if payment_done:
                screen = _wait_after_payment(driver, timeout=10.0)
                markers.mark(
                    "checkout_screen",
                    screen=screen,
                    round=round_i + 1,
                    via="post_payment",
                )
                print(f"  post-payment wait → {screen}")
                if screen == "payment":
                    continue
            else:
                _handle_payment_screen(driver, markers, outcome)
                payment_done = True
                screen = _wait_after_payment(driver, timeout=12.0)
                markers.mark(
                    "checkout_screen",
                    screen=screen,
                    round=round_i + 1,
                    via="post_payment",
                )
                print(f"  post-payment wait → {screen}")

            if screen == "no_thanks":
                _tap_no_thanks(driver, markers, outcome)
                time.sleep(0.3)
                continue
            if screen == "address":
                if not address_done:
                    _handle_address_screen(driver, markers, outcome)
                    address_done = True
                break
            if screen == "place_order":
                pass
            else:
                continue

        if screen == "address":
            if address_done:
                break
            _handle_address_screen(driver, markers, outcome)
            address_done = True
            break

        if screen == "place_order":
            if not payment_done:
                if _open_payment_from_place_order(driver, markers, outcome):
                    time.sleep(0.35)
                    continue
                payment_done = True
            if not address_done:
                if not _open_address_from_place_order(driver, markers, outcome):
                    raise AssertionError(
                        "Could not open Change delivery address on Place Your Order"
                    )
                _handle_address_screen(driver, markers, outcome)
                address_done = True
            break

        # Unknown / webview: native payment never appeared — fail fast (do not spin).
        if _checkout_webview_visible(driver):
            raise AssertionError(
                "Proceed opened Amazon webview checkout (not native grocery payment/"
                "Place Your Order). Clear the main Amazon cart and retry — a bloated "
                "cart often forces this inaccessible webview sheet."
            )
        if round_i == 0:
            _tap_no_thanks(driver, markers, outcome)
        time.sleep(0.35)

    outcome["payment_flow_done"] = payment_done
    outcome["address_flow_done"] = address_done
    markers.mark(
        "checkout_edits_done",
        payment_done=payment_done,
        address_done=address_done,
    )
    if not payment_done and not address_done:
        raise AssertionError(
            "After Proceed, could not complete payment or address screens "
            f"(last detect loop finished without edits)"
        )
    if payment_done and not address_done:
        raise AssertionError(
            "Payment step finished but address change did not complete"
        )
    print(
        f"  checkout edits done — payment={payment_done} address={address_done}"
    )


def run_test_case_3(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """TASK 3: Grocery → Household → +×5 → cart record → payment + address edits."""
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
    plan["max_price_usd"] = MAX_PRICE_USD
    plan["add_strategy"] = "home_grocery_household_plus_fast"
    plan["place_order"] = False
    plan["steps"] = [
        "Start new automation session (kill prior Amazon if running)",
        "Open Amazon App (cold start)",
        'Tap home "Grocery" tab',
        'Tap "Household" aisle under search bar',
        f"Add {TARGET_ITEMS} different Household items under ${MAX_PRICE_USD:g} only",
        "Open Cart screen",
        f"START screen recording on Cart for {rec_limit}s",
        'Tap "Proceed to checkout"',
        "Adaptively handle whichever checkout screen appears:",
        '  - "No thanks" (Prime) if shown',
        "  - Payment card picker → select 2nd card → Continue",
        "  - Address picker → select 2nd address → Use/Deliver this address",
        "  - Place Your Order → open Change payment / Change address only if needed",
        "Stop screen recording and quit session (do not place order)",
    ]
    plan["notes"] = (
        f"{TEST_CASE_NAME} (TASK 3) — Grocery → Household → yellow + ×{TARGET_ITEMS}; "
        "cart record → Proceed → screen-driven payment/address edits "
        "(skip steps whose screens do not appear). Does not place the order."
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

        _open_grocery_household(driver, markers, outcome)
        _apply_under_5_filter(driver, markers, outcome)
        _add_n_household_fast(
            driver, markers, outcome, target=TARGET_ITEMS, max_scrolls=14
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
        _tc3_checkout_edits(driver, markers, outcome)

        # Brief pause so last taps are captured before stop.
        time.sleep(0.4)
        markers.mark(
            "test_case_3_stopped_after_address",
            items=outcome.get("items_added_count"),
            checkout_steps=outcome.get("checkout_steps"),
        )
        print("Stopping process after address change (order not placed) …")

        outcome["status"] = "ok"
        outcome["message"] = (
            f"{TEST_CASE_NAME} OK — added {outcome.get('items_added_count')} "
            f"via Grocery→Household +; cart → record → checkout → No thanks → "
            f"2nd payment → Continue → 2nd address → Deliver"
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
