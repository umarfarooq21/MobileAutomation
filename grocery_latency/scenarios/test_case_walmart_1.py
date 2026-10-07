"""Test Case Walmart 1: My Items → Fresh Produce → +Add×5 → cart record → edits.

Walmart (iOS):
  1. Tap bottom tab "My Items"
  2. Tap "Fresh Produce" filter
  3. Tap blue "+ Add" on 5 different items
  4. Open Cart → start screen recording
  5. Tap "Change" (delivery time on cart) → Reserve a time
  6. Tap "Change" (address) → pick 2nd saved address
  7. Toggle delivery day between last / 2nd-last + same for time slot → tap "Reserve"
  8. Increase 1st cart item quantity via +
  9. Tap "Continue to checkout"
 10. On "Missing anything?" tap "Continue" → land on Checkout → stop recording

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
    find_first,
    looks_like_login_wall,
    tap_dismissibles,
)
from grocery_latency.markers import MarkerLog
from grocery_latency.recording import save_recording_bytes, start_ios_screen_recording

TEST_CASE_NAME = "Test Case Walmart 1"
TEST_CASE_ID = "test_case_walmart_1"
TARGET_ITEMS = 5
CART_QTY_INCREASE_BY = 4  # screenshots show qty 1 → 5
# Appium recording timeLimit (cap) — do not lower this for speed work.
CART_RECORDING_SECONDS = 240
# Short UI settles — prefer action over waiting.
_FAST = 0.08
_MED = 0.16
_SLOW = 0.28

WALMART_DISMISS_LABELS = (
    "Not Now",
    "Not now",
    "No Thanks",
    "No thanks",
    "Maybe Later",
    "Close",
    "Dismiss",
    "Got It",
    "Got it",
    "Allow",
    "Allow While Using App",
    "Don’t Allow",
    "Don't Allow",
    "OK",
)

MY_ITEMS_TAB_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "My Items"),
    # Do not require visible==1 — bottom tab is often visible=false but tappable.
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(label == "My Items" OR name == "My Items" OR '
        'label CONTAINS[c] "My Items" OR name CONTAINS[c] "My Items")',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeButton[`label == "My Items" OR name == "My Items"`]',
    ),
)

FRESH_PRODUCE_STRATEGIES = (
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeCell" AND visible == 1 AND '
        '(name == "myItems_departmentTab_0" OR '
        'label CONTAINS[c] "Fresh Produce")',
    ),
    (AppiumBy.ACCESSIBILITY_ID, "Fresh Produce"),
    (
        AppiumBy.IOS_PREDICATE,
        'visible == 1 AND '
        '(label == "Fresh Produce" OR name == "Fresh Produce" OR '
        'label CONTAINS[c] "Fresh Produce" OR name CONTAINS[c] "Fresh Produce")',
    ),
)

# Walmart: unused +Add is often name=stepper.addToCartButton (Other, visible=false);
# after first use it becomes Button stepper.mainButton label="Add …".
# Do NOT require visible==1 — chips often mark on-screen Adds as visible=false.
ADD_BUTTON_PREDICATE = (
    '(type == "XCUIElementTypeButton" AND name == "stepper.mainButton") OR '
    'name == "stepper.addToCartButton"'
)

CART_ICON_STRATEGIES = (
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label CONTAINS[c] "cart" OR name CONTAINS[c] "cart" OR '
        'label CONTAINS[c] "Cart" OR name CONTAINS[c] "Cart")',
    ),
    (AppiumBy.ACCESSIBILITY_ID, "Cart"),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeButton[`label CONTAINS[c] "Cart" OR name CONTAINS[c] "Cart" OR '
        'label CONTAINS[c] "cart" OR name CONTAINS[c] "cart"`]',
    ),
)


def _cart_screen_visible(driver) -> bool:
    """True when Cart page chrome is on screen (not header cart icon alone).

    Header cart badge labels look like
    'Your cart contains N items. The estimated total is $…' — never treat those
    as Cart page chrome (they match naive 'Estimated total' / 'Cart (' contains).
    """
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label == "Continue to checkout" OR name == "Continue to checkout" OR '
            'label CONTAINS[c] "Continue to checkout" OR '
            'label == "Estimated total" OR name == "Estimated total" OR '
            'label BEGINSWITH[c] "Estimated total" OR '
            'label == "Pickup and delivery" OR '
            'label CONTAINS[c] "Pickup and delivery options" OR '
            'name == "FulfillmentSummaryView.actionButton" OR '
            'label CONTAINS[c] "Change delivery" OR '
            'label == "Reservation expired" OR '
            'label CONTAINS[c] "Reservation expired" OR '
            'label == "Delivery from store" OR '
            'label CONTAINS[c] "Delivery from store" OR '
            'name == "CartPrimaryCTAButton")',
        )
        if els:
            # Exclude the top-right header cart VoiceOver summary button.
            for el in els[:12]:
                try:
                    lab = (el.get_attribute("label") or "").lower()
                    if "your cart contains" in lab:
                        continue
                    r = el.rect or {}
                    y = float(r.get("y", 0))
                    # Real cart CTAs / totals sit below the status+nav chrome.
                    if y < 90 and "continue to checkout" not in lab:
                        continue
                    return True
                except Exception:  # noqa: BLE001
                    continue
        # Overlay / heavy carts: match without visible==1; still skip header badge.
        els2 = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            '('
            'label == "Continue to checkout" OR name == "Continue to checkout" OR '
            'label CONTAINS[c] "Continue to checkout" OR '
            'label == "Estimated total" OR name == "Estimated total" OR '
            'label BEGINSWITH[c] "Estimated total" OR '
            'label CONTAINS[c] "Reservation expired" OR '
            'label CONTAINS[c] "Delivery from store" OR '
            'name == "CartPrimaryCTAButton" OR '
            'name == "FulfillmentSummaryView.actionButton" OR '
            '(type == "XCUIElementTypeStaticText" AND label CONTAINS[c] "Cart (")'
            ') AND NOT (label CONTAINS[c] "Your cart contains")',
        )
        return bool(els2)
    except Exception:  # noqa: BLE001
        return False


def _wait_cart_screen(driver, timeout: float = 8.0) -> bool:
    """Poll for cart chrome — large carts can take seconds to expose a11y nodes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _cart_screen_visible(driver):
            return True
        time.sleep(0.35)
    return _cart_screen_visible(driver)


def _header_cart_icon_present(driver) -> bool:
    """True when a top-right header cart control is still findable (not on Cart)."""
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        w, h = 390.0, 844.0
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label CONTAINS[c] "cart" OR name CONTAINS[c] "cart" OR '
            'label CONTAINS[c] "Cart" OR name CONTAINS[c] "Cart")',
        )
    except Exception:  # noqa: BLE001
        return False
    for el in els[:20]:
        try:
            low = (
                f"{el.get_attribute('label') or ''} "
                f"{el.get_attribute('name') or ''}"
            ).lower()
            if "add to cart" in low or "add to" in low:
                continue
            r = el.rect or {}
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            ww = float(r.get("width", 0))
            if y > h * 0.22 or y < 8:
                continue
            if x + ww / 2.0 < w * 0.55:
                continue
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _my_items_add_visible(driver) -> bool:
    """True when My Items listing Add steppers are still on screen."""
    try:
        els = driver.find_elements(AppiumBy.IOS_PREDICATE, ADD_BUTTON_PREDICATE)
        return bool(els)
    except Exception:  # noqa: BLE001
        return False


def _cart_likely_open_soft(driver) -> bool:
    """Soft confirm when a11y tree is sparse but Cart UI is almost certainly open.

    Heavy carts (e.g. 50 lines) often render visually while page_source stays a
    shallow Shop/Cart/Search shell — find_elements then miss Cart chrome.
    On Cart, the header cart icon is gone and My Items Add buttons are gone.
    Avoid page_source here — full dumps can hang WDA on large carts.
    """
    if _cart_screen_visible(driver):
        return True
    if _my_items_add_visible(driver):
        return False
    if _header_cart_icon_present(driver):
        return False
    return True


def _close_reserve_sheet_quick(
    driver, markers: MarkerLog | None = None
) -> bool:
    """Close Reserve a time sheet if it is covering Cart."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label == "Reserve a time" OR name == "Reserve a time")',
        )
        if not els:
            return False
    except Exception:  # noqa: BLE001
        return False
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "close"),
        (AppiumBy.ACCESSIBILITY_ID, "Close"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "close" OR name == "close" OR label == "Close" OR '
            'name == "Close")',
        ),
    ):
        try:
            el, _, _ = find_first(driver, ((by, value),), timeout=1.0)
            el.click()
            if markers is not None:
                markers.mark("reserve_sheet_closed_for_cart", by=by, value=value)
            print("  closed Reserve a time sheet (over Cart)")
            time.sleep(_MED)
            return True
        except TimeoutError:
            continue
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        _tap_center(driver, w * 0.92, 120.0)
        if markers is not None:
            markers.mark("reserve_sheet_closed_for_cart", via="coord")
        print("  closed Reserve a time sheet (coord)")
        time.sleep(_MED)
        return True
    except Exception:  # noqa: BLE001
        return False


def _walmart_pdp_visible(driver) -> bool:
    """True on a product detail page (mis-tap from My Items Add)."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label CONTAINS[c] "Overall pick" OR '
            'label CONTAINS[c] "bought since yesterday" OR '
            'label CONTAINS[c] "About this item" OR '
            'label CONTAINS[c] "Customer reviews" OR '
            'label CONTAINS[c] "Add to registry" OR '
            'label CONTAINS[c] "How do you want your items")',
        )
        return bool(els)
    except Exception:  # noqa: BLE001
        return False


def _dismiss_walmart_pdp_if_shown(
    driver, markers: MarkerLog | None = None
) -> bool:
    if not _walmart_pdp_visible(driver):
        return False
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "Back"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Back" OR name == "Back" OR '
            'label CONTAINS[c] "Back" OR name CONTAINS[c] "back")',
        ),
        (
            AppiumBy.IOS_CLASS_CHAIN,
            '**/XCUIElementTypeNavigationBar/**/XCUIElementTypeButton[1]',
        ),
    ):
        try:
            el, _, _ = find_first(driver, ((by, value),), timeout=1.0)
            el.click()
            if markers is not None:
                markers.mark("walmart_pdp_dismissed", by=by, value=value)
            print("  PDP → Back (dismissed)")
            time.sleep(_MED)
            return True
        except TimeoutError:
            continue
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        _tap_center(driver, w * 0.07, 56.0)
        if markers is not None:
            markers.mark("walmart_pdp_dismissed", via="coord")
        print("  PDP → Back coord (dismissed)")
        time.sleep(_MED)
        return True
    except Exception:  # noqa: BLE001
        return False


def _tap_header_cart_icon(driver) -> tuple[object, str, str]:
    """Prefer the top-right header cart (avoid matching 'Add to cart' / PDP)."""
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        w, h = 390.0, 844.0
    candidates: list[tuple[float, float, object, str]] = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label CONTAINS[c] "cart" OR name CONTAINS[c] "cart" OR '
            'label CONTAINS[c] "Cart" OR name CONTAINS[c] "Cart")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:20]:
        try:
            label = (el.get_attribute("label") or el.get_attribute("name") or "")[
                :80
            ]
            low = label.lower()
            # Skip PDP / listing add CTAs.
            if "add to cart" in low or "add to" in low:
                continue
            r = el.rect or {}
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            ww = float(r.get("width", 0))
            # Header cart sits top-right; reject bottom tab bar.
            if y > h * 0.22 or y < 8:
                continue
            if x + ww / 2.0 < w * 0.55:
                continue
            candidates.append((y, -(x + ww / 2.0), el, label))
        except Exception:  # noqa: BLE001
            continue
    if candidates:
        candidates.sort(key=lambda t: (t[0], t[1]))
        _y, _nx, el, label = candidates[0]
        return el, AppiumBy.IOS_PREDICATE, f"header_cart:{label}"
    el, by, value = find_first(driver, CART_ICON_STRATEGIES, timeout=2.5)
    return el, by, value

CHANGE_LINK_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Change"),
    (
        AppiumBy.IOS_PREDICATE,
        'visible == 1 AND '
        '(label == "Change" OR name == "Change" OR '
        'label CONTAINS[c] "Change" OR name CONTAINS[c] "Change")',
    ),
)

RESERVE_BUTTON_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Reserve"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label == "Reserve" OR name == "Reserve")',
    ),
)

CONTINUE_TO_CHECKOUT_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Continue to checkout"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label == "Continue to checkout" OR name == "Continue to checkout" OR '
        'label CONTAINS[c] "Continue to checkout" OR '
        'name CONTAINS[c] "Continue to checkout")',
    ),
)

CONTINUE_WITHOUT_ITEMS_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Continue without items"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label == "Continue without items" OR name == "Continue without items" OR '
        'label CONTAINS[c] "Continue without items")',
    ),
)

MISSING_ANYTHING_CONTINUE_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Continue"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label == "Continue" OR name == "Continue")',
    ),
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _mark_step(outcome: dict, key: str, *, status: str, **extra) -> None:
    outcome.setdefault("checkout_steps", {})[key] = {"status": status, **extra}


def _tap_center(driver, cx: float, cy: float) -> None:
    driver.execute_script(
        "mobile: tap",
        {"x": int(round(cx)), "y": int(round(cy))},
    )


def _apply_fast_wda_settings(driver) -> None:
    """Cut XCUITest idle/animation waits that stall ~10–20s per tap on Walmart modals."""
    try:
        driver.update_settings(
            {
                "waitForIdleTimeout": 0,
                "animationCoolOffTimeout": 0,
                "waitForQuiescence": False,
                # Deep trees on 50-line carts can hang WDA forever; keep shallow.
                "snapshotMaxDepth": 28,
                # Fail snapshot waits instead of hanging indefinitely on fat carts.
                "customSnapshotTimeout": 12,
            }
        )
        print("  WDA fast settings: waitForIdleTimeout=0, animationCoolOffTimeout=0")
    except Exception as exc:  # noqa: BLE001
        print(f"  WDA fast settings warning: {exc}")


def _scroll_down(driver, *, percent: float = 0.45) -> None:
    try:
        size = driver.get_window_size()
        w, h = int(size["width"]), int(size["height"])
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.12,
                "fromX": int(w * 0.50),
                "fromY": int(h * 0.72),
                "toX": int(w * 0.50),
                "toY": int(h * (0.72 - percent)),
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _add_fingerprint(el, *, gen: int = 0) -> str:
    try:
        label = (el.get_attribute("label") or el.get_attribute("name") or "").strip()
        name = (el.get_attribute("name") or "").strip()
        # Prefer stable product identity over screen coordinates.
        if label.lower().startswith("add "):
            return f"add:{label.lower()[:90]}"
        if "quantity" in label.lower():
            return f"qty:{label.lower()[:90]}"
        r = el.rect or {}
        # Empty-label addToCartButton reuses the same x/y after every scroll for
        # *different* products — include scroll gen so rows stay tappable.
        if name == "stepper.addToCartButton" and (
            not label or label == name or label.lower() == "add"
        ):
            return (
                f"addToCart:{int(r.get('x', 0))}:"
                f"{int(r.get('y', 0))}:g{gen}"
            )
        return f"{label[:40]}:{int(r.get('x', 0))}:{int(r.get('y', 0))}"
    except Exception:  # noqa: BLE001
        return f"el:{id(el)}"


def _dismiss_search_if_shown(driver) -> None:
    try:
        driver.hide_keyboard()
    except Exception:  # noqa: BLE001
        pass
    try:
        el = driver.find_element(AppiumBy.ACCESSIBILITY_ID, "Cancel")
        if el.is_displayed():
            el.click()
            time.sleep(_FAST)
            return
    except Exception:  # noqa: BLE001
        pass


def _ask_sparky_visible(driver) -> bool:
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label CONTAINS[c] "Ask Sparky" OR name CONTAINS[c] "Ask Sparky" OR '
            'label CONTAINS[c] "Ask me anything" OR '
            'label CONTAINS[c] "Trending near you")',
        )
        return bool(els)
    except Exception:  # noqa: BLE001
        return False


def _dismiss_ask_sparky_if_shown(
    driver, markers: MarkerLog | None = None
) -> bool:
    """If Ask Sparky AI screen is up, tap top-left Back to dismiss."""
    if not _ask_sparky_visible(driver):
        return False
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "Back"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Back" OR name == "Back" OR '
            'label CONTAINS[c] "Back" OR name CONTAINS[c] "back")',
        ),
        (
            AppiumBy.IOS_CLASS_CHAIN,
            '**/XCUIElementTypeNavigationBar/**/XCUIElementTypeButton[1]',
        ),
    ):
        try:
            el, _, _ = find_first(driver, ((by, value),), timeout=1.0)
            el.click()
            if markers is not None:
                markers.mark("ask_sparky_dismissed", by=by, value=value)
            print("  Ask Sparky → Back (dismissed)")
            time.sleep(_MED)
            return True
        except TimeoutError:
            continue
    # Top-left chevron fallback.
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        _tap_center(driver, w * 0.07, 56.0)
        if markers is not None:
            markers.mark("ask_sparky_dismissed", via="coord")
        print("  Ask Sparky → Back coord (dismissed)")
        time.sleep(_MED)
        return True
    except Exception:  # noqa: BLE001
        return False


def _checkout_or_payment_visible(driver) -> bool:
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label == "Checkout" OR name == "Checkout" OR '
            'label == "Save Card" OR name == "Save Card" OR '
            'label CONTAINS[c] "Card number" OR '
            'label CONTAINS[c] "Continue to checkout")',
        )
        return bool(els)
    except Exception:  # noqa: BLE001
        return False


def _escape_walmart_checkout_flow(driver, markers: MarkerLog | None = None) -> bool:
    """Leave Checkout / payment / sticky Continue CTA so bottom tabs are usable.

    Cart's sticky "Continue to checkout" sits where tab-bar coord taps land — never
    coord-tap the bottom while that CTA (or Checkout) is on screen.
    """
    escaped = False
    for _ in range(6):
        if not _checkout_or_payment_visible(driver):
            break
        escaped = True
        # Prefer explicit back / close over blind bottom taps.
        for by, value in (
            (AppiumBy.ACCESSIBILITY_ID, "Back"),
            (
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND visible == 1 AND '
                '(label == "Back" OR name == "Back" OR label == "Close" OR '
                'name == "Close")',
            ),
        ):
            try:
                el, _, _ = find_first(driver, ((by, value),), timeout=0.8)
                el.click()
                time.sleep(_FAST)
                break
            except TimeoutError:
                continue
        else:
            try:
                driver.back()
                time.sleep(_FAST)
            except Exception:  # noqa: BLE001
                try:
                    size = driver.get_window_size() or {}
                    w = float(size.get("width") or 390)
                    _tap_center(driver, w * 0.08, 56.0)
                    time.sleep(_FAST)
                except Exception:  # noqa: BLE001
                    break
    if escaped and markers is not None:
        markers.mark("escaped_checkout_flow")
        print("  escaped Checkout / Continue-to-checkout overlay")
    return escaped


def _tap_my_items_tab_coord(driver) -> None:
    """Bottom-tab fallback: Shop | My Items | … (My Items ≈ x=128 on 390-wide)."""
    # Never tap bottom while checkout CTA is up — that presses Continue.
    if _checkout_or_payment_visible(driver):
        _escape_walmart_checkout_flow(driver)
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        w, h = 390.0, 844.0
    if _checkout_or_payment_visible(driver):
        return
    _tap_center(driver, w * 0.33, h * 0.94)


def _tap_my_items_tab(driver, markers: MarkerLog, outcome: dict) -> None:
    print('Step: tap bottom tab "My Items" …')
    _dismiss_search_if_shown(driver)
    _dismiss_ask_sparky_if_shown(driver, markers)
    _escape_walmart_checkout_flow(driver, markers)
    # Sheets left after cart clear often cover the tab bar.
    try:
        for label in ("Got it", "Got It", "Close", "Not Now"):
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                f'type == "XCUIElementTypeButton" AND visible == 1 AND '
                f'(label == "{label}" OR name == "{label}")',
            )
            for el in els[:2]:
                try:
                    el.click()
                    time.sleep(0.15)
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass

    el = None
    by = value = ""
    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    y_min = h * 0.78
    # Prefer bottom tab — header also has a "My Items" control (~y=47) that is NOT the tab.
    try:
        tabs = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(label == "My Items" OR name == "My Items" OR '
            'label CONTAINS[c] "My Items")',
        )
        best = None
        for t in tabs[:12]:
            try:
                r = t.rect or {}
                y = float(r.get("y", 0))
                if y > y_min and (best is None or y > best[0]):
                    best = (y, t)
            except Exception:  # noqa: BLE001
                continue
        if best is not None:
            el = best[1]
            by, value = AppiumBy.IOS_PREDICATE, "My Items(bottom)"
    except Exception:  # noqa: BLE001
        pass

    if el is None:
        try:
            el, by, value = find_first(driver, MY_ITEMS_TAB_STRATEGIES, timeout=2.5)
            # Reject header / nav "My Items" (y too high on screen = small y).
            try:
                ry = float((el.rect or {}).get("y", 0))
                if ry < y_min:
                    el = None
            except Exception:  # noqa: BLE001
                el = None
        except TimeoutError:
            el = None

    if el is not None:
        try:
            el.click()
        except Exception:  # noqa: BLE001
            el = None

    if el is None:
        _tap_my_items_tab_coord(driver)
        by, value = "coordinate", "my_items_tab~0.33x0.94"
        markers.mark("my_items_tab_tapped", by=by, value=value, via="coord")
    else:
        markers.mark("my_items_tab_tapped", by=by, value=value)

    outcome.setdefault("selectors", {})["my_items_tab"] = {"by": by, "value": value}
    _mark_step(outcome, "my_items_tab", status="ok", by=by, value=value)
    print(f"  My Items: {by} = {value!r}")
    time.sleep(_MED)
    _dismiss_search_if_shown(driver)


def _scroll_up(driver, *, percent: float = 0.35) -> None:
    try:
        size = driver.get_window_size()
        w, h = int(size["width"]), int(size["height"])
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.18,
                "fromX": int(w * 0.50),
                "fromY": int(h * 0.40),
                "toX": int(w * 0.50),
                "toY": int(h * (0.40 + percent)),
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _tap_fresh_produce(driver, markers: MarkerLog, outcome: dict) -> None:
    print('Step: tap "Fresh Produce" filter …')
    _dismiss_search_if_shown(driver)
    try:
        el, by, value = find_first(driver, FRESH_PRODUCE_STRATEGIES, timeout=2.0)
    except TimeoutError:
        _dismiss_search_if_shown(driver)
        _tap_my_items_tab(driver, markers, outcome)
        el, by, value = find_first(driver, FRESH_PRODUCE_STRATEGIES, timeout=2.0)
    el.click()
    outcome.setdefault("selectors", {})["fresh_produce"] = {"by": by, "value": value}
    markers.mark("fresh_produce_tapped", by=by, value=value)
    _mark_step(outcome, "fresh_produce", status="ok", by=by, value=value)
    print(f"  Fresh Produce: {by} = {value!r}")
    time.sleep(_MED)
    # Only nudge if no Add is already in the tappable band.
    if not _collect_add_buttons(driver, set()):
        _scroll_down(driver, percent=0.14)
        time.sleep(_FAST)


def _collect_add_buttons(
    driver, used: set[str], *, gen: int = 0
) -> list[tuple]:
    batch: list[tuple] = []
    try:
        els = driver.find_elements(AppiumBy.IOS_PREDICATE, ADD_BUTTON_PREDICATE)
    except Exception:  # noqa: BLE001
        return batch
    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
        w_screen = float(size.get("width") or 390)
    except Exception:  # noqa: BLE001
        h = 844.0
        w_screen = 390.0
    for el in els[:40]:
        try:
            label = (el.get_attribute("label") or "").strip()
            name = (el.get_attribute("name") or "").strip()
            if "favorites" in label.lower():
                continue
            # After first add, the same control becomes a qty chip — skip those.
            if "current quantity" in label.lower() or "quantity is" in label.lower():
                continue
            if label and not label.lower().startswith("add "):
                # Empty label on addToCartButton is normal; odd labels only OK
                # on known stepper names.
                if name not in ("stepper.mainButton", "stepper.addToCartButton"):
                    continue
                if name == "stepper.mainButton" and "add" not in label.lower():
                    continue
            fp = _add_fingerprint(el, gen=gen)
            if fp in used:
                continue
            r = el.rect or {}
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            hh = float(r.get("height", 0))
            if w < 40 or hh < 20:
                continue
            # Below sticky chips (~210) and above bottom tab bar.
            # Slightly under chips is OK — we tap by coordinates.
            if y < 210 or y > h * 0.84:
                continue
            cx = x + w / 2.0
            cy = y + hh / 2.0
            if cx < 8 or cx > w_screen - 8:
                continue
            batch.append((el, fp, y, cx, cy, label or name))
        except Exception:  # noqa: BLE001
            continue
    batch.sort(key=lambda t: (round(t[2] / 12.0), t[2], t[3]))
    return batch


def _add_n_via_add_buttons(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    target: int = TARGET_ITEMS,
    max_scrolls: int = 8,
) -> list[dict]:
    print(f'Step: tap blue "+ Add" on {target} items …')
    used: set[str] = set()
    added: list[dict] = []
    scrolls = 0
    markers.mark("walmart_add_start", target=target)

    while len(added) < target and scrolls <= max_scrolls:
        batch = _collect_add_buttons(driver, used, gen=scrolls)
        if not batch:
            _scroll_down(driver, percent=0.26)
            scrolls += 1
            markers.mark("my_items_scrolled", index=scrolls)
            time.sleep(_FAST)
            continue

        added_before = len(added)
        for el, fp, y, cx, cy, label in batch:
            if len(added) >= target:
                break
            if "current quantity" in label.lower():
                used.add(fp)
                continue
            print(
                f"  + Add candidate #{len(added) + 1}/{target} "
                f"@({cx:.0f},{cy:.0f}) label={label[:48]!r}"
            )
            # Coordinate tap is faster/more reliable than element.click on Walmart.
            try:
                _tap_center(driver, cx, cy)
            except Exception:  # noqa: BLE001
                try:
                    el.click()
                except Exception as exc:  # noqa: BLE001
                    used.add(fp)
                    print(f"  + Add tap failed: {exc}")
                    continue
            used.add(fp)
            time.sleep(_FAST)
            entry = {
                "index": len(added) + 1,
                "source": "my_items_fresh_produce_add",
                "add_path": "stepper_main_button",
                "fingerprint": fp,
                "label": label[:120],
                "y": y,
                "x": cx,
                "confirmed": True,
            }
            added.append(entry)
            markers.mark(
                "item_added_to_cart",
                index=entry["index"],
                fingerprint=fp,
                y=y,
                label=label[:80],
            )
            print(f"  added #{entry['index']}/{target} via Add @ y={y:.0f}")

        if len(added) >= target:
            break
        if len(added) == added_before:
            _scroll_down(driver, percent=0.26)
            scrolls += 1
            markers.mark("my_items_scrolled", index=scrolls, reason="no_new_add")
            time.sleep(_FAST)
        else:
            _scroll_down(driver, percent=0.28)
            scrolls += 1
            markers.mark("my_items_scrolled", index=scrolls, reason="next_row")
            time.sleep(_FAST)

    outcome["items_added"] = list(added)
    outcome["items_added_count"] = len(added)
    markers.mark("walmart_add_done", added=len(added), target=target, scrolls=scrolls)
    if len(added) < target:
        raise AssertionError(
            f"Only added {len(added)}/{target} via Walmart + Add (scrolls={scrolls})"
        )
    return added


def _cart_icon_looks_empty(driver) -> bool:
    """True if cart badge/icon already indicates an empty cart (skip clear)."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(label CONTAINS[c] "cart" OR name CONTAINS[c] "cart")',
        )
    except Exception:  # noqa: BLE001
        return False
    for el in els[:6]:
        try:
            r = el.rect or {}
            if float(r.get("y", 999)) > 120:
                continue
            blob = (
                f"{el.get_attribute('label') or ''} "
                f"{el.get_attribute('name') or ''}"
            ).lower()
            if "empty" in blob or "0.00" in blob or blob.strip().endswith(" 0"):
                return True
            # "Cart, 0 items" style.
            if "cart" in blob and ("(0" in blob or " 0 " in f" {blob} "):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _open_cart(driver, markers: MarkerLog, outcome: dict) -> None:
    print("Step: open Cart screen …")
    _dismiss_ask_sparky_if_shown(driver, markers)
    _dismiss_walmart_pdp_if_shown(driver, markers)
    _close_reserve_sheet_quick(driver, markers)
    _dismiss_subscription_sheet(driver)
    by = value = ""
    for attempt in range(4):
        _close_reserve_sheet_quick(driver, markers)
        if _wait_cart_screen(driver, timeout=1.2):
            break
        _dismiss_walmart_pdp_if_shown(driver, markers)
        try:
            el, by, value = _tap_header_cart_icon(driver)
            el.click()
        except Exception as exc:  # noqa: BLE001
            print(f"  cart icon tap failed: {exc}")
            # Bottom-tab Cart (Shop/Cart chrome) when header cart is missing.
            try:
                el, by, value = find_first(
                    driver,
                    (
                        (
                            AppiumBy.IOS_PREDICATE,
                            'type == "XCUIElementTypeButton" AND '
                            '(label == "Cart" OR name == "Cart")',
                        ),
                        (AppiumBy.ACCESSIBILITY_ID, "Cart"),
                    ),
                    timeout=2.0,
                )
                el.click()
                by, value = "bottom_tab", "Cart"
            except Exception:  # noqa: BLE001
                try:
                    size = driver.get_window_size() or {}
                    w = float(size.get("width") or 390)
                    _tap_center(driver, w * 0.90, 56.0)
                    by, value = "coord", "header_cart"
                except Exception:  # noqa: BLE001
                    pass
        # Large baskets need longer for Cart a11y nodes to appear.
        if _wait_cart_screen(driver, timeout=6.0):
            break
        _dismiss_subscription_sheet(driver)
        # Ineligible-item carts often auto-open Reserve a time over Cart.
        if _close_reserve_sheet_quick(driver, markers):
            if _wait_cart_screen(driver, timeout=3.0):
                break
        if _cart_likely_open_soft(driver):
            print(f"  cart soft-confirmed (sparse a11y; attempt {attempt + 1})")
            break
        if _dismiss_walmart_pdp_if_shown(driver, markers):
            continue
        print(f"  cart screen not confirmed (attempt {attempt + 1}/4)")
    else:
        if not _cart_screen_visible(driver) and not _cart_likely_open_soft(driver):
            try:
                size = driver.get_window_size() or {}
                w = float(size.get("width") or 390)
                _tap_center(driver, w * 0.90, 56.0)
                by, value = "coord", "header_cart"
                _wait_cart_screen(driver, timeout=5.0)
                _close_reserve_sheet_quick(driver, markers)
            except Exception:  # noqa: BLE001
                pass
    if not by:
        by, value = "already_open", "cart_chrome"
    confirmed = _cart_screen_visible(driver) or _cart_likely_open_soft(driver)
    outcome.setdefault("selectors", {})["cart"] = {"by": by, "value": value}
    outcome["cart_page_opened"] = True
    markers.mark("cart_opened", by=by, value=value, confirmed=confirmed)
    _mark_step(outcome, "cart_opened", status="ok", by=by, value=value)
    print(f"  Cart: {by} = {value!r} (visible={confirmed})")
    time.sleep(_FAST)
    _dismiss_subscription_sheet(driver)
    if not confirmed:
        raise TimeoutError("Cart screen not confirmed after open attempts")


def _collapse_cart_fulfillment_if_expanded(driver) -> bool:
    """Collapse expanded Pickup/delivery block so cart line Remove controls load."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND label CONTAINS[c] "Pickup and delivery" AND '
            'label CONTAINS[c] "Expanded"',
        )
    except Exception:  # noqa: BLE001
        return False
    if not els:
        return False
    try:
        els[0].click()
        time.sleep(0.35)
        print("  collapsed Pickup and delivery options (expose Remove)")
        return True
    except Exception:  # noqa: BLE001
        return False


def _expand_cart_line_details(driver) -> bool:
    """Expand collapsed cart card ('See details') so per-line Remove is exposed."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND ('
            'name == "CartCardExpandCollapseCellContainer.expandCollapse" OR '
            'label == "See details" OR label CONTAINS[c] "See details")',
        )
    except Exception:  # noqa: BLE001
        return False
    if not els:
        return False
    try:
        els[0].click()
        time.sleep(0.45)
        print("  expanded cart line details (See details)")
        return True
    except Exception:  # noqa: BLE001
        return False


def _find_cart_remove_buttons(driver) -> list:
    """Collect on-screen Cart line Remove controls (not 'Removed:' toasts)."""
    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    try:
        buttons = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND ('
            'name CONTAINS[c] "CartItemTileActionsView.remove" OR '
            'label BEGINSWITH[c] "Remove,")',
        )
    except Exception:  # noqa: BLE001
        return []
    out = []
    for el in buttons[:16]:
        try:
            lab = (el.get_attribute("label") or "").lower()
            if lab.startswith("removed"):
                continue
            r = el.rect or {}
            y = float(r.get("y", 0))
            ww = float(r.get("width", 0))
            if y < 130 or y > h - 90 or ww < 12:
                continue
            out.append(el)
        except Exception:  # noqa: BLE001
            continue
    return out


def _clear_cart_items(driver, markers: MarkerLog, outcome: dict) -> None:
    """Remove existing cart lines so Fresh Produce shows blue + Add again."""
    print("Step: clear existing cart items (if any) …")
    if _cart_icon_looks_empty(driver):
        outcome["cart_items_cleared"] = 0
        markers.mark("cart_cleared", removed=0, skipped=True, reason="already_empty")
        print("  cart already empty — skip clear")
        return

    try:
        # Deep enough to expose Remove under cart cells; too deep hangs WDA.
        driver.update_settings({"snapshotMaxDepth": 36})
    except Exception:  # noqa: BLE001
        pass

    # Prefer the hardened cart open (header / bottom tab / soft confirm).
    try:
        _open_cart(driver, markers, outcome)
    except Exception as exc:  # noqa: BLE001
        print(f"  cart open for clear failed ({exc}); trying icon tap")
        try:
            find_first(driver, CART_ICON_STRATEGIES, timeout=2.5)[0].click()
            time.sleep(0.5)
        except Exception:  # noqa: BLE001
            print("  cart icon not found — skip clear")
            try:
                driver.update_settings({"snapshotMaxDepth": 28})
            except Exception:  # noqa: BLE001
                pass
            return

    # Ineligible carts may auto-open Reserve over Cart — close so Remove is visible.
    _close_reserve_sheet_quick(driver, markers)
    _collapse_cart_fulfillment_if_expanded(driver)
    # Collapsed cart card only shows thumbnails — expand for Remove controls.
    _expand_cart_line_details(driver)

    # Empty cart title / CTA.
    try:
        empty = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'label CONTAINS[c] "cart is empty" OR name CONTAINS[c] "cart is empty" OR '
            'label == "Cart (0 items)" OR name == "Cart (0 items)"',
        )
        if any(True for _ in empty[:2]):
            outcome["cart_items_cleared"] = 0
            markers.mark("cart_cleared", removed=0, skipped=True, reason="empty_cart_page")
            print("  cart page empty — skip remove")
            _return_to_my_items_fast(driver, markers)
            try:
                driver.update_settings({"snapshotMaxDepth": 28})
            except Exception:  # noqa: BLE001
                pass
            return
    except Exception:  # noqa: BLE001
        pass

    # Large carts: collapsed card / fulfillment chrome hide Remove until expanded.
    deadline = time.time() + 12.0
    while time.time() < deadline and not _find_cart_remove_buttons(driver):
        _collapse_cart_fulfillment_if_expanded(driver)
        _expand_cart_line_details(driver)
        _scroll_down(driver, percent=0.35)
        time.sleep(0.35)

    removed = 0
    stagnant = 0
    clear_deadline = time.time() + 240.0  # hard cap — avoid WDA hangs on fat carts
    for _ in range(120):
        if time.time() > clear_deadline:
            print("  clear time budget hit — stopping")
            break
        if _cart_icon_looks_empty(driver):
            print("  cart badge empty — stop clear")
            break
        _close_reserve_sheet_quick(driver, markers)
        # "Unable to remove" sheets block further Remove taps.
        try:
            for label in ("Got it", "Got It", "OK", "Close"):
                els = driver.find_elements(
                    AppiumBy.IOS_PREDICATE,
                    f'type == "XCUIElementTypeButton" AND visible == 1 AND '
                    f'(label == "{label}" OR name == "{label}")',
                )
                for el in els[:2]:
                    try:
                        el.click()
                        time.sleep(0.15)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
        buttons = _find_cart_remove_buttons(driver)
        # Tap every on-screen Remove in one pass (avoid re-query per item).
        tapped_this_pass = 0
        for el in buttons[:6]:
            try:
                r = el.rect or {}
                y = float(r.get("y", 0))
                x = float(r.get("x", 0))
                ww = float(r.get("width", 0))
                hh = float(r.get("height", 0))
                _tap_center(driver, x + ww / 2.0, y + hh / 2.0)
                removed += 1
                tapped_this_pass += 1
                time.sleep(0.12)
            except Exception:  # noqa: BLE001
                continue
        if tapped_this_pass == 0:
            stagnant += 1
            # Fat leftover carts need many scrolls; stop only when empty-looking.
            if stagnant >= 10:
                break
            _expand_cart_line_details(driver)
            _scroll_down(driver, percent=0.40)
            time.sleep(0.2)
            continue
        stagnant = 0
        if removed and removed % 10 == 0:
            print(f"  … cleared {removed} so far")
        time.sleep(0.08)

    outcome["cart_items_cleared"] = removed
    markers.mark("cart_cleared", removed=removed)
    print(f"  cleared {removed} cart item(s)")
    if removed == 0 and not _cart_icon_looks_empty(driver):
        print(
            "  WARNING: cart still looks non-empty after clear — "
            "+ Add buttons may be missing on My Items"
        )
    # Dismiss leftover error sheets before leaving cart.
    try:
        for label in ("Got it", "Got It", "OK", "Close"):
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                f'type == "XCUIElementTypeButton" AND visible == 1 AND '
                f'(label == "{label}" OR name == "{label}")',
            )
            for el in els[:2]:
                try:
                    el.click()
                    time.sleep(0.12)
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass
    _return_to_my_items_fast(driver, markers)
    try:
        driver.update_settings({"snapshotMaxDepth": 28})
    except Exception:  # noqa: BLE001
        pass


def _return_to_my_items_fast(driver, markers: MarkerLog) -> None:
    _dismiss_search_if_shown(driver)
    _dismiss_ask_sparky_if_shown(driver, markers)
    _escape_walmart_checkout_flow(driver, markers)
    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    y_min = h * 0.78
    try:
        tabs = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(label == "My Items" OR name == "My Items" OR '
            'label CONTAINS[c] "My Items")',
        )
        best = None
        for t in tabs[:10]:
            r = t.rect or {}
            y = float(r.get("y", 0))
            if y > y_min and (best is None or y > best[0]):
                best = (y, t)
        if best is not None:
            best[1].click()
            time.sleep(_MED)
            markers.mark("returned_to_my_items_after_clear", via="bottom_tab")
            _dismiss_search_if_shown(driver)
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        driver.back()
        time.sleep(_FAST)
    except Exception:  # noqa: BLE001
        pass
    try:
        _tap_my_items_tab_coord(driver)
        time.sleep(_MED)
        markers.mark("returned_to_my_items_after_clear", via="coord")
    except Exception:  # noqa: BLE001
        pass
    _dismiss_search_if_shown(driver)


def _dismiss_edit_address_if_shown(driver) -> bool:
    """Close 'Edit delivery details' sheet if we accidentally opened it."""
    try:
        titles = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND (label CONTAINS[c] "Edit delivery details" OR '
            'name CONTAINS[c] "Edit delivery details")',
        )
    except Exception:  # noqa: BLE001
        titles = []
    if not titles:
        return False
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "close"),
        (AppiumBy.ACCESSIBILITY_ID, "Close"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(label == "close" OR name == "close" OR label == "Close" OR name == "Close")',
        ),
    ):
        try:
            el = driver.find_element(by, value)
            el.click()
            time.sleep(_FAST)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _dismiss_subscription_sheet(driver) -> bool:
    """Close Walmart 'Set up subscription' sheet if it appears after Add."""
    try:
        titles = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND '
            '(label CONTAINS[c] "Set up subscription" OR '
            'name CONTAINS[c] "Set up subscription")',
        )
        if not titles:
            return False
    except Exception:  # noqa: BLE001
        return False
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "close"),
        (AppiumBy.ACCESSIBILITY_ID, "Close"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "close" OR name == "close" OR label == "Close" OR name == "Close")',
        ),
    ):
        try:
            el = driver.find_element(by, value)
            el.click()
            time.sleep(0.25)
            print("  dismissed Set up subscription sheet")
            return True
        except Exception:  # noqa: BLE001
            continue
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        # X is top-right of sheet.
        _tap_center(driver, w * 0.92, 120.0)
        time.sleep(0.25)
        print("  dismissed Set up subscription via coord")
        return True
    except Exception:  # noqa: BLE001
        return False


def _find_cart_change_delivery_el(driver):
    """Locate Change delivery control (often visible=false under sticky footer)."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND ('
            'name == "FulfillmentSummaryView.actionButton" OR '
            'label == "Change delivery method or slot" OR '
            'label CONTAINS[c] "Change delivery")',
        )
    except Exception:  # noqa: BLE001
        els = []
    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    best = None
    best_y = 1e9
    for el in els[:8]:
        try:
            r = el.rect or {}
            y = float(r.get("y", 0))
            # Prefer store-delivery Change in the mid band (above sticky CTA).
            if y < 120 or y > h - 40:
                continue
            if y < best_y:
                best, best_y = el, y
        except Exception:  # noqa: BLE001
            continue
    return best


def _tap_cart_change_delivery(driver, markers: MarkerLog, outcome: dict) -> None:
    print('Step: on Cart tap "Change" (delivery method or slot) …')
    _dismiss_subscription_sheet(driver)
    _dismiss_walmart_pdp_if_shown(driver, markers)
    if not _cart_screen_visible(driver):
        print("  not on Cart — re-opening …")
        _open_cart(driver, markers, outcome)
    try:
        for label in ("Got it", "Got It", "Close", "close", "Not Now"):
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                f'type == "XCUIElementTypeButton" AND visible == 1 AND '
                f'(label == "{label}" OR name == "{label}")',
            )
            for el in els[:2]:
                try:
                    el.click()
                    time.sleep(0.15)
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass
    # Fat carts bury Change under shipping + sticky footer — collapse + scroll.
    _collapse_cart_fulfillment_if_expanded(driver)
    el = None
    by = AppiumBy.IOS_PREDICATE
    value = "FulfillmentSummaryView.actionButton"
    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    for attempt in range(8):
        el = _find_cart_change_delivery_el(driver)
        if el is not None:
            r = el.rect or {}
            y = float(r.get("y", 0))
            if 140 <= y <= h * 0.72:
                break
            # Too low (under sticky CTA) — nudge content up.
            _scroll_down(driver, percent=0.28)
            time.sleep(0.25)
            continue
        _scroll_down(driver, percent=0.32)
        time.sleep(0.25)
    else:
        el = _find_cart_change_delivery_el(driver)
    if el is None:
        strategies = (
            (
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND '
                '(name == "FulfillmentSummaryView.actionButton" OR '
                'label == "Change delivery method or slot" OR '
                'label CONTAINS[c] "Change delivery")',
            ),
            (
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND '
                '(label == "Change" OR name == "Change")',
            ),
        )
        try:
            el, by, value = find_first(driver, strategies, timeout=4.0)
        except TimeoutError:
            _dismiss_subscription_sheet(driver)
            try:
                _open_cart(driver, markers, outcome)
            except Exception:  # noqa: BLE001
                pass
            el, by, value = find_first(driver, strategies, timeout=5.0)
    r = el.rect or {}
    y = float(r.get("y", 0))
    try:
        _tap_center(
            driver,
            float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0,
            float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0,
        )
    except Exception:  # noqa: BLE001
        el.click()
    markers.mark("cart_change_delivery", by=by, value=value, y=y)
    _mark_step(outcome, "cart_change_delivery", status="ok", by=by, value=value, y=y)
    print(f"  Change delivery: {by}={value!r} y={y:.0f}")
    time.sleep(_MED)


def _tap_reserve_change_address(driver, markers: MarkerLog, outcome: dict) -> None:
    print('Step: on Reserve a time tap address "Change" …')
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(label == "Change" OR name == "Change")',
        )
    except Exception:  # noqa: BLE001
        els = []
    candidates: list[tuple[float, object]] = []
    for el in els[:8]:
        try:
            r = el.rect or {}
            y = float(r.get("y", 0))
            if 150 <= y <= 280:
                candidates.append((y, el))
        except Exception:  # noqa: BLE001
            continue
    if not candidates:
        el, by, value = find_first(driver, CHANGE_LINK_STRATEGIES, timeout=2.5)
        r = el.rect or {}
        y = float(r.get("y", 0))
    else:
        candidates.sort(key=lambda t: t[0])
        y, el = candidates[0]
        by, value = AppiumBy.IOS_PREDICATE, "Change(address)"
        r = el.rect or {}
    try:
        _tap_center(
            driver,
            float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0,
            float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0,
        )
    except Exception:  # noqa: BLE001
        el.click()
    markers.mark("reserve_change_address", by=by, value=value, y=y)
    _mark_step(outcome, "reserve_change_address", status="ok", by=by, value=value, y=y)
    print(f"  Change address: {by}={value!r} y={y:.0f}")
    # Sheet animation is short — do not sleep long here; _select_second_address waits
    # for the Addresses title instead.


def _wait_addresses_sheet(driver, *, timeout: float = 2.5) -> bool:
    """Return True once the Addresses modal is on screen (cheap probes only)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            hits = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'visible == 1 AND '
                '(label == "Addresses" OR name == "Addresses" OR '
                'label == "Saved addresses" OR name == "Saved addresses")',
            )
            if hits:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.12)
    return False


def _select_second_address(driver, markers: MarkerLog, outcome: dict) -> None:
    """Pick the 2nd saved address on the Addresses sheet (fast path).

    Diagnosis:
      - Cell / CONTAINS tree scans: 30–50s on this modal
      - Even a cheap find_elements('Addresses') before tap left WDA stalled ~20s
        on the next command (mobile:tap)
    Fix: no XCUI queries on this sheet — short settle, then coordinate tap
    on the 2nd saved-address row (Bellevue under selected Seattle).
    """
    print("Step: on Addresses, select 2nd saved address …")
    t0 = time.monotonic()
    # Sheet animation only — do NOT query the Addresses tree (WDA stall).
    time.sleep(0.35)
    markers.mark(
        "addresses_sheet",
        ready=True,
        via="settle_only",
        wait_ms=int((time.monotonic() - t0) * 1000),
    )

    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        w, h = 390.0, 844.0

    # Left/center of 2nd card (avoid Edit on the right). y≈409 worked in prior run.
    cx, cy = w * 0.38, min(412.0, h * 0.49)
    _tap_center(driver, cx, cy)
    markers.mark(
        "address_second_selected",
        via="coord_second_row",
        x=cx,
        y=cy,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
    )
    _mark_step(
        outcome,
        "address_second",
        status="ok",
        via="coord_second_row",
        x=cx,
        y=cy,
    )
    print(
        f"  2nd address coord @ ({cx:.0f},{cy:.0f}) "
        f"({time.monotonic() - t0:.1f}s)"
    )
    time.sleep(_FAST)


def _swipe_date_row_left(driver, *, row_y: float | None = None) -> None:
    """Reveal later days in the horizontal date chip strip."""
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        w, h = 390.0, 844.0
    y = float(row_y) if row_y is not None else h * 0.40
    try:
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.12,
                "fromX": int(w * 0.88),
                "fromY": int(y),
                "toX": int(w * 0.12),
                "toY": int(y),
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _looks_like_day_chip_label(label: str) -> bool:
    low = (label or "").lower()
    if not low or "full" == low.strip():
        return False
    days = ("today", "mon", "tue", "wed", "thu", "fri", "sat", "sun")
    if any(d in low for d in days):
        return True
    # e.g. "10/5", "10/6"
    if "/" in low and any(ch.isdigit() for ch in low):
        return True
    return False


def _is_unavailable_day_label(label: str) -> bool:
    low = (label or "").lower()
    return "full" in low or "unavailable" in low or "sold out" in low


def _element_is_selected(el) -> bool:
    try:
        val = (el.get_attribute("value") or "").strip().lower()
        if val in {"1", "selected", "true"} or "selected" in val:
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        selected = el.get_attribute("selected")
        if selected is True or str(selected).lower() in {"1", "true", "selected"}:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _toggle_pick_last_or_second(
    items: list,
    *,
    selected_flags: list[bool],
    state_key: str | None = None,
) -> tuple[int, str]:
    """If last is selected → pick 2nd-last; else pick last.

    When no selected flag is set, alternate using a small state file so
    consecutive runs still flip last ↔ second-last.

    Returns (index, pick_name).
    """
    if not items:
        raise ValueError("no items to toggle")
    if len(items) == 1:
        return 0, "last"
    last_i = len(items) - 1
    second_i = len(items) - 2
    last_selected = (
        bool(selected_flags[last_i]) if last_i < len(selected_flags) else False
    )
    any_selected = any(selected_flags) if selected_flags else False
    if last_selected:
        pick = "second_last"
        idx = second_i
    elif any_selected:
        # Second-last (or another) selected → move to last.
        pick = "last"
        idx = last_i
    else:
        # A11y often omits selected — alternate via state file.
        prev = _load_toggle_state().get(state_key or "_", "last")
        if prev == "last":
            pick, idx = "second_last", second_i
        else:
            pick, idx = "last", last_i
        _save_toggle_state(state_key or "_", pick)
        return idx, pick
    if state_key:
        _save_toggle_state(state_key, pick)
    return idx, pick


def _toggle_state_path():
    from pathlib import Path

    return Path("artifacts") / ".walmart_day_slot_toggle.json"


def _load_toggle_state() -> dict:
    try:
        p = _toggle_state_path()
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_toggle_state(key: str, pick: str) -> None:
    try:
        p = _toggle_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data = _load_toggle_state()
        data[key] = pick
        p.write_text(json.dumps(data), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _collect_day_chips(
    driver,
) -> list[tuple[float, float, float, float, str, object, bool]]:
    """Return (y, x, cx, cy, label, el, selected) for date chips, left→right."""
    predicates = (
        'type == "XCUIElementTypeButton" AND '
        'label CONTAINS[c] "slots available"',
        'type == "XCUIElementTypeButton" AND '
        '(label CONTAINS[c] "Today" OR label CONTAINS[c] "Mon," OR '
        'label CONTAINS[c] "Tue," OR label CONTAINS[c] "Wed," OR '
        'label CONTAINS[c] "Thu," OR label CONTAINS[c] "Fri," OR '
        'label CONTAINS[c] "Sat," OR label CONTAINS[c] "Sun,")',
    )
    seen: set[str] = set()
    rows: list[tuple[float, float, float, float, str, object, bool]] = []
    for pred in predicates:
        try:
            els = driver.find_elements(AppiumBy.IOS_PREDICATE, pred)
        except Exception:  # noqa: BLE001
            els = []
        for el in els[:16]:
            try:
                label = (el.get_attribute("label") or "").strip()
                if not _looks_like_day_chip_label(label):
                    continue
                r = el.rect or {}
                x = float(r.get("x", 0))
                y = float(r.get("y", 0))
                w = float(r.get("width", 0))
                hh = float(r.get("height", 0))
                if w < 28 or hh < 20 or y < 180 or y > 560:
                    continue
                key = f"{round(x / 6)}:{round(y / 8)}:{label[:24].lower()}"
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    (
                        y,
                        x,
                        x + w / 2.0,
                        y + hh / 2.0,
                        label,
                        el,
                        _element_is_selected(el),
                    )
                )
            except Exception:  # noqa: BLE001
                continue
        if rows:
            break
    rows.sort(key=lambda t: (round(t[0] / 10.0), t[1]))
    return rows


def _looks_like_time_slot_label(label: str) -> bool:
    low = (label or "").lower().replace("–", "-").replace("—", "-")
    if any(
        b in low
        for b in (
            "camera",
            "claim",
            "walmart+",
            "check out",
            "checkout",
            "today",
            "slots available",
            "oct ",
            "nov ",
            "dec ",
            "jan ",
        )
    ):
        return False
    if re.search(r"\d{1,2}\s*(am|pm)\s*(-|to)\s*\d{1,2}\s*(am|pm)", low):
        return True
    return False


def _collect_time_slots(driver) -> list[tuple[float, float, str, bool]]:
    """Return (y, cx, label, selected) for time-slot rows, top→bottom."""
    predicates = (
        'label CONTAINS[c] " to " AND '
        '(label CONTAINS[c] "am" OR label CONTAINS[c] "pm")',
        'type == "XCUIElementTypeStaticText" AND '
        '(label CONTAINS[c] "am" OR label CONTAINS[c] "pm" OR '
        'name CONTAINS[c] "am" OR name CONTAINS[c] "pm")',
    )
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        w, h = 390.0, 844.0
    seen_y: list[float] = []
    slots: list[tuple[float, float, str, bool]] = []
    for pred in predicates:
        try:
            els = driver.find_elements(AppiumBy.IOS_PREDICATE, pred)
        except Exception:  # noqa: BLE001
            els = []
        for el in els[:30]:
            try:
                label = (
                    el.get_attribute("label")
                    or el.get_attribute("value")
                    or el.get_attribute("name")
                    or ""
                ).strip()
                if not _looks_like_time_slot_label(label):
                    continue
                r = el.rect or {}
                y = float(r.get("y", 0))
                x = float(r.get("x", 0))
                ww = float(r.get("width", 0))
                hh = float(r.get("height", 0))
                if hh < 8 or y < h * 0.28 or y > h * 0.90:
                    continue
                if any(abs(y - py) < 14 for py in seen_y):
                    continue
                seen_y.append(y)
                cx = x + ww / 2.0 if ww > 40 else w * 0.42
                slots.append((y, cx, label[:48], _element_is_selected(el)))
            except Exception:  # noqa: BLE001
                continue
        if len(slots) >= 2:
            break
    slots.sort(key=lambda t: t[0])
    return slots


def _wait_for_time_slots(
    driver, *, timeout: float = 1.4
) -> list[tuple[float, float, str, bool]]:
    end = time.monotonic() + timeout
    best: list[tuple[float, float, str, bool]] = []
    while time.monotonic() < end:
        slots = _collect_time_slots(driver)
        if len(slots) > len(best):
            best = slots
        if len(best) >= 2:
            return best
        time.sleep(0.15)
    return best


def _change_day_and_slot_then_reserve(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    print(
        "Step: toggle delivery day (last ↔ 2nd-last) + "
        "time slot (last ↔ 2nd-last), then Reserve …"
    )
    time.sleep(0.18)

    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        w, h = 390.0, 844.0

    date_tapped = False
    row_y = h * 0.40
    for _ in range(2):
        _swipe_date_row_left(driver, row_y=row_y)
        time.sleep(0.05)

    day_rows = _collect_day_chips(driver)
    available = [r for r in day_rows if not _is_unavailable_day_label(r[4])]
    if not available:
        available = list(day_rows)

    if len(available) >= 1:
        idx, pick = _toggle_pick_last_or_second(
            available,
            selected_flags=[r[6] for r in available],
            state_key="day",
        )
        _y, _x, cx, cy, label, _el, was_sel = available[idx]
        _tap_center(driver, cx, cy)
        date_tapped = True
        markers.mark(
            "delivery_day_changed",
            label=label,
            pick=pick,
            was_selected=was_sel,
            x=cx,
            y=cy,
        )
        _mark_step(
            outcome,
            "delivery_day",
            status="ok",
            label=label,
            pick=pick,
            was_selected=was_sel,
        )
        print(
            f"  delivery day ({pick}) → {label!r} "
            f"(last_selected={available[-1][6]})"
        )
    else:
        # Coord fallback: alternate last ↔ 2nd-last via state.
        prev = _load_toggle_state().get("day", "last")
        if prev == "last":
            day_cx, day_cy, pick = w * 0.64, h * 0.40, "second_last"
        else:
            day_cx, day_cy, pick = w * 0.86, h * 0.40, "last"
        _save_toggle_state("day", pick)
        _tap_center(driver, day_cx, day_cy)
        date_tapped = True
        markers.mark(
            "delivery_day_changed",
            label=f"coord_{pick}_day",
            pick=pick,
            x=day_cx,
            y=day_cy,
        )
        _mark_step(
            outcome,
            "delivery_day",
            status="ok",
            label=f"coord_{pick}_day",
            pick=pick,
        )
        print(f"  delivery day ({pick}) → coord @ ({day_cx:.0f},{day_cy:.0f})")

    slot_tapped = False
    time.sleep(_MED)
    for _ in range(2):
        _scroll_down(driver, percent=0.40)
        time.sleep(0.04)

    slots = _collect_time_slots(driver)
    # Prefer slots that won't leave us stuck ("N items ineligible").
    eligible = [s for s in slots if "ineligible" not in (s[2] or "").lower()]
    slot_pool = eligible if eligible else slots
    if len(slot_pool) >= 1:
        idx, pick = _toggle_pick_last_or_second(
            slot_pool,
            selected_flags=[s[3] for s in slot_pool],
            state_key="slot",
        )
        y, cx, label, was_sel = slot_pool[idx]
        _tap_center(driver, cx, y + 6.0)
        slot_tapped = True
        markers.mark(
            "delivery_slot_changed",
            label=label,
            pick=pick,
            was_selected=was_sel,
            slot_count=len(slot_pool),
            eligible_only=bool(eligible),
            x=cx,
            y=y,
        )
        _mark_step(
            outcome,
            "delivery_slot",
            status="ok",
            label=label,
            pick=pick,
            was_selected=was_sel,
            slot_count=len(slot_pool),
        )
        print(
            f"  delivery slot ({pick}) → {label!r} "
            f"(of {len(slot_pool)}{' eligible' if eligible else ''}; "
            f"last_selected={slot_pool[-1][3]})"
        )
        time.sleep(_FAST)
    else:
        prev = _load_toggle_state().get("slot", "last")
        if prev == "last":
            slot_cx, slot_cy, pick = w * 0.42, h * 0.62, "second_last"
        else:
            slot_cx, slot_cy, pick = w * 0.42, h * 0.74, "last"
        _save_toggle_state("slot", pick)
        _tap_center(driver, slot_cx, slot_cy)
        slot_tapped = True
        markers.mark(
            "delivery_slot_changed",
            label=f"coord_{pick}_slot",
            pick=pick,
            x=slot_cx,
            y=slot_cy,
        )
        _mark_step(
            outcome,
            "delivery_slot",
            status="ok",
            label=f"coord_{pick}_slot",
            pick=pick,
        )
        print(f"  delivery slot ({pick}) → coord @ ({slot_cx:.0f},{slot_cy:.0f})")
        time.sleep(_FAST)

    reserved = False
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND name == "Reserve"',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:3]:
        try:
            r = el.rect or {}
            y = float(r.get("y", 0))
            if y < 500:
                continue
            _tap_center(
                driver,
                float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0,
                float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0,
            )
            reserved = True
            markers.mark("reserve_tapped", y=y)
            _mark_step(
                outcome,
                "reserve",
                status="ok",
                y=y,
                day_changed=date_tapped,
                slot_changed=slot_tapped,
            )
            print(f"  Reserve tapped @ y={y:.0f}")
            break
        except Exception:  # noqa: BLE001
            continue
    if not reserved:
        el, by, value = find_first(driver, RESERVE_BUTTON_STRATEGIES, timeout=2.5)
        el.click()
        markers.mark("reserve_tapped", by=by, value=value)
        _mark_step(
            outcome,
            "reserve",
            status="ok",
            by=by,
            value=value,
            day_changed=date_tapped,
            slot_changed=slot_tapped,
        )
        print(f"  Reserve: {by} = {value!r}")
    time.sleep(_MED)
    # Hot/ready items may block reservation — continue without them if sheet appears.
    _dismiss_ineligible_delivery_sheet(driver, markers, outcome)
    # Large baskets: Reserve can leave us on the sheet with "items ineligible".
    _recover_if_still_on_reserve_sheet(driver, markers, outcome)


def _reserve_sheet_visible(driver) -> bool:
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label == "Reserve a time" OR name == "Reserve a time" OR '
            'label CONTAINS[c] "some items can\'t be delivered" OR '
            'label CONTAINS[c] "select an eligible delivery time")',
        )
        return bool(els)
    except Exception:  # noqa: BLE001
        return False


def _recover_if_still_on_reserve_sheet(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """If Reserve sheet still up, pick an eligible slot + Reserve, else close it."""
    if not _reserve_sheet_visible(driver):
        return
    print("  still on Reserve a time — picking eligible slot …")
    for _ in range(2):
        _scroll_down(driver, percent=0.35)
        time.sleep(0.05)
    slots = _collect_time_slots(driver)
    eligible = [s for s in slots if "ineligible" not in (s[2] or "").lower()]
    pool = eligible if eligible else slots
    if pool:
        # Prefer last eligible (often later evening without the banner).
        y, cx, label, _sel = pool[-1]
        _tap_center(driver, cx, y + 6.0)
        markers.mark("delivery_slot_eligible_retry", label=label)
        print(f"  eligible slot retry → {label!r}")
        time.sleep(_FAST)
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND '
                '(name == "Reserve" OR label == "Reserve")',
            )
            for el in els[:3]:
                r = el.rect or {}
                yb = float(r.get("y", 0))
                if yb < 500:
                    continue
                _tap_center(
                    driver,
                    float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0,
                    float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0,
                )
                markers.mark("reserve_tapped_eligible_retry", y=yb)
                print(f"  Reserve (eligible retry) @ y={yb:.0f}")
                time.sleep(_MED)
                break
        except Exception:  # noqa: BLE001
            pass
    _dismiss_ineligible_delivery_sheet(driver, markers, outcome)
    if not _reserve_sheet_visible(driver):
        return
    # Last resort: close the sheet so Cart Continue is reachable.
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "Close"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Close" OR name == "Close" OR label == "close")',
        ),
    ):
        try:
            el, _, _ = find_first(driver, ((by, value),), timeout=1.0)
            el.click()
            markers.mark("reserve_sheet_closed", by=by, value=value)
            print("  closed Reserve a time sheet")
            time.sleep(_MED)
            return
        except TimeoutError:
            continue
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        _tap_center(driver, w * 0.92, 120.0)
        markers.mark("reserve_sheet_closed", via="coord")
        print("  closed Reserve a time sheet (coord)")
        time.sleep(_MED)
    except Exception:  # noqa: BLE001
        pass


def _increase_first_cart_qty(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    increase_by: int = CART_QTY_INCREASE_BY,
) -> None:
    print(f"Step: increase 1st cart item quantity by +{increase_by} …")
    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0

    def _find_increment() -> tuple[float, float, float] | None:
        candidates: list[tuple[float, float, float]] = []
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND '
                '(name == "stepper.incrementButton" OR '
                'label == "+" OR name == "+")',
            )
        except Exception:  # noqa: BLE001
            els = []
        for el in els[:20]:
            try:
                r = el.rect or {}
                y = float(r.get("y", 0))
                x = float(r.get("x", 0))
                w = float(r.get("width", 0))
                hh = float(r.get("height", 0))
                name = (el.get_attribute("name") or "").lower()
                if "decrement" in name:
                    continue
                # Qty row is often under the sticky checkout CTA — still tappable.
                if y < 280 or y > h * 0.96:
                    continue
                if w < 14 or hh < 14 or w > 140:
                    continue
                candidates.append((y, x + w / 2.0, y + hh / 2.0))
            except Exception:  # noqa: BLE001
                continue
        if not candidates:
            return None
        # Prefer the topmost cart-line increment (first product).
        candidates.sort(key=lambda t: (round(t[0] / 8.0), -t[1]))
        return candidates[0]

    hit = _find_increment()
    if hit is None or hit[0] > h * 0.78:
        # Scroll cart body so the first item stepper clears the sticky footer.
        _scroll_down(driver, percent=0.35)
        time.sleep(0.15)
        hit = _find_increment()
    if hit is None:
        _scroll_down(driver, percent=0.30)
        time.sleep(0.15)
        hit = _find_increment()
    if hit is None:
        raise AssertionError("Could not find first cart item quantity + control")

    _y, cx, cy = hit
    # If still under sticky CTA, tap slightly above footer safe zone by scrolling more.
    if cy > h * 0.78:
        _scroll_down(driver, percent=0.28)
        time.sleep(0.12)
        hit2 = _find_increment()
        if hit2 is not None:
            _y, cx, cy = hit2

    print(f"  first cart qty + @ ({cx:.0f},{cy:.0f})")
    markers.mark("cart_first_qty_plus_found", x=cx, y=cy)

    taps = 0
    for i in range(increase_by):
        try:
            _tap_center(driver, cx, cy)
            taps += 1
            markers.mark("cart_first_qty_plus_tapped", index=i + 1, x=cx, y=cy)
            print(f"  cart qty + tap {i + 1}/{increase_by}")
            time.sleep(0.08)
        except Exception as exc:  # noqa: BLE001
            print(f"  cart qty + tap {i + 1} failed: {exc}")
            break

    outcome["cart_first_qty_increased_by"] = taps
    _mark_step(
        outcome,
        "cart_first_qty_increase",
        status="ok" if taps >= increase_by else "partial",
        taps=taps,
        increase_by=increase_by,
    )
    if taps < increase_by:
        raise AssertionError(f"Only tapped cart qty + {taps}/{increase_by} times")
    time.sleep(0.2)


def _dismiss_ineligible_delivery_sheet(
    driver, markers: MarkerLog, outcome: dict
) -> bool:
    """Dismiss 'Delivery unavailable for these items…' by continuing without them."""
    try:
        titles = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'label CONTAINS[c] "Delivery is unavailable for these items" OR '
            'label CONTAINS[c] "continue without this item" OR '
            'label CONTAINS[c] "Continue without items"',
        )
        if not titles:
            return False
    except Exception:  # noqa: BLE001
        return False

    try:
        el, by, value = find_first(
            driver, CONTINUE_WITHOUT_ITEMS_STRATEGIES, timeout=2.0
        )
        el.click()
        markers.mark("continue_without_items", by=by, value=value)
        _mark_step(outcome, "continue_without_items", status="ok", by=by, value=value)
        print(f"  Continue without items: {by} = {value!r}")
        time.sleep(_MED)
        return True
    except TimeoutError:
        # Fallback: tap secondary CTA near bottom.
        try:
            size = driver.get_window_size() or {}
            w = float(size.get("width") or 390)
            h = float(size.get("height") or 844)
            _tap_center(driver, w * 0.50, h * 0.90)
            markers.mark("continue_without_items", via="coord")
            _mark_step(outcome, "continue_without_items", status="ok", via="coord")
            print("  Continue without items → coord fallback")
            time.sleep(_MED)
            return True
        except Exception:  # noqa: BLE001
            return False


def _cart_ineligible_reserved_time_warning_visible(driver) -> bool:
    """Cart banner: 'N item(s) are ineligible for the reserved time'."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label CONTAINS[c] "ineligible for the reserved time" OR '
            'label CONTAINS[c] "Ineligible for reserved time" OR '
            'label CONTAINS[c] "Reserve a new time" OR '
            'name CONTAINS[c] "Reserve a new time")',
        )
        return bool(els)
    except Exception:  # noqa: BLE001
        return False


def _tap_reserve_a_new_time_link(driver, markers: MarkerLog) -> bool:
    strategies = (
        (AppiumBy.ACCESSIBILITY_ID, "Reserve a new time"),
        (
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label == "Reserve a new time" OR name == "Reserve a new time" OR '
            'label CONTAINS[c] "Reserve a new time" OR '
            'name CONTAINS[c] "Reserve a new time")',
        ),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeLink" AND visible == 1 AND '
            'label CONTAINS[c] "Reserve a new time"',
        ),
    )
    try:
        el, by, value = find_first(driver, strategies, timeout=2.5)
        el.click()
        markers.mark("reserve_a_new_time_tapped", by=by, value=value)
        print(f"  Reserve a new time: {by} = {value!r}")
        time.sleep(_MED)
        return True
    except TimeoutError:
        # Banner body often embeds the link — tap right half of warning text.
        try:
            banners = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'visible == 1 AND label CONTAINS[c] "ineligible for the reserved"',
            )
            for b in banners[:3]:
                r = b.rect or {}
                y = float(r.get("y", 0))
                x = float(r.get("x", 0))
                ww = float(r.get("width", 0))
                hh = float(r.get("height", 0))
                if y < 80 or ww < 80:
                    continue
                _tap_center(driver, x + ww * 0.55, y + hh * 0.72)
                markers.mark("reserve_a_new_time_tapped", via="banner_coord")
                print("  Reserve a new time → banner coord")
                time.sleep(_MED)
                return True
        except Exception:  # noqa: BLE001
            pass
        return False


def _tap_view_nearby_stores(driver, markers: MarkerLog) -> bool:
    strategies = (
        (AppiumBy.ACCESSIBILITY_ID, "View nearby stores"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "View nearby stores" OR name == "View nearby stores" OR '
            'label CONTAINS[c] "View nearby stores")',
        ),
    )
    try:
        el, by, value = find_first(driver, strategies, timeout=3.5)
        el.click()
        markers.mark("view_nearby_stores_tapped", by=by, value=value)
        print(f"  View nearby stores: {by} = {value!r}")
        time.sleep(_MED)
        return True
    except TimeoutError:
        try:
            size = driver.get_window_size() or {}
            w = float(size.get("width") or 390)
            h = float(size.get("height") or 844)
            _tap_center(driver, w * 0.50, h * 0.92)
            markers.mark("view_nearby_stores_tapped", via="coord")
            print("  View nearby stores → coord")
            time.sleep(_MED)
            return True
        except Exception:  # noqa: BLE001
            return False


def _select_store_from_nearby_list(driver, markers: MarkerLog, outcome: dict) -> bool:
    """On 'Select a store', pick a store (prefer 2nd) so ineligible warning clears."""
    print("Step: select a nearby store …")
    time.sleep(_FAST)
    # Prefer explicit store name cells / buttons.
    store_preds = (
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label CONTAINS[c] "miles away" OR name CONTAINS[c] "miles away" OR '
        'label CONTAINS[c] "Supercenter" OR label CONTAINS[c] "Neighborhood Market" OR '
        'label CONTAINS[c] "Store")',
        'type == "XCUIElementTypeStaticText" AND visible == 1 AND '
        'label CONTAINS[c] "miles away"',
    )
    candidates: list[tuple[float, float, float, str]] = []
    for pred in store_preds:
        try:
            els = driver.find_elements(AppiumBy.IOS_PREDICATE, pred)
        except Exception:  # noqa: BLE001
            els = []
        for el in els[:16]:
            try:
                label = (el.get_attribute("label") or el.get_attribute("name") or "")[
                    :80
                ]
                r = el.rect or {}
                y = float(r.get("y", 0))
                x = float(r.get("x", 0))
                hh = float(r.get("height", 0))
                if y < 160 or y > 720 or hh < 10:
                    continue
                candidates.append((y, x, y + hh / 2.0, label))
            except Exception:  # noqa: BLE001
                continue
        if len(candidates) >= 2:
            break
    candidates.sort(key=lambda t: t[0])
    # Dedupe by Y band.
    unique: list[tuple[float, float, float, str]] = []
    for c in candidates:
        if any(abs(c[0] - u[0]) < 28 for u in unique):
            continue
        unique.append(c)
    if not unique:
        # Radio buttons fallback — tap 2nd radio if present.
        try:
            radios = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND visible == 1 AND '
                '(name CONTAINS[c] "radio" OR label CONTAINS[c] "radio" OR '
                'value == "0" OR value == "1")',
            )
            picks = []
            for el in radios[:10]:
                r = el.rect or {}
                y = float(r.get("y", 0))
                if 180 < y < 700:
                    picks.append((y, el))
            picks.sort(key=lambda t: t[0])
            if picks:
                idx = 1 if len(picks) > 1 else 0
                picks[idx][1].click()
                markers.mark("nearby_store_selected", via="radio", index=idx)
                print(f"  selected store radio #{idx + 1}")
                time.sleep(_MED)
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    # Prefer 2nd store (change away from the ineligible current store).
    pick = unique[1] if len(unique) > 1 else unique[0]
    _y, x, cy, label = pick
    # Tap left side for radio / row select.
    _tap_center(driver, max(28.0, x + 24.0), cy)
    markers.mark("nearby_store_selected", label=label, y=_y)
    _mark_step(outcome, "nearby_store_selected", status="ok", label=label)
    print(f"  selected store → {label!r}")
    time.sleep(_MED)
    # Some UIs need an explicit Save / Select / Done.
    for lab in ("Save", "Select", "Done", "Confirm"):
        try:
            el, _, _ = find_first(
                driver,
                (
                    (AppiumBy.ACCESSIBILITY_ID, lab),
                    (
                        AppiumBy.IOS_PREDICATE,
                        f'type == "XCUIElementTypeButton" AND visible == 1 AND '
                        f'(label == "{lab}" OR name == "{lab}")',
                    ),
                ),
                timeout=0.8,
            )
            el.click()
            markers.mark("nearby_store_confirm", label=lab)
            time.sleep(_MED)
            break
        except TimeoutError:
            continue
    return True


def _resolve_ineligible_reserved_time_via_store(
    driver, markers: MarkerLog, outcome: dict
) -> bool:
    """Extra cart recovery when 'Ineligible for reserved time' banner is shown.

    Reserve a new time → View nearby stores → select a store → reserve eligible slot.
    """
    if not _cart_ineligible_reserved_time_warning_visible(driver):
        return False
    print(
        "Step: cart has Ineligible-for-reserved-time warning → "
        "Reserve a new time → View nearby stores → select store …"
    )
    if not _tap_reserve_a_new_time_link(driver, markers):
        print("  could not tap Reserve a new time")
        return False
    # Ensure we are on Reserve a time sheet.
    time.sleep(_FAST)
    if not _tap_view_nearby_stores(driver, markers):
        # Might still be on Delivery tab without the CTA — try Pickup then retry.
        try:
            el, _, _ = find_first(
                driver,
                (
                    (AppiumBy.ACCESSIBILITY_ID, "Pickup"),
                    (
                        AppiumBy.IOS_PREDICATE,
                        'type == "XCUIElementTypeButton" AND visible == 1 AND '
                        '(label == "Pickup" OR name == "Pickup")',
                    ),
                ),
                timeout=1.2,
            )
            el.click()
            time.sleep(_FAST)
        except TimeoutError:
            pass
        if not _tap_view_nearby_stores(driver, markers):
            print("  could not tap View nearby stores")
            return False
    if not _select_store_from_nearby_list(driver, markers, outcome):
        print("  could not select a nearby store")
        return False
    # Back on Reserve a time — pick eligible slot + Reserve.
    time.sleep(_MED)
    for _ in range(2):
        _scroll_down(driver, percent=0.30)
        time.sleep(0.04)
    slots = _collect_time_slots(driver)
    eligible = [s for s in slots if "ineligible" not in (s[2] or "").lower()]
    pool = eligible if eligible else slots
    if pool:
        y, cx, label, _sel = pool[-1]
        _tap_center(driver, cx, y + 6.0)
        markers.mark("delivery_slot_after_store_change", label=label)
        print(f"  slot after store change → {label!r}")
        time.sleep(_FAST)
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(name == "Reserve" OR label == "Reserve")',
        )
        for el in els[:3]:
            r = el.rect or {}
            yb = float(r.get("y", 0))
            if yb < 500:
                continue
            _tap_center(
                driver,
                float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0,
                float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0,
            )
            markers.mark("reserve_after_store_change", y=yb)
            print(f"  Reserve after store change @ y={yb:.0f}")
            time.sleep(_MED)
            break
    except Exception:  # noqa: BLE001
        pass
    _dismiss_ineligible_delivery_sheet(driver, markers, outcome)
    _recover_if_still_on_reserve_sheet(driver, markers, outcome)
    outcome["ineligible_store_recovery"] = True
    _mark_step(outcome, "ineligible_store_recovery", status="ok")
    print("  ineligible reserved-time recovery done")
    return True


def _dismiss_system_share_sheet(driver, markers: MarkerLog | None = None) -> bool:
    """Close iOS AirDrop / share UI if a mis-tap opened it over Cart."""
    try:
        clues = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label CONTAINS[c] "AirDrop" OR name CONTAINS[c] "AirDrop" OR '
            'label CONTAINS[c] "People nearby" OR '
            'label CONTAINS[c] "Use AirDrop" OR '
            'label CONTAINS[c] "Can’t see people nearby" OR '
            'label CONTAINS[c] "Can\'t see people nearby")',
        )
        if not clues:
            return False
    except Exception:  # noqa: BLE001
        return False

    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "Close"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Close" OR name == "Close" OR label == "Cancel" OR '
            'name == "Cancel")',
        ),
    ):
        try:
            el, _, _ = find_first(driver, ((by, value),), timeout=1.2)
            el.click()
            if markers is not None:
                markers.mark("system_share_sheet_dismissed", by=by, value=value)
            print(f"  dismissed system share sheet via {by}={value!r}")
            time.sleep(0.35)
            return True
        except TimeoutError:
            continue
    # Top-right X fallback on share sheet.
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        _tap_center(driver, w * 0.92, 56.0)
        if markers is not None:
            markers.mark("system_share_sheet_dismissed", via="coord")
        print("  dismissed system share sheet via coord")
        time.sleep(0.35)
        return True
    except Exception:  # noqa: BLE001
        return False


def _tap_continue_to_checkout(driver, markers: MarkerLog, outcome: dict) -> None:
    print('Step: tap "Continue to checkout" …')
    _dismiss_system_share_sheet(driver, markers)
    # Large baskets may show ineligible-item sheet after Reserve.
    _dismiss_ineligible_delivery_sheet(driver, markers, outcome)
    _recover_if_still_on_reserve_sheet(driver, markers, outcome)
    # Cart banner: "N items are ineligible for the reserved time" → change store.
    _resolve_ineligible_reserved_time_via_store(driver, markers, outcome)
    tap_dismissibles(driver, labels=WALMART_DISMISS_LABELS, rounds=1)
    try:
        el, by, value = find_first(driver, CONTINUE_TO_CHECKOUT_STRATEGIES, timeout=4.0)
    except TimeoutError:
        _dismiss_system_share_sheet(driver, markers)
        _dismiss_ineligible_delivery_sheet(driver, markers, outcome)
        _recover_if_still_on_reserve_sheet(driver, markers, outcome)
        _resolve_ineligible_reserved_time_via_store(driver, markers, outcome)
        # Qty scrolling may leave CTA off-screen — nudge list back toward footer.
        try:
            size = driver.get_window_size() or {}
            w = float(size.get("width") or 390)
            h = float(size.get("height") or 844)
            driver.execute_script(
                "mobile: dragFromToForDuration",
                {
                    "duration": 0.10,
                    "fromX": int(w * 0.50),
                    "fromY": int(h * 0.55),
                    "toX": int(w * 0.50),
                    "toY": int(h * 0.35),
                },
            )
            time.sleep(_FAST)
        except Exception:  # noqa: BLE001
            pass
        el, by, value = find_first(driver, CONTINUE_TO_CHECKOUT_STRATEGIES, timeout=5.0)
    el.click()
    outcome["continue_to_checkout_tapped"] = True
    markers.mark("continue_to_checkout", by=by, value=value)
    _mark_step(outcome, "continue_to_checkout", status="ok", by=by, value=value)
    print(f"  Continue to checkout: {by} = {value!r}")
    # Cart often shows a loading spark after CTA — Missing anything timeout covers it.
    time.sleep(_MED)


def _checkout_screen_visible(driver) -> bool:
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND '
            '(label == "Checkout" OR name == "Checkout" OR '
            'label CONTAINS[c] "Delivery from store" OR '
            'name CONTAINS[c] "Delivery from store")',
        )
        return any(True for _ in els[:4])
    except Exception:  # noqa: BLE001
        return False


def _tap_missing_anything_continue(driver, markers: MarkerLog, outcome: dict) -> None:
    print('Step: on "Missing anything?" tap "Continue" …')
    time.sleep(_FAST)
    # Small baskets sometimes skip "Missing anything?" and land on Checkout.
    if _checkout_screen_visible(driver):
        outcome["missing_anything_continue_tapped"] = False
        markers.mark("missing_anything_skipped", reason="already_on_checkout")
        _mark_step(
            outcome,
            "missing_anything_continue",
            status="skipped",
            reason="already_on_checkout",
        )
        print('  already on Checkout — skip "Missing anything?" Continue')
        return

    try:
        title = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND (label CONTAINS[c] "Missing anything" OR '
            'name CONTAINS[c] "Missing anything")',
        )
        if title:
            markers.mark("missing_anything_visible")
            print('  "Missing anything?" sheet visible')
    except Exception:  # noqa: BLE001
        pass

    try:
        el, by, value = find_first(
            driver, MISSING_ANYTHING_CONTINUE_STRATEGIES, timeout=5.0
        )
    except TimeoutError:
        if _checkout_screen_visible(driver):
            outcome["missing_anything_continue_tapped"] = False
            markers.mark("missing_anything_skipped", reason="checkout_during_wait")
            _mark_step(
                outcome,
                "missing_anything_continue",
                status="skipped",
                reason="checkout_during_wait",
            )
            print('  reached Checkout while waiting — skip "Missing anything?"')
            return
        raise

    try:
        buttons = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Continue" OR name == "Continue")',
        )
        best = None
        for b in buttons[:8]:
            r = b.rect or {}
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            if w < 120 or y < 500:
                continue
            if best is None or y > best[0]:
                best = (y, b)
        if best is not None:
            el = best[1]
            by, value = AppiumBy.IOS_PREDICATE, "Continue(bottom)"
    except Exception:  # noqa: BLE001
        pass

    el.click()
    outcome["missing_anything_continue_tapped"] = True
    markers.mark("missing_anything_continue", by=by, value=value)
    _mark_step(outcome, "missing_anything_continue", status="ok", by=by, value=value)
    print(f"  Continue: {by} = {value!r}")
    time.sleep(_MED)


def _wait_checkout_screen(driver, markers: MarkerLog, outcome: dict) -> None:
    print("Step: wait for Checkout screen (stop point) …")
    end = time.monotonic() + 4.0
    seen = False
    while time.monotonic() < end:
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'visible == 1 AND '
                '(label == "Checkout" OR name == "Checkout" OR '
                'label CONTAINS[c] "Checkout" OR name CONTAINS[c] "Checkout" OR '
                'label CONTAINS[c] "Delivery from store" OR '
                'name CONTAINS[c] "Delivery from store")',
            )
            for el in els[:6]:
                if el.is_displayed():
                    seen = True
                    break
        except Exception:  # noqa: BLE001
            pass
        if seen:
            break
        time.sleep(0.2)
    outcome["checkout_screen_reached"] = seen
    markers.mark("checkout_screen", reached=seen)
    _mark_step(
        outcome,
        "checkout_screen",
        status="ok" if seen else "partial",
        reached=seen,
    )
    if seen:
        print("  Checkout screen reached — stopping after this step")
    else:
        print("  Checkout title not confirmed — stopping anyway after Continue")


def run_test_case_walmart_1(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Walmart TC1: My Items → Fresh Produce → Add×5 → cart record → edits → Checkout."""
    if retailer != "walmart":
        raise ValueError(
            f"{TEST_CASE_NAME} is Walmart-only (use --retailer walmart)."
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
    plan["target_items"] = TARGET_ITEMS
    plan["cart_first_qty_increase_by"] = CART_QTY_INCREASE_BY
    plan["place_order"] = False
    plan["steps"] = [
        "Start new automation session (kill prior Walmart if running)",
        "Open Walmart App (cold start)",
        'Tap bottom tab "My Items"',
        "Clear existing cart items if any (so + Add is available)",
        'Tap "Fresh Produce" filter',
        f'Tap blue "+ Add" on {TARGET_ITEMS} different items',
        "Open Cart screen",
        f"START screen recording on Cart for {rec_limit}s",
        'Tap "Change" (delivery) on Cart → Reserve a time',
        'Tap "Change" (address) → select 2nd saved address',
        'Toggle delivery day last↔2nd-last + time slot last↔2nd-last → tap "Reserve"',
        f"Increase 1st cart item quantity by +{CART_QTY_INCREASE_BY}",
        'Tap "Continue to checkout"',
        'On "Missing anything?" tap "Continue"',
        "Land on Checkout screen → stop recording (do not place order)",
    ]
    plan["notes"] = (
        f"{TEST_CASE_NAME} — My Items → Fresh Produce → +Add ×{TARGET_ITEMS} → "
        "Cart record → Change delivery → Change address (2nd) → day/slot → Reserve → "
        f"1st item qty +{CART_QTY_INCREASE_BY} → Continue to checkout → Continue on "
        "Missing anything? → stop on Checkout. Does not place the order."
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
        "recording_started": False,
        "place_order": False,
        "cart_first_qty_increased_by": 0,
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
        # clear already returns to My Items — only re-tap if Fresh Produce missing
        try:
            find_first(driver, FRESH_PRODUCE_STRATEGIES, timeout=1.2)
        except TimeoutError:
            _tap_my_items_tab(driver, markers, outcome)
        _tap_fresh_produce(driver, markers, outcome)
        _add_n_via_add_buttons(driver, markers, outcome, target=TARGET_ITEMS)
        _open_cart(driver, markers, outcome)

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

        # 5) Change (delivery) on cart → Reserve a time
        _tap_cart_change_delivery(driver, markers, outcome)
        # 6) Change (address) → 2nd address
        _tap_reserve_change_address(driver, markers, outcome)
        _select_second_address(driver, markers, outcome)
        # 7) day + slot + Reserve
        _change_day_and_slot_then_reserve(driver, markers, outcome)
        # 8) qty + on first product
        _increase_first_cart_qty(
            driver, markers, outcome, increase_by=CART_QTY_INCREASE_BY
        )
        # 9–10) Continue to checkout → Continue on Missing anything? → Checkout
        _tap_continue_to_checkout(driver, markers, outcome)
        _tap_missing_anything_continue(driver, markers, outcome)
        _wait_checkout_screen(driver, markers, outcome)

        markers.mark(
            "test_case_walmart_1_stopped_on_checkout",
            items=outcome.get("items_added_count"),
            qty_plus=outcome.get("cart_first_qty_increased_by"),
            checkout_steps=outcome.get("checkout_steps"),
        )
        print("Stopping process on Checkout screen (order not placed) …")

        outcome["status"] = "ok"
        outcome["message"] = (
            f"{TEST_CASE_NAME} OK — My Items → Fresh Produce → "
            f"+Add ×{outcome.get('items_added_count')} → cart record → "
            f"Change delivery → Change address (2nd) → day/slot → Reserve → "
            f"1st qty +{outcome.get('cart_first_qty_increased_by')} → "
            f"Continue to checkout → Continue → Checkout"
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
            result_path.write_text(
                json.dumps(outcome, indent=2, default=str), encoding="utf-8"
            )
            print(f"Wrote result → {result_path}")
        except Exception as write_exc:  # noqa: BLE001
            print(f"Result write error: {write_exc}")
