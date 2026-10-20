"""Test Case 7_2: Scaled basket sizes (A–H) + TC7 checkout edits.

Grocery → Produce → add N distinct items × U units each (sample A–H) using
the fastest listing-side path (tap + then rapid qty + at the same spot),
then Cart → record → TC6 checkout (See more slots → last → payment 2nd →
address 2nd → Deliver).

Does not place the order.

Basket samples (from study matrix):
  A  1×1=1    B 10×1=10   C 1×25=25   D 25×1=25
  E  5×10=50  F 50×1=50   G 50×5=250  H 10×10=100

Select via --basket-size / env BASKET_SIZE (default A).
"""

from __future__ import annotations

import json
import os
import re
import time
import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.actions import interaction
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
from PIL import Image

from grocery_latency.config import Settings
from grocery_latency.driver import create_driver, describe_session
from grocery_latency.ios_helpers import (
    dump_page_source,
    looks_like_login_wall,
    tap_dismissibles,
)
from grocery_latency.markers import MarkerLog
from grocery_latency.recording import save_recording_bytes, start_ios_screen_recording
from grocery_latency.scenarios.test_case_2 import (
    _cart_badge_count,
    _open_cart_page,
    _scroll_down,
    _verify_five_in_cart,
)
from grocery_latency.scenarios.test_case_3 import (
    _detect_checkout_screen,
    _handle_address_screen,
    _mark_step,
    _tap_proceed_until_left_cart,
)
from grocery_latency.scenarios.test_case_5 import (
    _dismiss_overlay_fast,
    _open_grocery_produce,
)
from grocery_latency.scenarios.test_case_6 import (
    _recover_amazon_error_page,
    _dismiss_prime_if_blocking,
    _go_back,
    _tc6_checkout_edits,
)

TEST_CASE_NAME = "Test Case 7_2"
TEST_CASE_ID = "test_case_7_2"
DEFAULT_BASKET_SIZE = "A"
BASE_CART_RECORDING_SECONDS = 180


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


def _recording_seconds_for(sample: BasketSample, settings: Settings) -> int:
    """Scale cart recording cap with basket total; honor settings max."""
    # Checkout edits ~90–120s; larger carts need more headroom before stop.
    # Units are raised on Place Your Order while recording: ~7 s per
    # confirmed + (page reload + slow lookups on that page).
    plus_taps = sample.num_items * max(0, sample.units_each - 1)
    if sample.letter in BATCH_CART_BASKETS:
        plus_taps = 0  # raised before recording
    per_tap = 12 if sample.letter in CART_QTY_BASKETS else 11
    estimated = (
        BASE_CART_RECORDING_SECONDS + sample.num_items * 6 + plus_taps * per_tap
    )
    cap = max(int(settings.recording_time_limit_seconds), 1800)
    return max(BASE_CART_RECORDING_SECONDS, min(cap, int(estimated)))


def _tap_xy(driver, x: float, y: float) -> None:
    """One W3C pointer tap. Measured on the iPhone (Amazon Cart): ~1.0 s vs
    ~1.6 s for ``mobile: tap`` at the same point; WDA idle/animation
    settings made no difference."""
    try:
        finger = PointerInput(interaction.POINTER_TOUCH, "finger")
        actions = ActionBuilder(driver, mouse=finger)
        finger.create_pointer_move(duration=0, x=int(round(x)), y=int(round(y)))
        finger.create_pointer_down()
        finger.create_pause(0.04)
        finger.create_pointer_up(0)
        actions.perform()
    except Exception:  # noqa: BLE001
        driver.execute_script(
            "mobile: tap",
            {"x": int(round(x)), "y": int(round(y))},
        )


# Amazon grocery stepper (after Add To Cart): a "form" holding
#   Button "Minimum quantity is 1" / "Decrease quantity by one"  (− / trash)
#   StaticText "<qty>"
#   Button "Increase quantity by one"                            (+)
# Place Your Order names them differently: "Increase item quantity" and
# "Minimum quantity reached, Delete item" / "Decrease item quantity".
_INC_PRED = (
    'type == "XCUIElementTypeButton" AND visible == 1 AND '
    '(name == "Increase quantity by one" OR name == "Increase item quantity")'
)
_DEC_PRED = (
    'type == "XCUIElementTypeButton" AND visible == 1 AND '
    '(name == "Decrease quantity by one" OR name == "Minimum quantity is 1" OR '
    'name == "Decrease item quantity" OR '
    'name CONTAINS[c] "delete" OR name CONTAINS[c] "remove")'
)
_QTY_TEXT_PRED = (
    'type == "XCUIElementTypeStaticText" AND visible == 1 AND '
    'label MATCHES "^[0-9]{1,3}$"'
)
_QTY_SHEET_PRED = (
    'visible == 1 AND (label CONTAINS[c] "limit of" OR '
    'label CONTAINS[c] "Quantity not updated" OR '
    'label CONTAINS[c] "maximum quantity" OR '
    'label CONTAINS[c] "of these available")'
)
# Each + is its own server update: rapid taps come back "Quantity not
# updated" and the cart ends up short. One confirmed step at a time.
_QTY_STEP_TIMEOUT = 3.0
_ADD_CONFIRM_TIMEOUT = 8.0  # some Adds take 5–6 s to show their stepper
_ADDS_PER_AISLE = 15


def _rect_center(el) -> tuple[float, float, dict]:
    r = el.rect or {}
    return (
        float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0,
        float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0,
        r,
    )


def _stepper_near(driver, cx: float, cy: float) -> dict | None:
    """Stepper whose + sits where the product's Add button was."""
    # Only the row that was tapped: a rect band keeps each read ~1 s.
    band = f" AND rect.y > {int(cy) - 60} AND rect.y < {int(cy) + 40}"
    try:
        incs = driver.find_elements(AppiumBy.IOS_PREDICATE, _INC_PRED + band)
    except Exception:  # noqa: BLE001
        return None
    best = None
    for el in incs[:30]:
        try:
            px, py, r = _rect_center(el)
        except Exception:  # noqa: BLE001
            continue
        if abs(py - cy) > 25 or abs(px - cx) > 70:
            continue
        d = abs(py - cy) + abs(px - cx)
        if best is None or d < best[0]:
            best = (d, px, py, r)
    if best is None:
        return None
    _d, px, py, r = best
    qty = None
    left = float(r.get("x", 0)) - 110
    try:
        for t in driver.find_elements(AppiumBy.IOS_PREDICATE, _QTY_TEXT_PRED + band)[:40]:
            tx, ty, _tr = _rect_center(t)
            if abs(ty - py) <= 15 and left <= tx <= float(r.get("x", 0)):
                qty = int(t.get_attribute("label"))
                break
    except Exception:  # noqa: BLE001
        pass
    minus = None
    try:
        for m in driver.find_elements(AppiumBy.IOS_PREDICATE, _DEC_PRED + band)[:30]:
            mx, my, _mr = _rect_center(m)
            if abs(my - py) <= 15 and left <= mx < px:
                minus = (mx, my)
                break
    except Exception:  # noqa: BLE001
        pass
    return {"plus": (px, py), "qty": qty, "minus": minus}


def _qty_sheet(driver) -> str:
    """Text of a 'limit of N per customer' / 'Quantity not updated' sheet."""
    try:
        for el in driver.find_elements(AppiumBy.IOS_PREDICATE, _QTY_SHEET_PRED)[:3]:
            lab = (el.get_attribute("label") or "").strip()
            if lab:
                return lab
    except Exception:  # noqa: BLE001
        pass
    return ""


def _close_qty_sheet(driver) -> None:
    """Close the qty sheet with ITS X. The grocery header also has a "Close"
    X (top-right) — tapping that exits the whole grocery storefront."""
    try:
        h = float((driver.get_window_size() or {}).get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label ==[c] "close" OR name ==[c] "close" OR '
            'label ==[c] "dismiss" OR name ==[c] "dismiss" OR label == "OK")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:6]:
        try:
            if float((el.rect or {}).get("y", 0)) > h * 0.35:
                el.click()
                time.sleep(0.4)
                return
        except Exception:  # noqa: BLE001
            continue
    # Fallback: the sheet's X sits right of the sheet title (~0.93w, 0.73h).
    try:
        w = float((driver.get_window_size() or {}).get("width") or 390)
    except Exception:  # noqa: BLE001
        w = 390.0
    _tap_xy(driver, w * 0.93, h * 0.73)
    time.sleep(0.4)


_QUICK_PRED = (
    '(type == "XCUIElementTypeButton" AND '
    '(name == "Increase quantity by one" OR name == "Increase item quantity")) OR '
    '(type == "XCUIElementTypeStaticText" AND label MATCHES "^[0-9]{1,3}$")'
)


def _stepper_quick(driver, cx: float, cy: float) -> dict | None:
    """Same match as ``_stepper_near`` (+ within 70 px of the tapped Add, qty
    digit just left of it) but in ONE search of the tapped row — each search
    snapshots the whole web page, so this is ~3× cheaper. No minus."""
    band = (
        f" AND rect.y > {int(cy) - 60} AND rect.y < {int(cy) + 40}"
        f" AND rect.x > {int(cx) - 200} AND rect.x < {int(cx) + 90}"
    )
    try:
        els = driver.find_elements(AppiumBy.IOS_PREDICATE, f"({_QUICK_PRED}){band}")
    except Exception:  # noqa: BLE001
        return None
    pluses, digits = [], []
    for el in els[:12]:
        try:
            lab = (el.get_attribute("label") or "").strip()
            x, y, r = _rect_center(el)
        except Exception:  # noqa: BLE001
            continue
        if lab.isdigit():
            digits.append((x, y, int(lab)))
        else:
            pluses.append((x, y, r))
    best = None
    for px, py, r in pluses:
        if abs(py - cy) > 25 or abs(px - cx) > 70:
            continue
        d = abs(py - cy) + abs(px - cx)
        if best is None or d < best[0]:
            best = (d, px, py, r)
    if best is None:
        return None
    _d, px, py, r = best
    left = float(r.get("x", 0)) - 110
    qty = None
    for tx, ty, n in digits:
        if abs(ty - py) <= 15 and left <= tx <= float(r.get("x", 0)):
            qty = n
            break
    return {"plus": (px, py), "qty": qty, "minus": None}


# --- Basket G: raise ×5 on the grocery grid right after each Add ----------
# Tap style asked for G: W3C pointer, 300 ms movement + 40 ms press.
_GRID_TAP_MOVE_MS = 300
_GRID_TAP_PRESS_S = 0.04
_GRID_STEP_TIMEOUT = 4.0
_GRID_STEP_RETRIES = 3


def _grid_tap(driver, x: float, y: float) -> None:
    finger = PointerInput(interaction.POINTER_TOUCH, "finger")
    actions = ActionBuilder(driver, mouse=finger)
    finger.create_pointer_move(
        duration=_GRID_TAP_MOVE_MS, x=int(round(x)), y=int(round(y))
    )
    finger.create_pointer_down()
    finger.create_pause(_GRID_TAP_PRESS_S)
    finger.create_pointer_up(0)
    actions.perform()


def _grid_plus_at(driver, cx: float, cy: float) -> tuple[float, float] | None:
    """The tile's stepper "+" (where its Add was): one banded search + one
    rect read. Same window as ``_added_stepper_at``."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(name == "Increase quantity by one" OR name == "Increase item quantity") '
            f"AND rect.x > {int(cx) - 95} AND rect.x < {int(cx) + 72} "
            f"AND rect.y > {int(cy) - 50} AND rect.y < {int(cy) + 28}",
        )
        if els:
            px, py, _r = _rect_center(els[0])
            return px, py
    except Exception:  # noqa: BLE001
        pass
    return None


def _grid_qty(driver, plus: tuple[float, float]) -> int | None:
    """Qty digit just left of the tile's "+" (same row): banded search +
    one label read. The price sits ~35 pt lower, outside the band."""
    px, py = plus
    try:
        for el in driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeStaticText" AND label MATCHES "^[0-9]{1,3}$" '
            f"AND rect.x > {int(px) - 125} AND rect.x < {int(px) - 8} "
            f"AND rect.y > {int(py) - 24} AND rect.y < {int(py) + 6}",
        )[:2]:
            lab = (el.get_attribute("label") or "").strip()
            if lab.isdigit():
                return int(lab)
    except Exception:  # noqa: BLE001
        pass
    return None


def _grid_minus(driver, plus: tuple[float, float]) -> tuple[float, float] | None:
    px, py = plus
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(name == "Decrease quantity by one" OR name == "Minimum quantity is 1" OR '
            'name CONTAINS[c] "delete" OR name CONTAINS[c] "remove") '
            f"AND rect.x > {int(px) - 200} AND rect.x < {int(px) - 30} "
            f"AND rect.y > {int(py) - 40} AND rect.y < {int(py) + 20}",
        )
        if els:
            mx, my, _r = _rect_center(els[0])
            return mx, my
    except Exception:  # noqa: BLE001
        pass
    return None


def _grid_raise(driver, plus: tuple[float, float], target: int,
                start: int = 1) -> tuple[int, str]:
    """+ one confirmed step at a time (Amazon drops rapid taps). Returns
    (qty reached, "" | "limit" | "stuck")."""
    qty = start
    retries = 0
    while qty < target:
        _grid_tap(driver, *plus)
        t_tap = time.monotonic()
        moved = False
        sheet_checked = False
        while time.monotonic() - t_tap < _GRID_STEP_TIMEOUT:
            time.sleep(0.15)
            q = _grid_qty(driver, plus)
            if q is not None and q > qty:
                qty, moved = q, True
                break
            if not sheet_checked and time.monotonic() - t_tap > 2.0:
                sheet_checked = True
                sheet = _qty_sheet(driver)
                if sheet:
                    _close_qty_sheet(driver)
                    if _is_limit_text(sheet):
                        return qty, "limit"
        if moved:
            retries = 0
            continue
        retries += 1
        if retries >= _GRID_STEP_RETRIES:
            return qty, "stuck"
        time.sleep(0.8)
    return qty, ""


def _grid_remove(driver, plus: tuple[float, float], qty: int) -> bool:
    """Take a tile back to "Add To Cart": − one confirmed step at a time."""
    for _ in range(qty + 4):
        m = _grid_minus(driver, plus)
        if m is None:
            return _grid_plus_at(driver, *plus) is None
        before = _grid_qty(driver, plus)
        _grid_tap(driver, *m)
        end = time.monotonic() + _GRID_STEP_TIMEOUT
        while time.monotonic() < end:
            time.sleep(0.2)
            if _grid_plus_at(driver, *plus) is None:
                time.sleep(0.4)
                if _grid_plus_at(driver, *plus) is None:
                    return True
            q = _grid_qty(driver, plus)
            if before is not None and q is not None and q < before:
                break
    return _grid_plus_at(driver, *plus) is None


def _wait_qty(driver, cx: float, cy: float, *, above: int | None, timeout: float):
    """Poll the stepper until qty > ``above`` (None = stepper appears).
    Returns (stepper | None, sheet_text)."""
    start = time.monotonic()
    end = start + timeout
    st = None
    while time.monotonic() < end:
        # The limit / "not updated" sheet only matters once nothing changed.
        if time.monotonic() - start > 1.5:
            sheet = _qty_sheet(driver)
            if sheet:
                return _stepper_near(driver, cx, cy), sheet
        st = _stepper_quick(driver, cx, cy)
        if st is not None and st["qty"] is not None:
            if above is None or st["qty"] > above:
                return st, ""
        time.sleep(0.2)
    return st, ""


def _remove_line(driver, cx: float, cy: float, st: dict) -> bool:
    """− one confirmed step at a time until the stepper is gone (at ×1 the −
    is the trash). Never taps blindly: extra taps on an emptied tile open
    the product page."""
    for _ in range(int((st or {}).get("qty") or 1) + 3):
        if not st or not st.get("minus"):
            return st is None
        before = st.get("qty")
        _tap_xy(driver, *st["minus"])
        end = time.monotonic() + _QTY_STEP_TIMEOUT
        gone = 0
        while time.monotonic() < end:
            time.sleep(0.25)
            if _qty_sheet(driver):
                _close_qty_sheet(driver)
            cur = _stepper_near(driver, cx, cy)
            if cur is None:
                # Gone only if it stays gone (the stepper flickers mid-update).
                gone += 1
                if gone >= 2:
                    return True
                continue
            gone = 0
            if cur.get("qty") is None:
                continue  # label blank while Amazon syncs the change
            st = cur
            if before is not None and cur["qty"] < before:
                break
    return False


def _collect_add_to_cart(
    driver, used: set[str], *, check_deal: bool = True, limit: int = 6
) -> list[tuple]:
    """Unused "Add To Cart" buttons on the grocery grid (exact name only —
    a looser "Add…" match taps "Add to List" etc. on product pages).

    Measured on the phone: a search costs ~3.4 s and EVERY rect/label read
    ~0.4 s, so the band lives in the predicate and nothing else is read.
    """
    out: list[tuple] = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(name == "Add To Cart" OR name == "+" OR label == "+") '
            # On screen only: carousels keep their off-screen tiles (x≫390)
            # in the tree, and tapping those hits nothing.
            'AND rect.y > 180 AND rect.y < 600 AND rect.x > 0 AND rect.x < 370',
        )
    except Exception:  # noqa: BLE001
        return out
    if not els:
        return out
    # "Today's featured deal" (Red Raspberries) is capped at 5 per customer,
    # so it can never reach ×10/×25 — skip its tile when that matters.
    deal_band = None
    if check_deal:
        try:
            for t in driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'label CONTAINS[c] "featured deal" AND rect.y > -300 AND rect.y < 620',
            )[:1]:
                ty = float((t.rect or {}).get("y", 0))
                deal_band = (ty, ty + 280)
        except Exception:  # noqa: BLE001
            pass
    for el in els[:20]:
        try:
            cx, cy, _r = _rect_center(el)
        except Exception:  # noqa: BLE001
            continue
        # cy < 200: the tile's + sits under the sticky aisle tab row (y≈168);
        # a tap there hits the tabs, not the Add (missed Adds at y=162–178,
        # 19 Oct 2026). It comes into the clear band after the next scroll.
        if cy < 200 or cy > 610 or not (8 <= cx <= 382):
            continue
        if deal_band and deal_band[0] <= cy <= deal_band[1]:
            continue
        fp = f"{int(round(cx / 8.0) * 8)}:{int(round(cy / 8.0) * 8)}"
        if fp in used:
            continue
        out.append((el, AppiumBy.IOS_PREDICATE, "Add To Cart", fp, cy, cx))
        if len(out) >= limit:
            break
    out.sort(key=lambda c: (c[4], c[5]))
    return out


def _added_stepper_at(driver, cx: float, cy: float) -> bool:
    """True once the tapped Add turned into a stepper — ONE search, no reads:
    the "+" must sit where the Add was (same window as ``_stepper_near``:
    centre within 70 px across / 25 px down; neighbour tiles are 128 px
    apart, so they can't match)."""
    try:
        return bool(driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(name == "Increase quantity by one" OR name == "Increase item quantity") '
            f"AND rect.x > {int(cx) - 95} AND rect.x < {int(cx) + 72} "
            f"AND rect.y > {int(cy) - 50} AND rect.y < {int(cy) + 28}",
        ))
    except Exception:  # noqa: BLE001
        return False


_NEXT_AISLES = (
    "Dairy & Eggs",
    "Meat & Seafood",
    "Bakery",
    "Snacks",
    "Pantry",
    "Frozen",
    "Beverages",
)


def _go_back_to_grid(driver) -> bool:
    """nav back (up to 3×) until the grocery grid shows again."""
    for _ in range(3):
        if _on_grocery_grid(driver):
            return True
        for by, value in (
            (AppiumBy.ACCESSIBILITY_ID, "nav_back_button"),
            (AppiumBy.ACCESSIBILITY_ID, "Back"),
        ):
            try:
                el = driver.find_element(by, value)
                if el.is_displayed():
                    el.click()
                    break
            except Exception:  # noqa: BLE001
                continue
        else:
            _tap_xy(driver, 22.0, 72.0)
        time.sleep(1.0)
    return _on_grocery_grid(driver)


def _switch_aisle(driver, markers: MarkerLog, tried: set[str]) -> bool:
    """Produce ran dry — open the next grocery aisle tab."""
    for _ in range(4):
        _scroll_up_grid(driver)
    for name in _NEXT_AISLES:
        if name in tried:
            continue
        tried.add(name)
        try:
            for el in driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                f'visible == 1 AND (name == "{name}" OR label == "{name}")',
            )[:2]:
                el.click()
                markers.mark("grocery_aisle_switched", aisle=name)
                print(f"  switched aisle → {name}")
                time.sleep(1.5)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _scroll_up_grid(driver) -> None:
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.1,
                "fromX": int(w * 0.5),
                "fromY": int(h * 0.30),
                "toX": int(w * 0.5),
                "toY": int(h * 0.85),
            },
        )
        time.sleep(0.2)
    except Exception:  # noqa: BLE001
        pass


def _on_grocery_grid(driver) -> bool:
    # A search on a long grid can time out — an error is "don't know", not
    # "off the grid", so retry before anyone navigates away.
    for attempt in range(3):
        try:
            return bool(
                driver.find_elements(
                    AppiumBy.IOS_PREDICATE,
                    'label CONTAINS[c] "Search in Groceries" OR '
                    'name == "Produce" OR label == "Produce" OR '
                    'name == "Add To Cart" OR name == "+" OR '
                    'name == "Increase quantity by one"',
                )
            )
        except Exception:  # noqa: BLE001
            time.sleep(1.0 + attempt)
    return False


def _add_basket_scaled_fast(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    sample: BasketSample,
    max_scrolls: int | None = None,
    source: str = "grocery_produce_plus",
    listing_units: int | None = None,
) -> list[dict]:
    """Add N distinct listing items, each raised to U units — every unit
    confirmed on the item's stepper before the next tap.

    Items that can't reach U (Amazon "limit of N per customer") are taken
    back out and replaced by another product, so the basket stays exact.
    """
    target = sample.num_items
    units_each = listing_units or sample.units_each
    if max_scrolls is None:
        max_scrolls = max(10, target + 10)

    print(
        f"Step: basket {sample.letter} — add {target} items × {units_each} "
        f"unit(s) each (total {sample.total_units}), confirming every unit …"
    )
    _dismiss_overlay_fast(driver)
    start_badge = _cart_badge_count(driver)
    outcome["cart_count_before_adds"] = start_badge
    outcome["asin_add_strategy"] = source
    outcome["basket_sample"] = sample.letter
    outcome["basket_num_items"] = target
    outcome["basket_units_each"] = units_each
    outcome["basket_total_units"] = sample.total_units
    markers.mark(
        "basket_plus_add_start",
        sample=sample.letter,
        num_items=target,
        units_each=units_each,
        total=sample.total_units,
        badge=start_badge,
        source=source,
    )

    used: set[str] = set()
    added: list[dict] = []
    replaced: list[dict] = []
    scrolls = 0
    t0 = time.monotonic()

    recoveries = 0
    dry_scrolls = 0
    # Adds judged "not registered" can still land seconds later and would sit
    # in the cart uncounted — re-check them before scrolling past them.
    pending_late: list[tuple[float, float]] = []

    def _settle_late_adds() -> None:
        if pending_late:
            time.sleep(4.0)  # late Adds land up to ~10 s after the tap
        for lcx, lcy in list(pending_late):
            pending_late.remove((lcx, lcy))
            if units_each == 1:
                # ×1: a stepper in the tile means the Add landed (×1).
                lst = (
                    {"plus": None, "qty": 1, "minus": None}
                    if _added_stepper_at(driver, lcx, lcy) else None
                )
            else:
                lst = _stepper_near(driver, lcx, lcy)
            if lst is None or lst.get("qty") is None:
                continue
            if lst["qty"] == units_each and len(added) < target:
                n_late = len(added) + 1
                added.append({
                    "index": n_late,
                    "source": source,
                    "add_path": "late_confirmed",
                    "confirmed": True,
                    "confirm_via": "stepper_qty_late",
                    "units_target": units_each,
                    "units_confirmed": lst["qty"],
                    "units_extra_taps": 0,
                    "x": lcx,
                    "y": lcy,
                })
                markers.mark("item_added_late", index=n_late, x=lcx, y=lcy)
                print(f"  late Add landed @({lcx:.0f},{lcy:.0f}) — counted as #{n_late}")
            else:
                ok_rm = _remove_line(driver, lcx, lcy, lst)
                markers.mark("late_add_removed", x=lcx, y=lcy, ok=ok_rm)
                print(
                    f"  late Add landed @({lcx:.0f},{lcy:.0f}) ×{lst['qty']} — "
                    f"removed (ok={ok_rm})"
                )
    aisles_tried: set[str] = set()
    # Every lookup snapshots the whole page, and Amazon's grid keeps growing
    # as it scrolls — after ~15 adds each read takes 20–60 s. A fresh aisle
    # keeps the page short.
    in_aisle = 0
    # One search per screen: its Adds are tapped by position, one confirmed
    # at a time (searches are the slow part, ~3.4 s each).
    queue: list[tuple] = []
    check_deal = sample.units_each > 5
    while len(added) < target and scrolls <= max_scrolls:
        if in_aisle >= _ADDS_PER_AISLE:
            _settle_late_adds()
            if len(added) >= target:
                break
            queue.clear()
            if _switch_aisle(driver, markers, aisles_tried):
                used.clear()
                dry_scrolls = 0
                in_aisle = 0
            else:
                in_aisle = -10_000  # no aisle left: stay here
        if not queue:
            queue = _collect_add_to_cart(driver, used, check_deal=check_deal)
        batch = queue
        if not batch and not _on_grocery_grid(driver):
            # A stray tap opened a product page — go back to the grid.
            recoveries += 1
            if recoveries > 4:  # 5 in a row with no add in between
                break
            print("  left the grocery grid — going back …")
            markers.mark("grocery_grid_recover", index=recoveries)
            if not _go_back_to_grid(driver):
                try:
                    _open_grocery_produce(driver, markers, outcome)
                except AssertionError:
                    # Still inside the grocery store (no home tab): a fresh
                    # aisle gets back to a grid.
                    if _switch_aisle(driver, markers, aisles_tried):
                        used.clear()
                        in_aisle = 0
            continue
        if not batch:
            dry_scrolls += 1
            # An aisle can be empty on Amazon's side ("Our shelves are
            # restocking…", seen 19 Oct 2026) — don't scroll it, move on.
            try:
                if driver.find_elements(
                    AppiumBy.IOS_PREDICATE,
                    'label CONTAINS[c] "shelves are restocking"',
                ):
                    print("  aisle is restocking (empty) — switching aisle")
                    markers.mark("grocery_aisle_restocking")
                    dry_scrolls = 6
            except Exception:  # noqa: BLE001
                pass
            if dry_scrolls >= 6:
                # This aisle has no unused Add To Cart left.
                if not _switch_aisle(driver, markers, aisles_tried):
                    break
                used.clear()
                dry_scrolls = 0
                continue
            _settle_late_adds()
            if len(added) >= target:
                break
            _scroll_down(driver, percent=0.50)
            scrolls += 1
            markers.mark("produce_grid_scrolled", index=scrolls)
            time.sleep(0.6)
            continue
        dry_scrolls = 0

        el, by, value, fp, y, cx = queue.pop(0)
        cy = y
        used.add(fp)
        n = len(added) + 1
        if units_each == 1:
            # ×1: one tap on Add gives exactly 1 — confirmation is just the
            # stepper appearing in the tile (one search per poll, no reads).
            _tap_xy(driver, cx, cy)
            ok = False
            end = time.monotonic() + _ADD_CONFIRM_TIMEOUT
            while time.monotonic() < end:
                time.sleep(0.3)
                if _added_stepper_at(driver, cx, cy):
                    ok = True
                    break
            if not ok:
                print(f"  item #{n}: Add did not register @({cx:.0f},{cy:.0f}) — next product")
                markers.mark("item_add_not_registered", x=cx, y=cy)
                pending_late.append((cx, cy))
                queue.clear()  # the page may have moved — search again
                continue
            st = {"plus": None, "qty": 1, "minus": None}
        else:
            try:
                _tap_xy(driver, cx, cy)
            except Exception as exc:  # noqa: BLE001
                print(f"  Add tap failed: {exc}")
                continue
            st, sheet = _wait_qty(driver, cx, cy, above=None, timeout=_ADD_CONFIRM_TIMEOUT)
            if sheet:
                print(f"  item #{n}: {sheet[:70]!r}")
                _close_qty_sheet(driver)
                st = _stepper_near(driver, cx, cy)
            if st is None or st["qty"] is None:
                print(f"  item #{n}: Add did not register @({cx:.0f},{cy:.0f}) — next product")
                markers.mark("item_add_not_registered", x=cx, y=cy)
                pending_late.append((cx, cy))
                queue.clear()
                continue

        qty = st["qty"]
        capped = False
        taps = 0
        retries = 0
        while qty < units_each:
            _tap_xy(driver, *st["plus"])
            taps += 1
            new, sheet = _wait_qty(
                driver, cx, cy, above=qty, timeout=_QTY_STEP_TIMEOUT
            )
            if sheet:
                low = sheet.lower()
                _close_qty_sheet(driver)
                new = _stepper_near(driver, cx, cy) or new
                if _is_limit_text(low):
                    capped = True
                    if new and new["qty"] is not None:
                        qty, st = new["qty"], new
                    print(f"  item #{n}: purchase limit — capped at ×{qty}")
                    break
                # "Quantity not updated": wait, then retry the same step.
                time.sleep(1.0)
            if new is not None and new["qty"] is not None and new["qty"] > qty:
                qty, st = new["qty"], new
                retries = 0
                continue
            retries += 1
            if retries >= 3:
                capped = True
                print(f"  item #{n}: qty stuck at ×{qty}")
                break

        if qty != units_each:
            # Can't hit the target on this product — take it back out and
            # use another one, so the basket total stays exact.
            # Fast confirms skip the minus — re-read the full stepper.
            removed_ok = _remove_line(driver, cx, cy, _stepper_near(driver, cx, cy) or st)
            if not removed_ok:
                print(f"  item #{n}: WARNING — could not fully remove it")
            replaced.append(
                {"x": cx, "y": cy, "qty_reached": qty, "capped": capped,
                 "removed": removed_ok}
            )
            markers.mark("item_replaced_short", x=cx, y=cy, qty=qty)
            print(f"  item #{n}: removed (×{qty} ≠ ×{units_each}) — will replace")
            continue

        entry = {
            "index": n,
            "source": source,
            "add_path": "plus_then_confirmed_qty",
            "add_by": by,
            "add_value": value,
            "fingerprint": fp,
            "confirmed": True,
            "confirm_via": "stepper_qty",
            "units_target": units_each,
            "units_confirmed": qty,
            "units_extra_taps": taps,
            "x": cx,
            "y": cy,
        }
        added.append(entry)
        recoveries = 0
        in_aisle += 1
        markers.mark(
            "item_added_to_cart",
            index=n,
            confirmed=True,
            fingerprint=fp,
            units_target=units_each,
            units_confirmed=qty,
            taps=taps,
            path="plus_then_confirmed_qty",
        )
        print(f"  added #{n}/{target} ×{qty} (confirmed, {taps} + taps)")

    _settle_late_adds()
    elapsed = time.monotonic() - t0
    outcome["items_added"] = list(added)
    outcome["items_added_count"] = len(added)
    outcome["items_replaced_short"] = replaced
    outcome["basket_units_confirmed"] = sum(e["units_confirmed"] for e in added)
    outcome["basket_add_elapsed_seconds"] = round(elapsed, 2)
    end_badge = _cart_badge_count(driver)
    outcome["cart_count_after_adds"] = end_badge
    markers.mark(
        "basket_plus_add_done",
        sample=sample.letter,
        added=len(added),
        target=target,
        units_each=units_each,
        units_confirmed=outcome["basket_units_confirmed"],
        replaced=len(replaced),
        badge_before=start_badge,
        badge_after=end_badge,
        scrolls=scrolls,
        elapsed_s=round(elapsed, 2),
        source=source,
    )
    print(
        f"  basket add done: {len(added)}/{target} items, "
        f"{outcome['basket_units_confirmed']}/{sample.total_units} units confirmed "
        f"in {elapsed:.1f}s (replaced={len(replaced)}, scrolls={scrolls})"
    )
    if len(added) < target:
        raise AssertionError(
            f"Only added {len(added)}/{target} items for basket {sample.letter} "
            f"(scrolls={scrolls}, source={source})"
        )
    return added


def _proceed_checkout_units(driver) -> int | None:
    """N from the cart's "Proceed to checkout (N items)" button."""
    try:
        for el in driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            'label BEGINSWITH[c] "Proceed to checkout"',
        )[:3]:
            m = re.search(r"\((\d+)\s+items?\)", el.get_attribute("label") or "")
            if m:
                return int(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    return None


# Place Your Order reloads after every qty change and is huge: per-element
# lookups (rect/label calls) cost seconds each. Read it with ONE page_source
# snapshot per check and parse steppers, quantities and titles from that.
_PYO_RELOAD_WAIT = 3.0
_PYO_STEP_TIMEOUT = 12.0
_PYO_STEP_RETRIES = 4
_PYO_INC_NAMES = ("Increase item quantity", "Increase quantity by one")
_TITLE_SKIP = ("ships from", "sold by", "bought in past", "gift options")


def _pyo_snapshot(driver):
    try:
        return ET.fromstring(driver.page_source)
    except Exception:  # noqa: BLE001
        return None


def _node_box(n) -> tuple[float, float, float, float]:
    return (
        float(n.get("x") or 0),
        float(n.get("y") or 0),
        float(n.get("width") or 0),
        float(n.get("height") or 0),
    )


def _pyo_rows(root, h: float) -> list[dict]:
    """Visible steppers: {"plus": (x, y), "qty": int|None, "title": str}."""
    if root is None:
        return []
    texts = []
    for n in root.iter("XCUIElementTypeStaticText"):
        if n.get("visible") != "true":
            continue
        x, y, w, hh = _node_box(n)
        texts.append((x, y + hh / 2.0, (n.get("label") or "").strip()))
    rows = []
    for n in root.iter("XCUIElementTypeButton"):
        if n.get("visible") != "true" or (n.get("name") or "") not in _PYO_INC_NAMES:
            continue
        x, y, w, hh = _node_box(n)
        px, py = x + w / 2.0, y + hh / 2.0
        if not (150 <= py <= h * 0.85):
            continue
        qty = None
        title_best = None
        for tx, ty, lab in texts:
            if lab.isdigit() and len(lab) <= 3 and abs(ty - py) <= 15 and px - 110 <= tx <= px:
                qty = int(lab)
            low = lab.lower()
            if (
                len(lab) >= 8
                and tx >= 120
                and py - 200 <= ty <= py - 60
                and not any(k in low for k in _TITLE_SKIP)
                and not lab.startswith(("$", "(", "+"))
                and low != "amazon.com"
            ):
                if title_best is None or ty < title_best[0]:
                    title_best = (ty, lab)
        rows.append({
            "plus": (px, py),
            "qty": qty,
            "title": title_best[1] if title_best else f"item@{int(py)}",
        })
    rows.sort(key=lambda r: r["plus"][1])
    return rows


def _pyo_sheet(root) -> str:
    if root is None:
        return ""
    for n in root.iter():
        lab = (n.get("label") or "")
        low = lab.lower()
        if n.get("visible") == "true" and (
            "limit of" in low or "quantity not updated" in low
            or "maximum quantity" in low
        ):
            return lab
    return ""


# A tap must never land on "Place your order" (it is in the page flow just
# below the last item, not sticky) or on links like "Change" address.
_PYO_MAX_TAP_Y_FRAC = 0.70
_PYO_PLACE_ORDER_CLEARANCE = 90.0  # center-to-center; last + sits ~121 above it


# Amazon's Place Your Order can hang on an update: a spinner over a greyed
# page, every tap ignored, and the spinner is NOT in the accessibility tree.
# Pace the updates and detect the spinner from a screenshot instead.
_PYO_PACE_SECONDS = 3.0
_PYO_HANG_WAIT = 90.0
_PYO_MAX_REENTRIES = 2


def _pyo_spinner_visible(driver) -> bool:
    """Orange loading ring in the centre of a washed-out (greyed) page."""
    try:
        im = Image.open(io.BytesIO(driver.get_screenshot_as_png())).convert("RGB")
    except Exception:  # noqa: BLE001
        return False
    w, h = im.size
    small = im.resize((w // 6, h // 6))
    sw, sh = small.size
    orange = total = 0
    sat_sum = sat_n = 0
    for y in range(int(sh * 0.15), int(sh * 0.95), 2):
        for x in range(0, sw, 2):
            r, g, b = small.getpixel((x, y))
            sat_sum += max(r, g, b) - min(r, g, b)
            sat_n += 1
            if 0.25 * sw <= x <= 0.75 * sw and 0.30 * sh <= y <= 0.80 * sh:
                total += 1
                if r > 220 and 120 < g < 200 and b < 80:
                    orange += 1
    if not total or not sat_n:
        return False
    return orange / total > 0.003 and sat_sum / sat_n < 8.0


def _pyo_wait_spinner(driver, markers: MarkerLog, *, max_wait: float) -> bool:
    """If the hang spinner is up, wait for it to clear. True = page usable."""
    if not _pyo_spinner_visible(driver):
        return True
    print(f"  Place Your Order is hung (spinner) — waiting up to {max_wait:.0f}s …")
    markers.mark("place_order_spinner_wait")
    t0 = time.monotonic()
    while time.monotonic() - t0 < max_wait:
        time.sleep(3.0)
        if not _pyo_spinner_visible(driver):
            print(f"  spinner cleared after {time.monotonic() - t0:.0f}s")
            markers.mark("place_order_spinner_cleared", s=round(time.monotonic() - t0))
            return True
    markers.mark("place_order_spinner_timeout")
    return False


def _reenter_checkout(driver, markers: MarkerLog, outcome: dict) -> bool:
    """Checkout dropped back to Cart: Proceed → address → Prime → PYO.
    Note: PYO quantity changes are per checkout session — they come back ×1."""
    print("  checkout closed — re-entering Place Your Order …")
    markers.mark("place_order_reenter")
    try:
        _recover_amazon_error_page(driver, markers)
        _tap_proceed_until_left_cart(driver, markers, outcome)
    except Exception as exc:  # noqa: BLE001
        print(f"  re-enter: Proceed failed: {exc}")
        return False
    for _ in range(10):
        time.sleep(1.0)
        root = _pyo_snapshot(driver)
        if _pyo_on_page(root) and not _pyo_spinner_visible(driver):
            return True
        screen = _detect_checkout_screen(driver)
        if screen == "address":
            _handle_address_screen(driver, markers, outcome)
        elif screen == "no_thanks":
            _dismiss_prime_if_blocking(driver, markers, outcome, why="re-enter")
        elif screen == "payment":
            _go_back(driver, markers, why="re-enter: skip payment picker")
    return _pyo_on_page(_pyo_snapshot(driver))


def _cart_visible(driver) -> bool:
    return _proceed_checkout_units(driver) is not None


def _pyo_on_page(root) -> bool:
    if root is None:
        return False
    return any(
        (n.get("name") or "").startswith("Place Your Order")
        for n in root.iter("XCUIElementTypeNavigationBar")
    )


def _pyo_place_order_ys(root) -> list[float]:
    ys = []
    for n in root.iter("XCUIElementTypeButton"):
        if n.get("visible") == "true" and (n.get("label") or "") == "Place your order":
            x, y, w, hh = _node_box(n)
            ys.append(y + hh / 2.0)
    return ys


def _pyo_find_row(root, row: dict, h: float) -> dict | None:
    rows = _pyo_rows(root, h)
    for r in rows:
        if not r["title"].startswith("item@") and r["title"] == row["title"]:
            return r
    py = row["plus"][1]
    near = [r for r in rows if abs(r["plus"][1] - py) <= 60]
    return min(near, key=lambda r: abs(r["plus"][1] - py)) if near else None


_PYO_MARKERS: list = [None]  # MarkerLog for the step helper (set per run)


def _pyo_wait_on_page(driver, timeout: float = 25.0):
    """Snapshot of Place Your Order once it has (re)loaded, else None.

    After "Deliver to this address" / payment Continue the page reloads for
    several seconds; a single read then misses the "Place Your Order" title
    and the guard wrongly reported "left_place_order" (19 Oct 2026)."""
    end = time.monotonic() + timeout
    root = _pyo_snapshot(driver)
    while not _pyo_on_page(root) and time.monotonic() < end:
        time.sleep(1.0)
        root = _pyo_snapshot(driver)
    return root if _pyo_on_page(root) else None


def _pyo_step_up(driver, row: dict, h: float) -> tuple[int, str]:
    """One + on Place Your Order, confirmed from snapshots. → (qty, status).

    status: "" ok/no-change, sheet text, "left_place_order", "unsafe",
    "unstable", "row_lost". The tap only happens on a settled page, inside a
    freshly found + button, away from "Place your order".
    """
    qty = row["qty"] or 0
    prev_y = None
    target = None
    if not _pyo_wait_spinner(driver, _PYO_MARKERS[0], max_wait=_PYO_HANG_WAIT):
        return qty, "hung"
    for _ in range(4):
        root = _pyo_wait_on_page(driver)
        if root is None:
            return qty, "left_place_order"
        sheet = _pyo_sheet(root)
        if sheet:
            _close_qty_sheet(driver)
            return qty, sheet
        r = _pyo_find_row(root, row, h)
        if r is None:
            return qty, "row_lost"
        if r["qty"] is not None and r["qty"] > qty:
            row.update(r)
            return r["qty"], ""
        if prev_y is not None and abs(prev_y - r["plus"][1]) <= 3:
            target = (r, root)
            break
        prev_y = r["plus"][1]
    if target is None:
        return qty, "unstable"
    r, root = target
    px, py = r["plus"]
    if not (150 <= py <= h * _PYO_MAX_TAP_Y_FRAC):
        return qty, "unsafe"
    if any(abs(py - oy) < _PYO_PLACE_ORDER_CLEARANCE for oy in _pyo_place_order_ys(root)):
        return qty, "unsafe"
    row.update(r)
    _tap_xy(driver, px, py)
    time.sleep(_PYO_RELOAD_WAIT)
    end = time.monotonic() + _PYO_STEP_TIMEOUT
    while time.monotonic() < end:
        if _pyo_spinner_visible(driver):
            # Hung update: wait it out; the wait doesn't eat the step timeout.
            if not _pyo_wait_spinner(driver, _PYO_MARKERS[0], max_wait=_PYO_HANG_WAIT):
                return qty, "hung"
            end = time.monotonic() + _PYO_STEP_TIMEOUT
        root = _pyo_snapshot(driver)
        if not _pyo_on_page(root):
            # Mid-reload after the tap — wait for the page, don't abort.
            root = _pyo_wait_on_page(driver, timeout=15.0)
            if root is None:
                return qty, "left_place_order"
        sheet = _pyo_sheet(root)
        if sheet:
            _close_qty_sheet(driver)
            return qty, sheet
        cur = _pyo_find_row(root, row, h)
        if cur is not None and cur["qty"] is not None and cur["qty"] > qty:
            row.update(cur)
            time.sleep(_PYO_PACE_SECONDS)  # pace updates — rapid ones hang PYO
            return cur["qty"], ""
        time.sleep(0.2)
    return qty, ""


def _raise_qty_on_place_order(
    driver, markers: MarkerLog, outcome: dict, *, sample: BasketSample
) -> None:
    """On Place Your Order: raise every item's stepper to units_each.

    Driven by quantity (any visible stepper below target is raised); each +
    is confirmed before the next because Amazon rejects rapid updates.
    """
    target = sample.units_each
    if target <= 1:
        outcome["place_order_qty_raised"] = 0
        return
    print(
        f"Step: on Place Your Order raise every item to ×{target} "
        f"({sample.num_items} item(s)) …"
    )
    try:
        h = float((driver.get_window_size() or {}).get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    for _ in range(3):
        _scroll_up_grid(driver)
    _PYO_MARKERS[0] = markers
    reentries = 0
    final: dict[str, int] = {}
    gave_up: set[str] = set()
    idle_scrolls = 0
    taps = 0
    t0 = time.monotonic()
    for _pass in range(sample.num_items * 6 + 12):
        rows = _pyo_rows(_pyo_snapshot(driver), h)
        for r in rows:
            if r["qty"] is not None:
                final[r["title"]] = r["qty"]
        todo = [
            r for r in rows
            if r["qty"] is not None and r["qty"] < target
            and f"{r['title']}|{r['qty']}" not in gave_up
            and r["plus"][1] <= h * _PYO_MAX_TAP_Y_FRAC
        ]
        if not todo:
            if len(final) >= sample.num_items and all(
                q >= target for q in final.values()
            ):
                break
            idle_scrolls += 1
            if idle_scrolls > 3:
                break
            _scroll_down(driver, percent=0.35)
            time.sleep(0.6)
            continue
        idle_scrolls = 0
        row = todo[0]
        retries = 0
        capped = False
        aborted = False
        stop_all = False
        while (row["qty"] or 0) < target:
            before = row["qty"] or 0
            new_qty, sheet = _pyo_step_up(driver, row, h)
            taps += 1
            if sheet in ("left_place_order", "hung"):
                # Checkout dropped back to Cart (or stayed hung): re-enter it.
                # Only when the Cart is really showing — never navigate blind.
                if reentries < _PYO_MAX_REENTRIES and (
                    _cart_visible(driver)
                    or (sheet == "hung" and _pyo_on_page(_pyo_snapshot(driver)))
                ):
                    if sheet == "hung":
                        print("  still hung — leaving checkout via DONE …")
                        try:
                            for el in driver.find_elements(
                                AppiumBy.IOS_PREDICATE,
                                'type == "XCUIElementTypeButton" AND name == "Cancel"',
                            )[:3]:
                                if float((el.rect or {}).get("y", 999)) < 110:
                                    el.click()
                                    time.sleep(3.0)
                                    break
                        except Exception:  # noqa: BLE001
                            pass
                    reentries += 1
                    outcome["place_order_reentries"] = reentries
                    if _reenter_checkout(driver, markers, outcome):
                        print("  back on Place Your Order — quantities reset, continuing")
                        final.clear()
                        gave_up.clear()
                        aborted = True  # rescan rows from the top
                        break
                print(f"  {sheet} — stopping qty raise (no further taps)")
                markers.mark("place_order_qty_aborted", reason=sheet)
                outcome["place_order_qty_aborted"] = sheet
                aborted = True
                stop_all = True
                break
            if sheet == "unsafe":
                # Row too low / near "Place your order": bring it up first.
                _scroll_down(driver, percent=0.20)
                time.sleep(0.8)
                retries += 1
                if retries >= _PYO_STEP_RETRIES:
                    break
                continue
            if sheet and _is_limit_text(sheet):
                capped = True
                break
            if new_qty > before:
                row["qty"] = new_qty
                retries = 0
                continue
            retries += 1
            if retries >= _PYO_STEP_RETRIES:
                capped = True
                break
        q = row["qty"] or 0
        final[row["title"]] = q
        if q < target:
            gave_up.add(f"{row['title']}|{q}")
        markers.mark("place_order_qty_set", title=row["title"][:60], qty=q, capped=capped)
        print(
            f"  {row['title'][:40]!r}: ×{q}"
            + (" (CAPPED — purchase limit)" if q < target and capped else "")
            + (" (not reached)" if q < target and not capped else "")
        )
        if aborted and stop_all:
            break
        if aborted:
            for _ in range(3):
                _scroll_up_grid(driver)
            continue

    units = sum(final.values())
    short = {t: q for t, q in final.items() if q < target}
    outcome["place_order_item_qty"] = final
    outcome["place_order_units"] = units
    outcome["place_order_items_short"] = short
    outcome["place_order_plus_taps"] = taps
    outcome["basket_exact"] = (
        len(final) == sample.num_items and units == sample.total_units
    )
    markers.mark(
        "place_order_qty_done",
        items=len(final),
        units=units,
        target=sample.total_units,
        short=len(short),
        taps=taps,
        elapsed_s=round(time.monotonic() - t0, 1),
    )
    print(
        f"  Place Your Order: {len(final)}/{sample.num_items} item(s), "
        f"{units}/{sample.total_units} units, {taps} + taps in "
        f"{time.monotonic() - t0:.0f}s → basket_exact={outcome['basket_exact']}"
    )
    _mark_step(
        outcome,
        "place_order_qty",
        status="ok" if outcome["basket_exact"] else "partial",
        units=units,
        target=sample.total_units,
    )


# Hybrid: Place Your Order allows only ~30–45 qty updates per checkout session
# before it hangs (and leaving checkout resets them), so the big baskets raise
# units on the Cart instead (persistent, ~2.8 s per confirmed +, no hang).
# E moved to the Cart too: Place Your Order hung after ~20 updates.
CART_QTY_BASKETS = frozenset({"E", "G", "H"})
# No "visible == 1": the cart is web content and lines under/near the
# floating "Proceed to checkout" bar get visible=false while on screen.
# On-screen is decided by the element's real position instead.
_CART_INC_PRED = (
    'type == "XCUIElementTypeButton" AND '
    'name BEGINSWITH "Increase quantity by one, Quantity is"'
)
_CART_INC_RE = re.compile(r"Quantity is (\d+), (.+)$")
_CART_STEP_TIMEOUT = 8.0
_CART_STEP_RETRIES = 5


# At ×10+ a cart line drops its −/qty/+ stepper for a "Qty: [10]" text
# field named "Quantity <product>" — read both, or a line that just reached
# ×10 looks like it vanished (and was wrongly reported "capped at ×9").
_CART_QTY_FIELD_PRED = (
    'type == "XCUIElementTypeTextField" AND name BEGINSWITH "Quantity "'
)


def _cart_qty_rows(driver, h: float) -> list[dict]:
    """Visible cart lines: {"name", "qty", "plus": (x, y) | None}."""
    # Position filter inside the predicate (WDA supports rect.y): returns only
    # on-screen lines in ~1 s instead of reading every off-screen line (~15 s).
    band = f" AND rect.y > 140 AND rect.y < {int(h * 0.85)}"
    for _ in range(3):
        try:
            out = []
            for el in driver.find_elements(AppiumBy.IOS_PREDICATE, _CART_INC_PRED + band)[:10]:
                m = _CART_INC_RE.search(el.get_attribute("name") or "")
                if not m:
                    continue
                cx, cy, _r = _rect_center(el)
                if 150 <= cy <= h * 0.85:
                    out.append({"name": m.group(2).strip(), "qty": int(m.group(1)), "plus": (cx, cy)})
            for el in driver.find_elements(AppiumBy.IOS_PREDICATE, _CART_QTY_FIELD_PRED + band)[:10]:
                name = (el.get_attribute("name") or "")[len("Quantity "):].strip()
                val = (el.get_attribute("value") or "").strip()
                if name and val.isdigit():
                    cx, cy, _r = _rect_center(el)
                    if 150 <= cy <= h * 0.85:
                        out.append({"name": name, "qty": int(val), "plus": None, "y": cy})
            return sorted(out, key=lambda r: (r["plus"] or (0, r.get("y", 0)))[1])
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    return []


def _cart_drag(driver, *, frac: float, up: bool = False) -> None:
    """Controlled scroll for the Cart (no fling). The shared ``mobile: swipe``
    flings past several cart lines straight into the recommendations."""
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        w, h = 390.0, 844.0
    y0, y1 = (h * 0.30, h * (0.30 + frac)) if up else (h * 0.75, h * (0.75 - frac))
    try:
        driver.execute_script(
            "mobile: dragFromToWithVelocity",
            {
                "pressDuration": 0.05,
                "holdDuration": 0.25,
                "velocity": 500,
                "fromX": int(w * 0.88),
                "fromY": int(y0),
                "toX": int(w * 0.88),
                "toY": int(y1),
            },
        )
    except Exception:  # noqa: BLE001
        _scroll_down(driver, percent=frac)
    time.sleep(0.35)


def _cart_top_on_screen(driver) -> bool:
    """The cart is web content: its "visible" flags are true even off screen,
    so check the "Shopping Cart" title's real position instead."""
    try:
        for el in driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeStaticText" AND label == "Shopping Cart"',
        )[:2]:
            y = float((el.rect or {}).get("y", -1))
            if 90 <= y <= 450:
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _cart_scroll_to_top(driver, *, max_scrolls: int = 30) -> bool:
    """Back to the top of the cart: tap the Cart tab (reloads at the top),
    else scroll up until the "Shopping Cart" title is really on screen. (A
    floating "Proceed to checkout" copy shows deep in the page — not a top
    signal.)"""
    if _cart_top_on_screen(driver):
        return True
    try:
        driver.find_element(AppiumBy.ACCESSIBILITY_ID, "cartTab").click()
        time.sleep(1.5)
        if _cart_top_on_screen(driver):
            return True
    except Exception:  # noqa: BLE001
        pass
    for _ in range(max_scrolls):
        _scroll_up_grid(driver)
        if _cart_top_on_screen(driver):
            return True
    return False


def _cart_line_qty(
    driver, name: str, y_hint: float | None = None
) -> tuple[int | None, tuple | None]:
    """One cheap lookup for THIS line: (qty, plus point | None if Qty field).

    With ``y_hint`` the search is limited to a band around the line — a
    banded search costs ~0.4 s on a big cart vs ~3.4 s for the whole page
    (measured); it falls back to the whole page if the line moved away."""
    esc = name.replace('\\', '\\\\').replace('"', '\\"')
    pred = (
        f'((type == "XCUIElementTypeButton" AND '
        f'name BEGINSWITH "Increase quantity by one, Quantity is" AND '
        f'name ENDSWITH ", {esc}") OR '
        f'(type == "XCUIElementTypeTextField" AND name == "Quantity {esc}"))'
    )
    preds = [pred]
    if y_hint is not None:
        preds.insert(0, f"{pred} AND rect.y > {int(y_hint) - 220} AND rect.y < {int(y_hint) + 200}")
    for p in preds:
        for _ in range(2):
            try:
                found = driver.find_elements(AppiumBy.IOS_PREDICATE, p)[:2]
                for el in found:
                    nm = el.get_attribute("name") or ""
                    if nm.startswith("Quantity "):
                        val = (el.get_attribute("value") or "").strip()
                        return (int(val) if val.isdigit() else None), None
                    m = _CART_INC_RE.search(nm)
                    if m:
                        cx, cy, _r = _rect_center(el)
                        return int(m.group(1)), (cx, cy)
                break  # nothing in this scope — try the next one
            except Exception:  # noqa: BLE001
                time.sleep(0.15)
    return None, None


def _is_limit_text(text: str) -> bool:
    """Amazon purchase-limit wording: "limit of N per customer", "maximum
    quantity", "This seller has only 2 of these available" (19 Oct 2026)."""
    low = (text or "").lower()
    return "limit" in low or "maximum" in low or "of these available" in low


class LeftCartDuringRaise(AssertionError):
    """The app left the Cart (e.g. into checkout) while raising quantities."""


def _cart_tap_safe(driver, x: float, y: float) -> str:
    """"" if a Cart + tap at (x, y) is safe, else the reason.

    The Cart pins a "Proceed to checkout (N items)" bar at the top once
    scrolled; a stale + position tapped it and opened checkout (19 Oct
    2026, basket E). Before every tap: still on the Cart (no Place Your
    Order page), below the pinned bar, and clear of any checkout button."""
    if y < 200:
        return "under the pinned Proceed bar"
    try:
        if driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeNavigationBar" AND name BEGINSWITH "Place Your Order"',
        ) or driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(label ==[c] "Place your order" OR name ==[c] "Place your order")',
        ):
            return "left_cart"
        near = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(label BEGINSWITH[c] "Proceed to checkout" OR name BEGINSWITH[c] "Proceed to checkout") '
            f"AND rect.y > {int(y) - 90} AND rect.y < {int(y) + 50}",
        )
        if near:
            return "too close to Proceed to checkout"
    except Exception:  # noqa: BLE001
        return "could not verify"
    return ""


def _raise_qty_on_cart(
    driver, markers: MarkerLog, outcome: dict, *, sample: BasketSample,
    tap=None,
) -> None:
    """On the Cart (recording running): raise every line to units_each, one
    confirmed + at a time (rapid taps get dropped)."""
    target = sample.units_each
    print(f"Step: on Cart raise every item to ×{target} ({sample.num_items} item(s)) …")
    try:
        h = float((driver.get_window_size() or {}).get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    final: dict[str, int] = {}
    capped: set[str] = set()      # only on an explicit Amazon limit message
    deferred: set[str] = set()    # slow this sweep — retried next sweep
    taps = 0
    t0 = time.monotonic()

    def _raise_row(row: dict) -> dict:
        nonlocal taps
        retries = 0
        # The row just came from a fresh read — no second lookup needed.
        fresh = row.get("plus") is not None
        while row["qty"] < target:
            before = row["qty"]
            if fresh:
                q_now, plus_now = row["qty"], row["plus"]
            else:
                # Fresh position before a retry: a cart reload shifts lines,
                # and a stale point taps nothing.
                hint = row["plus"][1] if row.get("plus") else None
                q_now, plus_now = _cart_line_qty(driver, row["name"], hint)
            fresh = False
            if q_now is None:
                break  # line not found — never tap blind (left the Cart?)
            row = {"name": row["name"], "qty": q_now, "plus": plus_now}
            if q_now >= target or plus_now is None:
                break
            before = q_now
            # Web content reports off-screen positions too — tapping one hits
            # whatever is there (it opened "Amazon Now"). On-screen only.
            if not (150 <= plus_now[1] <= h * 0.85):
                break  # the sweep scrolls it into view and comes back
            unsafe = _cart_tap_safe(driver, *plus_now)
            if unsafe == "left_cart":
                markers.mark("cart_raise_left_cart", product=row["name"][:60])
                raise LeftCartDuringRaise(
                    "Left the Cart while raising quantities (checkout opened) — "
                    "stopped without tapping"
                )
            if unsafe:
                print(f"  skip + for {row['name'][:30]!r}: {unsafe}")
                break  # the sweep scrolls / re-reads and comes back
            (tap or _tap_xy)(driver, *plus_now)
            taps += 1
            t_tap = time.monotonic()
            end = t_tap + _CART_STEP_TIMEOUT
            moved = False
            sheet_checked = False
            while time.monotonic() < end:
                q, plus = _cart_line_qty(driver, row["name"], plus_now[1])
                if q is not None and q > before:
                    row = {"name": row["name"], "qty": q, "plus": plus}
                    moved = True
                    break
                # Sheets (limit / not updated) only matter if nothing moved.
                if not sheet_checked and time.monotonic() - t_tap > 3.0:
                    sheet_checked = True
                    sheet = _qty_sheet(driver)
                    if sheet:
                        _close_qty_sheet(driver)
                        if _is_limit_text(sheet):
                            capped.add(row["name"])
                            break
                time.sleep(0.1)
            if row["name"] in capped:
                break
            if moved and row["plus"] is None:
                break  # switched to the Qty text field (≥ ×10)
            if moved:
                retries = 0
                # The confirm read the + position — use it for the next tap.
                fresh = row["plus"] is not None
                continue
            retries += 1
            if retries >= _CART_STEP_RETRIES:
                # Slow, not necessarily capped — retry it in the next sweep.
                deferred.add(row["name"])
                break
            time.sleep(1.0)
        return row

    # Sweep top → bottom; repeat while a sweep still finds work (items above
    # the one just raised are only reached by starting from the top again).
    for sweep in range(10):
        _cart_scroll_to_top(driver)
        deferred.clear()
        worked = False
        empty_streak = 0
        for _step in range(sample.num_items * 3 + 20):
            rows = _cart_qty_rows(driver, h)
            if not rows and _recover_amazon_error_page(driver, markers):
                # A 50-line Cart can crash to "UH-OH … TRY AGAIN" — reload
                # and pick up where it stopped (quantities already saved).
                _cart_scroll_to_top(driver)
                continue
            for r in rows:
                final[r["name"]] = r["qty"]
            todo = [
                r for r in rows
                if r["qty"] < target and r["name"] not in capped
                and r["name"] not in deferred and r["plus"]
            ]
            if todo:
                row = _raise_row(todo[0])
                final[row["name"]] = row["qty"]
                worked = True
                empty_streak = 0
                markers.mark("cart_qty_set", product=row["name"][:60], qty=row["qty"])
                print(
                    f"  {row['name'][:40]!r}: ×{row['qty']}"
                    + (" (CAPPED — limit)" if row["name"] in capped else "")
                    + (" (slow — retry next sweep)" if row["name"] in deferred else "")
                )
                continue
            if len(final) >= sample.num_items and all(
                q >= target or n in capped for n, q in final.items()
            ):
                break
            empty_streak = 0 if rows else empty_streak + 1
            if empty_streak >= 4:
                break  # past the last cart line
            _cart_drag(driver, frac=0.30)
        markers.mark("cart_qty_sweep", index=sweep + 1, worked=worked, lines=len(final))
        if not worked:
            break
        if len(final) >= sample.num_items and all(
            q >= target or n in capped for n, q in final.items()
        ):
            break

    _cart_scroll_to_top(driver)
    checkout_units = _proceed_checkout_units(driver)
    units = sum(final.values())
    outcome["cart_item_qty"] = final
    outcome["cart_qty_plus_taps"] = taps
    outcome["checkout_units_after_cart_qty"] = checkout_units
    outcome["basket_exact"] = checkout_units == sample.total_units
    markers.mark(
        "cart_qty_done",
        items=len(final),
        units=units,
        checkout_units=checkout_units,
        target=sample.total_units,
        taps=taps,
        elapsed_s=round(time.monotonic() - t0, 1),
    )
    print(
        f"  Cart: {len(final)} line(s), {taps} + taps in "
        f"{time.monotonic() - t0:.0f}s → Proceed to checkout ({checkout_units} items) "
        f"vs {sample.total_units} → basket_exact={outcome['basket_exact']}"
    )


def _cart_line_names(driver) -> set[str]:
    """Products that are real cart lines (have a stepper or a Qty field)."""
    names: set[str] = set()
    try:
        for el in driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            'name BEGINSWITH "Increase quantity by one, Quantity is"',
        ):
            m = _CART_INC_RE.search(el.get_attribute("name") or "")
            if m:
                names.add(m.group(2).strip())
        for el in driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeTextField" AND name BEGINSWITH "Quantity "',
        ):
            names.add((el.get_attribute("name") or "")[len("Quantity "):].strip())
    except Exception:  # noqa: BLE001
        pass
    return names


def _cart_says_empty(driver) -> bool:
    try:
        return bool(
            driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'label CONTAINS[c] "Your Amazon Cart is empty" OR '
                'label CONTAINS[c] "Your cart is empty"',
            )
        )
    except Exception:  # noqa: BLE001
        return False


def _leave_checkout_if_open(driver, markers: MarkerLog | None = None) -> bool:
    """If checkout (Place Your Order / its Prime sheet) is open, leave it ONLY
    via the header's Cancel / Back button (never anything on the page), then
    reopen the Cart. True if it had to leave."""
    try:
        if not driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeNavigationBar" AND name BEGINSWITH "Place Your Order"',
        ):
            return False
    except Exception:  # noqa: BLE001
        return False
    print("  checkout opened unexpectedly — leaving via header Cancel/Back (no other taps)")
    if markers is not None:
        markers.mark("left_checkout_via_header")
    for _ in range(3):
        tapped = False
        try:
            for el in driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND rect.y < 110 AND '
                '(label ==[c] "Cancel" OR name ==[c] "Cancel" OR label ==[c] "Back" '
                'OR name == "BackButton")',
            )[:1]:
                el.click()
                tapped = True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2.0)
        try:
            still = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeNavigationBar" AND name BEGINSWITH "Place Your Order"',
            )
        except Exception:  # noqa: BLE001
            still = []
        if not still:
            break
        if not tapped:
            break
    try:
        driver.find_element(AppiumBy.ACCESSIBILITY_ID, "cartTab").click()
        time.sleep(2.0)
    except Exception:  # noqa: BLE001
        pass
    return True


def _clear_cart_lines(driver, markers: MarkerLog, outcome: dict) -> None:
    """Delete every cart line via its own "Delete <product>" button.

    TC9's generic clear misses lines at ×10+ (they show a Qty field, not a
    stepper) and left whole baskets behind. Only real cart lines are deleted
    ("Saved for later" items have neither a stepper nor a Qty field).
    """
    print("Step: clear cart lines (Delete <product>) …")
    try:
        _open_cart_page(driver, markers, outcome)
    except Exception as exc:  # noqa: BLE001
        print(f"  cart open for clear failed: {exc}")
        return
    try:
        h = float((driver.get_window_size() or {}).get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    _cart_scroll_to_top(driver)
    empty_checks = 0
    removed = 0
    empty_screens = 0
    t0 = time.monotonic()
    band = f"AND rect.y > 150 AND rect.y < {int(h * 0.85)}"

    def _q(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"')

    def _is_cart_line(product: str, cy: float) -> bool:
        # Real cart line = has a stepper or Qty field in the same tile
        # ("Saved for later" rows have neither). Banded search: ~0.4 s.
        try:
            return bool(driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                f'((type == "XCUIElementTypeButton" AND '
                f'name BEGINSWITH "Increase quantity by one, Quantity is" AND '
                f'name ENDSWITH ", {_q(product)}") OR '
                f'(type == "XCUIElementTypeTextField" AND name == "Quantity {_q(product)}")) '
                f"AND rect.y > {int(cy) - 260} AND rect.y < {int(cy) + 80}",
            ))
        except Exception:  # noqa: BLE001
            return False

    for _ in range(300):
        target = None
        if _leave_checkout_if_open(driver, markers):
            continue
        try:
            dels = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                f'type == "XCUIElementTypeButton" AND name BEGINSWITH "Delete " {band}',
            )
        except Exception:  # noqa: BLE001
            dels = []
        for el in dels[:4]:
            try:
                product = (el.get_attribute("name") or "")[len("Delete "):].strip()
                cx, cy, _r = _rect_center(el)
            except Exception:  # noqa: BLE001
                continue
            if product and _is_cart_line(product, cy):
                target = (product, cx, cy)
                break
        if target is None:
            # Nothing deletable on this screen: empty, or lines further down.
            units = _proceed_checkout_units(driver)
            if units == 0 or _cart_says_empty(driver):
                break
            if _recover_amazon_error_page(driver, markers):
                continue
            empty_screens += 1
            if empty_screens >= 6:
                empty_checks += 1
                if empty_checks > 4:
                    print("  could not confirm the cart is empty — continuing")
                    break
                _cart_scroll_to_top(driver)
                empty_screens = 0
            else:
                _cart_drag(driver, frac=0.33)
            continue
        empty_screens = 0
        product, cx, cy = target
        # Same guard as the + taps: a stale Delete position tapped the
        # pinned "Proceed to checkout" bar and opened checkout (19 Oct 2026).
        unsafe = _cart_tap_safe(driver, cx, cy)
        if unsafe:
            if unsafe != "left_cart":
                _cart_drag(driver, frac=0.25)
            continue
        _tap_xy(driver, cx, cy)
        gone_pred = (
            f'type == "XCUIElementTypeButton" AND name == "Delete {_q(product)}" '
            f"AND rect.y > {int(cy) - 120} AND rect.y < {int(cy) + 120}"
        )
        end = time.monotonic() + 6.0
        while time.monotonic() < end:
            time.sleep(0.4)
            try:
                if not driver.find_elements(AppiumBy.IOS_PREDICATE, gone_pred):
                    removed += 1
                    break
            except Exception:  # noqa: BLE001
                pass
        if removed and removed % 5 == 0:
            print(f"  … deleted {removed} line(s) in {time.monotonic() - t0:.0f}s")
    _cart_scroll_to_top(driver)
    left = _proceed_checkout_units(driver) or 0
    outcome["cart_lines_deleted"] = removed
    outcome["cart_count_after_clear"] = left
    markers.mark("cart_lines_cleared", deleted=removed, left=left,
                 elapsed_s=round(time.monotonic() - t0, 1))
    print(
        f"  deleted {removed} cart line(s) in {time.monotonic() - t0:.0f}s "
        f"→ Proceed to checkout ({left} items)"
    )
    if left:
        print(f"  WARNING: {left} item(s) still in the cart after clearing")
    # Back to Home — the next step opens Grocery from the home tab bar.
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "home"),
        (AppiumBy.ACCESSIBILITY_ID, "homeTab"),
        (AppiumBy.ACCESSIBILITY_ID, "Home"),
    ):
        try:
            driver.find_element(by, value).click()
            time.sleep(1.0)
            break
        except Exception:  # noqa: BLE001
            continue


# --- Basket G: batch raise on a small Cart ---------------------------------
# One 50-line Cart makes every + check read a huge page (18–22 s per step,
# stalls of minutes); the grid + is refused by Amazon ("Quantity not
# updated"). So G goes in 5 rounds: add 10 ×1 → Cart → raise those to ×5.
BATCH_CART_BASKETS = frozenset({"G"})
_BATCH_SIZE = 10
_BATCH_AISLES = ("Produce", "Dairy & Eggs", "Meat & Seafood", "Frozen", "Snacks",
                 "Bakery", "Pantry", "Beverages")


def _go_home_tab(driver) -> None:
    for value in ("home", "homeTab", "Home"):
        try:
            driver.find_element(AppiumBy.ACCESSIBILITY_ID, value).click()
            time.sleep(1.0)
            return
        except Exception:  # noqa: BLE001
            continue


def _open_grocery_aisle(driver, markers: MarkerLog, outcome: dict, aisle: str) -> None:
    _open_grocery_produce(driver, markers, outcome)
    if aisle != "Produce":
        # _switch_aisle opens the first aisle not in ``tried``.
        tried = {a for a in _NEXT_AISLES if a != aisle}
        if not _switch_aisle(driver, markers, tried):
            print(f"  aisle {aisle!r} not found — staying on Produce")


_LOW_STOCK_RE = re.compile(r"Only (\d+) left", re.I)


def _cart_low_stock_lines(driver, h: float, need: int) -> list[tuple[str, int, float, float]]:
    """Cart lines on screen marked "Only N left" with N < need →
    [(product, N, delete_x, delete_y)]. The note sits in the line's tile,
    above its Delete button."""
    out: list[tuple[str, int, float, float]] = []
    try:
        notes = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            f'label CONTAINS[c] "Only " AND label CONTAINS[c] " left" '
            f"AND rect.y > 140 AND rect.y < {int(h * 0.85)}",
        )
    except Exception:  # noqa: BLE001
        return out
    for note in notes[:6]:
        try:
            m = _LOW_STOCK_RE.search(note.get_attribute("label") or "")
            if not m or int(m.group(1)) >= need:
                continue
            ny = float((note.rect or {}).get("y", 0))
            dels = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND name BEGINSWITH "Delete " '
                f"AND rect.y > {int(ny) - 10} AND rect.y < {int(ny) + 260}",
            )
            if not dels:
                continue
            product = (dels[0].get_attribute("name") or "")[len("Delete "):].strip()
            dx, dy, _r = _rect_center(dels[0])
            # Same rule as the + taps: never under the pinned Proceed bar or
            # near a checkout button.
            if not (200 <= dy <= h * 0.85) or _cart_tap_safe(driver, dx, dy):
                continue
            if product and not any(o[0] == product for o in out):
                out.append((product, int(m.group(1)), dx, dy))
        except Exception:  # noqa: BLE001
            continue
    return out


def _replace_low_stock_lines(
    driver, markers: MarkerLog, outcome: dict, *, sample: BasketSample
) -> None:
    """Before recording (E/H): a line that can't reach ×units_each ("Only 2
    left" → "This seller has only 2 of these available") is deleted and a
    product from another aisle added in its place, so the Cart raise can be
    exact. Up to 3 rounds."""
    try:
        h = float((driver.get_window_size() or {}).get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    aisles = [a for a in _BATCH_AISLES if a != "Produce"]
    replaced: list[str] = []
    for rnd in range(3):
        _cart_scroll_to_top(driver)
        low: list[tuple[str, int, float, float]] = []
        for _ in range(sample.num_items + 4):
            found = _cart_low_stock_lines(driver, h, sample.units_each)
            for item in found:
                if not any(l[0] == item[0] for l in low):
                    low.append(item)
            if found:
                break  # delete what is on screen first (positions are fresh)
            if _proceed_checkout_units(driver) is None and _cart_says_empty(driver):
                break
            _cart_drag(driver, frac=0.30)
            if _cart_top_on_screen(driver):
                break
        if not low:
            break
        deleted = 0
        for product, n, dx, dy in low:
            print(f"  low stock: {product[:40]!r} only {n} left (< ×{sample.units_each}) — replacing")
            markers.mark("low_stock_line", product=product[:60], left=n)
            _tap_xy(driver, dx, dy)
            time.sleep(2.0)
            deleted += 1
            replaced.append(product)
        aisle = aisles[rnd % len(aisles)]
        _go_home_tab(driver)
        _open_grocery_aisle(driver, markers, outcome, aisle)
        # The add helper rewrites the basket's add bookkeeping — keep the
        # main basket's numbers (a swap doesn't change how many lines exist).
        keep = {k: outcome.get(k) for k in (
            "items_added_count", "items_added", "basket_add_elapsed_seconds",
            "basket_units_confirmed", "asin_add_strategy", "basket_sample",
            "basket_num_items", "basket_units_each", "basket_total_units",
            "cart_count_before_adds")}
        try:
            _add_basket_scaled_fast(
                driver, markers, outcome,
                sample=BasketSample(sample.letter, deleted, 1),
                max_scrolls=12, source=f"replacement_{aisle}", listing_units=1,
            )
        finally:
            outcome.update(keep)
        _open_cart_page(driver, markers, outcome)
    outcome["low_stock_replaced"] = replaced
    if replaced:
        print(f"  replaced {len(replaced)} low-stock line(s): {[r[:30] for r in replaced]}")


def _add_and_raise_in_batches(
    driver, markers: MarkerLog, outcome: dict, *, sample: BasketSample
) -> None:
    """G: 5 × (add 10 items ×1 → Cart → raise them to ×5), every step
    confirmed. Quantity taps: W3C, 300 ms movement + 40 ms press."""
    t0 = time.monotonic()
    rounds = (sample.num_items + _BATCH_SIZE - 1) // _BATCH_SIZE
    total_added = 0
    total_taps = 0
    add_s = 0.0
    raise_s = 0.0
    for i in range(rounds):
        n = min(_BATCH_SIZE, sample.num_items - total_added)
        aisle = _BATCH_AISLES[i % len(_BATCH_AISLES)]
        print(f"=== G round {i + 1}/{rounds}: add {n} from {aisle!r} → Cart → ×{sample.units_each} ===")
        markers.mark("g_batch_start", index=i + 1, aisle=aisle, items=n)
        if i > 0:
            _go_home_tab(driver)
        t = time.monotonic()
        _open_grocery_aisle(driver, markers, outcome, aisle)
        _add_basket_scaled_fast(
            driver, markers, outcome,
            sample=BasketSample(sample.letter, n, 1),
            max_scrolls=max(20, n * 2 + 10),
            source=f"grocery_{aisle.lower().replace(' & ', '_').replace(' ', '_')}",
            listing_units=1,
        )
        total_added += int(outcome.get("items_added_count") or 0)
        add_s += time.monotonic() - t

        t = time.monotonic()
        _open_cart_page(driver, markers, outcome)
        _raise_qty_on_cart(
            driver, markers, outcome,
            # Cumulative: earlier rounds' lines are already ×5 and only read.
            sample=BasketSample(sample.letter, total_added, sample.units_each),
            tap=_grid_tap,
        )
        total_taps += int(outcome.get("cart_qty_plus_taps") or 0)
        raise_s += time.monotonic() - t
        print(
            f"  round {i + 1}: {total_added} item(s) in cart, Proceed shows "
            f"{outcome.get('checkout_units_after_cart_qty')} (want "
            f"{total_added * sample.units_each}) — "
            f"{time.monotonic() - t0:.0f}s so far"
        )
    outcome["items_added_count"] = total_added
    outcome["basket_add_elapsed_seconds"] = round(add_s, 2)
    outcome["g_batch_raise_seconds"] = round(raise_s, 1)
    outcome["cart_qty_plus_taps"] = total_taps
    outcome["qty_raised_on"] = "cart (5 batches, before recording)"
    markers.mark("g_batches_done", items=total_added, taps=total_taps,
                 add_s=round(add_s, 1), raise_s=round(raise_s, 1))
    print(
        f"  G batches done: {total_added} items, {total_taps} + taps — "
        f"add {add_s:.0f}s, raise {raise_s:.0f}s, total {time.monotonic() - t0:.0f}s"
    )


def _add_basket_produce_or_search(
    driver, markers: MarkerLog, outcome: dict, *, sample: BasketSample
) -> list[dict]:
    """Grocery Produce grid (other aisles when it runs dry). No general
    Amazon search fallback — those results have no Add To Cart steppers."""
    _open_grocery_produce(driver, markers, outcome)
    return _add_basket_scaled_fast(
        driver,
        markers,
        outcome,
        sample=sample,
        max_scrolls=max(20, sample.num_items * 2 + 10),
        source="grocery_produce_plus",
        listing_units=1,  # units are raised on Place Your Order (recorded)
    )


_AFTER_ADDRESS_TIMEOUT = 45.0
_RECORDING_TAIL_HOLD = 4.0  # iOS recordings drop the last 1–2 s of frames


def _wait_after_address_change(driver, markers: MarkerLog, outcome: dict) -> None:
    """Wait (no taps) until the address picker and its spinner are gone and
    the next page — Place Your Order, or Amazon's "Make updates to your
    items" when the new address can't take an item — has loaded and is
    steady on two reads in a row; then hold a few seconds so the end of the
    recording shows it."""
    print("Step: wait for the page after the address change to finish loading …")
    t0 = time.monotonic()
    steady = 0
    last = None
    screen = "unknown"
    while time.monotonic() - t0 < _AFTER_ADDRESS_TIMEOUT:
        try:
            navs = [
                (e.get_attribute("name") or "")
                for e in driver.find_elements(
                    AppiumBy.IOS_PREDICATE, 'type == "XCUIElementTypeNavigationBar"'
                )[:3]
            ]
        except Exception:  # noqa: BLE001
            navs = []
        title = next((n for n in navs if n), "")
        on_picker = "delivery address" in title.lower()
        spinner = _pyo_spinner_visible(driver)
        if title and not on_picker and not spinner:
            steady = steady + 1 if title == last else 1
            last = title
            if steady >= 2:
                screen = title
                break
        else:
            steady = 0
            last = None
        time.sleep(1.0)
    waited = time.monotonic() - t0
    outcome["after_address_screen"] = screen
    outcome["after_address_wait_s"] = round(waited, 1)
    markers.mark("after_address_loaded", screen=screen[:60], waited_s=round(waited, 1))
    if screen == "unknown":
        print(f"  still loading after {waited:.0f}s — stopping the recording anyway")
    else:
        print(f"  loaded after {waited:.0f}s: {screen[:60]!r} — holding {_RECORDING_TAIL_HOLD:.0f}s for the video")
    time.sleep(_RECORDING_TAIL_HOLD)


def run_test_case_7_2(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
    basket_size: str | None = None,
) -> dict:
    """TASK 7_2: scaled basket A–H + TC6 checkout edits (no place order)."""
    if retailer != "amazon":
        raise ValueError(
            f"{TEST_CASE_NAME} is Amazon-only (do not invent Walmart/Instacart)."
        )

    sample = resolve_basket_sample(basket_size)
    rec_limit = _recording_seconds_for(sample, settings)

    plan = describe_session(settings, retailer, bundle_id)
    plan["scenario"] = TEST_CASE_ID
    plan["test_case"] = TEST_CASE_NAME
    plan["dry_run"] = dry_run
    plan["study_mode"] = "cancel_test"
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
    plan["recording"] = True
    plan["recording_starts_at"] = "cart_page"
    plan["recording_duration_seconds"] = rec_limit
    plan["add_strategy"] = "listing_plus_then_rapid_qty_scale"
    plan["place_order"] = False
    plan["steps"] = [
        "Start new automation session (kill prior Amazon if running)",
        "Open Amazon App (cold start)",
        'Tap home "Grocery" tab',
        'Tap "Produce" aisle under search bar',
        (
            f"Basket {sample.letter}: add {sample.num_items} distinct items ×1 "
            "on the Produce grid (each Add confirmed)"
        ),
        "Open Cart screen",
        f"START screen recording on Cart for {rec_limit}s",
        'Tap "Proceed to checkout"',
        'Dismiss Prime popup via close/X, Cancel, or "No thanks" if shown',
        'On Place Your Order: tap "See more delivery slots"',
        "Select the last delivery slot in the expanded grid",
        'Tap "Change payment method"',
        "Select 2nd payment card → Continue",
        'Tap "Change delivery or pickup address"',
        'Select 2nd address → "Deliver to this address"',
        (
            (
                f"On Cart (recording): raise every item to ×{sample.units_each} "
                f"with its + (one confirmed step at a time; total "
                f"{sample.total_units}) — before Proceed"
            )
            if sample.letter in CART_QTY_BASKETS
            else (
                f"On Place Your Order: raise every item to ×{sample.units_each} "
                f"with its + (one confirmed step at a time; total "
                f"{sample.total_units})"
            )
        ),
        "Stop screen recording (do not place order)",
    ]
    plan["notes"] = (
        f"{TEST_CASE_NAME} — basket sample {sample.letter} "
        f"({sample.num_items} items × {sample.units_each} units = "
        f"{sample.total_units} total). Qty scaled on listing with rapid taps "
        "to minimize add time. Then TC6 checkout edits. Does not place order. "
        f"Override sample with --basket-size or BASKET_SIZE ({', '.join(BASKET_SAMPLES)})."
    )

    if dry_run:
        print(json.dumps({"status": "dry_run_ok", "plan": plan}, indent=2))
        return {"status": "dry_run_ok", "plan": plan}

    if settings.new_command_timeout < 300 and sample.total_units >= 50:
        print(
            f"WARNING: NEW_COMMAND_TIMEOUT={settings.new_command_timeout}s may be "
            f"tight for basket {sample.letter} (total {sample.total_units}). "
            "Prefer 600+."
        )

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
        "basket_sample": sample.letter,
        "basket_num_items": sample.num_items,
        "basket_units_each": sample.units_each,
        "basket_total_units": sample.total_units,
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
        "delivery_slot_changed": False,
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

        markers.mark(
            "app_opened",
            app_state=outcome.get("app_state"),
            basket=sample.letter,
        )

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

        _clear_cart_lines(driver, markers, outcome)
        batch_g = sample.letter in BATCH_CART_BASKETS
        if batch_g:
            _add_and_raise_in_batches(driver, markers, outcome, sample=sample)
        else:
            _add_basket_produce_or_search(driver, markers, outcome, sample=sample)
        _open_cart_page(driver, markers, outcome)
        if sample.letter in CART_QTY_BASKETS and sample.units_each > 1 and not batch_g:
            # Lines that can't reach ×units_each (low stock) → swap them out
            # before recording, so the Cart raise can be exact.
            _replace_low_stock_lines(driver, markers, outcome, sample=sample)
        # Ground truth before recording: "Proceed to checkout (N items)" is
        # what actually goes to checkout (the tab badge can also count items
        # outside this checkout). Fall back to the badge delta.
        # A 50-line Cart takes a while to render its Proceed button — poll.
        checkout_units = None
        _deadline = time.monotonic() + 20.0
        while checkout_units is None and time.monotonic() < _deadline:
            checkout_units = _proceed_checkout_units(driver)
            if checkout_units is None:
                if _recover_amazon_error_page(driver, markers):
                    continue
                _cart_scroll_to_top(driver)
                time.sleep(1.0)
        badge = _cart_badge_count(driver)
        base = outcome.get("cart_count_after_clear") or 0
        outcome["cart_badge_units"] = badge
        outcome["checkout_units"] = checkout_units
        # Items go in ×1 here; units are raised on Place Your Order — except
        # G, whose ×5 is already in the cart (batch raise).
        cart_target = sample.total_units if batch_g else sample.num_items
        if checkout_units is not None:
            outcome["cart_items_exact"] = checkout_units == cart_target
        else:
            outcome["cart_items_exact"] = (
                badge is not None and badge - base == cart_target
            )
        print(
            f"  checkout units {checkout_units} (tab badge {badge}) vs "
            f"{cart_target} item(s) ×1 → cart_items_exact="
            f"{outcome['cart_items_exact']}"
        )

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

        cart_qty = (sample.letter in CART_QTY_BASKETS and sample.units_each > 1
                    and not batch_g)
        if not batch_g:
            outcome["qty_raised_on"] = (
                "cart" if cart_qty else ("place_order" if sample.units_each > 1 else "none")
            )
        if cart_qty:
            _raise_qty_on_cart(driver, markers, outcome, sample=sample)

        # Verify distinct line adds; badge often counts total units (>= num_items).
        _verify_five_in_cart(
            driver,
            markers,
            outcome,
            expected=sample.num_items,
            badge_before_adds=outcome.get("cart_count_before_adds"),
        )
        _mark_step(
            outcome,
            "cart_basket_verified",
            status="ok" if outcome.get("cart_five_items_verified") else "partial",
            sample=sample.letter,
            expected_units=sample.total_units,
            num_items=sample.num_items,
        )

        pyo_raise = not cart_qty and not batch_g and sample.units_each > 1
        if pyo_raise:
            # Raise on Place Your Order before the address change: the 2nd
            # address can make items unshippable (19 Oct 2026: "can't be
            # shipped to the address you selected"), and Place Your Order is
            # then replaced by "Make updates to your items".
            def _raise_before_address() -> None:
                outcome["pyo_raised_before_address"] = True
                _raise_qty_on_place_order(driver, markers, outcome, sample=sample)
            outcome["_before_address_hook"] = _raise_before_address
        try:
            _tc6_checkout_edits(driver, markers, outcome)
        finally:
            outcome.pop("_before_address_hook", None)  # never written to JSON
        if pyo_raise and not outcome.get("pyo_raised_before_address"):
            # Address was handled first (address-first checkout): raise now.
            _raise_qty_on_place_order(driver, markers, outcome, sample=sample)
        if sample.units_each <= 1 or batch_g:
            # ×1 baskets have no qty step — exact = the Cart's checkout count.
            outcome["basket_exact"] = bool(outcome.get("cart_items_exact"))

        # Keep recording until the screen after "Deliver to this address" has
        # loaded — videos used to end on the loading spinner (19 Oct 2026).
        _wait_after_address_change(driver, markers, outcome)
        markers.mark(
            "test_case_7_2_stopped_after_address",
            sample=sample.letter,
            items=outcome.get("items_added_count"),
            units_each=sample.units_each,
            total_units=sample.total_units,
            add_elapsed_s=outcome.get("basket_add_elapsed_seconds"),
            checkout_steps=outcome.get("checkout_steps"),
            slot=outcome.get("delivery_slot_label"),
        )
        print("Stopping process after address change (order not placed) …")

        outcome["status"] = "ok"
        outcome["message"] = (
            f"{TEST_CASE_NAME} OK — basket {sample.letter}: "
            f"{outcome.get('items_added_count')} items ×1 added in "
            f"{outcome.get('basket_add_elapsed_seconds')}s → cart → record → "
            f"See more slots → last slot → 2nd payment → Continue → "
            f"2nd address → Deliver → qty on {outcome.get('qty_raised_on')} → "
            f"{outcome.get('checkout_units_after_cart_qty') or outcome.get('place_order_units', sample.num_items)}/"
            f"{sample.total_units} units"
            + ("" if outcome.get("basket_exact") else " [NOT EXACT]")
        )
        if outcome.get("payment_change_skipped"):
            outcome["message"] = outcome["message"].replace(
                "2nd payment → Continue", "payment change SKIPPED"
            )
        if outcome.get("see_more_delivery_slots_missing"):
            outcome["message"] = outcome["message"].replace(
                "See more slots → last slot",
                "no See more link → last listed slot",
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
            result_path.write_text(json.dumps(outcome, indent=2, default=str), encoding="utf-8")
            print(f"Wrote result → {result_path}")
        except Exception as write_exc:  # noqa: BLE001
            print(f"Result write error: {write_exc}")
