"""Test Case Walmart 4: copy of Walmart TC3 (qty + on Checkout Item details).

Walmart (iOS):
  1. Tap bottom tab "My Items"
  2. Fresh Produce — +Add once per item for basket A–H (qty 1 on listing)
  3. Open Cart → start screen recording
  4. Tap "Change" (delivery) → Reserve a time
  5. Tap "Change" (address) → pick 2nd saved address
  6. Toggle delivery day last↔2nd-last + time slot → tap "Reserve"
  7. Continue to checkout → Continue on Missing anything? → Checkout
  8. On Checkout: Item details → View → raise EVERY item to units_each via +
  9. Tap Done → remain on Checkout → stop recording

Basket H: 10 Fresh Produce items ×1 on listing, then qty → ×10 on Item details
(same path as E, but 10 lines instead of 5). No Electronics / other-dept phase.

Does not place the order. Unit scaling is on Checkout Item details
(not on the Cart screen like Walmart TC1/TC2).
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
from grocery_latency.scenarios.test_case_walmart_1 import (
    CART_RECORDING_SECONDS,
    FRESH_PRODUCE_STRATEGIES,
    WALMART_DISMISS_LABELS,
    _apply_fast_wda_settings,
    _cart_screen_visible,
    _change_day_and_slot_then_reserve,
    _clear_cart_items,
    _collapse_cart_fulfillment_if_expanded,
    _dismiss_ask_sparky_if_shown,
    _dismiss_subscription_sheet,
    _dismiss_walmart_pdp_if_shown,
    _find_cart_change_delivery_el,
    _mark_step,
    _open_cart,
    _scroll_down,
    _scroll_up,
    _select_second_address,
    _tap_center,
    _tap_fresh_produce,
    _tap_missing_anything_continue,
    _tap_my_items_tab,
    _tap_reserve_change_address,
    _wait_checkout_screen,
)
from grocery_latency.scenarios import test_case_walmart_2 as _w2
from grocery_latency.scenarios.test_case_walmart_2 import (
    BASKET_SAMPLES,
    _tap_all_items_filter,
    resolve_basket_sample,
)

_MAX_QTY_RE = re.compile(r"max\s*(\d+)", re.I)
_CURRENT_QTY_RE = re.compile(r"current quantity is (\d+)", re.I)
_INC_ITEM_RE = re.compile(
    r"Increase quantity of (.+?), The current quantity is", re.I
)
# TC4 tuned faster than TC3 — still verify qty after bursts.
_QTY_TAP_GAP = 0.07
_QTY_SETTLE = 0.28
_QTY_MAX_ROUNDS = 5
_FAST = 0.05
_MED = 0.10

TEST_CASE_NAME = "Test Case Walmart 4"
TEST_CASE_ID = "test_case_walmart_4"


# Fresh-produce style grocery queries for H when My Items Reorder is empty.
_GROCERY_SEARCH_QUERIES = (
    "fresh banana",
    "fresh spinach",
    "whole milk",
    "large eggs",
    "white bread",
    "fresh apple",
    "roma tomato",
    "yellow onion",
    "fresh cucumber",
    "hass avocado",
    "plain yogurt",
    "cheddar cheese",
    "russet potato",
    "navel orange",
    "romaine lettuce",
)


def _dismiss_blocking_alerts(driver, markers: MarkerLog | None = None) -> bool:
    """Dismiss iOS system sheets that block search (Dictation, Siri, etc.)."""
    dismissed = False
    try:
        alert = driver.switch_to.alert
        text = (alert.text or "").lower()
        if "dictation" in text or "siri" in text or text:
            try:
                alert.dismiss()
            except Exception:  # noqa: BLE001
                alert.accept()
            dismissed = True
            print(f"  dismissed system alert ({text[:48]!r})")
    except Exception:  # noqa: BLE001
        pass
    # Never click plain "Cancel"/"Close" here — that dismisses Shop search.
    for label in (
        "Not Now",
        "Don't Allow",
        "Dont Allow",
        "Don't Allow Dictation",
        "OK",
    ):
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                f'type == "XCUIElementTypeButton" AND visible == 1 AND '
                f'(label == "{label}" OR name == "{label}")',
            )
            for el in els[:2]:
                try:
                    if el.is_displayed():
                        el.click()
                        dismissed = True
                        time.sleep(0.15)
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            continue
    if dismissed and markers is not None:
        markers.mark("blocking_alert_dismissed")
    return dismissed


def _tap_shop_tab(driver, markers: MarkerLog) -> None:
    strategies = (
        (AppiumBy.ACCESSIBILITY_ID, "Shop"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Shop" OR name == "Shop" OR '
            'label CONTAINS[c] "Shop(bottom)" OR name CONTAINS[c] "Shop(bottom)")',
        ),
    )
    try:
        el, by, value = find_first(driver, strategies, timeout=2.0)
        el.click()
        markers.mark("shop_tab_tapped", by=by, value=value)
        print(f"  Shop tab: {by}={value!r}")
        time.sleep(0.35)
    except TimeoutError:
        # Bottom-left-ish Shop tab fallback.
        try:
            size = driver.get_window_size() or {}
            w = float(size.get("width") or 390)
            h = float(size.get("height") or 844)
            _tap_center(driver, w * 0.10, h * 0.95)
            markers.mark("shop_tab_tapped", via="coord")
            print("  Shop tab: coord")
            time.sleep(0.35)
        except Exception:  # noqa: BLE001
            pass


def _prime_my_items_catalog(driver, markers: MarkerLog, outcome: dict) -> bool:
    """Try Fresh Produce / All Items on My Items. False → use Shop search."""
    _dismiss_blocking_alerts(driver, markers)
    try:
        driver.update_settings({"snapshotMaxDepth": 50})
    except Exception:  # noqa: BLE001
        pass
    try:
        size = driver.get_window_size() or {}
        w = int(size.get("width") or 390)
        h = int(size.get("height") or 844)
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.25,
                "fromX": w // 2,
                "fromY": int(h * 0.32),
                "toX": w // 2,
                "toY": int(h * 0.72),
            },
        )
        time.sleep(0.9)
    except Exception:  # noqa: BLE001
        pass
    chips = False
    try:
        _tap_fresh_produce(driver, markers, outcome)
        chips = True
    except TimeoutError:
        chips = _tap_all_items_filter(driver, markers)
        if not chips:
            _tap_my_items_tab(driver, markers, outcome)
            time.sleep(0.8)
            try:
                _tap_fresh_produce(driver, markers, outcome)
                chips = True
            except TimeoutError:
                chips = _tap_all_items_filter(driver, markers)
    if not chips:
        markers.mark("my_items_catalog_chips_missing")
        print(
            "  My Items Fresh Produce missing — "
            "will add via Shop grocery search …"
        )
        return False
    end = time.monotonic() + 4.0
    while time.monotonic() < end:
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'name == "stepper.addToCartButton" OR '
                'name == "stepper.mainButton"',
            )
            if els:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.35)
    # Chips present but no Adds yet — still try My Items scroll path.
    return True


_SEARCH_FIELD_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Search Walmart"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeSearchField" AND visible == 1',
    ),
    (
        AppiumBy.IOS_PREDICATE,
        'name == "Search Walmart" OR label == "Search Walmart" OR '
        'name CONTAINS[c] "Search" OR label CONTAINS[c] "Search Walmart"',
    ),
)


def _cancel_search_if_open(driver) -> None:
    try:
        cancels = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Cancel" OR name == "Cancel")',
        )
        if cancels:
            cancels[0].click()
            time.sleep(0.25)
    except Exception:  # noqa: BLE001
        pass


def _read_search_field_value(driver) -> str:
    for by, value in _SEARCH_FIELD_STRATEGIES:
        try:
            els = driver.find_elements(by, value)
        except Exception:  # noqa: BLE001
            continue
        for el in els[:3]:
            try:
                blob = " ".join(
                    filter(
                        None,
                        (
                            el.get_attribute("value"),
                            el.get_attribute("label"),
                            el.get_attribute("name"),
                        ),
                    )
                )
                if blob:
                    return blob
            except Exception:  # noqa: BLE001
                continue
    return ""


def _open_and_clear_search(driver) -> object:
    """Open Search Walmart with an empty field (Cancel first if needed)."""
    _dismiss_blocking_alerts(driver)
    _cancel_search_if_open(driver)
    el, _by, _v = find_first(driver, _SEARCH_FIELD_STRATEGIES, timeout=4.0)
    # Tap left half of the field to avoid the mic (Dictation) control.
    try:
        r = el.rect or {}
        x = float(r.get("x", 0)) + max(24.0, float(r.get("width", 100)) * 0.25)
        y = float(r.get("y", 0)) + float(r.get("height", 36)) / 2.0
        _tap_center(driver, x, y)
    except Exception:  # noqa: BLE001
        el.click()
    time.sleep(0.3)
    _dismiss_blocking_alerts(driver)
    # Re-find after focus (avoids stale element).
    el, _by, _v = find_first(driver, _SEARCH_FIELD_STRATEGIES, timeout=3.0)
    try:
        el.clear()
    except Exception:  # noqa: BLE001
        try:
            clears = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND visible == 1 AND '
                '(label == "Clear text" OR name == "Clear text" OR '
                'label == "Clear" OR name CONTAINS[c] "clear")',
            )
            if clears:
                clears[0].click()
                time.sleep(0.1)
                el, _by, _v = find_first(
                    driver, _SEARCH_FIELD_STRATEGIES, timeout=2.0
                )
        except Exception:  # noqa: BLE001
            pass
    return el


def _type_search_query(driver, field, query: str) -> None:
    """Set one query into a focused search field; rewrite if Dictation mangled it."""
    _dismiss_blocking_alerts(driver)
    q = query.strip()
    for attempt in range(3):
        try:
            field.set_value(q)
        except Exception:  # noqa: BLE001
            try:
                field.send_keys(q)
            except Exception:  # noqa: BLE001
                try:
                    driver.execute_script("mobile: type", {"text": q})
                except Exception:  # noqa: BLE001
                    field = _open_and_clear_search(driver)
                    try:
                        field.set_value(q)
                    except Exception:  # noqa: BLE001
                        field.send_keys(q)
        time.sleep(0.15)
        _dismiss_blocking_alerts(driver)
        seen = _read_search_field_value(driver).lower()
        if q.lower() in seen or seen.replace(" ", "") == q.lower().replace(" ", ""):
            return
        # Mangled / sticky prior text — clear and rewrite.
        print(f"  rewrote search (attempt {attempt + 1}): saw {seen!r} want {q!r}")
        field = _open_and_clear_search(driver)


def _submit_search(driver, query: str) -> None:
    """Submit search without tapping the bottom tab bar (Account lives there)."""
    # Prefer a suggestion matching the query.
    try:
        el = driver.find_element(
            AppiumBy.IOS_PREDICATE,
            f'type == "XCUIElementTypeButton" AND visible == 1 AND '
            f'(label CONTAINS[c] "{query}" OR name CONTAINS[c] "{query}") AND '
            f'NOT (label CONTAINS[c] "to search field")',
        )
        if el.is_displayed():
            el.click()
            time.sleep(0.25)
            return
    except Exception:  # noqa: BLE001
        pass

    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "Search"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Search" OR name == "Search")',
        ),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "search" OR name == "search")',
        ),
    ):
        try:
            btn = driver.find_element(by, value)
            if btn.is_displayed():
                # Skip bottom-tab "Shop" etc. — keyboard Search is usually mid/low
                # but above the tab bar (~y 761 on 844h). Prefer y < 0.90*h.
                try:
                    r = btn.rect or {}
                    h = float((driver.get_window_size() or {}).get("height") or 844)
                    if float(r.get("y", 0)) > h * 0.90:
                        continue
                except Exception:  # noqa: BLE001
                    pass
                btn.click()
                time.sleep(0.25)
                return
        except Exception:  # noqa: BLE001
            continue

    try:
        driver.execute_script("mobile: performEditorAction", {"action": "search"})
        time.sleep(0.25)
        return
    except Exception:  # noqa: BLE001
        pass

    try:
        driver.execute_script("mobile: keys", {"keys": ["\n"]})
        time.sleep(0.25)
        return
    except Exception:  # noqa: BLE001
        pass

    try:
        driver.execute_script("mobile: keys", {"keys": [{"key": "\n"}]})
        time.sleep(0.25)
    except Exception:  # noqa: BLE001
        # Never coord-tap bottom-right — that hits the Account tab.
        pass


def _find_product_add_buttons(driver) -> list[tuple[float, float, str]]:
    """Product Add CTAs — Walmart often marks on-screen steppers visible=false."""
    out: list[tuple[float, float, str]] = []
    try:
        h = float((driver.get_window_size() or {}).get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    # Do NOT require visible==1 (same quirk as My Items Fresh Produce Adds).
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'name == "stepper.addToCartButton" OR '
            'name == "stepper.mainButton"',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:20]:
        try:
            lab = (el.get_attribute("label") or el.get_attribute("name") or "")
            low = lab.lower()
            if "to search field" in low or "search suggestion" in low:
                continue
            r = el.rect or {}
            y = float(r.get("y", 0))
            if y < 120 or y > h * 0.88:
                continue
            ww = float(r.get("width", 0))
            hh = float(r.get("height", 0))
            if ww < 20 or hh < 16:
                continue
            cx = float(r.get("x", 0)) + ww / 2.0
            cy = y + hh / 2.0
            out.append((cx, cy, lab[:80] or "stepper.add"))
        except Exception:  # noqa: BLE001
            continue
    if not out:
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND '
                '(label CONTAINS[c] "Add to cart" OR '
                'name CONTAINS[c] "Add to cart")',
            )
        except Exception:  # noqa: BLE001
            els = []
        for el in els[:15]:
            try:
                lab = (el.get_attribute("label") or el.get_attribute("name") or "")
                low = lab.lower()
                if "to search field" in low or "search suggestion" in low:
                    continue
                r = el.rect or {}
                y = float(r.get("y", 0))
                if y < 120 or y > h * 0.88:
                    continue
                ww = float(r.get("width", 0))
                hh = float(r.get("height", 0))
                if ww < 20 or hh < 16:
                    continue
                cx = float(r.get("x", 0)) + ww / 2.0
                cy = y + hh / 2.0
                out.append((cx, cy, lab[:80]))
            except Exception:  # noqa: BLE001
                continue
    out.sort(key=lambda t: t[1])
    return out


def _add_basket_via_grocery_search(
    driver, markers: MarkerLog, outcome: dict, *, sample, fast: bool = False
) -> list:
    """Add N grocery SKUs via Shop search (My Items Reorder empty).

    ``fast=True`` (TC5 large carts): shorter result polls, fewer Shop re-taps,
    abort after 2 consecutive misses.
    """
    target = sample.num_items
    units_each = sample.units_each
    poll_s = 2.2 if fast else 4.5
    poll_gap = 0.22 if fast else 0.4
    add_settle = 0.22 if fast else 0.45
    fail_limit = 2 if fast else 3
    print(
        f"Step: basket {sample.letter} — Shop search-add {target} grocery "
        f"items ×1 (cart target ×{units_each}"
        f"{', fast' if fast else ''}) …"
    )
    markers.mark(
        "walmart_basket_search_add_start",
        sample=sample.letter,
        num_items=target,
        units_each=units_each,
        fast=fast,
    )
    _tap_shop_tab(driver, markers)
    _dismiss_blocking_alerts(driver, markers)
    added: list[dict] = []
    t0 = time.monotonic()
    consecutive_fails = 0
    for query in _GROCERY_SEARCH_QUERIES:
        if len(added) >= target:
            break
        # Search UI often wedges after a few successes — stop burning time.
        if consecutive_fails >= fail_limit:
            print(
                f"  search abort: {consecutive_fails} consecutive fails "
                f"(have {len(added)}/{target})"
            )
            break
        try:
            field = _open_and_clear_search(driver)
            _type_search_query(driver, field, query)
            time.sleep(0.12 if fast else 0.2)
            _dismiss_blocking_alerts(driver, markers)
            _submit_search(driver, query)
            # Poll for product Add tiles (results can be slow after Dictation).
            batch: list[tuple[float, float, str]] = []
            end = time.monotonic() + poll_s
            while time.monotonic() < end:
                _dismiss_blocking_alerts(driver, markers)
                batch = _find_product_add_buttons(driver)
                if batch:
                    break
                time.sleep(poll_gap)
            if not batch:
                _scroll_down(driver, percent=0.22)
                time.sleep(poll_gap)
                batch = _find_product_add_buttons(driver)
            if not batch:
                print(f"  search {query!r}: no product Add — next")
                consecutive_fails += 1
                _cancel_search_if_open(driver)
                if not fast:
                    _tap_shop_tab(driver, markers)
                continue
            cx, cy, label = batch[0]
            print(
                f"  + Add #{len(added) + 1}/{target} via search {query!r} "
                f"@({cx:.0f},{cy:.0f}) label={label[:40]!r}"
            )
            _tap_center(driver, cx, cy)
            time.sleep(add_settle)
            _dismiss_blocking_alerts(driver, markers)
            entry = {
                "index": len(added) + 1,
                "source": "shop_search_add",
                "query": query,
                "label": label[:120],
                "y": cy,
                "units_target": units_each,
                "units_on_listing": 1,
            }
            added.append(entry)
            consecutive_fails = 0
            markers.mark(
                "item_added_to_cart",
                index=entry["index"],
                query=query,
                y=cy,
                units=1,
                units_target=units_each,
            )
            _cancel_search_if_open(driver)
            # Fast path: Cancel usually returns to Shop — skip extra tab tap.
            if not fast or len(added) % 3 == 0:
                _tap_shop_tab(driver, markers)
        except Exception as exc:  # noqa: BLE001
            print(f"  search {query!r} failed: {exc}")
            consecutive_fails += 1
            _dismiss_blocking_alerts(driver, markers)
            _cancel_search_if_open(driver)
            _tap_shop_tab(driver, markers)
            continue
    elapsed = time.monotonic() - t0
    outcome["items_added"] = list(added)
    outcome["items_added_count"] = len(added)
    outcome["basket_add_elapsed_seconds"] = round(elapsed, 2)
    outcome["basket_sample"] = sample.letter
    outcome["basket_num_items"] = sample.num_items
    outcome["basket_units_each"] = units_each
    outcome["basket_total_units"] = sample.total_units
    markers.mark(
        "walmart_basket_add_done",
        sample=sample.letter,
        added=len(added),
        target=target,
        via="shop_grocery_search",
        elapsed_s=round(elapsed, 2),
    )
    print(
        f"  basket {sample.letter} search-add done: {len(added)}/{target} "
        f"in {elapsed:.1f}s"
    )
    if len(added) < target:
        raise AssertionError(
            f"Only search-added {len(added)}/{target} grocery items "
            f"for basket {sample.letter}"
        )
    return added


def _add_basket_fresh_produce_only(
    driver, markers: MarkerLog, outcome: dict, *, sample
) -> list:
    """+Add ``num_items`` from Fresh Produce (H = 10). Fall back to Shop search."""
    from grocery_latency.scenarios.test_case_walmart_2 import (
        _add_basket_via_add_buttons,
    )

    try:
        _tap_fresh_produce(driver, markers, outcome)
    except TimeoutError:
        if not _tap_all_items_filter(driver, markers):
            print("  Fresh Produce missing on My Items — Shop search fallback …")
            return _add_basket_via_grocery_search(
                driver, markers, outcome, sample=sample
            )
    print(
        f"Step: basket {sample.letter} — +Add {sample.num_items} Fresh Produce "
        f"items ×1 (cart will scale each to ×{sample.units_each}; "
        f"total {sample.total_units}) …"
    )
    try:
        return _add_basket_via_add_buttons(
            driver, markers, outcome, sample=sample
        )
    except AssertionError as exc:
        have = int(outcome.get("items_added_count") or 0)
        if have >= sample.num_items:
            raise
        need = sample.num_items - have
        print(
            f"  Fresh Produce only got {have}/{sample.num_items} "
            f"({exc}) — finishing +{need} via Shop search …"
        )
        markers.mark("fresh_produce_short_search_fallback", have=have, need=need)
        prior = list(outcome.get("items_added") or [])

        class _Rem:
            letter = sample.letter
            num_items = need
            units_each = sample.units_each
            total_units = need * sample.units_each

        extra = _add_basket_via_grocery_search(
            driver, markers, outcome, sample=_Rem()
        )
        merged = prior + [
            {**e, "index": have + i + 1} for i, e in enumerate(extra)
        ]
        outcome["items_added"] = merged
        outcome["items_added_count"] = len(merged)
        if len(merged) < sample.num_items:
            raise AssertionError(
                f"Only added {len(merged)}/{sample.num_items} items "
                f"for basket {sample.letter}"
            )
        return merged


def _add_basket_fast(driver, markers: MarkerLog, outcome: dict, *, sample) -> list:
    """Fresh Produce on My Items; Shop grocery search if catalog empty."""
    catalog_ok = outcome.pop("_tc4_catalog_ok", True)
    _dismiss_blocking_alerts(driver, markers)
    if not catalog_ok:
        return _add_basket_via_grocery_search(
            driver, markers, outcome, sample=sample
        )
    prev = (_w2._SNAP, _w2._BRIEF, _w2._SETTLE)
    _w2._SNAP, _w2._BRIEF, _w2._SETTLE = 0.02, 0.04, 0.06
    try:
        try:
            driver.update_settings({"snapshotMaxDepth": 50})
        except Exception:  # noqa: BLE001
            pass
        return _add_basket_fresh_produce_only(
            driver, markers, outcome, sample=sample
        )
    finally:
        _w2._SNAP, _w2._BRIEF, _w2._SETTLE = prev
        try:
            driver.update_settings({"snapshotMaxDepth": 28})
        except Exception:  # noqa: BLE001
            pass


def _tap_continue_to_checkout_tc4(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """Continue to checkout, or shipping CTA 'Checkout all items'."""
    from grocery_latency.scenarios.test_case_walmart_1 import (
        CONTINUE_TO_CHECKOUT_STRATEGIES,
        _dismiss_ineligible_delivery_sheet,
        _dismiss_system_share_sheet,
        _recover_if_still_on_reserve_sheet,
        _resolve_ineligible_reserved_time_via_store,
    )

    print('Step: tap "Continue to checkout" …')
    _dismiss_system_share_sheet(driver, markers)
    _dismiss_ineligible_delivery_sheet(driver, markers, outcome)
    _recover_if_still_on_reserve_sheet(driver, markers, outcome)
    _resolve_ineligible_reserved_time_via_store(driver, markers, outcome)
    tap_dismissibles(driver, labels=WALMART_DISMISS_LABELS, rounds=1)
    strategies = CONTINUE_TO_CHECKOUT_STRATEGIES + (
        (AppiumBy.ACCESSIBILITY_ID, "Checkout all items"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Checkout all items" OR '
            'name == "Checkout all items" OR '
            'label CONTAINS[c] "Checkout all" OR '
            'label == "Continue to checkout")',
        ),
    )
    try:
        el, by, value = find_first(driver, strategies, timeout=4.0)
    except TimeoutError:
        _scroll_down(driver, percent=0.25)
        time.sleep(_FAST)
        el, by, value = find_first(driver, strategies, timeout=5.0)
    el.click()
    outcome["continue_to_checkout_tapped"] = True
    markers.mark("continue_to_checkout", by=by, value=value)
    _mark_step(outcome, "continue_to_checkout", status="ok", by=by, value=value)
    print(f"  Continue to checkout: {by} = {value!r}")
    time.sleep(_MED)


def _tap_cart_change_delivery_fast(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """Find Cart Change fast — scroll UP first (fat carts bury it above)."""
    print('Step: on Cart tap "Change" (delivery method or slot) …')
    _dismiss_subscription_sheet(driver)
    _dismiss_walmart_pdp_if_shown(driver, markers)
    if not _cart_screen_visible(driver):
        print("  not on Cart — re-opening …")
        _open_cart(driver, markers, outcome)
    _collapse_cart_fulfillment_if_expanded(driver)
    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    for _ in range(3):
        _scroll_up(driver, percent=0.45)
        time.sleep(_FAST)
    el = None
    by = AppiumBy.IOS_PREDICATE
    value = "FulfillmentSummaryView.actionButton"
    for attempt in range(6):
        el = _find_cart_change_delivery_el(driver)
        if el is not None:
            r = el.rect or {}
            y = float(r.get("y", 0))
            if 120 <= y <= h * 0.75:
                break
            if y > h * 0.75:
                _scroll_down(driver, percent=0.25)
            else:
                _scroll_up(driver, percent=0.30)
            time.sleep(_FAST)
            continue
        if attempt % 2 == 0:
            _scroll_up(driver, percent=0.35)
        else:
            _scroll_down(driver, percent=0.28)
        time.sleep(_FAST)
    if el is None:
        strategies = (
            (
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND '
                '(name == "FulfillmentSummaryView.actionButton" OR '
                'label CONTAINS[c] "Change delivery")',
            ),
            (
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND '
                '(label == "Change" OR name == "Change")',
            ),
        )
        el, by, value = find_first(driver, strategies, timeout=3.5)
    r = el.rect or {}
    y = float(r.get("y", 0))
    _tap_center(
        driver,
        float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0,
        float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0,
    )
    markers.mark("cart_change_delivery", by=by, value=value, y=y)
    _mark_step(outcome, "cart_change_delivery", status="ok", by=by, value=value, y=y)
    print(f"  Change delivery: {by}={value!r} y={y:.0f}")
    time.sleep(_FAST)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _item_details_sheet_visible(driver) -> bool:
    """True when the Item details modal is open (not Checkout's section header)."""
    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    try:
        # Modal chrome: bottom Done CTA (Checkout uses Save Card, not Done).
        done = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Done" OR name == "Done")',
        )
        done_ok = False
        for el in done[:4]:
            r = el.rect or {}
            y = float(r.get("y", 0))
            ww = float(r.get("width", 0))
            if y > h * 0.70 and ww > 80:
                done_ok = True
                break
        return done_ok
    except Exception:  # noqa: BLE001
        return False


def _checkout_payment_screen_visible(driver) -> bool:
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label == "Save Card" OR name == "Save Card" OR '
            'label CONTAINS[c] "Pay with card" OR '
            'label CONTAINS[c] "Card number")',
        )
        return bool(els)
    except Exception:  # noqa: BLE001
        return False


def _dismiss_walmart_plus_checkout_splash(
    driver, markers: MarkerLog | None = None
) -> bool:
    """Dismiss Walmart+ trial sheet that covers Checkout (No thanks / Close)."""
    try:
        splash = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'name CONTAINS[c] "WalmartPlusSplash" OR '
            'label CONTAINS[c] "Confirm plan" OR '
            'label CONTAINS[c] "I agree to the terms"',
        )
    except Exception:  # noqa: BLE001
        splash = []
    if not splash:
        return False
    for by, value in (
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "No thanks" OR name == "No thanks" OR '
            'label == "No Thanks" OR name == "No Thanks")',
        ),
        (AppiumBy.ACCESSIBILITY_ID, "No thanks"),
        (AppiumBy.ACCESSIBILITY_ID, "close"),
        (AppiumBy.ACCESSIBILITY_ID, "Close"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Close" OR name == "Close" OR label == "close")',
        ),
    ):
        try:
            el, _, _ = find_first(driver, ((by, value),), timeout=1.2)
            el.click()
            if markers is not None:
                markers.mark("walmart_plus_splash_dismissed", by=by, value=value)
            print("  dismissed Walmart+ checkout splash (No thanks)")
            time.sleep(_MED)
            return True
        except TimeoutError:
            continue
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
        # "No thanks" sits under Confirm plan near the bottom.
        _tap_center(driver, w * 0.50, h * 0.88)
        time.sleep(_MED)
        if markers is not None:
            markers.mark("walmart_plus_splash_dismissed", via="coord")
        print("  dismissed Walmart+ checkout splash (coord)")
        return True
    except Exception:  # noqa: BLE001
        return False


def _tap_checkout_item_details_view(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """On Checkout, open Item details via the View link."""
    print('Step: on Checkout tap Item details "View" …')
    _dismiss_walmart_plus_checkout_splash(driver, markers)
    # Payment section is below Item details — scroll up first so View is on screen.
    for _ in range(3):
        if _checkout_payment_screen_visible(driver):
            _scroll_up(driver, percent=0.45)
            time.sleep(_FAST)
        else:
            break

    for attempt in range(4):
        _dismiss_walmart_plus_checkout_splash(driver, markers)
        strategies = (
            (AppiumBy.ACCESSIBILITY_ID, "View Item details"),
            (
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND '
                '(label == "View Item details" OR name == "View Item details" OR '
                'label CONTAINS[c] "View Item details")',
            ),
            (AppiumBy.ACCESSIBILITY_ID, "View"),
            (
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND '
                '(label == "View" OR name == "View")',
            ),
        )
        try:
            size = driver.get_window_size() or {}
            h = float(size.get("height") or 844)
            w = float(size.get("width") or 390)
        except Exception:  # noqa: BLE001
            h, w = 844.0, 390.0

        el = None
        by = value = ""
        try:
            # Prefer the right-side View next to "Item details".
            # Do not require visible==1 — splash overlays often mark it false.
            views = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND '
                '(label == "View Item details" OR name == "View Item details" OR '
                'label == "View" OR name == "View")',
            )
            picks = []
            for v in views[:10]:
                try:
                    r = v.rect or {}
                    y = float(r.get("y", 0))
                    x = float(r.get("x", 0))
                    lab = (v.get_attribute("label") or v.get_attribute("name") or "")[
                        :40
                    ]
                    if 120 < y < h * 0.82:
                        # Prefer right-side controls and explicit Item-details label.
                        score = y + (200.0 if "item details" in lab.lower() else 0.0)
                        score += x  # rightward View wins ties
                        picks.append((score, y, x, v, lab))
                except Exception:  # noqa: BLE001
                    continue
            if picks:
                picks.sort(key=lambda t: (-("item details" in t[4].lower()), -t[2], t[1]))
                _score, _y, _x, el, lab = picks[0]
                by, value = AppiumBy.IOS_PREDICATE, f"View:{lab}"
            else:
                el, by, value = find_first(driver, strategies, timeout=2.0)
        except TimeoutError:
            el = None

        if el is not None:
            try:
                el.click()
            except Exception:  # noqa: BLE001
                r = el.rect or {}
                _tap_center(
                    driver,
                    float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0,
                    float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0,
                )
            markers.mark("checkout_item_details_view", by=by, value=value)
            _mark_step(
                outcome, "checkout_item_details_view", status="ok", by=by, value=value
            )
            print(f"  Item details View: {by} = {value!r}")
            time.sleep(_MED)
            if _item_details_sheet_visible(driver):
                return
            # Wrong tap may open payment / scroll away — recover.
            if _checkout_payment_screen_visible(driver):
                print("  landed on payment — scrolling back to Item details …")
                for _ in range(4):
                    _scroll_up(driver, percent=0.45)
                    time.sleep(_FAST)
            continue

        # Hunt for View: slight down first, then up if payment shows.
        if _checkout_payment_screen_visible(driver):
            _scroll_up(driver, percent=0.40)
        else:
            _scroll_down(driver, percent=0.22)
        time.sleep(_FAST)
        print(f"  View not confirmed (attempt {attempt + 1}/4)")

    # Coord fallback near typical Item details row (upper-mid, right).
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
        for cy_frac in (0.48, 0.42, 0.55):
            _tap_center(driver, w * 0.88, h * cy_frac)
            time.sleep(_MED)
            if _item_details_sheet_visible(driver):
                markers.mark("checkout_item_details_view", via="coord", y_frac=cy_frac)
                _mark_step(
                    outcome, "checkout_item_details_view", status="ok", via="coord"
                )
                print(f"  Item details View → coord y_frac={cy_frac}")
                return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f'Could not tap Item details "View": {exc}') from exc

    raise AssertionError("Item details sheet did not open after View")


def _scroll_item_details_list(
    driver, *, direction: str = "down", percent: float = 0.14
) -> None:
    """Scroll inside Item details sheet without dismissing it.

    Keep a short drag in the sheet body only — long swipes dismiss the modal.
    """
    try:
        size = driver.get_window_size() or {}
        w = int(size.get("width") or 390)
        h = int(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        w, h = 390, 844
    # Stay in mid sheet (below title, above Done). Short travel only.
    delta = max(40, int(h * max(0.08, min(percent, 0.22))))
    mid = int(h * 0.52)
    if direction == "up":
        from_y, to_y = mid - delta // 2, mid + delta // 2
    else:
        from_y, to_y = mid + delta // 2, mid - delta // 2
    try:
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.12,
                "fromX": int(w * 0.50),
                "fromY": from_y,
                "toX": int(w * 0.50),
                "toY": to_y,
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _item_details_increment_els(
    driver, *, min_y: float | None = None, y_hi_frac: float = 0.88
) -> list[tuple[object, float, float, float]]:
    """Visible Item details qty + buttons as (el, y, cx, cy), top → bottom."""
    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
        win_w = float(size.get("width") or 390)
    except Exception:  # noqa: BLE001
        h, win_w = 844.0, 390.0
    candidates: list[tuple[object, float, float, float]] = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(name == "stepper.incrementButton" OR '
            'label CONTAINS[c] "Increase quantity")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:30]:
        try:
            r = el.rect or {}
            y = float(r.get("y", 0))
            x = float(r.get("x", 0))
            ww = float(r.get("width", 0))
            hh = float(r.get("height", 0))
            # Predicate already selects increment; skip extra name lookup (WDA snapshot).
            if y < 150 or y > h * y_hi_frac:
                continue
            if min_y is not None and y < min_y:
                continue
            if ww < 12 or hh < 12 or ww > 200:
                continue
            if x + ww / 2.0 < win_w * 0.45:
                continue
            candidates.append((el, y, x + ww / 2.0, y + hh / 2.0))
        except Exception:  # noqa: BLE001
            continue
    candidates.sort(key=lambda t: (round(t[1] / 8.0), -t[2]))
    unique: list[tuple[object, float, float, float]] = []
    for c in candidates:
        if any(abs(c[1] - u[1]) < 28 for u in unique):
            continue
        unique.append(c)
    return unique


def _item_details_increments(
    driver, *, min_y: float | None = None, y_hi_frac: float = 0.88
) -> list[tuple[float, float, float]]:
    """Visible qty + controls on Item details sheet (top → bottom) as (y,cx,cy)."""
    return [
        (y, cx, cy)
        for _el, y, cx, cy in _item_details_increment_els(
            driver, min_y=min_y, y_hi_frac=y_hi_frac
        )
    ]


def _parse_increment_label(label: str) -> tuple[str, int | None]:
    """Product key + current qty from one increment-button label."""
    key = ""
    m = _INC_ITEM_RE.search(label or "")
    if m:
        key = m.group(1).strip()[:48]
    qm = _CURRENT_QTY_RE.search(label or "")
    qty = int(qm.group(1)) if qm else None
    return key, qty


def _read_increment_label(el) -> tuple[str, int | None]:
    try:
        label = el.get_attribute("label") or ""
    except Exception:  # noqa: BLE001
        return "", None
    return _parse_increment_label(label)


def _snapshot_item_details_rows(
    driver, *, y_hi_frac: float = 0.72
) -> list[tuple[float, float, str, int | None]]:
    """One a11y scan → (cx, cy, item_key, qty) in the tappable band only.

    Keep y below ~0.72 so + is not under Done. Coord taps only afterward.
    """
    rows: list[tuple[float, float, str, int | None]] = []
    for el, _y, cx, cy in _item_details_increment_els(
        driver, y_hi_frac=y_hi_frac
    ):
        key, qty = _read_increment_label(el)
        rows.append((cx, cy, key, qty))
    return rows


def _burst_coord_plus(driver, *, cx: float, cy: float, n: int) -> int:
    """Rapid + taps at fixed coords — no element/tree lookups."""
    taps = 0
    for _ in range(max(0, n)):
        _tap_center(driver, cx, cy)
        taps += 1
        time.sleep(_QTY_TAP_GAP)
    return taps


def _increment_frame_ok(
    x: float, y: float, ww: float, hh: float, *, win_w: float, h: float
) -> bool:
    """Reject collapsed off-screen steppers (Walmart reports x≈16,y≈257)."""
    if ww < 8 or hh < 8 or ww > 200:
        return False
    if y < 0 or y > h * 1.15:
        return False
    # Real + sits on the right; Remove / phantom frames sit far left.
    if x + ww / 2.0 < win_w * 0.45:
        return False
    return True


def _qty_el_for_key(
    driver, item_key: str, *, near_y: float
) -> tuple[object, float, float, int | None] | None:
    """Targeted find for one SKU's + button → (el, cx, cy, qty)."""
    if not item_key:
        return None
    safe = item_key.replace("\\", "\\\\").replace('"', '\\"')[:40]
    try:
        size = driver.get_window_size() or {}
        win_w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        win_w, h = 390.0, 844.0
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            'name == "stepper.incrementButton" AND '
            f'label CONTAINS[c] "{safe}"',
        )
    except Exception:  # noqa: BLE001
        return None
    best: tuple[float, object, float, float, int | None] | None = None
    for el in els[:6]:
        try:
            r = el.rect or {}
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            ww = float(r.get("width", 0))
            hh = float(r.get("height", 0))
            if not _increment_frame_ok(x, y, ww, hh, win_w=win_w, h=h):
                continue
            cx, cy = x + ww / 2.0, y + hh / 2.0
            _key, qty = _read_increment_label(el)
            score = abs(cy - near_y)
            if best is None or score < best[0]:
                best = (score, el, cx, cy, qty)
        except Exception:  # noqa: BLE001
            continue
    if best is None:
        return None
    return best[1], best[2], best[3], best[4]


def _qty_for_key_cheap(
    driver, item_key: str, *, near_y: float
) -> tuple[float, float, int | None] | None:
    """Single targeted find for one SKU (coords + qty only)."""
    hit = _qty_el_for_key(driver, item_key, near_y=near_y)
    if hit is None:
        return None
    _el, cx, cy, qty = hit
    return cx, cy, qty


def _bring_row_into_band(
    driver,
    item_key: str,
    *,
    cx: float,
    cy: float,
    y_safe: float,
    y_top: float = 170.0,
) -> tuple[object | None, float, float, int | None]:
    """Re-locate one SKU and nudge-scroll until its + is tappable.

    Raising a line reflows the sheet, so rows below shift down under the
    footer (visible=false) where taps silently miss.
    """
    el: object | None = None
    qty: int | None = None
    try:
        h = float((driver.get_window_size() or {}).get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    for _ in range(6):
        found = _qty_el_for_key(driver, item_key, near_y=cy)
        if found is None:
            _scroll_item_details_list(driver, direction="down", percent=0.18)
            time.sleep(_FAST)
            continue
        el, cx, cy, qty = found
        # Prefer mid-band (clear of title + Done). Skip off-screen (y < 0).
        if y_top <= cy <= y_safe:
            return el, cx, cy, qty
        aim = h * 0.42
        _scroll_item_details_list(
            driver,
            direction="down" if cy > y_safe else "up",
            percent=max(0.12, min(0.22, abs(cy - aim) / h)),
        )
        time.sleep(_FAST)
    return el, cx, cy, qty


def _click_plus(el, driver, *, cx: float, cy: float) -> bool:
    """Tap the + via elementId / right-biased coords — never el.click().

    Walmart nests steppers under Remove in the a11y tree; el.click() and
    left-biased taps can activate Remove and delete the line.
    """
    try:
        eid = getattr(el, "id", None)
        if eid:
            driver.execute_script("mobile: tap", {"elementId": eid})
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        r = el.rect or {}
        x = float(r.get("x", 0))
        y = float(r.get("y", 0))
        ww = float(r.get("width", 0))
        hh = float(r.get("height", 0))
        if ww >= 8 and hh >= 8:
            # Bias right so the hit stays on + (Remove sits to the left).
            cx = x + ww * 0.72
            cy = y + hh / 2.0
    except Exception:  # noqa: BLE001
        pass
    try:
        _tap_center(driver, cx, cy)
        return True
    except Exception:  # noqa: BLE001
        return False


def _unfinished_item_keys(
    driver, *, done_keys: set[str], target_qty: int
) -> list[tuple[str, float, float, int]]:
    """Item details SKUs still below target.

    Prefer rows with a real on-screen + frame. Off-screen rows often report a
    collapsed phantom rect (x≈16) — keep them only as scroll hunt hints with
    cy forced below the safe band so we scroll instead of tapping Remove.
    """
    try:
        size = driver.get_window_size() or {}
        win_w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        win_w, h = 390.0, 844.0
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            'name == "stepper.incrementButton"',
        )
    except Exception:  # noqa: BLE001
        return []
    real: list[tuple[str, float, float, int]] = []
    phantom: list[tuple[str, float, float, int]] = []
    seen: set[str] = set()
    for el in els[:40]:
        try:
            key, qty = _read_increment_label(el)
            if not key or key in done_keys or key in seen:
                continue
            if qty is not None and qty >= target_qty:
                continue
            r = el.rect or {}
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            ww = float(r.get("width", 0))
            hh = float(r.get("height", 0))
            seen.add(key)
            start = qty if qty is not None else 1
            if _increment_frame_ok(x, y, ww, hh, win_w=win_w, h=h):
                real.append((key, x + ww / 2.0, y + hh / 2.0, start))
            else:
                # Hint only — cy below band forces hunt-scroll, never a tap.
                phantom.append((key, win_w * 0.85, h * 0.92, start))
        except Exception:  # noqa: BLE001
            continue
    real.sort(key=lambda t: t[2])
    phantom.sort(key=lambda t: t[0])
    return real + phantom


class ItemDetailsClosed(AssertionError):
    """The Item details sheet is no longer open — never tap on Checkout."""


def _dismiss_tech_issue_alert(driver) -> bool:
    """Walmart "Sorry! We're having a technical issue. Please try again." —
    every tap is ignored until OK is pressed."""
    try:
        if not driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND label CONTAINS[c] "having a technical issue"',
        ):
            return False
        for el in driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "OK" OR name == "OK")',
        )[:2]:
            el.click()
            print('  "technical issue" alert → OK')
            time.sleep(1.5)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _raise_one_item_details_line(
    driver,
    markers: MarkerLog,
    *,
    line_no: int,
    cx: float,
    cy: float,
    item_key: str,
    start_qty: int,
    target_qty: int,
    y_safe: float,
    relocate: bool = False,
) -> tuple[int, int]:
    """Raise one line to target via element.click() (coord taps miss after scroll).

    Walmart drops presses while syncing the cart, so verify and top up the
    shortfall. Never tap more than the verified shortfall.
    """
    current = start_qty if start_qty > 0 else 1
    taps = 0
    el: object | None = None
    alerts = 0
    tap_gap = _QTY_TAP_GAP
    if relocate and item_key:
        el, cx, cy, qty = _bring_row_into_band(
            driver, item_key, cx=cx, cy=cy, y_safe=y_safe
        )
        if qty is not None:
            current = start_qty = qty
    rounds = 0
    while rounds < _QTY_MAX_ROUNDS + min(alerts, 6):
        rounds += 1
        if _dismiss_tech_issue_alert(driver):
            alerts += 1
            tap_gap = max(tap_gap, 0.4)  # rapid updates trigger the alert
            el = None
        if current == target_qty:
            break
        # Safety: if the sheet closed, these coordinates are on Checkout
        # (which has Place order) — stop without tapping.
        if not _item_details_sheet_visible(driver):
            raise ItemDetailsClosed(
                f"Item details sheet closed during line #{line_no}"
            )
        if item_key:
            el, cx, cy, qty = _bring_row_into_band(
                driver, item_key, cx=cx, cy=cy, y_safe=y_safe
            )
            if qty is not None:
                current = qty
            if current == target_qty:
                break
        if current < target_qty:
            need = target_qty - current
            # Fresh element each burst — stale refs after scroll are dead.
            if el is None and item_key:
                hit = _qty_el_for_key(driver, item_key, near_y=cy)
                if hit is not None:
                    el, cx, cy, _q = hit
            for _ in range(need):
                if el is not None:
                    if not _click_plus(el, driver, cx=cx, cy=cy):
                        break
                else:
                    _tap_center(driver, cx, cy)
                taps += 1
                time.sleep(tap_gap)
                # Rebind every few clicks — sheet reflows as qty grows.
                if taps % 3 == 0 and item_key:
                    hit = _qty_el_for_key(driver, item_key, near_y=cy)
                    if hit is not None:
                        el, cx, cy, qty = hit
                        if qty is not None:
                            current = qty
                        if current >= target_qty:
                            break
                        try:
                            if str(el.get_attribute("enabled")).lower() == "false":
                                break
                        except Exception:  # noqa: BLE001
                            pass
        else:
            for _ in range(current - target_qty):
                _tap_center(driver, max(20.0, cx - 86.0), cy)
                taps += 1
                time.sleep(_QTY_TAP_GAP)
        time.sleep(_QTY_SETTLE)
        if _dismiss_tech_issue_alert(driver):
            alerts += 1
            tap_gap = max(tap_gap, 0.4)
        verified = _qty_el_for_key(driver, item_key, near_y=cy) if item_key else None
        if verified is None:
            el = None
            continue
        el, cx, cy, qty = verified
        if qty is not None:
            current = qty

    markers.mark(
        "item_details_qty_plus_tapped",
        line=line_no,
        taps=taps,
        start_qty=start_qty,
        final_qty=current,
        item=(item_key or "")[:32] or None,
        x=cx,
        y=cy,
    )
    if current < target_qty:
        raise AssertionError(
            f"Item details line #{line_no}: qty ×{current} "
            f"< target ×{target_qty} after {taps} + tap(s)"
            + (f" [{item_key}]" if item_key else "")
        )
    if current > target_qty:
        raise AssertionError(
            f"Item details line #{line_no}: qty ×{current} "
            f"> target ×{target_qty} (could not trim)"
        )
    return start_qty, current


def _increase_item_details_qty(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    target_qty: int,
    expected_items: int | None = None,
) -> None:
    """Raise every Item details line to ``target_qty`` — fast coord bursts.

    One screen scan per visible batch; tap all in-band rows before scrolling.
    Avoids per-tap / per-row full-tree snapshots (~12s each on Checkout).
    """
    increase_by = max(0, target_qty - 1)
    if target_qty <= 1:
        print("Step: Item details qty increase skipped (units_each=1) …")
        outcome["checkout_item_details_qty_increased_by"] = 0
        outcome["checkout_item_details_lines_increased"] = 0
        markers.mark("item_details_qty_increase_skipped", reason="units_each_is_1")
        return

    end = time.monotonic() + 2.5
    while time.monotonic() < end and not _item_details_sheet_visible(driver):
        time.sleep(0.12)
    if not _item_details_sheet_visible(driver):
        raise AssertionError(
            "Item details sheet not open — cannot increase quantities"
        )

    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    # Only tap steppers clearly above Done.
    y_safe = h * 0.70

    print(
        f"Step: on Item details raise EVERY line to ×{target_qty} "
        f"(fast coord burst)"
        + (f" — {expected_items} lines expected" if expected_items else "")
        + " …"
    )

    lines_done = 0
    stagnant = 0
    done_keys: set[str] = set()
    max_rounds = max(12, (expected_items or 8) * 3)

    for round_i in range(max_rounds):
        if expected_items is not None and lines_done >= expected_items:
            break
        if not _item_details_sheet_visible(driver):
            print("  Item details sheet closed — reopening …")
            try:
                _tap_checkout_item_details_view(driver, markers, outcome)
                stagnant = 0
                time.sleep(_MED)
            except Exception as exc:  # noqa: BLE001
                print(f"  reopen failed: {exc}")
                break
            continue

        # Always take the topmost unfinished SKU (incl. under Done). Visible-only
        # snapshots skip buried rows and jump to Roma while Yellow sits at ×1 —
        # later scrolls then can't find it if Remove was hit mid-raise.
        unfinished = _unfinished_item_keys(
            driver, done_keys=done_keys, target_qty=target_qty
        )
        rows = _snapshot_item_details_rows(driver, y_hi_frac=0.88)
        for _cx, _cy, key, qty in rows:
            if key and qty is not None and qty >= target_qty:
                done_keys.add(key)

        pending: list[tuple[float, float, str, int]] = []
        if unfinished:
            key, cx, cy, start_qty = unfinished[0]
            _el, cx, cy, qty = _bring_row_into_band(
                driver, key, cx=cx, cy=cy, y_safe=y_safe
            )
            if qty is not None:
                start_qty = qty
            if qty is not None and qty >= target_qty:
                done_keys.add(key)
                continue
            if 170 <= cy <= y_safe:
                pending = [(cx, cy, key, start_qty)]

        if not pending:
            stagnant += 1
            if stagnant >= 12:
                break
            _scroll_item_details_list(
                driver,
                direction="up" if stagnant in (4, 7, 10) else "down",
                percent=0.16,
            )
            time.sleep(_FAST)
            markers.mark(
                "item_details_qty_scrolled",
                index=round_i + 1,
                lines_done=lines_done,
                reason="hunt",
            )
            continue

        stagnant = 0
        # One row per round — raising qty reflows the sheet and invalidates
        # sibling coords (rows slide under Done → silent tap misses).
        cx, cy, key, start_qty = pending[0]
        if key and key in done_keys:
            continue
        line_no = lines_done + 1
        start_qty, final_qty = _raise_one_item_details_line(
            driver,
            markers,
            line_no=line_no,
            cx=cx,
            cy=cy,
            item_key=key,
            start_qty=start_qty,
            target_qty=target_qty,
            y_safe=y_safe,
            relocate=True,
        )
        print(
            f"  item details line #{line_no}: ×{start_qty}→×{final_qty} "
            f"(target ×{target_qty})"
            + (f" [{key[:36]}]" if key else "")
            + f" @ ({cx:.0f},{cy:.0f})"
        )
        markers.mark(
            "item_details_line_qty_increased",
            line=line_no,
            start_qty=start_qty,
            final_qty=final_qty,
            target_qty=target_qty,
            item=key[:48] if key else None,
            y=cy,
        )
        lines_done += 1
        if key:
            done_keys.add(key)
        # No forced scroll — next round rescans; hunt-scroll only when pending empty.

    outcome["checkout_item_details_qty_increased_by"] = increase_by
    outcome["checkout_item_details_lines_increased"] = lines_done
    _mark_step(
        outcome,
        "checkout_item_details_qty_increase",
        status=(
            "ok"
            if expected_items is None or lines_done >= expected_items
            else "partial"
        ),
        lines=lines_done,
        increase_by=increase_by,
        target_qty=target_qty,
        expected_items=expected_items,
    )
    print(
        f"  item details qty + done: {lines_done} line(s) → ×{target_qty}"
        + (
            f" (expected {expected_items})"
            if expected_items is not None
            else ""
        )
    )
    if expected_items is not None and lines_done < expected_items:
        raise AssertionError(
            f"Only increased qty on {lines_done}/{expected_items} "
            f"Item details line(s) to ×{target_qty}"
        )


def _tap_item_details_done(driver, markers: MarkerLog, outcome: dict) -> None:
    print('Step: on Item details tap "Done" …')
    strategies = (
        (AppiumBy.ACCESSIBILITY_ID, "Done"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Done" OR name == "Done")',
        ),
    )
    try:
        el, by, value = find_first(driver, strategies, timeout=3.0)
        el.click()
    except TimeoutError:
        try:
            size = driver.get_window_size() or {}
            w = float(size.get("width") or 390)
            h = float(size.get("height") or 844)
            _tap_center(driver, w * 0.50, h * 0.92)
            by, value = "coord", "Done"
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f'Could not tap Item details "Done": {exc}') from exc
    markers.mark("item_details_done", by=by, value=value)
    _mark_step(outcome, "item_details_done", status="ok", by=by, value=value)
    print(f"  Done: {by} = {value!r}")
    time.sleep(_MED)


def run_test_case_walmart_4(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
    basket_size: str | None = None,
) -> dict:
    """Walmart TC4: basket A–H + qty + on Checkout Item details (not Cart)."""
    if retailer != "walmart":
        raise ValueError(
            f"{TEST_CASE_NAME} is Walmart-only (use --retailer walmart)."
        )

    sample = resolve_basket_sample(basket_size)
    qty_plus = max(0, sample.units_each - 1)
    # Item details qty+ for E/H/G often exceeds the shared 240s Cart cap;
    # Appium timeLimit cuts the file mid-increments. Keep recording until
    # Done → Checkout, then stop_recording_screen() saves the full clip.
    rec_limit = max(
        600,
        int(getattr(settings, "recording_time_limit_seconds", 600) or 600),
        CART_RECORDING_SECONDS,
    )
    plan = describe_session(settings, retailer, bundle_id)
    plan["scenario"] = TEST_CASE_ID
    plan["test_case"] = TEST_CASE_NAME
    plan["dry_run"] = dry_run
    plan["study_mode"] = "cancel_test"
    plan["recording"] = True
    plan["recording_starts_at"] = "cart_page"
    plan["recording_duration_seconds"] = rec_limit
    plan["basket_sample"] = sample.letter
    plan["basket_num_items"] = sample.num_items
    plan["basket_units_each"] = sample.units_each
    plan["basket_total_units"] = sample.total_units
    plan["basket_matrix"] = {
        k: {"num_items": v.num_items, "units_each": v.units_each, "total": v.total_units}
        for k, v in BASKET_SAMPLES.items()
    }
    plan["checkout_item_details_qty_increase_by"] = qty_plus
    plan["place_order"] = False
    plan["steps"] = [
        "Start new automation session (kill prior Walmart if running)",
        "Open Walmart App (cold start)",
        'Tap bottom tab "My Items"',
        "Clear existing cart items if any (so + Add is available)",
        'Tap "Fresh Produce" filter (All Items for large baskets)',
        (
            f"Basket {sample.letter}: tap blue + Add once on {sample.num_items} "
            f"item(s) (qty=1 on My Items; units set on Checkout Item details)"
        ),
        "Open Cart screen",
        f"START screen recording on Cart for {rec_limit}s",
        'Tap "Change" (delivery) on Cart → Reserve a time',
        'Tap "Change" (address) → select 2nd saved address',
        'Toggle delivery day last↔2nd-last + time slot last↔2nd-last → tap "Reserve"',
        'Tap "Continue to checkout"',
        'On "Missing anything?" tap "Continue"',
        "Land on Checkout screen",
        'Tap Item details "View"',
        (
            f"On Item details: every line qty +{qty_plus} (to ×{sample.units_each})"
            if qty_plus
            else "On Item details: skip qty bump (units_each=1)"
        ),
        'Tap "Done" → remain on Checkout → stop recording (do not place order)',
    ]
    plan["notes"] = (
        f"{TEST_CASE_NAME} — basket {sample.letter} "
        f"({sample.num_items}×{sample.units_each}={sample.total_units}): "
        "same as Walmart TC2 through Checkout, but unit scaling is on "
        "Checkout → Item details → View (not Cart). Does not place the order."
    )

    if dry_run:
        print(json.dumps({"status": "dry_run_ok", "plan": plan}, indent=2))
        return {"status": "dry_run_ok", "plan": plan}

    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    prefix = f"{retailer}_{TEST_CASE_ID}_{sample.letter}_{stamp}"
    result_path = settings.artifacts_dir / f"{prefix}.json"
    page_source_path = settings.artifacts_dir / f"{prefix}_page.xml"
    screenshot_path = settings.artifacts_dir / f"{prefix}_blocker.png"
    video_path = settings.artifacts_dir / f"{TEST_CASE_ID}_{sample.letter}_{stamp}.mp4"

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
        "basket_sample": sample.letter,
        "basket_num_items": sample.num_items,
        "basket_units_each": sample.units_each,
        "basket_total_units": sample.total_units,
        "items_added_count": 0,
        "cart_page_opened": False,
        "recording_started": False,
        "place_order": False,
        "checkout_item_details_qty_increased_by": 0,
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
        try:
            find_first(driver, FRESH_PRODUCE_STRATEGIES, timeout=1.2)
        except TimeoutError:
            _tap_my_items_tab(driver, markers, outcome)
        # Prime catalog (handles empty Reorder after cold start).
        outcome["_tc4_catalog_ok"] = _prime_my_items_catalog(
            driver, markers, outcome
        )
        _add_basket_fast(driver, markers, outcome, sample=sample)
        _open_cart(driver, markers, outcome)

        print(
            f"START RECORDING at Cart page for {rec_limit}s "
            f"(checkout steps run while recording) …"
        )
        try:
            start_ios_screen_recording(driver, time_limit=rec_limit)
            recording = True
            outcome["recording_started"] = True
            markers.mark(
                "recording_started_at_cart",
                duration_seconds=rec_limit,
                basket=sample.letter,
            )
            print(f"Screen recording started — recording for up to {rec_limit}s …")
        except Exception as exc:  # noqa: BLE001
            outcome["recording_started"] = False
            outcome["recording_start_error"] = str(exc)
            print(f"Screen recording failed to start: {exc}")
            raise AssertionError(
                f"Could not start screen recording on cart: {exc}"
            ) from exc

        _tap_cart_change_delivery_fast(driver, markers, outcome)
        _tap_reserve_change_address(driver, markers, outcome)
        _select_second_address(driver, markers, outcome)
        _change_day_and_slot_then_reserve(driver, markers, outcome)
        # No cart qty bump — units are scaled on Checkout Item details.
        _tap_continue_to_checkout_tc4(driver, markers, outcome)
        _tap_missing_anything_continue(driver, markers, outcome)
        _wait_checkout_screen(driver, markers, outcome)
        _dismiss_walmart_plus_checkout_splash(driver, markers)

        _tap_checkout_item_details_view(driver, markers, outcome)
        _increase_item_details_qty(
            driver,
            markers,
            outcome,
            target_qty=sample.units_each,
            expected_items=sample.num_items,
        )
        _tap_item_details_done(driver, markers, outcome)
        _wait_checkout_screen(driver, markers, outcome)

        markers.mark(
            "test_case_walmart_4_stopped_on_checkout",
            basket=sample.letter,
            items=outcome.get("items_added_count"),
            units_each=sample.units_each,
            qty_plus=outcome.get("checkout_item_details_qty_increased_by"),
            lines=outcome.get("checkout_item_details_lines_increased"),
            checkout_steps=outcome.get("checkout_steps"),
        )
        print("Stopping process on Checkout screen (order not placed) …")

        outcome["status"] = "ok"
        outcome["message"] = (
            f"{TEST_CASE_NAME} OK — basket {sample.letter}: "
            f"{sample.num_items}×{sample.units_each}={sample.total_units} "
            f"(add {outcome.get('basket_add_elapsed_seconds')}s) → cart record → "
            f"Change delivery → Change address (2nd) → day/slot → Reserve → "
            f"Continue to checkout → Item details View → "
            f"all lines qty +{outcome.get('checkout_item_details_qty_increased_by')} "
            f"({outcome.get('checkout_item_details_lines_increased')} lines) → Done"
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
                markers.mark("recording_stopped", saved=bool(video_b64))
            except Exception as rec_exc:  # noqa: BLE001
                outcome["recording_stop_error"] = str(rec_exc)
                print(f"Recording stop error: {rec_exc}")
        if driver is not None:
            try:
                driver.quit()
                print("Appium session stopped.")
            except Exception:  # noqa: BLE001
                pass
        try:
            result_path.write_text(
                json.dumps(outcome, indent=2, default=str), encoding="utf-8"
            )
            print(f"Wrote result → {result_path}")
        except Exception as write_exc:  # noqa: BLE001
            print(f"Result write error: {write_exc}")
