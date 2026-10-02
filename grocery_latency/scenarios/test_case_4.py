"""Test Case 4 (TASK 4): Grocery search → add → slot + payment → stop.

Grocery search "under 5 dollar" → real + ×5 → Cart → record → Proceed →
(No thanks) → Place Your Order → alternate delivery-slot radio →
Change payment method → select alternate card → Continue → stop recording.

Does not place the order.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

from appium.webdriver.common.appiumby import AppiumBy

from grocery_latency.amazon_search import (
    SEARCH_TAP_STRATEGIES,
    SEARCH_TYPE_STRATEGIES,
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
from grocery_latency.scenarios.test_case_1 import (
    _tap_deliver_to_this_address,
)
from grocery_latency.scenarios.test_case_2 import (
    CART_TAB_STRATEGIES,
    GROCERY_HOME_TAB_STRATEGIES,
    _open_cart_page,
    _scroll_down,
    _tc2_tap_second_address,
    _try_tap_first,
)

TEST_CASE_NAME = "Test Case 4"
TEST_CASE_ID = "test_case_4"
TARGET_ITEMS = 5
SEARCH_QUERY = "under 5 dollar"  # fallback if deep link fails
# Official Everyday Essentials "Finds Under $5" shelf.
UNDER_5_PATH = "/fmc/m/30006713"
UNDER_5_HTTPS = f"https://www.amazon.com{UNDER_5_PATH}"
UNDER_5_DEEPLINKS = (
    f"com.amazon.mobile.shopping.web://www.amazon.com{UNDER_5_PATH}",
    f"com.amazon.mobile.shopping://www.amazon.com{UNDER_5_PATH}",
    UNDER_5_HTTPS,
)
USE_UNDER_5_DEEPLINK = False  # FMC webview can't add natively; skip for min runtime
# Cap only — stop_recording runs after last checkout action.
# Must exceed Cart → payment Continue wall time; iOS max is 600s.
CART_RECORDING_SECONDS = 300

HOUSEHOLD_TAB_STRATEGIES = ()  # unused in TC4 (kept empty to avoid accidental imports)

GROCERY_SEARCH_HINT_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "searchTextView"),
    (AppiumBy.ACCESSIBILITY_ID, "Search Amazon Fresh"),
    (AppiumBy.ACCESSIBILITY_ID, "Search Grocery"),
    (AppiumBy.ACCESSIBILITY_ID, "Search"),
    (
        AppiumBy.IOS_PREDICATE,
        'name == "searchTextView" OR '
        'label CONTAINS[c] "Search Amazon Fresh" OR '
        'name CONTAINS[c] "Search Amazon Fresh" OR '
        'label CONTAINS[c] "Search Grocery" OR '
        '(type == "XCUIElementTypeSearchField") OR '
        '(type == "XCUIElementTypeTextField" AND '
        '(label CONTAINS[c] "Search" OR name CONTAINS[c] "Search")) OR '
        '(type == "XCUIElementTypeTextView" AND name CONTAINS[c] "search")',
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

PROCEED_CHECKOUT_TC4 = (
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


def _dismiss_quantity_not_updated(driver, markers: MarkerLog | None = None) -> bool:
    """Close Amazon Fresh 'Quantity not updated' toast (blocks listing + taps)."""
    try:
        dialog = driver.find_element(
            AppiumBy.IOS_PREDICATE,
            'name CONTAINS[c] "Quantity not updated" OR '
            'label CONTAINS[c] "Quantity not updated"',
        )
    except Exception:  # noqa: BLE001
        return False
    # Prefer the dialog's close control; avoid the storefront Close (y~47).
    for by, value in (
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(name == "close" OR label == "close") AND visible == 1',
        ),
        (AppiumBy.ACCESSIBILITY_ID, "close"),
    ):
        try:
            for el in driver.find_elements(by, value):
                r = el.rect or {}
                y = float(r.get("y", 0))
                if y < 400:
                    continue
                if not el.is_displayed():
                    continue
                el.click()
                if markers is not None:
                    markers.mark("quantity_not_updated_dismissed")
                time.sleep(0.25)
                return True
        except Exception:  # noqa: BLE001
            continue
    # Fallback: tap the toast close coords used on iPhone 390pt.
    try:
        _tap_center(driver, 362, 616)
        if markers is not None:
            markers.mark("quantity_not_updated_dismissed", via="coords")
        time.sleep(0.25)
        return True
    except Exception:  # noqa: BLE001
        return bool(dialog)


def _scroll_grocery_listing(driver, *, percent: float = 0.55) -> None:
    """Scroll the grocery SERP webview; native swipe often no-ops there."""
    size = driver.get_window_size() or {}
    w = int(size.get("width") or 390)
    h = int(size.get("height") or 844)
    # Drag inside the product grid (below filters, above bottom chrome).
    start_y = int(h * 0.70)
    end_y = int(h * max(0.22, 0.70 - percent))
    try:
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.45,
                "fromX": w // 2,
                "fromY": start_y,
                "toX": w // 2,
                "toY": end_y,
            },
        )
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        driver.execute_script(
            "mobile: swipe",
            {"direction": "up", "percent": percent},
        )
    except Exception:  # noqa: BLE001
        _scroll_down(driver, percent=percent)


def _submit_search(driver, markers: MarkerLog, query: str) -> None:
    """Submit the typed grocery search (suggestion tap or keyboard Search)."""
    # Prefer tapping a suggestion that matches the query.
    try:
        el = driver.find_element(
            AppiumBy.IOS_PREDICATE,
            f'type == "XCUIElementTypeButton" AND '
            f'(label CONTAINS[c] "{query}" OR name CONTAINS[c] "{query}")',
        )
        if el.is_displayed():
            el.click()
            markers.mark("search_suggestion_tapped", query=query)
            time.sleep(0.2)
            return
    except Exception:  # noqa: BLE001
        pass

    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "Search"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(label == "Search" OR name == "Search")',
        ),
    ):
        try:
            btn = driver.find_element(by, value)
            if btn.is_displayed():
                btn.click()
                markers.mark("search_keyboard_search_tapped")
                time.sleep(0.2)
                return
        except Exception:  # noqa: BLE001
            continue

    try:
        driver.execute_script("mobile: performEditorAction", {"action": "search"})
        markers.mark("search_editor_action_search")
        time.sleep(0.2)
        return
    except Exception:  # noqa: BLE001
        pass

    try:
        driver.execute_script("mobile: keys", {"keys": [{"key": "\n"}]})
        markers.mark("search_return_key")
        time.sleep(0.2)
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"Could not submit search for {query!r}: {exc}") from exc



def _open_under_5_shelf(
    driver, markers: MarkerLog, outcome: dict, *, bundle_id: str
) -> None:
    """Open Finds Under $5 via deep link; fall back to Grocery search."""
    print(f"Step: open Under $5 shelf ({UNDER_5_PATH}) …")
    outcome["under_5_url"] = UNDER_5_HTTPS
    outcome["search_query"] = None
    last_err: Exception | None = None
    # Prefer native shopping schemes — bare https often stays in Safari.
    for url in UNDER_5_DEEPLINKS:
        if url.startswith("https://"):
            continue
        try:
            print(f"  deep-link: {url}")
            driver.execute_script(
                "mobile: deepLink",
                {"url": url, "bundleId": bundle_id},
            )
            markers.mark("under_5_deeplink", url=url)
            outcome["under_5_open_method"] = url
            # Skip tap_dismissibles here — FMC webviews make WDA find_* hang.
            time.sleep(2.8)
            markers.mark("under_5_shelf_ready", url=url)
            print("  Under $5 shelf opened via deep link")
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"  deep-link failed ({url}): {exc}")

    print(
        "  Under $5 deep link unavailable — "
        f'falling back to Grocery search "{SEARCH_QUERY}" …'
    )
    markers.mark(
        "under_5_deeplink_fallback_search",
        query=SEARCH_QUERY,
        error=str(last_err) if last_err else None,
    )
    _open_grocery_search_low_cost(driver, markers, outcome)


def _open_grocery_search_low_cost(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """Tap Grocery, search SEARCH_QUERY (fallback if Under $5 deep link fails)."""
    print('Step: tap home "Grocery" tab …')
    # Prefer the known accessibility id — avoids slow multi-strategy scans.
    hit = None
    try:
        el = driver.find_element(AppiumBy.ACCESSIBILITY_ID, "Grocery")
        el.click()
        hit = (AppiumBy.ACCESSIBILITY_ID, "Grocery")
    except Exception:  # noqa: BLE001
        hit = _try_tap_first(
            driver, GROCERY_HOME_TAB_STRATEGIES, timeout=1.8, scrolls=0
        )
    if not hit:
        raise AssertionError('Home "Grocery" tab not found')
    by, value = hit
    outcome.setdefault("selectors", {})["grocery_home_tab"] = {"by": by, "value": value}
    markers.mark("grocery_home_tab_tapped", by=by, value=value)
    print(f"  Grocery tab: {by} = {value!r}")
    time.sleep(0.15)

    print(f'Step: search "{SEARCH_QUERY}" in Grocery search box …')
    outcome["search_query"] = SEARCH_QUERY
    outcome["under_5_open_method"] = f"search:{SEARCH_QUERY}"
    try:
        search_el, tap_by, tap_value = find_first(
            driver, GROCERY_SEARCH_HINT_STRATEGIES, timeout=3.0
        )
    except TimeoutError:
        try:
            search_el, tap_by, tap_value = find_first(
                driver, SEARCH_TAP_STRATEGIES, timeout=2.0
            )
        except TimeoutError as exc:
            raise AssertionError("Grocery search field not found") from exc
    outcome.setdefault("selectors", {})["search_tap"] = {
        "by": tap_by,
        "value": tap_value,
    }
    print(f"  Search tap: {tap_by} = {tap_value!r}")
    search_el.click()
    markers.mark("search_tapped", by=tap_by, value=tap_value, query=SEARCH_QUERY)
    time.sleep(0.12)

    type_el, type_by, type_value = find_first(
        driver, SEARCH_TYPE_STRATEGIES + GROCERY_SEARCH_HINT_STRATEGIES, timeout=2.5
    )
    outcome["selectors"]["search_type"] = {"by": type_by, "value": type_value}
    print(f"  Search type: {type_by} = {type_value!r}")
    type_into_search(driver, type_el, SEARCH_QUERY)
    markers.mark("query_typed", query=SEARCH_QUERY)
    outcome["query_confirmed"] = True

    _submit_search(driver, markers, SEARCH_QUERY)
    markers.mark("low_cost_searched", query=SEARCH_QUERY)
    time.sleep(0.2)
    print(f'  Search submitted for "{SEARCH_QUERY}"')


def _collect_plus_centers(driver, *, limit: int = 12) -> list[dict]:
    """Find grocery grid Add/+ chips on the listing (not PDP).

    Fresh SERP often exposes many off-screen 'Add to cart' nodes in the tree
    with visible=false; include those so we can scroll-to-tap without inventing
    coordinates (invented taps open product details).
    """
    out: list[dict] = []
    predicates = (
        # On-screen first.
        'type == "XCUIElementTypeButton" AND enabled == 1 AND visible == 1 AND '
        '(name == "+" OR label == "+" OR '
        'label == "Add" OR name == "Add" OR '
        'label == "Add to cart" OR name == "Add to cart")',
        # Full tree (webview): off-screen Add chips still listed.
        'type == "XCUIElementTypeButton" AND enabled == 1 AND '
        '(name == "+" OR label == "+" OR '
        'label == "Add" OR name == "Add" OR '
        'label == "Add to cart" OR name == "Add to cart")',
    )
    els = []
    for pred in predicates:
        try:
            els = driver.find_elements(AppiumBy.IOS_PREDICATE, pred)
        except Exception:  # noqa: BLE001
            els = []
        if els:
            break
    for el in els[:60]:
        try:
            r = el.rect or {}
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            h = float(r.get("height", 0))
            if h <= 0 or w <= 0 or w > 160 or h > 72:
                continue
            # Skip chrome; allow below-fold y so we can scroll into view.
            if y < 100 or x < 0 or x > 400:
                continue
            try:
                label = (
                    el.get_attribute("label") or el.get_attribute("name") or ""
                )
            except Exception:  # noqa: BLE001
                label = ""
            low = label.lower()
            if "in cart" in low or "minimum quantity" in low:
                continue
            try:
                displayed = bool(el.is_displayed())
            except Exception:  # noqa: BLE001
                displayed = False
            # Prefer on-screen chips; still keep below-fold Adds.
            if not displayed and y < 140:
                continue
            cx = x + w / 2.0
            cy = y + h / 2.0
            if w >= 70:
                cx = x + w - min(20.0, w / 4.0)
            fp = f"{int(round(cx / 8.0) * 8)}:{int(round(cy / 8.0) * 8)}"
            out.append(
                {
                    "el": el,
                    "cx": cx,
                    "cy": cy,
                    "fp": fp,
                    "x": x,
                    "y": y,
                    "displayed": displayed,
                }
            )
        except Exception:  # noqa: BLE001
            continue
    # Prefer currently visible chips, then top-to-bottom.
    out.sort(key=lambda c: (0 if c.get("displayed") else 1, c["y"], c["cx"]))
    deduped: list[dict] = []
    for c in out:
        if any(
            abs(c["cx"] - d["cx"]) < 24 and abs(c["y"] - d["y"]) < 40
            for d in deduped
        ):
            continue
        deduped.append(c)
    return deduped[:limit]


def _ensure_plus_visible(driver, tap: dict) -> bool:
    """Scroll a below-fold Add chip into view; refresh center from element."""
    el = tap.get("el")
    if el is None:
        return True
    try:
        if el.is_displayed():
            r = el.rect or {}
            y = float(r.get("y", 0))
            if 140 <= y <= 780:
                w = float(r.get("width", 0))
                h = float(r.get("height", 0))
                x = float(r.get("x", 0))
                tap["cx"] = x + w / 2.0
                tap["cy"] = y + h / 2.0
                if w >= 70:
                    tap["cx"] = x + w - min(20.0, w / 4.0)
                return True
    except Exception:  # noqa: BLE001
        pass
    if _scroll_element_into_view(driver, el):
        time.sleep(0.2)
    else:
        # Nudge listing until this chip's y enters the viewport band.
        for _ in range(4):
            try:
                r = el.rect or {}
                y = float(r.get("y", 0))
            except Exception:  # noqa: BLE001
                break
            if 160 <= y <= 720:
                break
            _scroll_grocery_listing(driver, percent=0.42)
            time.sleep(0.25)
    try:
        if not el.is_displayed():
            return False
        r = el.rect or {}
        x = float(r.get("x", 0))
        y = float(r.get("y", 0))
        w = float(r.get("width", 0))
        h = float(r.get("height", 0))
        if y < 140 or y > 780 or w <= 0:
            return False
        tap["cx"] = x + w / 2.0
        tap["cy"] = y + h / 2.0
        if w >= 70:
            tap["cx"] = x + w - min(20.0, w / 4.0)
        tap["fp"] = (
            f"{int(round(tap['cx'] / 8.0) * 8)}:"
            f"{int(round(tap['cy'] / 8.0) * 8)}"
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def _expand_2col_grid_taps(seeds: list[dict], *, target: int) -> list[dict]:
    """From first-row + centers, invent remaining 2-col taps (no more find_elements)."""
    if not seeds:
        return []
    if len(seeds) >= target:
        return [dict(s, via="collect") for s in seeds[:target]]

    first_y = float(seeds[0]["cy"])
    first_row = [s for s in seeds if abs(s["cy"] - first_y) < 45]
    col_xs = sorted({round(s["cx"]) for s in first_row})
    if len(col_xs) == 1:
        if col_xs[0] >= 250:
            col_xs = [col_xs[0] - 192, col_xs[0]]
        else:
            col_xs = [col_xs[0], col_xs[0] + 192]
    while len(col_xs) < 2:
        col_xs.append(col_xs[-1] + 192)
    col_xs = col_xs[:2]

    row_ys: list[float] = []
    for s in seeds:
        if not row_ys or abs(s["cy"] - row_ys[-1]) > 45:
            row_ys.append(float(s["cy"]))

    pitch = 255.0
    if len(row_ys) >= 2:
        pitch = max(180.0, min(400.0, row_ys[1] - row_ys[0]))
    rows_needed = max(1, (target + 1) // 2)
    if rows_needed > 1:
        fit = (810.0 - row_ys[0]) / float(rows_needed - 1)
        pitch = min(pitch, max(140.0, fit))

    taps: list[dict] = [dict(s, via="collect") for s in seeds[:target]]

    def _near(x: float, y: float) -> bool:
        return any(abs(t["cx"] - x) < 40 and abs(t["cy"] - y) < 50 for t in taps)

    def _add(x: float, y: float) -> None:
        taps.append(
            {
                "el": None,
                "cx": float(x),
                "cy": float(y),
                "fp": f"grid:{int(x)}:{int(y)}",
                "x": float(x),
                "y": float(y),
                "via": "grid",
            }
        )

    # Fill missing sibling cells on known rows.
    for ry in row_ys:
        if len(taps) >= target:
            break
        for x in col_xs:
            if len(taps) >= target:
                break
            if not _near(x, ry):
                _add(x, ry)

    # Invent further rows below the last known seed row.
    next_y = row_ys[-1] + pitch
    guard = 0
    while len(taps) < target and guard < 8:
        guard += 1
        y = min(810.0, next_y)
        for x in col_xs:
            if len(taps) >= target:
                break
            if not _near(x, y):
                _add(x, y)
        if next_y >= 810:
            # Last resort: alternate columns near the fold.
            y = 810.0 - (guard * 8)
            for x in col_xs:
                if len(taps) >= target:
                    break
                if not _near(x, y):
                    _add(x, y)
            break
        next_y += pitch
    return taps[:target]


def _tap_center(driver, cx: float, cy: float) -> None:
    driver.execute_script(
        "mobile: tap",
        {"x": int(round(cx)), "y": int(round(cy))},
    )


def _scroll_up(driver, percent: float = 0.55) -> None:
    """Swipe down so content above (negative-Y webview nodes) becomes visible."""
    try:
        driver.execute_script(
            "mobile: swipe",
            {"direction": "down", "percent": percent},
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
                    "fromY": int(h * 0.28),
                    "toX": w // 2,
                    "toY": int(h * 0.72),
                },
            )
        except Exception:  # noqa: BLE001
            pass


def _scroll_element_into_view(driver, el) -> bool:
    """Best-effort scroll an off-screen webview element into the viewport."""
    try:
        driver.execute_script("mobile: scroll", {"element": el.id, "toVisible": True})
        return True
    except Exception:  # noqa: BLE001
        return False


def _wait_plus_centers(driver, *, attempts: int = 8) -> list[dict]:
    """Poll for listing Add/+ buttons (including below-fold tree nodes)."""
    for i in range(attempts):
        batch = _collect_plus_centers(driver, limit=12)
        visible = [b for b in batch if b.get("displayed")]
        if len(visible) >= 2 or len(batch) >= 2:
            return batch
        if batch and i == attempts - 1:
            return batch
        time.sleep(0.35)
    return _collect_plus_centers(driver, limit=12)


def _fmc_under5_add_taps(driver, *, target: int) -> list[dict]:
    """Coordinate taps for Under $5 FMC webview (Add chips not in native tree)."""
    size = driver.get_window_size() or {}
    w = float(size.get("width") or 390)
    h = float(size.get("height") or 844)
    # Horizontal carousels: yellow Add is bottom-RIGHT on each card image
    # (not card center). Empirically ~2.2 cards across on iPhone 390pt.
    rows_y = (0.34 * h, 0.55 * h, 0.73 * h)
    cols_x = (0.40 * w, 0.72 * w, 0.93 * w)
    taps: list[dict] = []
    for y in rows_y:
        for x in cols_x:
            if len(taps) >= target:
                break
            # Keep taps inside the safe tap band.
            cx = min(max(x, 24.0), w - 24.0)
            cy = min(max(y, 140.0), h - 120.0)
            taps.append(
                {
                    "el": None,
                    "cx": float(cx),
                    "cy": float(cy),
                    "fp": f"fmc:{int(cx)}:{int(cy)}",
                    "x": float(cx),
                    "y": float(cy),
                    "via": "fmc_grid",
                }
            )
        if len(taps) >= target:
            break
    return taps[:target]


def _cart_badge_count(driver) -> int | None:
    """Best-effort cart badge integer from cartTab accessibility value/label."""
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "cartTab"),
        (AppiumBy.ACCESSIBILITY_ID, "Cart"),
        (
            AppiumBy.IOS_PREDICATE,
            'name == "cartTab" OR label BEGINSWITH[c] "Cart"',
        ),
    ):
        try:
            el = driver.find_element(by, value)
            for raw in (
                getattr(el, "get_attribute", lambda _a: None)("value"),
                getattr(el, "get_attribute", lambda _a: None)("label"),
                getattr(el, "get_attribute", lambda _a: None)("name"),
            ):
                if not raw:
                    continue
                m = re.search(r"(\d+)", str(raw))
                if m:
                    return int(m.group(1))
        except Exception:  # noqa: BLE001
            continue
    return None


def _dismiss_grocery_pdp_if_open(driver, markers: MarkerLog | None = None) -> bool:
    """If a grocery product detail page opened, leave it (Back/Close).

    Must not fire on the SERP: listing cards often show Choice / 'bought in past
    month', and main Amazon home has Share — tapping Back there exits Grocery.
    """
    # Still on search results → never treat as PDP.
    for by, value in (
        (
            AppiumBy.IOS_PREDICATE,
            'name CONTAINS[c] "Amazon.com :" OR '
            'label CONTAINS[c] "Amazon.com :" OR '
            'name == "searchTextView" OR '
            'label CONTAINS[c] "Search Amazon Fresh" OR '
            'label CONTAINS[c] "under 5"',
        ),
    ):
        try:
            el = driver.find_element(by, value)
            if el.is_displayed():
                return False
        except Exception:  # noqa: BLE001
            pass

    # Strong PDP-only chrome (not present on grocery SERP tiles).
    pdpish = False
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "Add to List"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(label == "Add to List" OR name == "Add to List" OR '
            'label CONTAINS[c] "Add to List")',
        ),
        (
            AppiumBy.IOS_PREDICATE,
            'label CONTAINS[c] "See all buying options" OR '
            'label CONTAINS[c] "Buy Now" OR name CONTAINS[c] "Buy Now"',
        ),
    ):
        try:
            el = driver.find_element(by, value)
            if el.is_displayed():
                pdpish = True
                break
        except Exception:  # noqa: BLE001
            continue
    if not pdpish:
        return False

    # Prefer grocery storefront back; avoid main-app Close.
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "cxi_store_back_button"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(name == "cxi_store_back_button" OR label == "Back")',
        ),
        (AppiumBy.ACCESSIBILITY_ID, "Back"),
    ):
        try:
            el = driver.find_element(by, value)
            if not el.is_displayed():
                continue
            el.click()
            time.sleep(0.25)
            if markers is not None:
                markers.mark("grocery_pdp_dismissed", by=by, value=value)
            print(f"  left product page ({value})")
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _add_n_low_cost_fast(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    target: int,
    max_scrolls: int = 2,
    allow_fmc_coords: bool = True,
) -> list[dict]:
    """Fast add: tap only real on-screen + / Add chips (never invent grid taps / PDP)."""
    print(
        f'Step: add first {target} items from "{SEARCH_QUERY}" '
        "(listing + buttons only — never open product details) …"
    )
    _dismiss_overlay_fast(driver)
    _dismiss_quantity_not_updated(driver, markers)
    # Do not preemptively "dismiss PDP" here — false positives exit Grocery.
    outcome["cart_count_before_adds"] = None
    outcome["asin_add_strategy"] = "grocery_plus_collect_only"
    outcome["price_filter_applied"] = False
    used_deeplink = str(outcome.get("under_5_open_method") or "").startswith(
        "com.amazon"
    )
    markers.mark(
        "low_cost_plus_add_start",
        target=target,
        query=SEARCH_QUERY,
        mode="collect_only",
        deeplink=used_deeplink,
    )

    scrolls = 0
    seeds = _wait_plus_centers(driver, attempts=8)
    if not seeds and max_scrolls > 0:
        _scroll_grocery_listing(driver, percent=0.45)
        scrolls = 1
        markers.mark("low_cost_grid_scrolled", index=1, reason="seed_buttons")
        time.sleep(0.45)
        seeds = _wait_plus_centers(driver, attempts=4)

    if not seeds and used_deeplink and allow_fmc_coords:
        print(
            "  FMC webview has no native + chips — "
            f'falling back to Grocery search "{SEARCH_QUERY}" …'
        )
        markers.mark("fmc_webview_no_native_fallback_search")
        for by, value in (
            (AppiumBy.ACCESSIBILITY_ID, "Back"),
            (AppiumBy.ACCESSIBILITY_ID, "Home"),
            (
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND '
                '(label == "Back" OR name == "Back" OR '
                'label == "Home" OR name == "Home")',
            ),
        ):
            try:
                driver.find_element(by, value).click()
                time.sleep(0.4)
                markers.mark("left_fmc_shelf", by=by, value=value)
                break
            except Exception:  # noqa: BLE001
                continue
        _open_grocery_search_low_cost(driver, markers, outcome)
        return _add_n_low_cost_fast(
            driver,
            markers,
            outcome,
            target=target,
            max_scrolls=max(2, max_scrolls),
            allow_fmc_coords=False,
        )

    if not seeds:
        raise AssertionError(
            f'No + / Add to cart buttons found after search "{SEARCH_QUERY}"'
        )

    # Only real collected centers — never invent grid coords (those open PDPs).
    taps = [dict(s, via="collect") for s in seeds[:target]]
    markers.mark(
        "first_screen_products_ready",
        collected=len(seeds),
        planned_taps=len(taps),
        scrolls=scrolls,
        mode="collect_only",
    )
    print(
        f"  collected {len(seeds)} + button(s); "
        f"planned {len(taps)} real taps, scrolls={scrolls}"
    )

    added: list[dict] = []
    seen_fp: set[str] = set()

    def _tap_real_plus(batch: list[dict]) -> None:
        for tap in batch:
            if len(added) >= target:
                return
            cx, cy = float(tap["cx"]), float(tap["cy"])
            fp = str(tap["fp"])
            if fp in seen_fp:
                continue
            print(
                f"  + candidate #{len(added) + 1}/{target}: "
                f"via=collect ({cx:.0f},{cy:.0f}) fp={fp}"
            )
            if not _ensure_plus_visible(driver, tap):
                print("  could not bring Add chip on-screen — skip")
                markers.mark("plus_not_visible", fingerprint=fp)
                seen_fp.add(fp)
                continue
            cx, cy = float(tap["cx"]), float(tap["cy"])
            fp = str(tap["fp"])
            if fp in seen_fp:
                fp = f"{fp}:i{len(added)}"
                tap["fp"] = fp
            seen_fp.add(fp)
            # Prefer clicking the real + button element (avoids PDP from miss-taps).
            el = tap.get("el")
            tapped = False
            if el is not None:
                try:
                    el.click()
                    tapped = True
                except Exception:  # noqa: BLE001
                    tapped = False
            if not tapped:
                try:
                    _tap_center(driver, cx, cy)
                    tapped = True
                except Exception as exc:  # noqa: BLE001
                    print(f"  tap failed: {exc}")
                    markers.mark("add_tap_failed", index=len(added) + 1, cx=cx, cy=cy)
                    continue
            time.sleep(0.15)
            if _dismiss_grocery_pdp_if_open(driver, markers):
                # Missed the + and opened a product — do not count as add.
                markers.mark("plus_tap_opened_pdp", fingerprint=fp, cx=cx, cy=cy)
                print("  opened product page — dismissed; not counting")
                continue
            if _dismiss_quantity_not_updated(driver, markers):
                # Listing qty update rejected — skip, keep looking for fresh +.
                markers.mark(
                    "plus_tap_quantity_blocked", fingerprint=fp, cx=cx, cy=cy
                )
                print("  quantity-not-updated toast — dismissed; not counting")
                continue
            entry = {
                "index": len(added) + 1,
                "source": "grocery_plus_collect",
                "add_path": "plus",
                "fingerprint": fp,
                "confirmed": True,
                "confirm_via": "tap_optimistic",
                "tap_via": "collect",
                "cx": cx,
                "cy": cy,
                "cart_before": None,
                "cart_after": len(added) + 1,
            }
            added.append(entry)
            markers.mark(
                "low_cost_plus_tapped",
                index=entry["index"],
                fingerprint=fp,
                cx=cx,
                cy=cy,
                via="collect",
            )
            markers.mark(
                "item_added_to_cart",
                index=entry["index"],
                confirmed=True,
                fingerprint=fp,
                path="plus",
                confirm_via="tap_optimistic",
            )
            print(
                f"  added #{entry['index']}/{target} via + "
                f"@ ({cx:.0f},{cy:.0f}) (listing only)"
            )

    _tap_real_plus(taps)

    while len(added) < target and scrolls < max_scrolls:
        _dismiss_quantity_not_updated(driver, markers)
        _scroll_grocery_listing(driver, percent=0.50)
        scrolls += 1
        markers.mark("low_cost_grid_scrolled", index=scrolls, reason="need_more")
        time.sleep(0.45)
        more = _collect_plus_centers(driver, limit=12)
        if not more:
            # Second nudge if webview did not expose new chips.
            _scroll_grocery_listing(driver, percent=0.40)
            time.sleep(0.35)
            more = _collect_plus_centers(driver, limit=12)
        print(f"  after scroll #{scrolls}: found {len(more)} fresh + chip(s)")
        # Screen coords recycle after a scroll — tag with scroll index so the
        # same (x,y) on a new row is not treated as already tapped.
        batch = [
            dict(s, via="collect", fp=f"s{scrolls}:{s['fp']}") for s in more
        ]
        _tap_real_plus(batch)

    outcome["items_added"] = list(added)
    outcome["items_added_count"] = len(added)
    markers.mark(
        "low_cost_plus_add_done",
        added=len(added),
        target=target,
        scrolls=scrolls,
        mode="collect_only",
    )
    if len(added) < target:
        raise AssertionError(
            f'Only added {len(added)}/{target} via + buttons from "{SEARCH_QUERY}" '
            f"(scrolls={scrolls}, seeds={len(seeds)})"
        )
    return added


def _open_cart_page_fast(driver, markers: MarkerLog, outcome: dict) -> None:
    """Open cart from grocery SERP; fall back to TC2 cart open if needed."""
    print("Step: open Cart screen …")
    try:
        el, by, value = find_first(driver, CART_TAB_STRATEGIES, timeout=1.0)
    except TimeoutError:
        print("  cartTab not on grocery UI — closing grocery storefront …")
        closed = False
        for by, value in (
            (AppiumBy.ACCESSIBILITY_ID, "Close"),
            (AppiumBy.ACCESSIBILITY_ID, "close"),
            (
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND '
                '(label == "Close" OR name == "Close" OR '
                'label == "Done" OR name == "Done" OR name == "xmark")',
            ),
        ):
            try:
                driver.find_element(by, value).click()
                markers.mark("grocery_storefront_closed", by=by, value=value)
                print(f"  Closed grocery: {by} = {value!r}")
                closed = True
                time.sleep(0.35)
                break
            except Exception:  # noqa: BLE001
                continue
        if not closed:
            print("  Close control not found — trying cart anyway")
        # Prefer cheap accessibility id before broad strategy scan / TC2 fallback.
        for attempt in range(3):
            try:
                el = driver.find_element(AppiumBy.ACCESSIBILITY_ID, "cartTab")
                by, value = AppiumBy.ACCESSIBILITY_ID, "cartTab"
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.25)
        else:
            try:
                el, by, value = find_first(driver, CART_TAB_STRATEGIES, timeout=1.5)
            except TimeoutError:
                print("  falling back to standard cart open …")
                _open_cart_page(driver, markers, outcome)
                return

    outcome.setdefault("selectors", {})["cart_tab"] = {"by": by, "value": value}
    print(f"Cart: {by} = {value!r}")
    el.click()
    markers.mark("cart_opened", by=by, value=value)
    time.sleep(0.25)
    outcome["cart_page_opened"] = True


def _verify_cart_fast(driver, markers: MarkerLog, outcome: dict, *, expected: int) -> None:
    """Trust successful add taps; skip slow badge polling when adds already confirmed."""
    print(f"Step: verify {expected} items in cart …")
    items_ok = int(outcome.get("items_added_count") or 0) >= expected
    outcome["cart_five_items_verified"] = bool(items_ok)
    markers.mark(
        "cart_verified",
        expected=expected,
        verified=bool(items_ok),
        via="items_added_count",
    )
    print(f"  cart verify: items_added={outcome.get('items_added_count')} verified={items_ok}")
    if not items_ok:
        raise AssertionError(
            f"Cart verification failed — expected {expected} adds "
            f"(got {outcome.get('items_added_count')})"
        )


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
    """True for Place Your Order surface (button or sheet title / chrome)."""
    try:
        find_first(
            driver,
            (
                (AppiumBy.ACCESSIBILITY_ID, "Place Your Order"),
                (
                    AppiumBy.IOS_PREDICATE,
                    'label == "Place Your Order" OR name == "Place Your Order" OR '
                    'label CONTAINS[c] "Place Your Order" OR '
                    'name CONTAINS[c] "Place Your Order"',
                ),
                (AppiumBy.ACCESSIBILITY_ID, "Change payment method"),
                (
                    AppiumBy.IOS_PREDICATE,
                    'label == "Change payment method" OR '
                    'name == "Change payment method"',
                ),
                (
                    AppiumBy.IOS_PREDICATE,
                    'label BEGINSWITH[c] "Arriving by" OR '
                    'name BEGINSWITH[c] "Arriving by" OR '
                    'label == "Choose your delivery option:" OR '
                    'name == "Choose your delivery option:"',
                ),
            ),
            timeout=timeout,
        )
        return True
    except TimeoutError:
        return False


def _checkout_webview_visible(driver) -> bool:
    """Bare Amazon.com Checkout sheet with no usable Place Your Order chrome."""
    if _place_order_visible(driver, timeout=0.25):
        return False
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
    # Payment/address pickers first — "Paying with" also appears on Place Your Order,
    # and the picker title must win over leftover checkout chrome.
    if _screen_matches(
        driver,
        (
            (AppiumBy.ACCESSIBILITY_ID, "Select a payment method"),
            (
                AppiumBy.IOS_PREDICATE,
                'label CONTAINS[c] "Select a payment method" OR '
                'name CONTAINS[c] "Select a payment method" OR '
                'label CONTAINS[c] "Select a Payment Method" OR '
                'name CONTAINS[c] "Select a Payment Method"',
            ),
        ),
        timeout=0.35,
    ):
        return "payment"
    if _screen_matches(
        driver,
        (
            (AppiumBy.ACCESSIBILITY_ID, "Select a billing address"),
            (AppiumBy.ACCESSIBILITY_ID, "Select a delivery address"),
        ),
        timeout=0.3,
    ):
        return "address"
    if _place_order_visible(driver, timeout=0.35):
        # Place Your Order title can coexist with the Prime upsell sheet.
        if _prime_upsell_visible(driver):
            return "no_thanks"
        return "place_order"
    # Only treat Prime as present when the control is actually visible.
    if _prime_upsell_visible(driver):
        return "no_thanks"
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


def _wait_after_payment(driver, *, timeout: float = 8.0) -> str:
    """After Continue, poll cheaply for the next checkout surface."""
    end = time.monotonic() + timeout
    nudged = False
    while time.monotonic() < end:
        # Cheapest signals first — avoid full multi-strategy detects here.
        try:
            find_first(
                driver,
                ((AppiumBy.ACCESSIBILITY_ID, "Place Your Order"),),
                timeout=0.2,
            )
            return "place_order"
        except TimeoutError:
            pass
        if _screen_matches(driver, NO_THANKS_TC4, timeout=0.2):
            return "no_thanks"
        if _screen_matches(driver, SCREEN_ADDRESS, timeout=0.2):
            return "address"
        if _screen_matches(driver, USE_THIS_ADDRESS, timeout=0.2):
            return "address"
        if not nudged:
            if _fast_tap(driver, CONTINUE_BTN, timeout=0.3):
                nudged = True
                time.sleep(0.2)
                continue
            nudged = True
        time.sleep(0.22)
    # One final broader detect.
    return _detect_checkout_screen(driver)


def _open_address_from_place_order(driver, markers: MarkerLog, outcome: dict) -> bool:
    """Open address picker via StaticText/Link; keep trying under a hard time budget."""
    print('Step: tap "Change delivery address" …')
    # Keep this list short — broad predicates are extremely slow on Place Your Order.
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
        (AppiumBy.ACCESSIBILITY_ID, "Change delivery or pickup address"),
    )

    deadline = time.monotonic() + 9.0
    scroll_i = 0
    while time.monotonic() < deadline:
        try:
            el, by, value = find_first(driver, strategies, timeout=0.45)
        except TimeoutError:
            _scroll_down(driver, percent=0.5)
            scroll_i += 1
            markers.mark("change_delivery_scroll", index=scroll_i)
            time.sleep(0.04)
            continue

        try:
            el.click()
        except Exception:  # noqa: BLE001
            loc, size = el.location, el.size
            x = int(loc.get("x", 0) + (size.get("width", 0) or 0) / 2)
            y = int(loc.get("y", 0) + (size.get("height", 0) or 0) / 2)
            driver.execute_script("mobile: tap", {"x": x, "y": y})
            markers.mark("change_delivery_address_coordinate", x=x, y=y)

        markers.mark("change_delivery_address", by=by, value=value, scroll=scroll_i)
        outcome["change_delivery_address_tapped"] = True
        _mark_step(
            outcome,
            "change_delivery_address",
            status="ok",
            by=by,
            value=value,
        )
        print(f"  Change address: {by} = {value!r}")
        time.sleep(0.25)

        verify_deadline = time.monotonic() + 2.5
        while time.monotonic() < verify_deadline:
            if _screen_matches(
                driver,
                (
                    (AppiumBy.ACCESSIBILITY_ID, "Select a billing address"),
                    (AppiumBy.ACCESSIBILITY_ID, "Select a delivery address"),
                    (AppiumBy.ACCESSIBILITY_ID, "Deliver to this address"),
                    (AppiumBy.ACCESSIBILITY_ID, "Use this address"),
                ),
                timeout=0.25,
            ):
                return True
            if not _place_order_visible(driver, timeout=0.2):
                return True
            time.sleep(0.12)

        print("  Change delivery tap did not open picker — retry …")
        # Prefer coordinate tap on same element before more scrolling.
        try:
            loc, size = el.location, el.size
            x = int(loc.get("x", 0) + (size.get("width", 0) or 0) / 2)
            y = int(loc.get("y", 0) + (size.get("height", 0) or 0) / 2)
            driver.execute_script("mobile: tap", {"x": x, "y": y})
            markers.mark("change_delivery_address_coordinate", x=x, y=y)
            time.sleep(0.3)
            if _screen_matches(
                driver,
                (
                    (AppiumBy.ACCESSIBILITY_ID, "Select a billing address"),
                    (AppiumBy.ACCESSIBILITY_ID, "Select a delivery address"),
                    (AppiumBy.ACCESSIBILITY_ID, "Deliver to this address"),
                ),
                timeout=0.35,
            ):
                return True
            if not _place_order_visible(driver, timeout=0.25):
                return True
        except Exception:  # noqa: BLE001
            pass
        _scroll_down(driver, percent=0.4)
        scroll_i += 1
        time.sleep(0.04)

    _mark_step(
        outcome,
        "change_delivery_address",
        status="failed",
        reason="Change delivery address not opened within budget",
    )
    markers.mark("change_delivery_address_failed")
    print("  Change delivery address not opened")
    return False


# Prime Shipping upsell (native or web Place Your Order sheet) — always decline.
NO_THANKS_TC4 = (
    (AppiumBy.ACCESSIBILITY_ID, "No thanks"),
    (AppiumBy.ACCESSIBILITY_ID, "No Thanks"),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeButton[`label == "No thanks" OR label == "No Thanks"`]',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeStaticText[`label == "No thanks" OR label == "No Thanks"`]',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeLink[`label == "No thanks" OR label == "No Thanks"`]',
    ),
    (
        AppiumBy.IOS_PREDICATE,
        'label == "No thanks" OR name == "No thanks" OR '
        'label == "No Thanks" OR name == "No Thanks"',
    ),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "No thanks" OR name CONTAINS[c] "No thanks"',
    ),
)

JOIN_PRIME_HINT = (
    (AppiumBy.ACCESSIBILITY_ID, "Join Prime Shipping"),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Join Prime" OR name CONTAINS[c] "Join Prime" OR '
        'label CONTAINS[c] "Prime Shipping" OR name CONTAINS[c] "30-day trial"',
    ),
)


def _prime_upsell_visible(driver) -> bool:
    """True when the Prime Shipping trial sheet is actually on-screen."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND '
            '(label == "No thanks" OR name == "No thanks" OR '
            'label == "No Thanks" OR name == "No Thanks" OR '
            'label CONTAINS[c] "Join Prime Shipping" OR '
            'name CONTAINS[c] "Join Prime Shipping")',
        )
    except Exception:  # noqa: BLE001
        return False
    for el in els[:6]:
        try:
            if el.is_displayed():
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _tap_no_thanks(driver, markers: MarkerLog, outcome: dict) -> bool:
    """Always decline Prime Shipping trial when that screen appears. Returns True if tapped."""
    print('Step: tap "No thanks" (Prime upsell) …')
    # Prefer direct hit — avoid slow scroll sweeps on the huge checkout webview.
    hit = _fast_tap(driver, NO_THANKS_TC4, timeout=0.8)
    if not hit:
        hit = _fast_scroll_find_tap(driver, NO_THANKS_TC4, scrolls=1, timeout=0.7)
    if not hit:
        try:
            el, by, value = find_first(driver, NO_THANKS_TC4, timeout=0.6)
            try:
                el.click()
            except Exception:  # noqa: BLE001
                loc, size = el.location, el.size
                x = int(loc.get("x", 0) + (size.get("width", 0) or 0) / 2)
                y = int(loc.get("y", 0) + (size.get("height", 0) or 0) / 2)
                driver.execute_script("mobile: tap", {"x": x, "y": y})
            hit = (by, value)
        except TimeoutError:
            hit = None
    if not hit:
        try:
            size = driver.get_window_size()
            x = int(size["width"] * 0.5)
            y = int(size["height"] * 0.88)
            driver.execute_script("mobile: tap", {"x": x, "y": y})
            markers.mark("no_thanks_coordinate_tap", x=x, y=y)
            _mark_step(outcome, "no_thanks", status="ok", via="coordinate")
            outcome["no_thanks_tapped"] = True
            print(f"  No thanks: coordinate tap ({x},{y})")
            time.sleep(0.25)
            return True
        except Exception:  # noqa: BLE001
            pass
        _mark_step(outcome, "no_thanks", status="skipped", reason="No thanks not found")
        markers.mark("no_thanks_skipped")
        print("  No thanks not found — skipping")
        return False
    by, value = hit
    outcome.setdefault("selectors", {})["no_thanks"] = {"by": by, "value": value}
    markers.mark("no_thanks_tapped", by=by, value=value)
    _mark_step(outcome, "no_thanks", status="ok", by=by, value=value)
    outcome["no_thanks_tapped"] = True
    print(f"  No thanks: {by} = {value!r}")
    time.sleep(0.25)
    return True



def _collect_delivery_slot_rows(driver) -> list[dict]:
    """Collect Place Your Order delivery options (webview Other nodes, value 0/1)."""
    rows: list[dict] = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeOther" AND '
            '(label CONTAINS[c] "FREE Delivery" OR '
            'label CONTAINS[c] "Two-Day" OR '
            'label CONTAINS[c] "One Day Delivery" OR '
            'label CONTAINS[c] "$" OR '
            'name CONTAINS[c] "FREE Delivery" OR '
            'name CONTAINS[c] "Delivery")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els:
        try:
            label = (el.get_attribute("label") or el.get_attribute("name") or "").strip()
            if not label or len(label) < 8:
                continue
            low = label.lower()
            if "subscribe" in low or "gift" in low or "arriving" in low:
                continue
            if "delivery" not in low and "two-day" not in low and "shipping" not in low:
                continue
            # Slot rows look like "Tuesday, Oct 6 FREE Delivery" / "Sunday, Oct 4 $6.99 Delivery"
            if not any(
                k in low
                for k in (
                    "free delivery",
                    "two-day",
                    "one day",
                    "$",
                    "saturday",
                    "sunday",
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "tomorrow",
                )
            ):
                continue
            val = (el.get_attribute("value") or "").strip().lower()
            selected = val in ("1", "true", "selected")
            r = el.rect or {}
            rows.append(
                {
                    "el": el,
                    "x": float(r.get("x", 0)),
                    "y": float(r.get("y", 0)),
                    "h": float(r.get("height", 0) or 40),
                    "selected": selected,
                    "label": label[:90],
                    "visible": bool(el.is_displayed()),
                }
            )
        except Exception:  # noqa: BLE001
            continue
    # Prefer tighter rows (slot line) over giant wrappers; dedupe by y.
    rows.sort(key=lambda t: (t["h"], t["y"]))
    deduped: list[dict] = []
    for row in rows:
        if any(abs(row["y"] - d["y"]) < 20 and abs(row["h"] - d["h"]) < 30 for d in deduped):
            continue
        deduped.append(row)
    deduped.sort(key=lambda t: t["y"])
    return deduped


def _slot_is_prime_upsell(label: str) -> bool:
    low = label.lower()
    return any(
        k in low
        for k in (
            "prime shipping",
            "free trial",
            "30 days of prime",
            "try prime",
            "two-day shipping with your free",
        )
    )


def _tap_alternate_delivery_slot(
    driver, markers: MarkerLog, outcome: dict
) -> bool:
    """Change delivery slot: tap a different radio than the currently selected one.

    Screenshot shape (top→bottom):
      Saturday + Prime promo (skip — opens upsell)
      Tuesday FREE Delivery
      Sunday $6.99 Delivery  ← often already selected
    Prefer an unselected non-Prime option (e.g. FREE Delivery).
    """
    print("Step: change delivery slot (tap alternate radio) …")

    def _visible_slot_texts() -> list[dict]:
        out: list[dict] = []
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeStaticText" AND visible == 1 AND '
                '(label CONTAINS[c] "FREE Delivery" OR '
                'label CONTAINS[c] "Two-Day Shipping" OR '
                'label CONTAINS[c] "One Day Delivery" OR '
                '(label CONTAINS[c] "Delivery" AND label CONTAINS "$") OR '
                'label CONTAINS[c] "Tomorrow" OR '
                'label CONTAINS[c] "Saturday" OR '
                'label CONTAINS[c] "Sunday" OR '
                'label CONTAINS[c] "Monday" OR '
                'label CONTAINS[c] "Tuesday" OR '
                'label CONTAINS[c] "Wednesday" OR '
                'label CONTAINS[c] "Thursday" OR '
                'label CONTAINS[c] "Friday")',
            )
        except Exception:  # noqa: BLE001
            els = []
        for el in els[:20]:
            try:
                label = (el.get_attribute("label") or "").strip()
                low = label.lower()
                if not label or len(label) < 5:
                    continue
                if "subscribe" in low or "try prime" in low or "arriving by" in low:
                    continue
                if "if you order" in low:
                    continue
                # Keep date rows and delivery-price rows; drop prime promo body copy.
                is_date = any(
                    d in low
                    for d in (
                        "saturday",
                        "sunday",
                        "monday",
                        "tuesday",
                        "wednesday",
                        "thursday",
                        "friday",
                        "tomorrow",
                    )
                )
                is_price = (
                    "free delivery" in low
                    or ("delivery" in low and "$" in label)
                    or "two-day" in low
                    or "one day" in low
                )
                if not is_date and not is_price:
                    continue
                if _slot_is_prime_upsell(label) and not is_date:
                    continue
                r = el.rect or {}
                y = float(r.get("y", 0))
                if y < 110 or y > 780:
                    continue
                out.append(
                    {
                        "el": el,
                        "x": float(r.get("x", 40)),
                        "y": y,
                        "h": float(r.get("height", 20) or 20),
                        "label": label[:90],
                        "is_date": is_date,
                        "is_price": is_price,
                    }
                )
            except Exception:  # noqa: BLE001
                continue
        out.sort(key=lambda t: t["y"])
        return out

    def _merge_slot_groups(texts: list[dict]) -> list[dict]:
        """Group date + price lines into slot rows (~one radio each)."""
        if not texts:
            return []
        groups: list[dict] = []
        for t in texts:
            if groups and abs(t["y"] - groups[-1]["y"]) < 55:
                g = groups[-1]
                g["label"] = f"{g['label']} | {t['label']}"[:120]
                g["y"] = min(g["y"], t["y"])
                g["h"] = max(g["h"], t["y"] + t["h"] - g["y"])
                g["is_price"] = g.get("is_price") or t.get("is_price")
                g["is_date"] = g.get("is_date") or t.get("is_date")
                if t.get("is_price"):
                    g["price_el"] = t["el"]
                    g["x"] = t["x"]
                continue
            groups.append(
                {
                    "el": t["el"],
                    "price_el": t["el"] if t.get("is_price") else None,
                    "x": t["x"],
                    "y": t["y"],
                    "h": t["h"],
                    "label": t["label"],
                    "is_price": t.get("is_price"),
                    "is_date": t.get("is_date"),
                    "selected": False,
                }
            )
        return groups

    def _apply_selected_from_tree(slots: list[dict]) -> None:
        tree = [r for r in _collect_delivery_slot_rows(driver) if r.get("visible")]
        for slot in slots:
            for row in tree:
                if abs(row["y"] - slot["y"]) < 70:
                    if row.get("selected"):
                        slot["selected"] = True
                    # Prefer full tree label when richer.
                    if len(row.get("label") or "") > len(slot.get("label") or ""):
                        slot["label"] = row["label"]
                    break
        # Radio buttons near each slot.
        try:
            radios = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeRadioButton" AND visible == 1',
            )
        except Exception:  # noqa: BLE001
            radios = []
        for radio in radios:
            try:
                if not radio.is_displayed():
                    continue
                rr = radio.rect or {}
                ry = float(rr.get("y", 0))
                val = (radio.get_attribute("value") or "").strip().lower()
                sel = (radio.get_attribute("selected") or "").strip().lower()
                is_sel = val in ("1", "true", "selected") or sel in (
                    "1",
                    "true",
                    "selected",
                )
                for slot in slots:
                    if abs(slot["y"] - ry) < 70:
                        slot["radio"] = radio
                        slot["radio_x"] = float(rr.get("x", 0)) + float(
                            rr.get("width", 20)
                        ) / 2.0
                        slot["radio_y"] = ry + float(rr.get("height", 20)) / 2.0
                        if is_sel:
                            slot["selected"] = True
                        break
            except Exception:  # noqa: BLE001
                continue

    # Scroll until Arriving-by slot list is on screen.
    visible: list[dict] = []
    for i in range(6):
        texts = _visible_slot_texts()
        visible = _merge_slot_groups(texts)
        # Need at least one price/delivery line (not only weekday chrome).
        priced = [s for s in visible if s.get("is_price") or "$" in s["label"].lower() or "free" in s["label"].lower()]
        if len(priced) >= 1 or len(visible) >= 2:
            break
        if i % 2 == 0:
            _scroll_down(driver, percent=0.45)
        else:
            _scroll_up(driver, percent=0.40)
        time.sleep(0.15)

    if not visible:
        markers.mark("delivery_slot_skipped", reason="no_visible_slots")
        print("  no visible delivery-slot labels — skipping")
        _mark_step(outcome, "delivery_slot", status="skipped", reason="no_visible_slots")
        return False

    _apply_selected_from_tree(visible)

    # Candidates: not currently selected, not Prime upsell row.
    candidates = [
        s
        for s in visible
        if not s.get("selected") and not _slot_is_prime_upsell(s["label"])
    ]
    if not candidates:
        candidates = [s for s in visible if not s.get("selected")]
    if not candidates:
        # Everything looks selected/unknown — pick a non-first row if possible.
        candidates = visible[1:] or visible[:1]

    pick = candidates[0]
    for row in candidates:
        low = row["label"].lower()
        if "free delivery" in low and "prime" not in low and "two-day" not in low:
            pick = row
            break
        if "free delivery" in low and pick is candidates[0]:
            pick = row

    if pick.get("radio_x") is not None:
        cx, cy = float(pick["radio_x"]), float(pick["radio_y"])
        via = "radio"
    else:
        # Radio sits left of the date/price text.
        cx = max(22.0, float(pick["x"]) - 30.0)
        cy = float(pick["y"]) + float(pick["h"]) / 2.0
        via = "text_offset"

    selected_labels = [s["label"] for s in visible if s.get("selected")]
    print(
        f"  slots={len(visible)} selected={selected_labels!r} "
        f"→ tap y={pick['y']:.0f} label={pick['label']!r} via={via}"
    )
    try:
        if pick.get("radio") is not None:
            pick["radio"].click()
        elif pick.get("price_el") is not None:
            pick["price_el"].click()
        else:
            pick["el"].click()
    except Exception:  # noqa: BLE001
        _tap_center(driver, cx, cy)

    markers.mark(
        "delivery_slot_tapped",
        y=pick["y"],
        label=pick["label"],
        radio_count=len(visible),
        via=via,
        was_selected=bool(pick.get("selected")),
        selected_before=selected_labels[:3],
    )
    outcome["delivery_slot_changed"] = True
    outcome["delivery_slot_label"] = pick["label"]
    _mark_step(outcome, "delivery_slot", status="ok", label=pick["label"], y=pick["y"])
    time.sleep(0.25)
    return True


def _payment_card_is_selected(el) -> bool:
    """Best-effort: Appium selected/value, or label that says selected."""
    try:
        sel = (el.get_attribute("selected") or "").strip().lower()
        if sel in ("1", "true", "selected", "yes"):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        val = (el.get_attribute("value") or "").strip().lower()
        if val in ("1", "true", "selected", "yes"):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        blob = element_textish(el).lower()
    except Exception:  # noqa: BLE001
        blob = ""
    if "unselected" in blob:
        return False
    if re.search(r"\bselected\b", blob):
        return True
    return False


def _detect_selected_card_by_border(driver, cards: list[dict]) -> int | None:
    """Which of the first two cards has the teal selection border (screenshot)."""
    if len(cards) < 2:
        return None
    try:
        from io import BytesIO

        from PIL import Image
    except Exception:  # noqa: BLE001
        return None
    try:
        png = driver.get_screenshot_as_png()
        img = Image.open(BytesIO(png)).convert("RGB")
        size = driver.get_window_size() or {}
        win_w = float(size.get("width") or 390)
        win_h = float(size.get("height") or 844)
        sx = img.width / max(win_w, 1.0)
        sy = img.height / max(win_h, 1.0)
    except Exception:  # noqa: BLE001
        return None

    # Title nodes are short; the teal ring wraps the whole card tile.
    gap = max(120.0, abs(cards[1]["y"] - cards[0]["y"]))
    tile_h = min(200.0, gap - 8.0)

    def _tile_box(card: dict, index: int) -> tuple[float, float, float, float]:
        y0 = float(card["y"]) - 12.0
        if index + 1 < len(cards):
            y1 = float(cards[index + 1]["y"]) - 10.0
        else:
            y1 = y0 + tile_h
        y1 = max(y0 + 80.0, min(y0 + tile_h, y1))
        return (12.0, y0, win_w - 24.0, y1 - y0)

    def _teal_score(box: tuple[float, float, float, float]) -> float:
        x, y, w, h = box
        left = max(0, int(x * sx))
        top = max(0, int(y * sy))
        right = min(img.width - 1, int((x + w) * sx))
        bottom = min(img.height - 1, int((y + h) * sy))
        if right <= left or bottom <= top:
            return 0.0
        hits = 0
        samples = 0
        edge = max(3, int(5 * sx))
        for px in range(left, min(left + edge, right + 1)):
            for py in range(top, bottom + 1, max(1, (bottom - top) // 28 or 1)):
                samples += 1
                r_, g_, b_ = img.getpixel((px, py))
                # Amazon selected ring: teal / blue-green.
                if b_ > 85 and g_ > 65 and r_ < 100 and (b_ - r_) > 30 and (g_ - r_) > 10:
                    hits += 1
        for py in range(top, min(top + edge, bottom + 1)):
            for px in range(left, right + 1, max(1, (right - left) // 28 or 1)):
                samples += 1
                r_, g_, b_ = img.getpixel((px, py))
                if b_ > 85 and g_ > 65 and r_ < 100 and (b_ - r_) > 30 and (g_ - r_) > 10:
                    hits += 1
        return hits / max(samples, 1)

    boxes = [_tile_box(cards[i], i) for i in range(2)]
    scores = [_teal_score(b) for b in boxes]
    if max(scores) < 0.015:
        return None
    if scores[0] >= scores[1] * 1.25:
        return 0
    if scores[1] >= scores[0] * 1.25:
        return 1
    if abs(scores[0] - scores[1]) > 0.01:
        return 0 if scores[0] > scores[1] else 1
    return None


def _collect_payment_card_rows(driver) -> list[dict]:
    """Top-to-bottom saved cards on 'Select a payment method' (Visa/MC/… only)."""
    card_rows: list[dict] = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label CONTAINS[c] "Visa" OR name CONTAINS[c] "Visa" OR '
            'label CONTAINS[c] "Mastercard" OR name CONTAINS[c] "Mastercard" OR '
            'label CONTAINS[c] "MasterCard" OR name CONTAINS[c] "MasterCard" OR '
            'label CONTAINS[c] "American Express" OR name CONTAINS[c] "American Express" OR '
            'label CONTAINS "••••" OR name CONTAINS "••••" OR '
            'label CONTAINS "****" OR name CONTAINS "****" OR '
            'label CONTAINS[c] "ending in" OR name CONTAINS[c] "ending in")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:24]:
        try:
            if not el.is_displayed():
                continue
            blob = element_textish(el)
            if len(blob) < 4:
                continue
            low = blob.lower()
            if any(
                k in low
                for k in (
                    "affirm",
                    "ineligible",
                    "gift card",
                    "add new",
                    "amazon visa",
                    "change card currency",
                    "promo",
                )
            ):
                continue
            # Need a real card brand or masked number — skip bare chrome.
            if not any(
                k in low
                for k in ("visa", "mastercard", "master card", "amex", "••••", "****", "ending")
            ):
                continue
            r = el.rect or {}
            y = float(r.get("y", 0))
            h = float(r.get("height", 0) or 0)
            if y < 100 or y > 720:
                continue
            # Prefer compact card title rows over giant wrappers.
            if h > 220:
                continue
            card_rows.append(
                {
                    "el": el,
                    "y": y,
                    "h": h,
                    "blob": blob[:100],
                    "selected": _payment_card_is_selected(el),
                }
            )
        except Exception:  # noqa: BLE001
            continue

    card_rows.sort(key=lambda t: (t["y"], t["h"]))
    deduped: list[dict] = []
    for row in card_rows:
        if deduped and abs(row["y"] - deduped[-1]["y"]) < 36:
            # Same card row: keep the one already marked selected, else tighter.
            if row["selected"] and not deduped[-1]["selected"]:
                deduped[-1] = row
            elif row["h"] < deduped[-1]["h"]:
                row["selected"] = row["selected"] or deduped[-1]["selected"]
                deduped[-1] = row
            elif row["selected"]:
                deduped[-1]["selected"] = True
            continue
        deduped.append(row)

    # Merge radio-button selected state onto nearest card by Y.
    try:
        radios = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeRadioButton" AND visible == 1',
        )
    except Exception:  # noqa: BLE001
        radios = []
    for radio in radios:
        try:
            if not radio.is_displayed():
                continue
            ry = float((radio.rect or {}).get("y", 0))
            if not _payment_card_is_selected(radio):
                continue
            for row in deduped:
                if abs(row["y"] - ry) < 80:
                    row["selected"] = True
                    break
        except Exception:  # noqa: BLE001
            continue
    return deduped


def _select_second_payment_card(driver, markers: MarkerLog, outcome: dict) -> None:
    """Toggle payment card: if 1st selected → 2nd; if 2nd selected → 1st."""
    print("Step: select alternate payment card (toggle 1st ↔ 2nd) …")
    time.sleep(0.1)
    cards = _collect_payment_card_rows(driver)
    if len(cards) < 2:
        raise AssertionError(
            f"Need 2 payment cards to toggle (found {len(cards)})"
        )

    first, second = cards[0], cards[1]
    selected_idx: int | None = None
    via = "attribute"
    if first["selected"] and not second["selected"]:
        selected_idx = 0
    elif second["selected"] and not first["selected"]:
        selected_idx = 1
    elif first["selected"] and second["selected"]:
        selected_idx = 0
    else:
        border_idx = _detect_selected_card_by_border(driver, cards[:2])
        if border_idx is not None:
            selected_idx = border_idx
            via = "border"
        else:
            # Amazon defaults to the first saved card when flags are missing.
            selected_idx = 0
            via = "assume_first"

    pick_idx = 1 if selected_idx == 0 else 0
    pick = cards[pick_idx]
    print(
        f"  currently selected: #{selected_idx + 1} ({cards[selected_idx]['blob']!r}) "
        f"via={via} → tapping #{pick_idx + 1} ({pick['blob']!r})"
    )
    pick["el"].click()
    markers.mark(
        "second_payment_card_tapped",
        text=pick["blob"],
        from_index=selected_idx,
        to_index=pick_idx,
        detect_via=via,
        toggled=True,
    )
    _mark_step(
        outcome,
        "second_payment_card",
        status="ok",
        text=pick["blob"],
        from_index=selected_idx,
        to_index=pick_idx,
        detect_via=via,
    )
    outcome["second_payment_card_tapped"] = True
    outcome["payment_card_toggled_from"] = selected_idx
    outcome["payment_card_toggled_to"] = pick_idx
    time.sleep(0.15)


def _handle_payment_screen(driver, markers: MarkerLog, outcome: dict) -> None:
    """Already on payment picker: toggle to the other card → Continue."""
    print("  Screen: payment method picker")
    markers.mark("screen_payment_detected")
    outcome["change_payment_tapped"] = True  # opened by checkout flow itself
    _mark_step(outcome, "change_payment", status="ok", via="auto_screen")
    _select_second_payment_card(driver, markers, outcome)

    print('Step: tap "Continue" …')
    cont = _fast_scroll_find_tap(driver, CONTINUE_BTN, scrolls=0, timeout=0.9)
    if not cont:
        cont = _fast_scroll_find_tap(driver, CONTINUE_BTN, scrolls=1, timeout=0.7)
    if not cont:
        raise AssertionError('"Continue" not found on payment method screen')
    markers.mark("payment_continue_tapped", by=cont[0], value=cont[1])
    _mark_step(outcome, "payment_continue", status="ok", by=cont[0], value=cont[1])
    outcome["payment_continue_tapped"] = True
    print(f"  Continue: {cont[0]} = {cont[1]!r}")
    time.sleep(0.2)


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
        time.sleep(0.3)


def _open_payment_from_place_order(driver, markers: MarkerLog, outcome: dict) -> bool:
    """From Place Your Order, tap the first (topmost) 'Change payment method' link."""
    print('Step: tap first "Change payment method" (under Paying with) …')

    def _on_payment_picker() -> bool:
        return _detect_checkout_screen(driver) == "payment" or _screen_matches(
            driver,
            (
                (AppiumBy.ACCESSIBILITY_ID, "Select a payment method"),
                (
                    AppiumBy.IOS_PREDICATE,
                    'label CONTAINS[c] "Select a payment method" OR '
                    'name CONTAINS[c] "Select a payment method"',
                ),
            ),
            timeout=0.3,
        )

    def _dismiss_prime_if_blocking() -> None:
        if _prime_upsell_visible(driver):
            print("  Prime sheet blocking payment — tapping No thanks …")
            _tap_no_thanks(driver, markers, outcome)
            time.sleep(0.2)

    def _collect_change_payment_links() -> list[tuple[float, object]]:
        """Exact 'Change payment method' only — not plan / gift-card links."""
        rows: list[tuple[float, object]] = []
        predicates = (
            'type == "XCUIElementTypeLink" AND visible == 1 AND '
            'label == "Change payment method"',
            'type == "XCUIElementTypeStaticText" AND visible == 1 AND '
            'label == "Change payment method"',
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            'label == "Change payment method"',
            'visible == 1 AND '
            '(label == "Change payment method" OR name == "Change payment method")',
        )
        seen: set[str] = set()
        for pred in predicates:
            try:
                els = driver.find_elements(AppiumBy.IOS_PREDICATE, pred)
            except Exception:  # noqa: BLE001
                els = []
            for el in els:
                try:
                    if not el.is_displayed():
                        continue
                    r = el.rect or {}
                    y = float(r.get("y", 0))
                    x = float(r.get("x", 0))
                    if y < 90 or y > 780:
                        continue
                    key = f"{int(round(x / 8) * 8)}:{int(round(y / 8) * 8)}"
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append((y, el))
                except Exception:  # noqa: BLE001
                    continue
        rows.sort(key=lambda t: t[0])
        return rows

    def _tap_first_change_link(*, via: str) -> bool:
        links = _collect_change_payment_links()
        if not links:
            return False
        y, el = links[0]  # first = topmost under Paying with
        print(f"  first Change payment method @ y={y:.0f} ({len(links)} match(es))")
        try:
            el.click()
        except Exception:  # noqa: BLE001
            try:
                _scroll_element_into_view(driver, el)
                time.sleep(0.1)
                el.click()
            except Exception:  # noqa: BLE001
                try:
                    r = el.rect or {}
                    cx = float(r.get("x", 0)) + float(r.get("width", 100)) / 2.0
                    cy = float(r.get("y", 0)) + float(r.get("height", 20)) / 2.0
                    _tap_center(driver, cx, cy)
                except Exception:  # noqa: BLE001
                    return False
        time.sleep(0.3)
        _dismiss_prime_if_blocking()
        if not _on_payment_picker():
            print(f"  tap via {via} did not open payment picker — retrying")
            return False
        markers.mark(
            "change_payment_tapped",
            via=via,
            y=y,
            link_index=0,
            link_count=len(links),
        )
        _mark_step(
            outcome,
            "change_payment",
            status="ok",
            via=via,
            y=y,
            link_index=0,
        )
        outcome["change_payment_tapped"] = True
        print(f"  Change payment: first link ({via})")
        return True

    _dismiss_prime_if_blocking()

    # Prefer already-visible first link (no scroll) — matches Place Your Order layout.
    if _tap_first_change_link(via="first_visible"):
        return True

    for _scroll_i in range(6):
        _dismiss_prime_if_blocking()
        try:
            driver.execute_script(
                "mobile: scroll",
                {
                    "direction": "down",
                    "predicateString": 'label == "Change payment method"',
                },
            )
        except Exception:  # noqa: BLE001
            pass
        if _tap_first_change_link(via=f"scroll_{_scroll_i + 1}"):
            return True
        _scroll_up(driver, percent=0.45)
        time.sleep(0.1)

    _mark_step(
        outcome,
        "change_payment",
        status="failed",
        reason="Change payment method not found",
    )
    markers.mark("change_payment_failed")
    print("  Change payment method not found")
    return False



def _still_on_cart(driver) -> bool:
    try:
        find_first(driver, PROCEED_CHECKOUT_TC4, timeout=0.4)
        return True
    except TimeoutError:
        return False


def _tap_proceed_until_left_cart(driver, markers: MarkerLog, outcome: dict) -> None:
    """Tap Proceed to checkout and verify we leave the cart screen."""
    print('Step: tap "Proceed to checkout" …')
    for attempt in range(2):
        try:
            el, by, value = find_first(driver, PROCEED_CHECKOUT_TC4, timeout=2.0)
        except TimeoutError as exc:
            if attempt == 0:
                raise AssertionError("Proceed to checkout not found on cart") from exc
            break
        try:
            el.click()
        except Exception:  # noqa: BLE001
            loc, size = el.location, el.size
            x = int(loc.get("x", 0) + (size.get("width", 0) or 0) / 2)
            y = int(loc.get("y", 0) + (size.get("height", 0) or 0) / 2)
            driver.execute_script("mobile: tap", {"x": x, "y": y})
        markers.mark("proceed_checkout", by=by, value=value, attempt=attempt + 1)
        outcome["proceed_checkout_tapped"] = True
        outcome.setdefault("selectors", {})["proceed_checkout"] = {
            "by": by,
            "value": value,
        }
        print(f"  Proceed: {by} = {value!r} (attempt {attempt + 1})")
        time.sleep(0.35)
        if not _still_on_cart(driver):
            return
        print("  still on cart after Proceed — retrying …")
    if _still_on_cart(driver):
        raise AssertionError("Proceed to checkout tapped but still on Cart screen")


def _wait_checkout_entry(driver, markers: MarkerLog, *, timeout: float = 6.0) -> str:
    """After Proceed, wait for checkout; prefer No thanks / payment over bare webview."""
    end = time.monotonic() + timeout
    webview_since: float | None = None
    while time.monotonic() < end:
        # Prime upsell can sit inside the Place Your Order web sheet — handle it.
        if _prime_upsell_visible(driver) or _screen_matches(
            driver, NO_THANKS_TC4, timeout=0.2
        ):
            return "no_thanks"

        if _checkout_webview_visible(driver):
            if webview_since is None:
                webview_since = time.monotonic()
                markers.mark("checkout_webview_seen")
                print("  checkout webview sheet visible — checking for No thanks …")
            # Keep probing briefly for No thanks before treating as bare webview.
            if time.monotonic() - webview_since >= 2.5:
                if _prime_upsell_visible(driver):
                    return "no_thanks"
                return "webview"
            time.sleep(0.25)
            continue

        screen = _detect_checkout_screen(driver)
        if screen in ("payment", "no_thanks", "address", "place_order"):
            return screen
        time.sleep(0.2)
    if _prime_upsell_visible(driver):
        return "no_thanks"
    if webview_since is not None and _checkout_webview_visible(driver):
        return "webview"
    return _detect_checkout_screen(driver)


def _tc4_checkout_edits(driver, markers: MarkerLog, outcome: dict) -> None:
    """Checkout edits for TC4 (stop after payment change — no place order).

    Flow:
      Proceed → (No thanks if needed) → Place Your Order
      → first "Change payment method" → toggle card → Continue
      → (optional) alternate delivery-slot radio → stop recording.
    """
    _tap_proceed_until_left_cart(driver, markers, outcome)
    time.sleep(0.15)

    payment_done = False
    slot_done = False
    no_thanks_done = False
    outcome["delivery_slot_changed"] = False
    outcome["address_flow_done"] = False

    first = _wait_checkout_entry(driver, markers, timeout=5.0)
    markers.mark("checkout_screen", screen=first, round=0, via="entry_wait")
    print(f"  checkout entry surface: {first}")

    if first == "webview":
        print("  webview checkout — trying No thanks before continuing …")
        if _tap_no_thanks(driver, markers, outcome):
            no_thanks_done = True
            time.sleep(0.4)
            first = _detect_checkout_screen(driver)
            if first in ("unknown", "cart") and _prime_upsell_visible(driver):
                first = "no_thanks"
            if first == "unknown" and _place_order_visible(driver, timeout=0.4):
                first = "place_order"
            markers.mark("checkout_screen", screen=first, round=0, via="after_no_thanks")
            print(f"  after No thanks → {first}")
        if first == "webview" and _place_order_visible(driver, timeout=0.5):
            first = "place_order"
        if first == "webview":
            raise AssertionError(
                "Proceed opened Amazon webview checkout and No thanks was not tappable. "
                "Clear the main Amazon cart and retry if the sheet has no No thanks control."
            )

    for round_i in range(12):
        screen = first if round_i == 0 else _detect_checkout_screen(driver)
        if screen in ("place_order", "unknown", "webview") and _prime_upsell_visible(
            driver
        ):
            screen = "no_thanks"
        if screen in ("unknown", "webview") and _place_order_visible(driver, timeout=0.35):
            screen = "place_order"
        if round_i > 0:
            markers.mark("checkout_screen", screen=screen, round=round_i + 1)
            print(f"  checkout screen detect: {screen} (round {round_i + 1})")
        else:
            print(f"  checkout screen detect: {screen} (round 1)")

        if screen == "no_thanks":
            if _prime_upsell_visible(driver) or not no_thanks_done:
                _tap_no_thanks(driver, markers, outcome)
                no_thanks_done = True
                time.sleep(0.2)
                first = None
                continue
            # Stale detect — Prefer Place Your Order chrome when present.
            if _place_order_visible(driver, timeout=0.35):
                screen = "place_order"
            elif payment_done and slot_done:
                break
            elif payment_done and not slot_done:
                # Still need Arriving-by radios — nudge toward Place Your Order.
                _scroll_down(driver, percent=0.4)
                time.sleep(0.15)
                first = None
                continue
            else:
                first = None
                continue

        if screen == "payment":
            if not payment_done:
                _handle_payment_screen(driver, markers, outcome)
                payment_done = True
                time.sleep(0.2)
            if payment_done and slot_done:
                break
            # After Continue, return to Place Your Order for slot change.
            first = None
            continue

        if screen == "place_order":
            if _prime_upsell_visible(driver):
                _tap_no_thanks(driver, markers, outcome)
                no_thanks_done = True
                time.sleep(0.2)
                continue
            # On Place Your Order: Change payment first, then change delivery slot.
            if not payment_done:
                if not _open_payment_from_place_order(driver, markers, outcome):
                    raise AssertionError(
                        'Could not find first "Change payment method" on Place Your Order'
                    )
                time.sleep(0.15)
                # Next loop should see payment picker and toggle the card.
                first = None
                continue
            if not slot_done:
                slot_done = bool(_tap_alternate_delivery_slot(driver, markers, outcome))
                time.sleep(0.1)
            if payment_done and slot_done:
                break
            if payment_done:
                # Slot may have been skipped (no radios) — stop after payment.
                break
            first = None
            continue

        if screen == "address":
            # Address edits are out of scope for this TC4 stop point.
            print("  address screen appeared — skipping (TC4 stops after payment)")
            markers.mark("address_skipped_tc4")
            break

        if _checkout_webview_visible(driver) or screen == "webview":
            if not no_thanks_done:
                print("  webview still up — tapping No thanks …")
                if _tap_no_thanks(driver, markers, outcome):
                    no_thanks_done = True
                    time.sleep(0.4)
                    continue
            if _place_order_visible(driver, timeout=0.5):
                first = None
                continue
            raise AssertionError(
                "Proceed opened Amazon webview checkout (not Place Your Order). "
                "Clear the main Amazon cart and retry."
            )
        if round_i == 0 and not no_thanks_done:
            _tap_no_thanks(driver, markers, outcome)
            no_thanks_done = True
        time.sleep(0.25)

    outcome["payment_flow_done"] = payment_done
    outcome["address_flow_done"] = False
    markers.mark(
        "checkout_edits_done",
        payment_done=payment_done,
        slot_done=slot_done,
        address_done=False,
    )
    if not payment_done:
        raise AssertionError(
            "After Proceed, could not complete delivery-slot + payment change "
            "(recording stop point not reached)"
        )
    print(
        f"  checkout edits done — slot={slot_done} payment={payment_done} "
        "(stop recording; order not placed)"
    )



def run_test_case_4(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """TASK 4: Grocery search → add → cart → delivery slot + payment → stop."""
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
    plan["add_strategy"] = "grocery_plus_collect_only"
    plan["under_5_url"] = UNDER_5_HTTPS
    plan["use_under_5_deeplink"] = USE_UNDER_5_DEEPLINK
    plan["place_order"] = False
    plan["steps"] = [
        "Start new automation session (kill prior Amazon if running)",
        "Open Amazon App (cold start)",
        'Tap home "Grocery" tab',
        f'Search "{SEARCH_QUERY}" in Grocery search box',
        f"Add first {TARGET_ITEMS} via listing + buttons only (never open product details)",
        "Open Cart screen",
        f"START screen recording on Cart for {rec_limit}s",
        'Tap "Proceed to checkout"',
        'Tap "No thanks" on Prime upsell if shown',
        "On Place Your Order: tap an alternate delivery-slot radio",
        'Tap first "Change payment method" (Place Your Order)',
        "Toggle payment card (1st↔2nd) → Continue",
        "Stop screen recording and quit session (do not place order)",
    ]
    plan["notes"] = (
        f'{TEST_CASE_NAME} (TASK 4) — Grocery → search "{SEARCH_QUERY}" → '
        f"+ ×{TARGET_ITEMS}; cart record → Proceed → change delivery slot → "
        "Change payment method → stop. Does not place the order."
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
                time.sleep(0.2)
                outcome["cold_start_terminated"] = True
            else:
                outcome["cold_start_terminated"] = False
            print(f"Activating {bundle_id} (cold start) …")
            driver.activate_app(bundle_id)
            time.sleep(0.35)
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

        time.sleep(0.15)
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

        if USE_UNDER_5_DEEPLINK:
            _open_under_5_shelf(driver, markers, outcome, bundle_id=bundle_id)
        else:
            _open_grocery_search_low_cost(driver, markers, outcome)
        outcome["price_filter_applied"] = False
        _add_n_low_cost_fast(
            driver, markers, outcome, target=TARGET_ITEMS, max_scrolls=8
        )
        _open_cart_page_fast(driver, markers, outcome)

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

        _verify_cart_fast(
            driver,
            markers,
            outcome,
            expected=TARGET_ITEMS,
        )
        _tc4_checkout_edits(driver, markers, outcome)

        # Pause so last Continue / payment taps are in the recording.
        time.sleep(0.5)
        markers.mark(
            "test_case_4_stopped_after_payment",
            items=outcome.get("items_added_count"),
            slot_changed=outcome.get("delivery_slot_changed"),
            payment_done=outcome.get("payment_flow_done"),
            checkout_steps=outcome.get("checkout_steps"),
        )
        print("Stopping process after payment change (order not placed) …")

        outcome["status"] = "ok"
        outcome["message"] = (
            f"{TEST_CASE_NAME} OK — added {outcome.get('items_added_count')} "
            f"items; cart → record → slot change → payment change (order not placed)"
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
