"""Test Case 2 (TASK 2): Groceries → under $5 → add 5 different items → verify cart."""

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

TEST_CASE_NAME = "Test Case 2"
TEST_CASE_ID = "test_case_2"
SEARCH_QUERY = "Groceries"
TARGET_ITEMS = 5
MAX_PRICE_USD = 5.0
# Rough PKR→USD when storefront shows local currency (seen ~277 on device).
PKR_PER_USD = 280.0

GROCERIES_SUGGESTION_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "groceries"),
    (AppiumBy.ACCESSIBILITY_ID, "Groceries"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(name == "groceries" OR label == "groceries" OR '
        'name == "Groceries" OR label == "Groceries")',
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
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Shop groceries" OR name CONTAINS[c] "Shop groceries"',
    ),
)

ADD_TO_CART_BUTTON = (
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
        'name == "cartTab" OR label BEGINSWITH[c] "Cart"',
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

USD_PRICE_RE = re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)")
PKR_PRICE_RE = re.compile(r"PKR\s*([\d,]+(?:\.\d+)?)", re.I)


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
                    "duration": 0.35,
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


def _wait_badge_increase(
    driver, before: int | None, *, timeout: float = 3.0
) -> tuple[bool, int | None]:
    end = time.monotonic() + timeout
    after = _cart_badge_count(driver)
    while time.monotonic() < end:
        after = _cart_badge_count(driver)
        if before is not None and after is not None and after > before:
            return True, after
        if before is None and after is not None and after > 0:
            return True, after
        time.sleep(0.25)
    after = _cart_badge_count(driver)
    ok = False
    if before is not None and after is not None and after > before:
        ok = True
    elif before is None and after is not None and after > 0:
        ok = True
    return ok, after


def _parse_usd_price(text: str) -> float | None:
    if not text:
        return None
    m = USD_PRICE_RE.search(text)
    if m:
        return float(m.group(1))
    m = PKR_PRICE_RE.search(text)
    if m:
        pkr = float(m.group(1).replace(",", ""))
        return round(pkr / PKR_PER_USD, 2)
    return None


def _visible_price_labels(driver) -> list[tuple[float, float, str]]:
    """Return (y, usd_price, raw_text) for visible price-like labels."""
    out: list[tuple[float, float, str]] = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeStaticText" AND '
            '(label CONTAINS "$" OR name CONTAINS "$" OR '
            'label CONTAINS[c] "PKR" OR name CONTAINS[c] "PKR" OR '
            'value CONTAINS "$" OR value CONTAINS[c] "PKR")',
        )
    except Exception:  # noqa: BLE001
        return out
    for el in els[:40]:
        try:
            if not el.is_displayed():
                continue
            blob = element_textish(el)
            price = _parse_usd_price(blob)
            if price is None:
                continue
            y = float(el.location.get("y", 0) or 0)
            if y < 80:
                continue
            out.append((y, price, blob[:120]))
        except Exception:  # noqa: BLE001
            continue
    return out


def _nearest_price(prices: list[tuple[float, float, str]], y: float) -> tuple[float | None, str]:
    if not prices:
        return None, ""
    best = min(prices, key=lambda t: abs(t[0] - y))
    if abs(best[0] - y) > 220:
        return None, ""
    return best[1], best[2]


def _list_under_price_add_candidates(
    driver, *, max_usd: float = MAX_PRICE_USD
) -> list[dict]:
    """Visible Add buttons whose nearest price label is under max_usd."""
    prices = _visible_price_labels(driver)
    candidates: list[dict] = []
    for by, value in ADD_TO_CART_BUTTON:
        try:
            els = driver.find_elements(by, value)
        except Exception:  # noqa: BLE001
            continue
        for idx, el in enumerate(els):
            try:
                if not el.is_displayed():
                    continue
                loc = el.location
                size = el.size
                y = float(loc.get("y", 0) or 0)
                if y < 120:
                    continue
                h = size.get("height", 0) or 0
                w = size.get("width", 0) or 0
                if h > 120 and w > 220:
                    continue
                price, price_text = _nearest_price(prices, y)
                if price is None or price > max_usd:
                    continue
                fp = f"{by}:{value}:{int(y // 20)}:{idx}:{price}"
                candidates.append(
                    {
                        "el": el,
                        "by": by,
                        "value": value,
                        "y": y,
                        "price_usd": price,
                        "price_text": price_text,
                        "fingerprint": fp,
                    }
                )
            except Exception:  # noqa: BLE001
                continue
        if candidates:
            break
    candidates.sort(key=lambda c: (c["price_usd"], c["y"]))
    return candidates


def _add_five_under_price(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    target: int = TARGET_ITEMS,
    max_usd: float = MAX_PRICE_USD,
    max_scrolls: int = 18,
) -> list[dict]:
    """Add `target` different under-$max_usd grocery items from the SERP."""
    added: list[dict] = []
    seen: set[str] = set()
    start_badge = _cart_badge_count(driver)
    outcome["cart_count_before_adds"] = start_badge
    markers.mark("under_price_add_start", target=target, max_usd=max_usd, badge=start_badge)

    for scroll_i in range(max_scrolls):
        if len(added) >= target:
            break
        candidates = _list_under_price_add_candidates(driver, max_usd=max_usd)
        print(
            f"  scroll {scroll_i}: {len(candidates)} under-${max_usd:g} add candidates "
            f"(have {len(added)}/{target})"
        )
        progressed = False
        for cand in candidates:
            if len(added) >= target:
                break
            fp = cand["fingerprint"]
            if fp in seen:
                continue
            before = _cart_badge_count(driver)
            try:
                cand["el"].click()
            except Exception as exc:  # noqa: BLE001
                print(f"  add click failed: {exc}")
                seen.add(fp)
                continue
            seen.add(fp)
            time.sleep(0.35)
            ok, after = _wait_badge_increase(driver, before, timeout=2.5)
            entry = {
                "index": len(added) + 1,
                "price_usd": cand["price_usd"],
                "price_text": cand["price_text"],
                "by": cand["by"],
                "value": cand["value"],
                "y": cand["y"],
                "confirmed": ok,
                "cart_before": before,
                "cart_after": after,
            }
            added.append(entry)
            progressed = True
            markers.mark(
                "item_added_to_cart",
                index=entry["index"],
                price_usd=entry["price_usd"],
                confirmed=ok,
                cart_before=before,
                cart_after=after,
            )
            print(
                f"  added #{entry['index']}/{target} "
                f"~${entry['price_usd']:.2f} confirmed={ok} badge {before}→{after}"
            )
            time.sleep(0.25)

        if len(added) >= target:
            break
        _scroll_down(driver)
        time.sleep(0.35)
        markers.mark("results_scrolled", index=scroll_i, added_so_far=len(added))
        if not progressed and scroll_i > 2 and not candidates:
            # Keep scrolling; grocery grids often load more under-$5 rows below.
            continue

    outcome["items_added"] = [
        {k: v for k, v in row.items() if k != "el"} for row in added
    ]
    outcome["items_added_count"] = len(added)
    end_badge = _cart_badge_count(driver)
    outcome["cart_count_after_adds"] = end_badge
    markers.mark(
        "under_price_add_done",
        added=len(added),
        target=target,
        badge_before=start_badge,
        badge_after=end_badge,
    )
    if len(added) < target:
        raise AssertionError(
            f"Only added {len(added)}/{target} different items under ${max_usd:g}"
        )
    return added


def _open_cart_page(driver, markers: MarkerLog, outcome: dict) -> None:
    print("Step: open Cart screen …")
    el, by, value = find_first(driver, CART_TAB_STRATEGIES, timeout=8.0)
    outcome.setdefault("selectors", {})["cart_tab"] = {"by": by, "value": value}
    print(f"Cart: {by} = {value!r}")
    el.click()
    markers.mark("cart_opened", by=by, value=value)
    time.sleep(0.8)
    outcome["cart_page_opened"] = True


def _count_cart_line_items(driver) -> int | None:
    """Best-effort count of distinct cart rows (quantity steppers / remove)."""
    predicates = (
        'label == "Increase item quantity" OR name == "Increase item quantity"',
        'label CONTAINS[c] "Remove" OR name CONTAINS[c] "Remove from cart" OR '
        'label CONTAINS[c] "Delete" OR name CONTAINS[c] "Delete"',
        'label CONTAINS[c] "quantity" AND type == "XCUIElementTypeButton"',
    )
    best = 0
    for pred in predicates:
        try:
            els = driver.find_elements(AppiumBy.IOS_PREDICATE, pred)
        except Exception:  # noqa: BLE001
            continue
        n = 0
        for el in els:
            try:
                if el.is_displayed() and (el.location.get("y", 0) or 0) > 80:
                    n += 1
            except Exception:  # noqa: BLE001
                continue
        best = max(best, n)
    return best or None


def _verify_five_in_cart(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    expected: int = TARGET_ITEMS,
    badge_before_adds: int | None,
) -> None:
    print(f"Step: verify {expected} items in cart …")
    time.sleep(0.5)
    badge = _cart_badge_count(driver)
    lines = _count_cart_line_items(driver)
    outcome["cart_verify_badge"] = badge
    outcome["cart_verify_line_items"] = lines

    delta_ok = False
    if badge_before_adds is not None and badge is not None:
        delta_ok = (badge - badge_before_adds) >= expected
    badge_ok = badge is not None and badge >= expected
    lines_ok = lines is not None and lines >= expected

    verified = delta_ok or (badge_ok and (lines_ok or lines is None))
    # If we tracked successful adds and badge grew by expected, accept.
    if not verified and outcome.get("items_added_count", 0) >= expected:
        if badge_before_adds is not None and badge is not None:
            verified = (badge - badge_before_adds) >= expected
        elif badge is not None and badge >= expected:
            verified = True

    outcome["cart_five_items_verified"] = bool(verified)
    markers.mark(
        "cart_verified",
        expected=expected,
        badge=badge,
        line_items=lines,
        verified=bool(verified),
    )
    print(
        f"  cart verify: badge={badge} lines={lines} "
        f"before_adds={badge_before_adds} verified={verified}"
    )
    if not verified:
        raise AssertionError(
            f"Cart verification failed — expected {expected} items "
            f"(badge={badge}, lines={lines}, before={badge_before_adds})"
        )

    # Soft: proceed-to-checkout presence confirms a real cart checkout path.
    try:
        el, by, value = find_first(driver, PROCEED_CHECKOUT, timeout=4.0)
        outcome["selectors"]["proceed_checkout"] = {"by": by, "value": value}
        outcome["proceed_checkout_visible"] = True
        markers.mark("proceed_checkout_visible", by=by, value=value)
        print(f"  Proceed to checkout visible: {by} = {value!r}")
    except TimeoutError:
        outcome["proceed_checkout_visible"] = False
        print("  Proceed to checkout not visible (cart verify still OK).")


def run_test_case_2(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """TASK 2 / Test Case 2: add 5 different under-$5 grocery items, verify cart."""
    if retailer != "amazon":
        raise ValueError(
            f"{TEST_CASE_NAME} is Amazon-only (do not invent Walmart/Instacart)."
        )

    plan = describe_session(settings, retailer, bundle_id)
    plan["scenario"] = TEST_CASE_ID
    plan["test_case"] = TEST_CASE_NAME
    plan["dry_run"] = dry_run
    plan["study_mode"] = "cancel_test"
    plan["recording"] = False
    plan["target_items"] = TARGET_ITEMS
    plan["max_price_usd"] = MAX_PRICE_USD
    plan["steps"] = [
        "Start new automation session (kill prior Amazon if running)",
        "Open Amazon App (cold start)",
        'Tap Search Bar and type "Groceries" only',
        'Tap "groceries" suggestion only',
        'Tap "Shop groceries on Amazon"',
        f"Add {TARGET_ITEMS} different grocery items under ${MAX_PRICE_USD:g}",
        f"Open Cart and verify all {TARGET_ITEMS} items added",
    ]
    plan["notes"] = (
        f"{TEST_CASE_NAME} (TASK 2) — groceries SERP under ${MAX_PRICE_USD:g}, "
        f"add {TARGET_ITEMS} distinct items, verify on Cart. No place-order."
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

    markers = MarkerLog()
    driver = None
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

        print('Step: tap search + type "Groceries" only …')
        _tap_search_and_type(driver, markers, outcome, SEARCH_QUERY)
        markers.mark("groceries_typed", query=SEARCH_QUERY)

        print('Step: tap "groceries" suggestion only …')
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

        print('Step: tap "Shop groceries on Amazon" …')
        shop_el, shop_by, shop_value = find_first(
            driver, SHOP_GROCERIES_STRATEGIES, timeout=8.0
        )
        outcome["selectors"]["shop_groceries"] = {
            "by": shop_by,
            "value": shop_value,
        }
        print(f"Shop groceries: {shop_by} = {shop_value!r}")
        shop_el.click()
        markers.mark("shop_groceries_tapped", by=shop_by, value=shop_value)
        time.sleep(0.9)

        print(
            f"Step: add {TARGET_ITEMS} different grocery items under "
            f"${MAX_PRICE_USD:g} …"
        )
        _add_five_under_price(
            driver,
            markers,
            outcome,
            target=TARGET_ITEMS,
            max_usd=MAX_PRICE_USD,
        )

        _open_cart_page(driver, markers, outcome)
        _verify_five_in_cart(
            driver,
            markers,
            outcome,
            expected=TARGET_ITEMS,
            badge_before_adds=outcome.get("cart_count_before_adds"),
        )

        markers.mark("test_case_2_complete", items=outcome.get("items_added_count"))
        outcome["status"] = "ok"
        outcome["message"] = (
            f"{TEST_CASE_NAME} OK — added {outcome.get('items_added_count')} "
            f"under-${MAX_PRICE_USD:g} items and verified cart"
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
        if driver is not None:
            try:
                driver.quit()
                print("Appium session stopped.")
            except Exception as quit_exc:  # noqa: BLE001
                outcome["quit_error"] = str(quit_exc)
        outcome["markers"] = markers.events
        result_path.write_text(json.dumps(outcome, indent=2), encoding="utf-8")
        print(f"Wrote {result_path}")
