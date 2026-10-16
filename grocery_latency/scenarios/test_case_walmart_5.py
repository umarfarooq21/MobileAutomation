"""Test Case Walmart 5: My Items search "low cost products" → basket A–E/G/H → Cart slot change.

Walmart (iOS):
  1. Open Walmart app (cold start)
  2. Tap bottom tab "My Items"
  3. Top search bar → search "low cost products"
  4. On search results tap Add to cart on ``num_items`` products; raise each
     to ``units_each`` with the listing stepper + (basket A–E, G, H)
  5. Open Cart → start screen recording
  6. Tap "Change" (delivery) → Reserve a time (day + time slots)
  7. Change day + time slot → tap "Reserve"
  8. Tap "Continue to checkout" → stop recording

Basket matrix (``--basket-size`` / ``BASKET_SIZE``):
  A 1×1, B 10×1, C 1×25, D 25×1, E 5×10, G 50×5, H 10×10.

Does not place the order. Units are set on the search listing (before
recording), not on Cart / Checkout like Walmart TC2–TC4.
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
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
from grocery_latency.scenarios.test_case_walmart_1 import (
    CART_RECORDING_SECONDS,
    WALMART_DISMISS_LABELS,
    _apply_fast_wda_settings,
    _cart_icon_looks_empty,
    _change_day_and_slot_then_reserve,
    _checkout_screen_visible,
    _clear_cart_items,
    _dismiss_ask_sparky_if_shown,
    _dismiss_system_share_sheet,
    _mark_step,
    _open_cart,
    _scroll_down,
    _tap_center,
    _tap_my_items_tab,
)
from grocery_latency.scenarios.test_case_walmart_2 import (
    BASKET_SAMPLES,
    _dismiss_add_blockers,
    resolve_basket_sample,
)
from grocery_latency.scenarios.test_case_walmart_4 import (
    _dismiss_blocking_alerts,
    _open_and_clear_search,
    _submit_search,
    _tap_cart_change_delivery_fast,
    _tap_continue_to_checkout_tc4,
    _type_search_query,
)

TEST_CASE_NAME = "Test Case Walmart 5"
TEST_CASE_ID = "test_case_walmart_5"
SEARCH_QUERY = "low cost products"
ALLOWED_BASKETS = ("A", "B", "C", "D", "E", "F", "G", "H")

_IN_CART_RE = re.compile(r"(\d+)\s+in cart", re.I)
_RESULTS_RE = re.compile(r"^[\d,]+\s+Results?$", re.I)
_CART_BADGE_RE = re.compile(r"cart contains (\d+) items?", re.I)
_QTY_TAP_GAP = 0.12
_QTY_SETTLE = 0.6
_QTY_MAX_ROUNDS = 4
_FAST = 0.05
_MED = 0.10
_SETTLE = 0.22


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _resolve_tc5_basket(letter: str | None):
    sample = resolve_basket_sample(letter)
    if sample.letter not in ALLOWED_BASKETS:
        raise ValueError(
            f"{TEST_CASE_NAME} supports baskets {', '.join(ALLOWED_BASKETS)} "
            f"(got {sample.letter!r})"
        )
    return sample


def _search_my_items(
    driver, markers: MarkerLog, outcome: dict, query: str = SEARCH_QUERY
) -> None:
    """My Items top search bar → type ``query`` → submit."""
    print(f'Step: My Items top search bar → search "{query}" …')
    field = _open_and_clear_search(driver)
    _type_search_query(driver, field, query)
    time.sleep(_MED)
    _dismiss_blocking_alerts(driver, markers)
    _submit_search(driver, query)
    # Wait for result tiles (Add buttons or in-cart steppers).
    end = time.monotonic() + 8.0
    while time.monotonic() < end:
        _dismiss_blocking_alerts(driver, markers)
        root = _snapshot(driver)
        if root is not None and (
            _collect_result_adds(root, set()) or _result_steppers(root)
        ):
            break
        time.sleep(0.3)
    markers.mark("search_submitted", query=query)
    _mark_step(outcome, "search", status="ok", query=query)


# --- page_source snapshot helpers (one WDA round trip per read) -------------


def _snapshot(driver):
    try:
        return ET.fromstring(driver.page_source)
    except Exception:  # noqa: BLE001
        return None


def _attrs(node) -> tuple[str, str, str]:
    return (
        node.get("type") or "",
        node.get("name") or "",
        node.get("label") or "",
    )


def _rect(node) -> tuple[float, float, float, float]:
    return (
        float(node.get("x") or 0),
        float(node.get("y") or 0),
        float(node.get("width") or 0),
        float(node.get("height") or 0),
    )


def _on_results_page(root) -> bool:
    """Search results list: "N Results" header, Add buttons or in-cart steppers."""
    for node in root.iter():
        typ, name, label = _attrs(node)
        if typ == "XCUIElementTypeStaticText" and _RESULTS_RE.match(label):
            return True
        if name in ("stepper.mainButton", "stepper.incrementButton"):
            return True
    return False


_CONFIRM_ADDRESS_YES = "Yes, this is the right address"


def _confirm_delivery_address(driver, markers: MarkerLog | None = None) -> bool:
    """"Want it delivered? Let's make sure this is the right address." sheet:
    tap the blue "Yes, this is the right address" (its X can cancel)."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            f'type == "XCUIElementTypeButton" AND '
            f'(label == "{_CONFIRM_ADDRESS_YES}" OR name == "{_CONFIRM_ADDRESS_YES}")',
        )
    except Exception:  # noqa: BLE001
        return False
    for el in els[:2]:
        try:
            el.click()
            print('  "Want it delivered?" — tapped "Yes, this is the right address"')
            if markers is not None:
                markers.mark("delivery_address_confirmed")
            time.sleep(1.0)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _popup_kind(root) -> str | None:
    """Sheets / pages that cover the results list (see TC5 screenshots)."""
    for node in root.iter():
        typ, name, label = _attrs(node)
        if node.get("visible") == "false" and typ != "XCUIElementTypeStaticText":
            continue
        low = label.lower()
        if "set up subscription" in low:
            return "subscription"
        if low.startswith("images ("):
            return "image_gallery"
        if "unable to add to cart" in low or "technical issue" in low:
            return "unable_to_add"
        if "airdrop" in low or (typ == "XCUIElementTypeStaticText" and label == "Share"):
            return "share"
        if (
            "select similar items" in low
            or "replace this item" in low
            or "ship this item separately" in low
        ):
            # Non-grocery item can't ship with the basket (accidental Add).
            return "similar_items"
        if "missing anything" in low:
            return "missing_anything"
        if label == _CONFIRM_ADDRESS_YES or "make sure this is the right address" in low:
            return "confirm_address"
        if name in (
            "TitleView_productTitle",
            "BrandNameView_productRating",
            "VisualMediaCarouselOverlay_container",
        ) or name.startswith("PageIndicatorView_"):
            return "product_page"
    # Catch-all: any other sheet that shows a close X at its top-right.
    app = next(root.iter("XCUIElementTypeApplication"), None)
    w = float(app.get("width") or 390) if app is not None else 390.0
    h = float(app.get("height") or 844) if app is not None else 844.0
    for node in root.iter("XCUIElementTypeButton"):
        if node.get("visible") == "false":
            continue
        lab = (node.get("label") or node.get("name") or "").strip().lower()
        if lab not in ("close", "dismiss", "xmark", "close sheet"):
            continue
        x, y, _bw, _bh = _rect(node)
        if x > w * 0.70 and 90 < y < h * 0.55:
            return "sheet_with_close"
    return None


def _close_popup(driver, root, kind: str, w: float, h: float) -> str:
    """Close one popup layer: X / Close / Got it, else header Back."""
    if kind == "confirm_address" and _confirm_delivery_address(driver):
        return f"button:{_CONFIRM_ADDRESS_YES}"
    for label in ("Close", "close", "Dismiss", "Got it", "Got It", "Not now", "Not Now"):
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                f'type == "XCUIElementTypeButton" AND visible == 1 AND '
                f'(label == "{label}" OR name == "{label}")',
            )
        except Exception:  # noqa: BLE001
            els = []
        for el in els[:2]:
            try:
                el.click()
                return f"button:{label}"
            except Exception:  # noqa: BLE001
                continue
    # Unlabeled sheet X: small button at the top-right of the sheet.
    best = None
    for node in root.iter("XCUIElementTypeButton"):
        _typ, name, label = _attrs(node)
        x, y, bw, bh = _rect(node)
        if x < w * 0.75 or bw > 64 or bh > 64 or y < 100 or y > h * 0.62:
            continue
        if label and label.lower() not in ("close", "xmark", "dismiss", "x"):
            continue
        if best is None or y < best[1]:
            best = (x + bw / 2.0, y + bh / 2.0)
    if best is not None:
        _tap_center(driver, *best)
        return "sheet_x"
    # Full product page: header back chevron returns to results.
    _tap_center(driver, w * 0.07, 56.0)
    return "header_back"


def _clear_popups(driver, markers: MarkerLog, w: float, h: float):
    """Dismiss popups until the results list is clear. Returns fresh snapshot."""
    root = _snapshot(driver)
    for _ in range(5):
        if root is None:
            time.sleep(_SETTLE)
            root = _snapshot(driver)
            continue
        kind = _popup_kind(root)
        if kind is None:
            return root
        if kind == "share":
            _dismiss_system_share_sheet(driver, markers)
        via = _close_popup(driver, root, kind, w, h)
        markers.mark("popup_dismissed", kind=kind, via=via)
        print(f"  popup {kind!r} dismissed via {via}")
        time.sleep(0.35)
        _dismiss_ask_sparky_if_shown(driver, markers)
        root = _snapshot(driver)
    return root


def _settled_snapshot(driver, markers: MarkerLog, w: float, h: float):
    """Snapshot once scroll momentum stops (Add positions equal twice in a row).

    A snapshot taken mid-inertia gives stale y; tapping it hits the product
    image and opens the PDP instead of adding.
    """
    root = _clear_popups(driver, markers, w, h)
    for _ in range(4):
        if root is None:
            return root
        time.sleep(0.25)
        again = _clear_popups(driver, markers, w, h)
        if again is None:
            return root
        a = [round(t[1]) for t in _collect_result_adds(root, set(), h)]
        b = [round(t[1]) for t in _collect_result_adds(again, set(), h)]
        root = again
        if a == b:
            break
    return root


def _collect_result_adds(root, used: set[str], h: float = 844.0) -> list[tuple]:
    """Real product Adds only (``stepper.mainButton`` "Add to cart <name>").

    Each product also has a ``stepper.addToCartButton`` wrapper at the same
    rect — skip it, or one product is counted twice. After adding, the wrapper
    keeps that name but holds −/"N in cart"/+; tapping it opens the PDP.
    """
    out: list[tuple] = []
    for node in root.iter("XCUIElementTypeButton"):
        _typ, name, label = _attrs(node)
        if name != "stepper.mainButton" or not label.lower().startswith("add to cart"):
            continue
        key = label.lower()
        if key in used:
            continue
        x, y, bw, bh = _rect(node)
        cy = y + bh / 2.0
        if bw < 40 or cy < 180 or cy > h * 0.84:
            continue
        out.append((x + bw / 2.0, cy, label))
    out.sort(key=lambda t: t[1])
    return out


def _result_steppers(root) -> list[tuple[float, float, float, int | None]]:
    """In-cart steppers on results: (row_cy, plus_cx, plus_cy, qty)."""
    out: list[tuple[float, float, float, int | None]] = []
    for wrap in root.iter():
        if wrap.get("name") != "stepper.addToCartButton":
            continue
        plus = None
        qty = None
        for node in wrap.iter():
            typ, name, label = _attrs(node)
            if name == "stepper.incrementButton":
                x, y, bw, bh = _rect(node)
                plus = (x + bw / 2.0, y + bh / 2.0)
            elif typ == "XCUIElementTypeStaticText":
                m = _IN_CART_RE.search(label)
                if m:
                    qty = int(m.group(1))
        if plus is None:
            continue
        _x, y, _bw, bh = _rect(wrap)
        out.append((y + bh / 2.0, plus[0], plus[1], qty))
    return out


def _stepper_near(root, row_cy: float):
    best = None
    for st in _result_steppers(root):
        dy = abs(st[0] - row_cy)
        if dy <= 60 and (best is None or dy < best[0]):
            best = (dy, st)
    return best[1] if best else None


def _cart_badge_units(root) -> int | None:
    for node in root.iter("XCUIElementTypeButton"):
        m = _CART_BADGE_RE.search(node.get("name") or "")
        if m:
            return int(m.group(1))
    return None


def _raise_listing_qty(
    driver, markers: MarkerLog, *, row_cy: float, target: int, w: float, h: float
) -> tuple[int | None, int]:
    """Tap the row's + until "N in cart" == target. Returns (qty, taps)."""
    taps = 0
    root = _clear_popups(driver, markers, w, h)
    st = _stepper_near(root, row_cy) if root is not None else None
    if st is None:
        return None, taps
    qty = st[3]
    stalls = 0
    for _round in range(_QTY_MAX_ROUNDS):
        if qty is not None and qty >= target:
            break
        need = target - (qty or 1)
        _row, pcx, pcy, _q = st
        for _ in range(need):
            _tap_center(driver, pcx, pcy)
            taps += 1
            time.sleep(_QTY_TAP_GAP)
        markers.mark("listing_qty_plus_burst", taps=need, x=pcx, y=pcy)
        time.sleep(_QTY_SETTLE)
        root = _clear_popups(driver, markers, w, h)
        st_new = _stepper_near(root, row_cy) if root is not None else None
        if st_new is None:
            break
        prev = qty
        st, qty = st_new, st_new[3]
        if qty is not None and prev is not None and qty <= prev:
            stalls += 1
            if stalls >= 2:
                print(f"  qty stuck at {qty} (item Max cap?)")
                break
    return qty, taps


def _clear_cart_fully(
    driver, markers: MarkerLog, outcome: dict, *, max_passes: int = 6
) -> None:
    """Repeat the shared clear (45 s budget per pass) until the badge is empty."""
    total = 0
    empty = False
    for i in range(max_passes):
        outcome.pop("cart_items_cleared", None)
        _clear_cart_items(driver, markers, outcome)
        total += int(outcome.get("cart_items_cleared") or 0)
        _tap_my_items_tab(driver, markers, outcome)
        empty = _cart_icon_looks_empty(driver)
        if empty:
            break
        print(f"  cart still has items after clear pass {i + 1} — clearing again …")
        markers.mark("cart_clear_repass", index=i + 1)
    outcome["cart_items_cleared_total"] = total
    outcome["cart_empty_before_add"] = empty
    if not empty:
        print(f"  WARNING: cart not empty after {max_passes} clear passes")


def _add_basket_from_search(
    driver, markers: MarkerLog, outcome: dict, *, sample
) -> list[dict]:
    """Add ``num_items`` distinct results; raise each to ``units_each`` on listing."""
    target = sample.num_items
    units_each = sample.units_each
    max_scrolls = max(15, target * 2 + 10)
    print(
        f"Step: basket {sample.letter} — Add to cart {target} result(s) "
        f"×{units_each} (total {sample.total_units}) …"
    )
    markers.mark(
        "walmart_basket_add_start",
        sample=sample.letter,
        num_items=target,
        units_each=units_each,
        total=sample.total_units,
    )
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        w, h = 390.0, 844.0

    root = _clear_popups(driver, markers, w, h)
    badge_before = _cart_badge_units(root) if root is not None else None
    outcome["cart_badge_units_before_add"] = badge_before
    if badge_before:
        print(f"  WARNING: cart already holds {badge_before} unit(s) before adding")

    used: set[str] = set()
    added: list[dict] = []
    scrolls = 0
    recoveries = 0
    t0 = time.monotonic()

    just_scrolled = False
    while len(added) < target and scrolls <= max_scrolls:
        if just_scrolled:
            root = _settled_snapshot(driver, markers, w, h)
            just_scrolled = False
        else:
            root = _clear_popups(driver, markers, w, h)
        if root is None or not _on_results_page(root):
            recoveries += 1
            if recoveries > 3 + target // 10:
                raise AssertionError(
                    f"Lost the search results page ({recoveries - 1} recoveries)"
                )
            print("  not on search results — re-running search …")
            markers.mark("search_results_recover", index=recoveries)
            _search_my_items(driver, markers, outcome)
            continue
        adds = _collect_result_adds(root, used, h)
        if not adds:
            _scroll_down(driver, percent=0.36)
            scrolls += 1
            markers.mark("search_results_scrolled", index=scrolls)
            just_scrolled = True
            continue

        cx, cy, label = adds[0]
        used.add(label.lower())
        name = label[len("Add to cart "):].strip() or label
        print(f"  Add #{len(added) + 1}/{target} @({cx:.0f},{cy:.0f}) {name[:48]!r}")
        _tap_center(driver, cx, cy)
        time.sleep(_SETTLE)
        _dismiss_blocking_alerts(driver, markers)
        if _dismiss_add_blockers(driver, markers):
            print("  Unable to add / blocked — skip this product (not counted)")
            markers.mark("item_add_skipped_unable", label=label[:80])
            continue

        qty, taps = _raise_listing_qty(
            driver, markers, row_cy=cy, target=units_each, w=w, h=h
        )
        if qty is None:
            # Stepper not found — Add probably didn't register.
            time.sleep(_SETTLE)
            root = _clear_popups(driver, markers, w, h)
            st = _stepper_near(root, cy) if root is not None else None
            if st is None:
                print("  Add did not register (no in-cart stepper) — skip")
                markers.mark("item_add_not_registered", label=label[:80])
                continue
            qty = st[3]
        entry = {
            "index": len(added) + 1,
            "source": "my_items_search_add",
            "query": SEARCH_QUERY,
            "label": name[:120],
            "y": cy,
            "x": cx,
            "units_target": units_each,
            "qty_plus_taps": taps,
            "units_on_listing": qty,
        }
        added.append(entry)
        markers.mark(
            "item_added_to_cart",
            index=entry["index"],
            label=name[:80],
            units_target=units_each,
            units_seen=qty,
            taps=taps,
        )
        if qty is None or qty < units_each:
            print(
                f"  WARNING: item #{entry['index']} qty "
                f"{qty if qty is not None else '?'}/{units_each} (Max cap?)"
            )
        else:
            print(f"  added #{entry['index']}/{target} ×{qty}")

    elapsed = time.monotonic() - t0
    root = _clear_popups(driver, markers, w, h)
    badge = _cart_badge_units(root) if root is not None else None
    short = [e["index"] for e in added if (e["units_on_listing"] or 0) < units_each]
    outcome["items_added"] = list(added)
    outcome["items_added_count"] = len(added)
    outcome["items_qty_short"] = short
    outcome["cart_badge_units_after_add"] = badge
    outcome["cart_badge_units_added"] = (
        badge - (badge_before or 0) if badge is not None else None
    )
    outcome["basket_add_elapsed_seconds"] = round(elapsed, 2)
    markers.mark(
        "walmart_basket_add_done",
        sample=sample.letter,
        added=len(added),
        target=target,
        units_each=units_each,
        qty_short=short,
        cart_badge_units=badge,
        scrolls=scrolls,
        elapsed_s=round(elapsed, 2),
    )
    print(
        f"  basket {sample.letter} add done: {len(added)}/{target} items "
        f"×{units_each} in {elapsed:.1f}s (scrolls={scrolls}; "
        f"cart badge={badge} vs expected {sample.total_units})"
    )
    if len(added) < target:
        raise AssertionError(
            f"Only added {len(added)}/{target} search results for basket "
            f"{sample.letter} (scrolls={scrolls})"
        )
    return added


def _missing_anything_visible(driver) -> bool:
    try:
        return bool(
            driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'visible == 1 AND (label CONTAINS[c] "Missing anything" OR '
                'name CONTAINS[c] "Missing anything")',
            )
        )
    except Exception:  # noqa: BLE001
        return False


def _dismiss_missing_anything(driver, markers: MarkerLog, outcome: dict) -> None:
    """Close the "Missing anything?" upsell sheet with its X (not Continue)."""
    via = "coord"
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        w, h = 390.0, 844.0
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(label ==[c] "close" OR name ==[c] "close" OR '
            'label ==[c] "dismiss" OR name ==[c] "dismiss")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:4]:
        try:
            if float((el.rect or {}).get("y", 0)) < h * 0.30:
                el.click()
                via = "close_button"
                break
        except Exception:  # noqa: BLE001
            continue
    else:
        # X sits top-right of the sheet, level with the title.
        _tap_center(driver, w * 0.90, h * 0.146)
    outcome["missing_anything_dismissed"] = True
    markers.mark("missing_anything_dismissed", via=via)
    _mark_step(outcome, "missing_anything_dismissed", status="ok", via=via)
    print(f'  "Missing anything?" sheet dismissed via {via}')
    time.sleep(_MED)


def _wait_left_cart_after_continue(driver, markers: MarkerLog, outcome: dict) -> None:
    """After Continue to checkout: close "Missing anything?" if it appears,
    otherwise just let Checkout land (no further taps)."""
    end = time.monotonic() + 8.0
    landed = False
    while time.monotonic() < end:
        if _missing_anything_visible(driver):
            _dismiss_missing_anything(driver, markers, outcome)
            landed = True
            break
        if _checkout_screen_visible(driver):
            landed = True
            break
        time.sleep(0.25)
    outcome["post_continue_screen_landed"] = landed
    markers.mark("post_continue_screen", landed=landed)
    print(f"  next screen after Continue to checkout landed={landed}")


def run_test_case_walmart_5(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
    basket_size: str | None = None,
) -> dict:
    """Walmart TC5: My Items search → basket A–E/G/H → Cart Change slot → Continue."""
    if retailer != "walmart":
        raise ValueError(
            f"{TEST_CASE_NAME} is Walmart-only (use --retailer walmart)."
        )

    sample = _resolve_tc5_basket(basket_size)
    rec_limit = CART_RECORDING_SECONDS
    plan = describe_session(settings, retailer, bundle_id)
    plan["scenario"] = TEST_CASE_ID
    plan["test_case"] = TEST_CASE_NAME
    plan["dry_run"] = dry_run
    plan["study_mode"] = "cancel_test"
    plan["recording"] = True
    plan["recording_starts_at"] = "cart_page"
    plan["recording_duration_seconds"] = rec_limit
    plan["search_query"] = SEARCH_QUERY
    plan["basket_sample"] = sample.letter
    plan["basket_num_items"] = sample.num_items
    plan["basket_units_each"] = sample.units_each
    plan["basket_total_units"] = sample.total_units
    plan["basket_matrix"] = {
        k: {"num_items": v.num_items, "units_each": v.units_each, "total": v.total_units}
        for k, v in BASKET_SAMPLES.items()
        if k in ALLOWED_BASKETS
    }
    plan["place_order"] = False
    plan["steps"] = [
        "Start new automation session (kill prior Walmart if running)",
        "Open Walmart App (cold start)",
        'Tap bottom tab "My Items"',
        "Clear existing cart items if any (exact basket)",
        f'Top search bar → search "{SEARCH_QUERY}"',
        (
            f"Basket {sample.letter}: Add to cart on {sample.num_items} result(s), "
            f"each raised to ×{sample.units_each} with listing +"
        ),
        "Open Cart screen",
        f"START screen recording on Cart (limit {rec_limit}s)",
        'Tap "Change" on Cart → Reserve a time (day + time slots)',
        'Change delivery day + time slot → tap "Reserve"',
        'Tap "Continue to checkout"',
        "Stop recording (do not place order)",
    ]
    plan["notes"] = (
        f"{TEST_CASE_NAME} — basket {sample.letter} "
        f"({sample.num_items}×{sample.units_each}={sample.total_units}): "
        f'My Items search "{SEARCH_QUERY}"; units set on the search listing. '
        "Does not place the order."
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
    run_t0 = time.monotonic()
    phases: dict[str, float] = {}
    driver = None
    recording = False
    outcome: dict = {
        "status": "starting",
        "test_case": TEST_CASE_NAME,
        "plan": plan,
        "markers": markers.events,
        "selectors": {},
        "checkout_steps": {},
        "search_query": SEARCH_QUERY,
        "basket_sample": sample.letter,
        "basket_num_items": sample.num_items,
        "basket_units_each": sample.units_each,
        "basket_total_units": sample.total_units,
        "items_added_count": 0,
        "cart_page_opened": False,
        "recording_started": False,
        "place_order": False,
        "continue_to_checkout_tapped": False,
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

        phases["app_launch"] = time.monotonic() - run_t0

        t = time.monotonic()
        _tap_my_items_tab(driver, markers, outcome)
        _clear_cart_fully(driver, markers, outcome)
        phases["cart_clear"] = time.monotonic() - t

        t = time.monotonic()
        _search_my_items(driver, markers, outcome)
        _add_basket_from_search(driver, markers, outcome, sample=sample)
        phases["search_and_add_basket"] = time.monotonic() - t

        t = time.monotonic()
        _open_cart(driver, markers, outcome)
        phases["open_cart"] = time.monotonic() - t

        print(f"START RECORDING at Cart page (limit {rec_limit}s) …")
        try:
            start_ios_screen_recording(driver, time_limit=rec_limit)
            recording = True
            rec_t0 = time.monotonic()
            outcome["recording_started"] = True
            markers.mark(
                "recording_started_at_cart",
                duration_seconds=rec_limit,
                basket=sample.letter,
            )
        except Exception as exc:  # noqa: BLE001
            outcome["recording_started"] = False
            outcome["recording_start_error"] = str(exc)
            print(f"Screen recording failed to start: {exc}")
            raise AssertionError(
                f"Could not start screen recording on cart: {exc}"
            ) from exc

        _tap_cart_change_delivery_fast(driver, markers, outcome)
        _change_day_and_slot_then_reserve(driver, markers, outcome)
        _tap_continue_to_checkout_tc4(driver, markers, outcome)
        _wait_left_cart_after_continue(driver, markers, outcome)
        phases["recorded_checkout_steps"] = time.monotonic() - rec_t0

        markers.mark(
            "test_case_walmart_5_stopped_after_continue",
            basket=sample.letter,
            items=outcome.get("items_added_count"),
            units_each=sample.units_each,
            checkout_steps=outcome.get("checkout_steps"),
        )
        print("Stopping after Continue to checkout (order not placed) …")

        outcome["status"] = "ok"
        outcome["message"] = (
            f"{TEST_CASE_NAME} OK — basket {sample.letter}: "
            f"{sample.num_items}×{sample.units_each}={sample.total_units} "
            f'via search "{SEARCH_QUERY}" '
            f"(add {outcome.get('basket_add_elapsed_seconds')}s) → cart record → "
            "Change → day/slot → Reserve → Continue to checkout"
        )
        if outcome.get("items_qty_short"):
            outcome["message"] += (
                f" (qty short on items {outcome['items_qty_short']})"
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
        total = time.monotonic() - run_t0
        outcome["phase_seconds"] = {k: round(v, 2) for k, v in phases.items()}
        outcome["total_run_seconds"] = round(total, 2)
        markers.mark("run_finished", total_s=round(total, 2))
        print("Run time by phase:")
        for k, v in phases.items():
            print(f"  {k:<26} {v:8.1f}s")
        mins, secs = divmod(int(round(total)), 60)
        print(f"TOTAL RUN TIME: {total:.1f}s ({mins}m {secs:02d}s)")
        try:
            result_path.write_text(
                json.dumps(outcome, indent=2, default=str), encoding="utf-8"
            )
            print(f"Wrote result → {result_path}")
        except Exception as write_exc:  # noqa: BLE001
            print(f"Result write error: {write_exc}")
