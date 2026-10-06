"""Test Case Walmart 2: TC1 flow with scaled baskets A–H.

Walmart (iOS) — same checkout path as Walmart TC1, but basket size follows
the study matrix sample sizes A–H:

  A  1×1=1     B 10×1=10    C 1×25=25    D 25×1=25
  E  5×10=50   F 50×1=50    G 50×5=250   H 10×10=100

Flow:
  1. My Items → Fresh Produce
  2. Clear cart if needed
  3. On My Items: + Add each of N items once only (qty stays 1 — no listing qty +)
  4. Cart → start screen recording
  5. Change delivery → Change address (2nd) → toggle last↔2nd-last day/slot → Reserve
  6. On Cart: raise EVERY line to U units via qty + (units_each − 1 taps)
  7. Continue to checkout → Continue on Missing anything? → stop on Checkout

Does not place the order.
Select via --basket-size / env BASKET_SIZE (default A).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
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
    _change_day_and_slot_then_reserve,
    _clear_cart_items,
    _collect_add_buttons,
    _dismiss_subscription_sheet,
    _dismiss_ask_sparky_if_shown,
    _dismiss_system_share_sheet,
    _escape_walmart_checkout_flow,
    _mark_step,
    _open_cart,
    _resolve_ineligible_reserved_time_via_store,
    _scroll_down,
    _select_second_address,
    _tap_cart_change_delivery,
    _tap_center,
    _tap_continue_to_checkout,
    _tap_fresh_produce,
    _tap_missing_anything_continue,
    _tap_my_items_tab,
    _tap_reserve_change_address,
    _wait_checkout_screen,
    _FAST,
)

TEST_CASE_NAME = "Test Case Walmart 2"
TEST_CASE_ID = "test_case_walmart_2"
DEFAULT_BASKET_SIZE = "A"
# Faster settles than shared _FAST — do not touch CART_RECORDING_SECONDS.
_SNAP = 0.05
_BRIEF = 0.08
_SETTLE = 0.12


@dataclass(frozen=True)
class BasketSample:
    letter: str
    num_items: int
    units_each: int

    @property
    def total_units(self) -> int:
        return self.num_items * self.units_each


# Study matrix: Sample Size → (# of Items, # of Units for Each Item, Total).
BASKET_SAMPLES: dict[str, BasketSample] = {
    "A": BasketSample("A", 1, 1),
    "B": BasketSample("B", 10, 1),
    "C": BasketSample("C", 1, 25),
    "D": BasketSample("D", 25, 1),
    "E": BasketSample("E", 5, 10),
    "F": BasketSample("F", 50, 1),
    "G": BasketSample("G", 50, 5),
    "H": BasketSample("H", 10, 10),
}


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def resolve_basket_sample(letter: str | None = None) -> BasketSample:
    """Resolve A–H from arg, else env BASKET_SIZE, else default A."""
    raw = (letter or os.getenv("BASKET_SIZE") or DEFAULT_BASKET_SIZE).strip().upper()
    if raw not in BASKET_SAMPLES:
        allowed = ", ".join(sorted(BASKET_SAMPLES))
        raise ValueError(f"Unknown basket size {raw!r}; expected one of: {allowed}")
    return BASKET_SAMPLES[raw]


def _find_increment_near(
    driver, *, near_y: float, prefer_x: float | None = None
) -> tuple[float, float] | None:
    """Locate listing/cart stepper + near a product row."""
    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(name == "stepper.incrementButton" OR '
            'label == "+" OR name == "+")',
        )
    except Exception:  # noqa: BLE001
        els = []
    best: tuple[float, float, float] | None = None  # (dy, cx, cy)
    for el in els[:24]:
        try:
            r = el.rect or {}
            y = float(r.get("y", 0))
            x = float(r.get("x", 0))
            w = float(r.get("width", 0))
            hh = float(r.get("height", 0))
            name = (el.get_attribute("name") or "").lower()
            if "decrement" in name:
                continue
            if y < 180 or y > h * 0.92 or w < 12 or hh < 12:
                continue
            dy = abs(y - near_y)
            if dy > 90:
                continue
            cx = x + w / 2.0
            cy = y + hh / 2.0
            # Prefer closer Y; break ties toward prefer_x (right side of chip).
            score = dy + (0.02 * abs(cx - prefer_x) if prefer_x is not None else 0.0)
            if best is None or score < best[0]:
                best = (score, cx, cy)
        except Exception:  # noqa: BLE001
            continue
    if best is None:
        return None
    return best[1], best[2]


def _unable_to_add_sheet_visible(driver) -> bool:
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label CONTAINS[c] "Unable to add to cart" OR '
            'name CONTAINS[c] "Unable to add to cart" OR '
            'label CONTAINS[c] "technical issue adding this item" OR '
            'label CONTAINS[c] "having a technical issue")',
        )
        return bool(els)
    except Exception:  # noqa: BLE001
        return False


def _dismiss_unable_to_add_sheet(
    driver, markers: MarkerLog | None = None
) -> bool:
    """If 'Unable to add to cart' is up, tap Got it and return True."""
    if not _unable_to_add_sheet_visible(driver):
        return False
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "Got it"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Got it" OR name == "Got it" OR '
            'label == "Got It" OR name == "Got It")',
        ),
    ):
        try:
            el, _, _ = find_first(driver, ((by, value),), timeout=1.5)
            el.click()
            if markers is not None:
                markers.mark("unable_to_add_got_it", by=by, value=value)
            print('  "Unable to add to cart" → Got it (skip this product)')
            time.sleep(_SETTLE)
            return True
        except TimeoutError:
            continue
    # X close on sheet as last resort.
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Close" OR name == "Close" OR label == "close")',
        )
        for el in els[:2]:
            el.click()
            if markers is not None:
                markers.mark("unable_to_add_got_it", via="close")
            print('  "Unable to add to cart" → Close (skip this product)')
            time.sleep(_SETTLE)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _rapid_listing_qty_plus(
    driver,
    markers: MarkerLog,
    *,
    near_y: float,
    add_cx: float,
    extra_units: int,
    item_index: int,
) -> tuple[int, bool]:
    """Raise listing qty by tapping stepper +.

    Returns (taps, blocked). blocked=True means Unable-to-add sheet appeared —
    caller must skip / not count this product.
    """
    if extra_units <= 0:
        return 0, False
    hit = _find_increment_near(driver, near_y=near_y, prefer_x=add_cx + 40.0)
    if hit is None:
        # Walmart qty chip: + sits to the right of the former Add control.
        hit = (add_cx + 48.0, near_y)
    cx, cy = hit
    taps = 0
    pause = 0.06 if extra_units <= 10 else 0.05
    for i in range(extra_units):
        # Re-find periodically — chip can shift slightly after taps.
        if i > 0 and i % 5 == 0:
            again = _find_increment_near(driver, near_y=near_y, prefer_x=cx)
            if again is not None:
                cx, cy = again
        try:
            _tap_center(driver, cx, cy)
            taps += 1
            markers.mark(
                "listing_qty_plus_tapped",
                item_index=item_index,
                tap=i + 1,
                of=extra_units,
                x=cx,
                y=cy,
            )
            time.sleep(pause)
            if _dismiss_unable_to_add_sheet(driver, markers):
                return taps, True
        except Exception as exc:  # noqa: BLE001
            print(f"  listing qty + {i + 1}/{extra_units} failed: {exc}")
            if _dismiss_unable_to_add_sheet(driver, markers):
                return taps, True
            break
    return taps, False


def _dismiss_add_blockers(driver, markers: MarkerLog | None = None) -> bool:
    """Close 'Unable to add to cart' / similar sheets so Add scanning can continue."""
    if _dismiss_ask_sparky_if_shown(driver, markers):
        return True
    if _dismiss_unable_to_add_sheet(driver, markers):
        return True
    dismissed = False
    for label in ("Got it", "Got It", "OK", "Close", "Dismiss"):
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                f'type == "XCUIElementTypeButton" AND visible == 1 AND '
                f'(label == "{label}" OR name == "{label}")',
            )
        except Exception:  # noqa: BLE001
            els = []
        for el in els[:3]:
            try:
                el.click()
                dismissed = True
                time.sleep(_BRIEF)
            except Exception:  # noqa: BLE001
                continue
    return dismissed


def _tap_all_items_filter(driver, markers: MarkerLog) -> bool:
    """Leave Fresh Produce filter so more My Items become available."""
    strategies = (
        (AppiumBy.ACCESSIBILITY_ID, "All Items"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeCell" AND visible == 1 AND '
            '(name == "myItems_departmentTab" OR label == "All Items" OR '
            'label CONTAINS[c] "All Items")',
        ),
        (
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND (label == "All Items" OR name == "All Items")',
        ),
    )
    try:
        el, by, value = find_first(driver, strategies, timeout=1.0)
        el.click()
        markers.mark("all_items_filter_tapped", by=by, value=value)
        print(f"  All Items filter: {by} = {value!r}")
        time.sleep(_SETTLE)
        return True
    except TimeoutError:
        return False


def _swipe_department_chips_left(driver) -> None:
    """Reveal next My Items department chips (Dairy, Snacks, …)."""
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        w, h = 390.0, 844.0
    y = h * 0.155
    try:
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.10,
                "fromX": int(w * 0.85),
                "fromY": int(y),
                "toX": int(w * 0.15),
                "toY": int(y),
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _scroll_up_my_items(driver, *, percent: float = 0.55) -> None:
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.10,
                "fromX": int(w * 0.50),
                "fromY": int(h * 0.35),
                "toX": int(w * 0.50),
                "toY": int(h * (0.35 + percent)),
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _add_basket_via_add_buttons(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    sample: BasketSample,
    max_scrolls: int | None = None,
) -> list[dict]:
    """Add N My Items at qty 1 via + Add only (cart scales to units_each)."""
    target = sample.num_items
    units_each = sample.units_each
    if max_scrolls is None:
        # Large baskets need more scrolling across My Items departments.
        max_scrolls = max(40, target * 4 + 20) if target >= 40 else max(20, target * 2 + 10)

    print(
        f"Step: basket {sample.letter} — +Add {target} items ×1 on My Items "
        f"(cart will scale each to ×{units_each}; total {sample.total_units}) …"
    )
    outcome["basket_sample"] = sample.letter
    outcome["basket_num_items"] = target
    outcome["basket_units_each"] = units_each
    outcome["basket_total_units"] = sample.total_units
    markers.mark(
        "walmart_basket_add_start",
        sample=sample.letter,
        num_items=target,
        units_each=units_each,
        total=sample.total_units,
    )

    used: set[str] = set()
    added: list[dict] = []
    scrolls = 0
    stagnant_rounds = 0
    dept_swipes = 0
    widened_filter = sample.num_items >= 25
    t0 = time.monotonic()

    while len(added) < target and scrolls <= max_scrolls:
        _dismiss_add_blockers(driver)
        _dismiss_subscription_sheet(driver)
        batch = _collect_add_buttons(driver, used)
        if not batch:
            stagnant_rounds += 1
            if stagnant_rounds >= 3 and not widened_filter:
                widened_filter = _tap_all_items_filter(driver, markers)
                stagnant_rounds = 0
                continue
            if stagnant_rounds >= 6:
                # Hit bottom (Give Feedback) or exhausted dept — nudge catalog.
                _scroll_up_my_items(driver, percent=0.45)
                _swipe_department_chips_left(driver)
                dept_swipes += 1
                markers.mark("my_items_dept_swiped", index=dept_swipes)
                stagnant_rounds = 0
                time.sleep(_SETTLE)
                continue
            _scroll_down(driver, percent=0.36)
            scrolls += 1
            markers.mark("my_items_scrolled", index=scrolls)
            time.sleep(_SNAP)
            continue

        added_before = len(added)
        for el, fp, y, cx, cy, label in batch:
            if len(added) >= target:
                break
            if "current quantity" in label.lower():
                used.add(fp)
                continue
            print(
                f"  + Add #{len(added) + 1}/{target} "
                f"@({cx:.0f},{cy:.0f}) label={label[:48]!r} → ×1 "
                f"(cart will scale to ×{units_each})"
            )
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
            time.sleep(_BRIEF)
            if _dismiss_add_blockers(driver, markers):
                print("  Unable to add / blocked — skip this product (not counted)")
                markers.mark(
                    "item_add_skipped_unable",
                    fingerprint=fp,
                    label=label[:80],
                )
                continue
            _dismiss_subscription_sheet(driver)
            # My Items: single +Add only — never bump qty on this screen.
            if _dismiss_unable_to_add_sheet(driver, markers):
                print("  Unable to add — skip this product (not counted)")
                markers.mark(
                    "item_add_skipped_unable",
                    fingerprint=fp,
                    label=label[:80],
                )
                continue

            entry = {
                "index": len(added) + 1,
                "source": "my_items_add",
                "add_path": "stepper_main_button",
                "fingerprint": fp,
                "label": label[:120],
                "y": y,
                "x": cx,
                "units_target": units_each,
                "units_on_listing": 1,
                "qty_plus_taps": 0,
                "confirmed": True,
            }
            added.append(entry)
            markers.mark(
                "item_added_to_cart",
                index=entry["index"],
                fingerprint=fp,
                y=y,
                label=label[:80],
                units=1,
                units_target=units_each,
            )
            print(
                f"  added #{entry['index']}/{target} "
                f"×1 via Add @ y={y:.0f} (cart target ×{units_each})"
            )

        if len(added) >= target:
            break
        if len(added) == added_before:
            stagnant_rounds += 1
            if stagnant_rounds >= 3 and not widened_filter:
                widened_filter = _tap_all_items_filter(driver, markers)
                stagnant_rounds = 0
                continue
            if stagnant_rounds >= 6:
                _scroll_up_my_items(driver, percent=0.45)
                _swipe_department_chips_left(driver)
                dept_swipes += 1
                markers.mark("my_items_dept_swiped", index=dept_swipes)
                stagnant_rounds = 0
                time.sleep(_SETTLE)
                continue
            _scroll_down(driver, percent=0.36)
            scrolls += 1
            markers.mark("my_items_scrolled", index=scrolls, reason="no_new_add")
            time.sleep(_SNAP)
        else:
            stagnant_rounds = 0
            if target - len(added) > 0:
                _scroll_down(driver, percent=0.30)
                scrolls += 1
                markers.mark("my_items_scrolled", index=scrolls, reason="next_row")
                time.sleep(_SNAP)

    elapsed = time.monotonic() - t0
    outcome["items_added"] = list(added)
    outcome["items_added_count"] = len(added)
    outcome["basket_add_elapsed_seconds"] = round(elapsed, 2)
    markers.mark(
        "walmart_basket_add_done",
        sample=sample.letter,
        added=len(added),
        target=target,
        units_each=units_each,
        total=sample.total_units,
        scrolls=scrolls,
        elapsed_s=round(elapsed, 2),
        widened_filter=widened_filter,
    )
    print(
        f"  basket {sample.letter} add done: {len(added)}/{target} items "
        f"×1 on listing in {elapsed:.1f}s (scrolls={scrolls}; "
        f"cart target ×{units_each})"
    )
    if len(added) < target:
        raise AssertionError(
            f"Only added {len(added)}/{target} items for basket {sample.letter} "
            f"(scrolls={scrolls})"
        )
    return added


def _top_cart_increment(
    driver,
    *,
    min_y: float | None = None,
    y_hi_frac: float = 0.84,
) -> tuple[float, float] | None:
    """Topmost cart qty + in the tappable band (above sticky checkout CTA)."""
    try:
        size = driver.get_window_size() or {}
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    y_lo = 240.0 if min_y is None else max(240.0, min_y)
    y_hi = h * y_hi_frac
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(name == "stepper.incrementButton" OR '
            'label == "+" OR name == "+")',
        )
    except Exception:  # noqa: BLE001
        els = []
    best: tuple[float, float, float] | None = None  # (y, cx, cy)
    for el in els[:30]:
        try:
            r = el.rect or {}
            y = float(r.get("y", 0))
            x = float(r.get("x", 0))
            w = float(r.get("width", 0))
            hh = float(r.get("height", 0))
            name = (el.get_attribute("name") or "").lower()
            if "decrement" in name:
                continue
            if y < y_lo or y > y_hi:
                continue
            if w < 14 or hh < 14 or w > 140:
                continue
            cx = x + w / 2.0
            cy = y + hh / 2.0
            if best is None or y < best[0]:
                best = (y, cx, cy)
        except Exception:  # noqa: BLE001
            continue
    if best is None:
        return None
    return best[1], best[2]


def _scroll_up_cart(driver, *, percent: float = 0.40) -> None:
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.10,
                "fromX": int(w * 0.50),
                "fromY": int(h * 0.38),
                "toX": int(w * 0.50),
                "toY": int(h * (0.38 + percent)),
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _scroll_away_from_cart_line(driver, *, prev_cy: float) -> None:
    """Scroll until the prior line's + leaves (or enough to reveal the next)."""
    for _ in range(4):
        _scroll_down(driver, percent=0.28)
        time.sleep(_SNAP)
        hit = _top_cart_increment(driver, y_hi_frac=0.90)
        if hit is None:
            return
        _cx, cy = hit
        # Next line is sufficiently different in Y from the one we just did.
        if abs(cy - prev_cy) > 45:
            return


def _increase_all_cart_item_qty(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    increase_by: int = 1,
    expected_items: int | None = None,
) -> int:
    """Tap + on every cart line item (scroll through the full cart).

    Listing adds qty 1 only — ``increase_by`` should be ``units_each - 1``.
    """
    if increase_by <= 0:
        print("Step: cart qty increase skipped (units_each=1) …")
        outcome["cart_lines_qty_increased"] = 0
        outcome["cart_qty_increase_by"] = 0
        markers.mark("cart_all_qty_increase_skipped", reason="units_each_is_1")
        return 0
    print(
        f"Step: on Cart increase EVERY line by +{increase_by} "
        f"(to units_each={increase_by + 1})"
        + (f" — {expected_items} lines expected" if expected_items else "")
        + " …"
    )
    # Start near top of cart list (under fulfillment card).
    for _ in range(2):
        _scroll_up_cart(driver, percent=0.55)
        time.sleep(_SNAP)

    lines_done = 0
    lines_skipped = 0
    stagnant = 0
    max_rounds = max(18, (expected_items or 10) * 4)
    last_cy: float | None = None

    for round_i in range(max_rounds):
        processed = lines_done + lines_skipped
        if expected_items is not None and processed >= expected_items:
            break

        # Prefer a + below the previous line so we never re-hit the same chip.
        # Widen Y band when hunting remaining lines (last chip often near CTA).
        y_hi = 0.90 if stagnant >= 2 else 0.84
        min_y = (last_cy + 40.0) if last_cy is not None else None
        hit = _top_cart_increment(driver, min_y=min_y, y_hi_frac=y_hi)
        if hit is None:
            hit = _top_cart_increment(driver, y_hi_frac=y_hi)
            if hit is not None and last_cy is not None and abs(hit[1] - last_cy) < 40:
                # Same chip still on screen — scroll it away.
                hit = None

        if hit is None:
            stagnant += 1
            if stagnant >= 8:
                break
            # Small nudges so tall cart cards don't skip the last stepper.
            if stagnant in (3, 6):
                _scroll_up_cart(driver, percent=0.16)
            else:
                _scroll_down(driver, percent=0.22)
            time.sleep(_SNAP)
            markers.mark(
                "cart_qty_scrolled",
                index=round_i + 1,
                lines_done=lines_done,
                reason="hunt",
            )
            continue

        stagnant = 0
        cx, cy = hit
        taps = 0
        blocked = False
        for i in range(increase_by):
            try:
                # Re-find only every few taps — saves a11y scans on large units.
                if i == 0 or i % 4 == 0:
                    near = _find_increment_near(driver, near_y=cy, prefer_x=cx)
                    if near is not None:
                        cx, cy = near
                # If chip sits under sticky CTA, nudge list up then re-find.
                try:
                    size = driver.get_window_size() or {}
                    h = float(size.get("height") or 844)
                except Exception:  # noqa: BLE001
                    h = 844.0
                if cy > h * 0.78:
                    # Move chip up away from sticky Continue CTA.
                    _scroll_up_cart(driver, percent=0.14)
                    time.sleep(_SNAP)
                    near = _find_increment_near(
                        driver, near_y=min(cy, h * 0.70), prefer_x=cx
                    )
                    if near is None:
                        near = _top_cart_increment(driver, y_hi_frac=0.90)
                    if near is not None:
                        cx, cy = near
                _tap_center(driver, cx, cy)
                taps += 1
                markers.mark(
                    "cart_line_qty_plus_tapped",
                    line=lines_done + lines_skipped + 1,
                    index=i + 1,
                    x=cx,
                    y=cy,
                )
                time.sleep(_SNAP)
                if _dismiss_unable_to_add_sheet(driver, markers):
                    blocked = True
                    break
            except Exception as exc:  # noqa: BLE001
                print(
                    f"  cart line #{lines_done + lines_skipped + 1} qty + "
                    f"{i + 1}/{increase_by} failed: {exc}"
                )
                if _dismiss_unable_to_add_sheet(driver, markers):
                    blocked = True
                break

        last_cy = cy
        if blocked:
            lines_skipped += 1
            print(
                f"  cart line skipped (Unable to add) after +{taps} "
                f"@ ({cx:.0f},{cy:.0f}) — moving on"
            )
            markers.mark(
                "cart_line_qty_skipped_unable",
                line=lines_done + lines_skipped,
                taps=taps,
            )
        else:
            lines_done += 1
            print(
                f"  cart line #{lines_done} qty +{taps}/{increase_by} "
                f"@ ({cx:.0f},{cy:.0f})"
            )
            markers.mark(
                "cart_line_qty_increased",
                line=lines_done,
                taps=taps,
                increase_by=increase_by,
            )
            if taps < increase_by:
                raise AssertionError(
                    f"Cart line #{lines_done}: only tapped + "
                    f"{taps}/{increase_by} times"
                )

        processed = lines_done + lines_skipped
        if expected_items is not None and processed >= expected_items:
            break
        _scroll_away_from_cart_line(driver, prev_cy=cy)
        last_cy = None  # after scroll, take next topmost fresh
        markers.mark(
            "cart_qty_scrolled",
            index=round_i + 1,
            lines_done=lines_done,
            lines_skipped=lines_skipped,
            reason="next_line",
        )

    outcome["cart_lines_qty_increased"] = lines_done
    outcome["cart_lines_qty_skipped_unable"] = lines_skipped
    outcome["cart_qty_increase_by"] = increase_by
    _mark_step(
        outcome,
        "cart_all_qty_increase",
        status="ok" if lines_done > 0 else "miss",
        lines=lines_done,
        skipped_unable=lines_skipped,
        increase_by=increase_by,
        expected_items=expected_items,
    )
    markers.mark(
        "cart_all_qty_increase_done",
        lines=lines_done,
        skipped_unable=lines_skipped,
        increase_by=increase_by,
    )
    if lines_done <= 0 and lines_skipped <= 0:
        raise AssertionError("Could not find any cart item quantity + controls")
    processed = lines_done + lines_skipped
    if expected_items is not None and processed < expected_items and lines_done <= 0:
        raise AssertionError(
            f"Only increased qty on {lines_done}/{expected_items} cart line(s) "
            f"(skipped_unable={lines_skipped})"
        )
    if expected_items is not None and processed < expected_items:
        print(
            f"  warning: processed {processed}/{expected_items} cart lines "
            f"(ok={lines_done}, skipped_unable={lines_skipped})"
        )
    print(
        f"  cart qty + done: {lines_done} line(s) × +{increase_by}"
        + (f", skipped_unable={lines_skipped}" if lines_skipped else "")
    )
    time.sleep(_SNAP)
    return lines_done


def run_test_case_walmart_2(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
    basket_size: str | None = None,
) -> dict:
    """Walmart TC2: scaled baskets A–H + TC1 checkout path (no place order)."""
    if retailer != "walmart":
        raise ValueError(
            f"{TEST_CASE_NAME} is Walmart-only (use --retailer walmart)."
        )

    sample = resolve_basket_sample(basket_size)
    rec_limit = CART_RECORDING_SECONDS
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
        k: {
            "num_items": v.num_items,
            "units_each": v.units_each,
            "total": v.total_units,
        }
        for k, v in BASKET_SAMPLES.items()
    }
    plan["place_order"] = False
    plan["steps"] = [
        "Start new automation session (kill prior Walmart if running)",
        "Open Walmart App (cold start)",
        'Tap bottom tab "My Items"',
        "Clear existing cart items if any (so + Add is available)",
        'Tap "Fresh Produce" filter',
        (
            f"Basket {sample.letter}: tap blue + Add once on {sample.num_items} "
            f"item(s) (qty=1 on My Items; total units {sample.total_units} set on Cart)"
        ),
        "Open Cart screen",
        f"START screen recording on Cart for {rec_limit}s",
        'Tap "Change" (delivery) on Cart → Reserve a time',
        'Tap "Change" (address) → select 2nd saved address',
        'Change delivery day to last ↔ 2nd-last + time slot last ↔ 2nd-last → tap "Reserve"',
        (
            f"On Cart: increase EVERY line by +{max(0, sample.units_each - 1)} "
            f"(to {sample.units_each} each)"
            if sample.units_each > 1
            else "On Cart: skip qty bump (units_each=1)"
        ),
        'Tap "Continue to checkout"',
        'On "Missing anything?" tap "Continue"',
        "Land on Checkout screen → stop recording (do not place order)",
    ]
    plan["cart_qty_increase_by"] = max(0, sample.units_each - 1)
    plan["listing_qty_fixed"] = 1
    plan["notes"] = (
        f"{TEST_CASE_NAME} — basket sample {sample.letter} "
        f"({sample.num_items}×{sample.units_each}={sample.total_units}): "
        "My Items → Fresh Produce → +Add ×1 each → Cart record → "
        "Change delivery → Change address (2nd) → toggle day/slot last↔2nd-last → Reserve → "
        f"all cart lines qty +{max(0, sample.units_each - 1)} → "
        "Continue to checkout → Continue on Missing anything? → stop on Checkout. "
        f"Override with --basket-size or BASKET_SIZE ({', '.join(BASKET_SAMPLES)}). "
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

        markers.mark(
            "app_opened",
            app_state=outcome.get("app_state"),
            basket=sample.letter,
        )

        time.sleep(0.12)
        _dismiss_ask_sparky_if_shown(driver, markers)
        _dismiss_system_share_sheet(driver, markers)
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

        _dismiss_system_share_sheet(driver, markers)
        try:
            _tap_my_items_tab(driver, markers, outcome)
        except TimeoutError:
            _dismiss_system_share_sheet(driver, markers)
            tap_dismissibles(driver, labels=WALMART_DISMISS_LABELS, rounds=1)
            _tap_my_items_tab(driver, markers, outcome)
        _clear_cart_items(driver, markers, outcome)
        _dismiss_ask_sparky_if_shown(driver, markers)
        _dismiss_system_share_sheet(driver, markers)
        tap_dismissibles(driver, labels=WALMART_DISMISS_LABELS, rounds=2)
        _dismiss_add_blockers(driver)
        # Cart sticky CTA can leave us on Checkout if a bottom coord was mis-tapped.
        _escape_walmart_checkout_flow(driver, markers)
        _dismiss_ask_sparky_if_shown(driver, markers)
        _tap_my_items_tab(driver, markers, outcome)
        try:
            find_first(driver, FRESH_PRODUCE_STRATEGIES, timeout=1.2)
        except TimeoutError:
            try:
                _dismiss_system_share_sheet(driver, markers)
                _tap_my_items_tab(driver, markers, outcome)
            except TimeoutError:
                _dismiss_system_share_sheet(driver, markers)
                _dismiss_add_blockers(driver)
                tap_dismissibles(driver, labels=WALMART_DISMISS_LABELS, rounds=1)
                _tap_my_items_tab(driver, markers, outcome)
        # Large baskets need the full My Items catalog (All Items).
        # If Fresh Produce chip is missing (Reorder/empty state), fall back to All Items.
        if sample.num_items >= 25:
            _tap_all_items_filter(driver, markers)
        else:
            try:
                _tap_fresh_produce(driver, markers, outcome)
            except TimeoutError:
                print("  Fresh Produce not found — falling back to All Items …")
                if not _tap_all_items_filter(driver, markers):
                    raise
        _add_basket_via_add_buttons(driver, markers, outcome, sample=sample)
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

        _tap_cart_change_delivery(driver, markers, outcome)
        _tap_reserve_change_address(driver, markers, outcome)
        _select_second_address(driver, markers, outcome)
        _change_day_and_slot_then_reserve(driver, markers, outcome)
        # Unlike TC1 fixed +4: scale every cart line to units_each.
        _increase_all_cart_item_qty(
            driver,
            markers,
            outcome,
            increase_by=max(0, sample.units_each - 1),
            expected_items=sample.num_items,
        )
        # If cart warns about ineligible reserved time, change store first.
        _resolve_ineligible_reserved_time_via_store(driver, markers, outcome)
        _tap_continue_to_checkout(driver, markers, outcome)
        _tap_missing_anything_continue(driver, markers, outcome)
        _wait_checkout_screen(driver, markers, outcome)

        markers.mark(
            "test_case_walmart_2_stopped_on_checkout",
            basket=sample.letter,
            items=outcome.get("items_added_count"),
            units_each=sample.units_each,
            total_units=sample.total_units,
            checkout_steps=outcome.get("checkout_steps"),
        )
        print("Stopping process on Checkout screen (order not placed) …")

        outcome["status"] = "ok"
        store_note = (
            " → store recovery"
            if outcome.get("ineligible_store_recovery")
            else ""
        )
        outcome["message"] = (
            f"{TEST_CASE_NAME} OK — basket {sample.letter}: "
            f"{sample.num_items}×{sample.units_each}={sample.total_units} "
            f"(add {outcome.get('basket_add_elapsed_seconds')}s) → cart record → "
            f"Change delivery → Change address (2nd) → day/slot → Reserve → "
            f"all lines qty +{outcome.get('cart_qty_increase_by')}"
            f"{store_note} → "
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
                print(f"Session quit warning: {quit_exc}")

        outcome["markers"] = markers.events
        try:
            result_path.write_text(
                json.dumps(outcome, indent=2, default=str), encoding="utf-8"
            )
            print(f"Wrote result → {result_path}")
            outcome["result_path"] = str(result_path)
        except Exception as write_exc:  # noqa: BLE001
            print(f"Result write warning: {write_exc}")
