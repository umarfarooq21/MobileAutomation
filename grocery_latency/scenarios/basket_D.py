"""Basket D Instacart — independent automation (25 items × 1 unit = 25).

Self-contained copy of the Instacart TC1 flow. Changes here do not affect other
basket sizes. Artifacts: artifacts/basket_D/

Run:
  python -m grocery_latency run --retailer instacart --scenario basket_D
  python -m grocery_latency run --retailer instacart --scenario test_case_D_instacart

Payment step (rev 2): the selected card is read from the footer at runtime and
the OTHER tile is tapped — no hardcoded last-4. Continue is never tapped.
"""

from __future__ import annotations

import collections
import json
import os
import re
import time
from datetime import datetime, timezone

from appium.webdriver.common.appiumby import AppiumBy

from grocery_latency.config import Settings
from grocery_latency.common.appium_connection import create_driver, describe_session
from grocery_latency.ios_helpers import (
    dump_page_source,
    find_first,
    looks_like_login_wall,
    tap_dismissibles,
)
from grocery_latency.markers import MarkerLog
from grocery_latency.recording import save_recording_bytes, start_ios_screen_recording

BASKET_LETTER = "D"
NUM_ITEMS = 25
UNITS_EACH = 1
TOTAL_UNITS = 25
TEST_CASE_NAME = "Basket D Instacart"
TEST_CASE_ID = "basket_D"
LEGACY_SCENARIO_ID = "test_case_D_instacart"
# No price cap (as validated Basket H: storefront, any price). Each product's
# CURRENT price must be strictly readable — it is the qty-1 evidence in the cart.
MAX_ITEM_PRICE = None
# Record Basket / cart screen for up to this many seconds (Appium timeLimit).
# Longer now that address change + Schedule and save are included.
CART_RECORDING_SECONDS = 360   # Basket D: 25-row cart check at ~8 s/read live (limit = 360 + 3·25 + 25 = 460 s)
# Keep recording after final schedule taps so they land in the MP4
# (iOS/Appium often drops the last 1–2s of frames if we stop immediately).
RECORDING_HOLD_AFTER_CHECKOUT_S = 6.0
CART_TARGET_QTY = 1
# Quantity method: "cart" = direct cart-row stepper (DEFAULT; ported from
# Basket H runs 20261009_142846 / 145239 / 20261012_053039 and Basket E run
# 20261012_061827). Basket D is 10 × 1: no + / − taps and no W3C sequence are
# ever sent; the cart phase is a read-only quantity-1 verification.
QTY_METHOD = (os.environ.get("BASKET_D_QTY_METHOD") or "cart").strip().lower()
# Cart method only: how the + burst is sent. "w3c" = one W3C touch sequence of
# distinct taps (finishes before Instacart's debounced update/reply can
# overwrite the count); "single" = one mobile:tap per +, as in run 20261009_131036.
CART_TAP_MODE = (os.environ.get("BASKET_D_CART_TAP") or "w3c").strip().lower()
# W3C burst defaults (Basket H run 20261009_142846: 40 ms touches, 300 ms timed
# moves — visible human-paced 1→10 ramp, ~0.3 s per step). Env overrides:
# BASKET_D_W3C_PRESS_MS, BASKET_D_W3C_GAP_MS, BASKET_D_W3C_SPACING.
CART_W3C_PRESS_MS = int(os.environ.get("BASKET_D_W3C_PRESS_MS") or 40)
CART_W3C_GAP_MS = int(os.environ.get("BASKET_D_W3C_GAP_MS") or 300)
CART_W3C_SPACING = (os.environ.get("BASKET_D_W3C_SPACING") or "move").strip().lower()
# Wall-clock budget to fire all qty-pill + taps (1 ct → 5 ct).
CART_QTY_RAISE_SECONDS = 1.0
_FAST = 0.12
_MED = 0.28
_SLOW = 0.55
_PRICE_RE = re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)")

INSTACART_DISMISS_LABELS = (
    "Not Now",
    "Not now",
    "No Thanks",
    "No thanks",
    "Maybe Later",
    "Close",
    "Dismiss",
    "Got It",
    "Got It!",
    "Got it",
    "Got it!",
    "Allow",
    "Allow While Using App",
    "Don’t Allow",
    "Don't Allow",
    "OK",
    "Skip",
)


def _d_tap_dismissibles(driver, labels=None, rounds: int = 1) -> list[str]:
    """Same result as ios_helpers.tap_dismissibles — the first label in list
    order that is on screen is tapped once per round — with ONE predicate
    query per round instead of 3 lookups per label (run 20261015_073354:
    111 lookups, 50 s, nothing found)."""
    labels = tuple(labels or INSTACART_DISMISS_LABELS)
    names = ", ".join('"' + lab.replace('"', '\\"') + '"' for lab in labels)
    tapped: list[str] = []
    for _ in range(max(1, rounds)):
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                f"visible == 1 AND (label IN {{{names}}} OR name IN {{{names}}})")
        except Exception:  # noqa: BLE001
            els = []
        best = None
        for el in els[:8]:
            try:
                texts = {el.get_attribute("label") or "", el.get_attribute("name") or ""}
            except Exception:  # noqa: BLE001
                continue
            ranks = [labels.index(t) for t in texts if t in labels]
            if ranks and (best is None or min(ranks) < best[0]):
                best = (min(ranks), el)
        if best is None:
            break
        try:
            best[1].click()
        except Exception:  # noqa: BLE001
            break
        tapped.append(labels[best[0]])
        time.sleep(0.6)
    return tapped


OFFERS_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Offers"),
    (
        AppiumBy.IOS_PREDICATE,
        'visible == 1 AND '
        '(label == "Offers" OR name == "Offers" OR '
        'label CONTAINS[c] "Offers" OR name CONTAINS[c] "Offers")',
    ),
)

# Grocery Outlet store icon on the home retail row (below Offers chip row).
GROCERY_OUTLET_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Grocery Outlet"),
    (AppiumBy.ACCESSIBILITY_ID, "GroceryOutlet"),
    (
        AppiumBy.IOS_PREDICATE,
        'visible == 1 AND '
        '(label == "Grocery Outlet" OR name == "Grocery Outlet" OR '
        'label CONTAINS[c] "Grocery Outlet" OR name CONTAINS[c] "Grocery Outlet")',
    ),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label CONTAINS[c] "Outlet" OR name CONTAINS[c] "Outlet")',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeButton[`label CONTAINS[c] "Grocery Outlet" OR '
        'name CONTAINS[c] "Grocery Outlet" OR label CONTAINS[c] "Outlet"`]',
    ),
)

FLYER_DEALS_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Flyer deals"),
    (
        AppiumBy.IOS_PREDICATE,
        'visible == 1 AND '
        '(label CONTAINS[c] "Flyer deals" OR name CONTAINS[c] "Flyer deals" OR '
        'label CONTAINS[c] "Flyer Deals" OR name CONTAINS[c] "Flyer Deals")',
    ),
)

HOUSEHOLD_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Household"),
    (
        AppiumBy.IOS_PREDICATE,
        'visible == 1 AND '
        '(label == "Household" OR name == "Household" OR '
        'label CONTAINS[c] "Household" OR name CONTAINS[c] "Household")',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeButton[`label == "Household" OR name == "Household" OR '
        'label CONTAINS[c] "Household"`]',
    ),
)

# Bottom green floating bar — cart icon on the right (name=floating_cart_button).
BASKET_CART_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "floating_cart_button"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(name == "floating_cart_button" OR '
        'label CONTAINS[c] "Basket" OR name CONTAINS[c] "Basket")',
    ),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label CONTAINS[c] "cart" OR name CONTAINS[c] "cart" OR '
        'label CONTAINS[c] "delivery fee")',
    ),
)

GO_TO_CHECKOUT_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Go to checkout"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label BEGINSWITH[c] "Go to checkout" OR '
        'name BEGINSWITH[c] "Go to checkout" OR '
        'label CONTAINS[c] "Go to checkout" OR '
        'name CONTAINS[c] "Go to checkout")',
    ),
)

# Checkout screen — top-right ">" / chevron.right disclosure (see user screenshot).
CHECKOUT_CHEVRON_STRATEGIES = (
    (
        AppiumBy.IOS_PREDICATE,
        'visible == 1 AND ('
        'name CONTAINS[c] "chevron.right" OR '
        'label CONTAINS[c] "chevron.right" OR '
        'name == "chevron.right" OR '
        'name CONTAINS[c] "chevron" OR '
        'label CONTAINS[c] "chevron")',
    ),
)

_QTY_CT_RE = re.compile(r"(\d+)\s*ct\b", re.I)
# "Add 1 ct Foo" / "Add 1 lb Grapes" / "Add 1 each …" — group(1) = unit
_ADD_UNIT_RE = re.compile(r"^Add\s+1\s*(ct|lb|lbs|oz|each|ea|pk|pack)\b\s*", re.I)
# Collapsed qty pill after an add: "1 ct", "1 lb", "Quantity: 1 …"
_QTY_PILL_RE = re.compile(r"(^|\b)(\d{1,2})\s*(ct|lb|lbs|oz|each|ea|pk)\b|quantity:\s*\d", re.I)
_QTY_CT_EXACT_RE = re.compile(r"^\s*(\d+)\s*ct\s*$", re.I)
_QTY_ITEM_RE = re.compile(r"Quantity:\s*(\d+)\s*item", re.I)
# Pack sizes on sponsored tiles (e.g. "50 ct") are not cart-line quantities.
_MAX_PLAUSIBLE_CART_QTY = 20


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _mark_step(outcome: dict, key: str, *, status: str, **extra) -> None:
    outcome.setdefault("checkout_steps", {})[key] = {"status": status, **extra}


def _tap_center(driver, cx: float, cy: float) -> None:
    driver.execute_script(
        "mobile: tap",
        {"x": int(round(cx)), "y": int(round(cy))},
    )


def _tap_center_n_times(
    driver,
    cx: float,
    cy: float,
    n: int,
    *,
    budget_s: float = CART_QTY_RAISE_SECONDS,
) -> float:
    """Fire n taps at (cx, cy) in one W3C gesture (~budget_s wall time).

    One Appium round-trip instead of n× mobile:tap (each ~0.5–0.7s).
    Returns elapsed seconds.
    """
    from selenium.webdriver.common.actions import interaction
    from selenium.webdriver.common.actions.action_builder import ActionBuilder
    from selenium.webdriver.common.actions.pointer_input import PointerInput

    x, y = int(round(cx)), int(round(cy))
    n = max(0, int(n))
    if n == 0:
        return 0.0

    # Split budget across (n taps + n-1 gaps). Aim for ~1s total.
    pause_between = max(0.05, (budget_s - 0.12 * n) / max(n - 1, 1)) if n > 1 else 0.0
    finger = PointerInput(interaction.POINTER_TOUCH, "finger")
    actions = ActionBuilder(driver, mouse=finger)
    pa = actions.pointer_action
    t0 = time.monotonic()
    for i in range(n):
        pa.move_to_location(x, y)
        pa.pointer_down()
        pa.pause(0.04)
        pa.pointer_up()
        if i + 1 < n and pause_between > 0:
            pa.pause(pause_between)
    actions.perform()
    return time.monotonic() - t0


def _apply_fast_wda_settings(driver) -> None:
    try:
        driver.update_settings(
            {
                "waitForIdleTimeout": 0,
                "animationCoolOffTimeout": 0,
                "waitForQuiescence": False,
                "snapshotMaxDepth": 50,
            }
        )
        print("  WDA fast settings applied")
    except Exception as exc:  # noqa: BLE001
        print(f"  WDA fast settings warning: {exc}")


def _install_call_timer(driver, markers: MarkerLog, sink: list) -> None:
    """Record every WebDriver command's start (MarkerLog clock) + duration.

    Client-side complement to Appium's --log-timestamp server log: each row
    lines up with the run's markers, so per-phase Appium cost is measurable.
    """
    orig = driver.execute

    def timed(driver_command, params=None):
        t0 = time.monotonic()
        try:
            return orig(driver_command, params)
        finally:
            row = {
                "t_ms": int((t0 - markers.t0) * 1000),
                "ms": int((time.monotonic() - t0) * 1000),
                "cmd": driver_command,
            }
            if isinstance(params, dict):
                if params.get("script"):
                    row["script"] = str(params["script"])[:40]
                if params.get("using"):
                    row["using"] = params["using"]
                    row["value"] = str(params.get("value") or "")[:80]
            sink.append(row)

    driver.execute = timed


def _scroll_home_down(driver, *, percent: float = 0.22) -> None:
    """Nudge home feed so the store-icon row under Offers is on screen."""
    try:
        size = driver.get_window_size()
        w, h = int(size["width"]), int(size["height"])
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.15,
                "fromX": int(w * 0.50),
                "fromY": int(h * 0.55),
                "toX": int(w * 0.50),
                "toY": int(h * (0.55 - percent)),
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _offers_y(driver) -> float | None:
    """Y of the Offers icon — Grocery Outlet sits in the row below it."""
    try:
        el, _, _ = find_first(driver, OFFERS_STRATEGIES, timeout=2.5)
        r = el.rect or {}
        y = float(r.get("y", 0))
        h = float(r.get("height", 0))
        if y > 0:
            return y + h
    except TimeoutError:
        pass
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND '
            '(label == "Offers" OR name == "Offers" OR '
            'label CONTAINS[c] "Offers")',
        )
        for el in els[:6]:
            r = el.rect or {}
            y = float(r.get("y", 0))
            h = float(r.get("height", 0))
            if y > 40:
                return y + h
    except Exception:  # noqa: BLE001
        pass
    return None


def _collect_outlet_candidates(
    driver, *, offers_bottom_y: float | None
) -> list[tuple[float, float, float, object, str]]:
    """Return (y, x, cy, el, label) for Grocery Outlet / Outlet icons below Offers."""
    rows: list[tuple[float, float, float, object, str]] = []
    try:
        size = driver.get_window_size() or {}
        sh = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        sh = 844.0

    y_min = (offers_bottom_y + 4.0) if offers_bottom_y is not None else 80.0
    y_max = min(sh * 0.72, y_min + 280.0)

    predicates = (
        'visible == 1 AND '
        '(label == "Grocery Outlet" OR name == "Grocery Outlet" OR '
        'label CONTAINS[c] "Grocery Outlet" OR name CONTAINS[c] "Grocery Outlet")',
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label CONTAINS[c] "Outlet" OR name CONTAINS[c] "Outlet")',
        'visible == 1 AND '
        '(label CONTAINS[c] "Outlet" OR name CONTAINS[c] "Outlet")',
    )
    seen: set[str] = set()
    for pred in predicates:
        try:
            els = driver.find_elements(AppiumBy.IOS_PREDICATE, pred)
        except Exception:  # noqa: BLE001
            continue
        for el in els[:20]:
            try:
                label = (
                    el.get_attribute("label")
                    or el.get_attribute("name")
                    or ""
                ).strip()
                if "outlet" not in label.lower():
                    continue
                # Skip unrelated "outlet" copy (terms, etc.).
                if "offers" in label.lower():
                    continue
                r = el.rect or {}
                x = float(r.get("x", 0))
                y = float(r.get("y", 0))
                w = float(r.get("width", 0))
                hh = float(r.get("height", 0))
                if w < 12 or hh < 12:
                    continue
                if y < y_min or y > y_max:
                    continue
                key = f"{round(x / 8)}:{round(y / 8)}:{label[:40].lower()}"
                if key in seen:
                    continue
                seen.add(key)
                rows.append((y, x, y + hh / 2.0, el, label[:80]))
            except Exception:  # noqa: BLE001
                continue
        if rows:
            break

    rows.sort(key=lambda t: (round(t[0] / 10.0), t[1]))
    return rows


def _tap_grocery_outlet_below_offers(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """Home: find Offers landmark, then tap Grocery Outlet icon in the row below."""
    print('Step: on home, tap Grocery Outlet icon below "Offers" …')

    offers_bottom = None
    for attempt in range(8):
        offers_bottom = _offers_y(driver)
        if offers_bottom is not None:
            markers.mark("offers_located", y=offers_bottom, attempt=attempt)
            print(f'  Offers landmark bottom y≈{offers_bottom:.0f}')
            break
        if attempt in (2, 5):
            _d_tap_dismissibles(driver, labels=INSTACART_DISMISS_LABELS, rounds=1)
        _scroll_home_down(driver, percent=0.12)
        time.sleep(_MED if attempt < 3 else _FAST)

    if offers_bottom is None:
        print('  Offers icon not confirmed — still searching for Grocery Outlet')

    candidate = None
    for attempt in range(8):
        batch = _collect_outlet_candidates(driver, offers_bottom_y=offers_bottom)
        if batch:
            # Prefer the topmost Outlet row under Offers (first store-icon band).
            candidate = batch[0]
            break
        # Maybe Offers row is above fold and outlet row needs a slight scroll,
        # or home feed needs a nudge to load store icons.
        _scroll_home_down(driver, percent=0.18 if attempt < 3 else 0.28)
        time.sleep(_MED)
        offers_bottom = _offers_y(driver) or offers_bottom

    if candidate is not None:
        y, x, cy, el, label = candidate
        r = el.rect or {}
        cx = float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0
        cy = float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0
        try:
            _tap_center(driver, cx, cy)
        except Exception:  # noqa: BLE001
            el.click()
        outcome.setdefault("selectors", {})["grocery_outlet"] = {
            "by": AppiumBy.IOS_PREDICATE,
            "value": label,
            "x": cx,
            "y": cy,
        }
        markers.mark(
            "grocery_outlet_tapped",
            label=label,
            x=cx,
            y=cy,
            offers_bottom_y=offers_bottom,
        )
        _mark_step(
            outcome,
            "grocery_outlet",
            status="ok",
            label=label,
            x=cx,
            y=cy,
            below_offers=offers_bottom is not None,
        )
        outcome["grocery_outlet_tapped"] = True
        print(f'  Grocery Outlet tapped: {label!r} @ ({cx:.0f},{cy:.0f})')
        time.sleep(_SLOW)
        return

    # Fallback: accessibility / predicate strategies (no Offers Y filter).
    try:
        el, by, value = find_first(driver, GROCERY_OUTLET_STRATEGIES, timeout=4.0)
        r = el.rect or {}
        cx = float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0
        cy = float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0
        try:
            _tap_center(driver, cx, cy)
        except Exception:  # noqa: BLE001
            el.click()
        outcome.setdefault("selectors", {})["grocery_outlet"] = {
            "by": by,
            "value": value,
        }
        markers.mark("grocery_outlet_tapped", by=by, value=value, via="strategy")
        _mark_step(
            outcome,
            "grocery_outlet",
            status="ok",
            by=by,
            value=value,
            via="strategy",
        )
        outcome["grocery_outlet_tapped"] = True
        print(f"  Grocery Outlet: {by} = {value!r}")
        time.sleep(_SLOW)
        return
    except TimeoutError:
        pass

    raise AssertionError(
        'Could not find Grocery Outlet icon below "Offers" on Instacart home'
    )


def _safe_rect(el) -> dict | None:
    try:
        r = el.rect
        return r if isinstance(r, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _list_category_chip_labels(driver) -> list[str]:
    """Labels of buttons currently in the department ScrollView (y≈298)."""
    labels: list[str] = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(visible == 1 OR visible == 0)',
        )
    except Exception:  # noqa: BLE001
        return labels
    for el in els[:40]:
        try:
            r = _safe_rect(el)
            if not r:
                continue
            y = float(r.get("y", 0))
            h = float(r.get("height", 0))
            w = float(r.get("width", 0))
            # Category chips: ~80×105 @ y≈298 (allow partially off-screen x).
            if not (270 <= y <= 330 and 70 <= h <= 130 and 50 <= w <= 120):
                continue
            label = (
                el.get_attribute("label") or el.get_attribute("name") or ""
            ).strip()
            if not label or label.lower() == "loading":
                continue
            labels.append(label)
        except Exception:  # noqa: BLE001
            continue
    return labels


def _wait_grocery_outlet_store(driver, markers: MarkerLog, *, timeout: float = 20.0) -> None:
    """Wait until Grocery Outlet storefront + category chips are loaded."""
    print("Step: wait for Grocery Outlet store page …")
    end = time.monotonic() + timeout
    saw_store = False
    while time.monotonic() < end:
        try:
            hits = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'visible == 1 AND ('
                'label CONTAINS[c] "Search Grocery Outlet" OR '
                'name CONTAINS[c] "Search Grocery Outlet" OR '
                'label == "Search" OR '
                'label CONTAINS[c] "Flyer deals" OR '
                'name CONTAINS[c] "Flyer deals" OR '
                'label == "Delivery" OR name == "Delivery")',
            )
            if hits:
                saw_store = True
        except Exception:  # noqa: BLE001
            pass

        chips = _list_category_chip_labels(driver)
        if saw_store and chips:
            markers.mark("grocery_outlet_store_ready", chips=chips[:8])
            print(f"  Grocery Outlet store ready — categories: {chips[:8]}")
            time.sleep(_MED)
            return
        time.sleep(0.30)

    chips = _list_category_chip_labels(driver)
    markers.mark(
        "grocery_outlet_store_ready",
        partial=True,
        saw_store=saw_store,
        chips=chips[:8],
    )
    print(f"  store page partial — categories so far: {chips[:8] or '(none)'}")


class QuantityUnverified(AssertionError):
    """Cart mode fail-fast: a product's qty is not confirmed — stop the run."""


class ProductUnavailable(QuantityUnverified):
    """A cart line needs a replacement / is out of stock — stop before taps
    or checkout; never raise its qty or remove/replace it automatically."""


class ManualVerificationRequired(QuantityUnverified):
    """A promotional row's qty cannot be verified programmatically — stop
    before checkout and ask a person to confirm (never assumed as 10)."""


class UnexpectedScreen(QuantityUnverified):
    """Basket D: the add loop is not on the storefront (bar missing/invalid,
    cart, checkout or product page) — stopped before any further tap."""


class BasketNotEmpty(AssertionError):
    """Store basket bar shows an item count — stop before adding/recording."""


def _parse_basket_bar_label(label: str) -> tuple[str, int | None]:
    """Classify the store's green bottom bar from its accessibility label.

    Live labels (Button name="floating_cart_button"):
      empty:     "Basket, $0 delivery fee on $10, Service fees apply"
                 "Basket, Get $15 off $100"
      non-empty: "5, Add $14.14 to get $15 off" / "10, You've unlocked $15 off"
    Only the first comma field is used, so prices/discounts never count.
    Returns ("empty", None) | ("count", n) | ("unknown", None).
    """
    head = (label or "").split(",", 1)[0].strip()
    if head.lower() == "basket":
        return "empty", None
    if re.fullmatch(r"\d{1,3}", head):
        return "count", int(head)
    return "unknown", None


def _check_store_basket_empty(
    driver, markers: MarkerLog, outcome: dict, *, timeout: float = 6.0
) -> None:
    """Require an empty basket on the loaded store page (before any add).

    Reads the visible bottom green bar (floating_cart_button). Empty → continue.
    A count → raise BasketNotEmpty (no auto-clear). Unknown after `timeout`
    → raise AssertionError (never assume empty).
    """
    print("Step: check store basket bar is empty …")
    try:
        sh = float((driver.get_window_size() or {}).get("height") or 844)
    except Exception:  # noqa: BLE001
        sh = 844.0
    last_label: str | None = None
    end = time.monotonic() + timeout
    while True:
        # Accessibility-id lookup (same as the cart tap). A `visible == 1`
        # predicate returned 0 matches here although the bar was on screen
        # (run 20261009_094317), so visibility is checked per element.
        try:
            els = driver.find_elements(AppiumBy.ACCESSIBILITY_ID, "floating_cart_button")
        except Exception:  # noqa: BLE001
            els = []
        seen: list[tuple[str, int | None, str]] = []
        for el in els[:4]:
            try:
                r = _safe_rect(el)
                # The bottom bar only (~358×56 @ y≈742–756), on screen.
                if not r or float(r.get("width", 0)) < 150:
                    continue
                y = float(r.get("y", 0))
                if y < sh * 0.75 or y > sh - 20:
                    continue
                if not el.is_displayed():
                    continue
                lab = (el.get_attribute("label") or "").strip()
            except Exception:  # noqa: BLE001
                continue
            state, count = _parse_basket_bar_label(lab)
            seen.append((state, count, lab))
            last_label = lab
        # Decide only when every displayed bar agrees on a known state.
        states = {(s, c) for s, c, _ in seen}
        if len(states) == 1 and next(iter(states))[0] != "unknown":
            state, count = next(iter(states))
            last_label = seen[0][2]
            outcome["basket_bar_label"] = last_label
            if state == "empty":
                outcome["basket_empty_at_start"] = True
                markers.mark("basket_empty_confirmed", label=last_label[:60])
                print(f"  basket empty — bar reads {last_label[:60]!r}")
                return
            if state == "count":
                outcome["basket_empty_at_start"] = False
                outcome["basket_not_empty"] = True
                outcome["basket_count_displayed"] = count
                markers.mark("basket_not_empty", count=count, label=last_label[:60])
                print(f"  basket_not_empty — bar shows {count} ({last_label[:60]!r})")
                raise BasketNotEmpty(
                    f"Basket is not empty: store bar shows {count} item(s) "
                    f"({last_label[:60]!r}) — empty the Instacart basket and rerun"
                )
        if time.monotonic() >= end:
            break
        time.sleep(0.3)
    outcome["basket_empty_at_start"] = None
    markers.mark("basket_state_unknown", label=(last_label or "")[:60])
    raise AssertionError(
        "Could not determine basket state from the store's bottom bar "
        f"(last label: {last_label!r}) — not assuming empty"
    )


def _flyer_deals_y(driver) -> float | None:
    """Top Y of the Flyer deals heading (category row sits just above it)."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND '
            '(label CONTAINS[c] "Flyer deals" OR name CONTAINS[c] "Flyer deals")',
        )
    except Exception:  # noqa: BLE001
        return None
    for el in els[:8]:
        r = _safe_rect(el)
        if not r:
            continue
        y = float(r.get("y", 0))
        if y > 80:
            return y
    return None


def _category_row_band(driver) -> tuple[float, float, float]:
    """Return (row_center_y, y_min, y_max) for the department ScrollView.

    Live dump: ScrollView {{16, 298}, {358, 105}} with 80×105 category cells.
    """
    try:
        scrolls = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeScrollView" AND visible == 1',
        )
    except Exception:  # noqa: BLE001
        scrolls = []
    for sv in scrolls[:10]:
        r = _safe_rect(sv)
        if not r:
            continue
        y = float(r.get("y", 0))
        h = float(r.get("height", 0))
        w = float(r.get("width", 0))
        # Category strip: short horizontal scroller under Delivery (~y 280–360).
        if 250 <= y <= 380 and 70 <= h <= 140 and w > 200:
            return y + h / 2.0, y - 8.0, y + h + 8.0

    flyer_y = _flyer_deals_y(driver)
    if flyer_y is not None:
        return flyer_y - 70.0, max(180.0, flyer_y - 160.0), flyer_y - 4.0

    return 350.0, 280.0, 420.0


def _swipe_category_row(
    driver, *, row_y: float, direction: str = "left", distance_px: float = 160.0
) -> None:
    """Horizontal drag on the department strip. direction: left | right."""
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
    except Exception:  # noqa: BLE001
        w = 390.0
    y = int(round(row_y))
    # Keep swipe inside the ScrollView (x 16–374 on 390-wide).
    mid = w * 0.50
    half = min(distance_px / 2.0, w * 0.32)
    if direction == "left":
        from_x, to_x = int(mid + half), int(mid - half)
    else:
        from_x, to_x = int(mid - half), int(mid + half)
    try:
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.28,
                "fromX": from_x,
                "fromY": y,
                "toX": to_x,
                "toY": y,
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _find_household_in_category_row(
    driver, *, y_min: float, y_max: float
) -> tuple[object, float, float, str] | None:
    """Return (el, cx, cy, label) for Household in the category strip.

    Include partially off-screen chips (visible==0) — XCUI often marks edge
    cells that way while still tappable.
    """
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(label == "Household" OR name == "Household" OR '
            'label CONTAINS[c] "Household" OR name CONTAINS[c] "Household")',
        )
    except Exception:  # noqa: BLE001
        return None

    best = None
    for el in els[:16]:
        try:
            label = (
                el.get_attribute("label") or el.get_attribute("name") or ""
            ).strip()
            if label.lower() != "household" and "household" not in label.lower():
                continue
            if any(
                bad in label.lower()
                for bad in ("flyer", "deal", "basket", "search", "add ", "loading")
            ):
                continue
            r = _safe_rect(el)
            if not r:
                continue
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            hh = float(r.get("height", 0))
            if w < 40 or hh < 40:
                continue
            if y < y_min or y > y_max:
                continue
            # Prefer on-screen (x within display); still allow slightly clipped.
            if x + w < 8 or x > 382:
                continue
            cx = x + w / 2.0
            cy = y + hh / 2.0
            # Prefer fully on-screen chips.
            onscreen = 0.0 if 0 <= x and x + w <= 390 else 40.0
            score = onscreen + abs(hh - 105.0) + abs(w - 80.0)
            if best is None or score < best[0]:
                best = (score, el, cx, cy, label[:80])
        except Exception:  # noqa: BLE001
            continue

    if best is None:
        return None
    _score, el, cx, cy, label = best
    return el, cx, cy, label


def _tap_household_above_flyer_deals(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """Swipe category row (R→L) above Flyer deals until Household, then tap."""
    print(
        'Step: swipe category row above "Flyer deals" (right→left) → tap Household …'
    )
    time.sleep(_SLOW)
    _wait_grocery_outlet_store(driver, markers)

    row_y, y_min, y_max = _category_row_band(driver)
    flyer_y = _flyer_deals_y(driver)
    if flyer_y is not None:
        markers.mark("flyer_deals_located", y=flyer_y)
        print(f'  "Flyer deals" landmark y≈{flyer_y:.0f}')
    print(f"  category row band y={y_min:.0f}–{y_max:.0f}, swipe @ y≈{row_y:.0f}")

    # If we landed near the end (All Aisles), rewind right first.
    chips0 = _list_category_chip_labels(driver)
    print(f"  visible categories: {chips0}")
    markers.mark("category_chips_initial", chips=chips0)
    if any(c.lower() in {"all aisles", "health care", "personal care"} for c in chips0):
        if not any("household" in c.lower() for c in chips0):
            print("  near end of row — swipe right to rewind …")
            for _ in range(6):
                _swipe_category_row(driver, row_y=row_y, direction="right", distance_px=200)
                time.sleep(_FAST)
            chips0 = _list_category_chip_labels(driver)
            print(f"  after rewind: {chips0}")
            markers.mark("category_chips_after_rewind", chips=chips0)

    hit = None
    for swipe_i in range(12):
        row_y, y_min, y_max = _category_row_band(driver)
        hit = _find_household_in_category_row(driver, y_min=y_min, y_max=y_max)
        if hit is not None:
            markers.mark("household_visible", swipe_index=swipe_i)
            break
        chips = _list_category_chip_labels(driver)
        print(f"  Household not visible ({chips}) — swipe left #{swipe_i + 1}")
        # ~2 chips per swipe so we do not jump past Household.
        _swipe_category_row(driver, row_y=row_y, direction="left", distance_px=168)
        markers.mark(
            "category_row_swiped_left",
            index=swipe_i + 1,
            y=row_y,
            chips=chips,
        )
        time.sleep(_MED)
        # Stop if we hit the terminal "All Aisles" chip without finding Household.
        chips_after = _list_category_chip_labels(driver)
        if "All Aisles" in chips_after and not any(
            "household" in c.lower() for c in chips_after
        ):
            print("  reached All Aisles — stopping left swipes")
            markers.mark("category_row_end_reached", chips=chips_after)
            break

    if hit is None:
        # One more pass after a full rewind.
        print("  retry: rewind + scan left again …")
        for _ in range(8):
            _swipe_category_row(driver, row_y=row_y, direction="right", distance_px=200)
            time.sleep(_FAST)
        for swipe_i in range(14):
            row_y, y_min, y_max = _category_row_band(driver)
            hit = _find_household_in_category_row(driver, y_min=y_min, y_max=y_max)
            if hit is not None:
                markers.mark("household_visible", swipe_index=swipe_i, pass_n=2)
                break
            chips = _list_category_chip_labels(driver)
            print(f"  pass2 swipe left #{swipe_i + 1}: {chips}")
            _swipe_category_row(driver, row_y=row_y, direction="left", distance_px=140)
            time.sleep(_MED)
            if "All Aisles" in chips and not any(
                "household" in c.lower() for c in chips
            ):
                break

    if hit is None:
        chips = _list_category_chip_labels(driver)
        raise AssertionError(
            'Could not find "Household" in category row above Flyer deals '
            f"after swiping (visible={chips})"
        )

    el, cx, cy, label = hit
    try:
        _tap_center(driver, cx, cy)
    except Exception:  # noqa: BLE001
        try:
            el.click()
        except Exception:  # noqa: BLE001
            hit2 = _find_household_in_category_row(driver, y_min=y_min, y_max=y_max)
            if hit2 is None:
                raise
            _el2, cx, cy, label = hit2
            _tap_center(driver, cx, cy)

    outcome.setdefault("selectors", {})["household"] = {
        "label": label,
        "x": cx,
        "y": cy,
    }
    markers.mark("household_tapped", label=label, x=cx, y=cy)
    _mark_step(
        outcome,
        "household",
        status="ok",
        label=label,
        x=cx,
        y=cy,
        flyer_deals_y=flyer_y,
        category_row_y=row_y,
    )
    outcome["household_tapped"] = True
    print(f"  Household tapped: {label!r} @ ({cx:.0f},{cy:.0f})")
    time.sleep(_SLOW)


def _parse_price_usd(text: str) -> float | None:
    """Return the first $price in text, or None."""
    if not text:
        return None
    m = _PRICE_RE.search(text.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _scroll_products_down(driver, *, percent: float = 0.35) -> None:
    try:
        size = driver.get_window_size()
        w, h = int(size["width"]), int(size["height"])
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.22,
                "fromX": int(w * 0.50),
                "fromY": int(h * 0.72),
                "toX": int(w * 0.50),
                "toY": int(h * (0.72 - percent)),
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _wait_household_listing(driver, markers: MarkerLog, *, timeout: float = 12.0) -> None:
    """Wait until store products (or Add buttons) appear on Grocery Outlet."""
    print("Step: wait for store product listing …")
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            hits = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'visible == 1 AND ('
                'label CONTAINS[c] "$" OR '
                'label == "Add" OR name == "Add" OR '
                'label == "+" OR name == "+" OR '
                'label CONTAINS[c] "Household")',
            )
            priced = 0
            for el in hits[:20]:
                blob = (
                    (el.get_attribute("label") or "")
                    + " "
                    + (el.get_attribute("value") or "")
                )
                if _parse_price_usd(blob) is not None:
                    priced += 1
            if priced >= 1:
                markers.mark("household_listing_ready", priced_visible=priced)
                print(f"  Household listing ready ({priced} priced elements)")
                time.sleep(_MED)
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.30)
    markers.mark("household_listing_ready", partial=True)
    print("  Household listing not fully confirmed — continuing")


# Primary price sold by weight: "$1.99 /lb", "$3.49/kg", "$0.50 per oz".
_WEIGHT_PRICE_RE = re.compile(
    r"\$\s*\d+(?:\.\d{1,2})?\s*(?:/|per)\s*(lb|lbs|kg|g|oz)\b", re.I
)
_PRICE_LINE_RE = re.compile(r"\$\s*\d+(?:\.\d{1,2})?")


def _weighted_product_reason(blob: str) -> str | None:
    """Why a store card is sold by weight (None = unit-based / unknown).

    Judged on the card's FIRST price line only — e.g. Banana reads
    "$0.24 each (est.) … $0.59 / lb" (sold each, reaches 10), while Green
    Grapes Bag reads "$1.99 /lb" (0.25 lb stepper: 9 taps → 3.25 lbs).
    """
    for line in re.split(r"[\n,]", blob or ""):
        if not _PRICE_LINE_RE.search(line):
            continue
        if re.search(r"\beach\b", line, re.I):
            return None
        m = _WEIGHT_PRICE_RE.search(line)
        return f"priced per {m.group(1).lower()}: {line.strip()[:40]!r}" if m else None
    if re.search(r"\bper\s+(lb|lbs|kg)\b", blob or "", re.I):
        return "sold per lb/kg"
    return None


# Explicit availability warnings on store cards / cart rows (run 20261012_060058:
# Reese's card "Likely out of stock", cart row "Choose replacement").
_UNAVAILABLE_RE = re.compile(
    r"likely out of stock|out of stock|\bunavailable\b|sold out|no longer available"
    r"|currently unavailable|choose replacement",
    re.I,
)


def _unavailable_reason(text: str) -> str | None:
    """The explicit availability warning in ``text``, or None."""
    m = _UNAVAILABLE_RE.search(text or "")
    return m.group(0) if m else None


def _cart_row_state(value: str) -> str:
    """"replacement_required" for out-of-stock / choose-replacement rows, else "normal"."""
    return "replacement_required" if _unavailable_reason(value) else "normal"


# Store-card value lines (real dumps): the selling price is a line that is ONLY
# a dollar amount ("$5.79"); offers put other amounts first ("Spend $20, save
# $3", "Buy 2 Save $1.50") and the crossed-out original is labelled ("Price
# before promotion: reg. $4.99"). Per-weight / estimated prices are variable.
_PURE_PRICE_LINE_RE = re.compile(r"^\$\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?$")
_VARIABLE_PRICE_LINE_RE = re.compile(r"^\$\s*\d[\d,]*(?:\.\d{1,2})?\s*(?:/|per\b|each\s*\(est)", re.I)


# Store cards since 2026-10-13 (run 20261013_063337): Button, empty value,
# label "<name> [badges] Current price: $2.99 [offer] <name>\n<size>".
_CURRENT_PRICE_RE = re.compile(
    r"Current price:\s*\$\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?(\s*(?:/|per\b|each\s*\(est))?", re.I)


_CARD_BADGE_RE = re.compile(
    r"(?:\s|^)(?:Current price:|Deal ending soon|\d+\+? bought yesterday|FSA/HSA|Sponsored|Many in stock|"
    r"In season|Likely out of stock|Out of stock|\d+ in basket|Low carb|Gluten-free|Organic|Fresh\b|Kosher|Vegan)",
    re.I)


# Storefront tags that follow the product name on a card ("… Low calorie",
# "… 999+ bought yesterday"). Stripped ONLY from the end of the name.
_D_TRAILING_TAG_RE = re.compile(
    r"(?:\s+|^)(?:\d[\d,]*\+?\s*bought yesterday|low calorie|low carb|low sodium|low fat|"
    r"sugar[- ]free|gluten[- ]free|trans fat[- ]free|fat[- ]free|dairy[- ]free|high protein|"
    r"small breed|large breed|deal ending soon|many in stock|sponsored|in season|"
    r"likely out of stock|out of stock|\d+ in basket|fsa/hsa|organic|vegan|kosher|non-gmo|"
    r"plant[- ]based|keto|best seller|new)\s*$", re.I)


def _d_strip_trailing_tags(text: str) -> str:
    t = (text or "").strip(" ,")
    while True:
        m = _D_TRAILING_TAG_RE.search(t)
        if not m or m.start() == 0:
            return t
        t = t[: m.start()].strip(" ,")



def _card_display_name(label: str) -> str:
    """Product name of a store card, with its storefront tags removed.

    Basket D: live card label is "<name> [tags] Current price: $X [offer]
    <name>\n<size>". The name is the longest leading part of the text before
    "Current price:" that the first line after the price ends with — so
    "This Organic Life Immunity & Boost" and "Fresh Z Dental Flossers" keep
    their words, while "Red Bull … Drink Low calorie" → "Red Bull … Drink".
    Fallback (no repeated name): strip tags from the END only.
    """
    text = label or ""
    m = re.search(r"\s*Current price:", text, re.I)
    if m:
        head = text[: m.start()].strip(" ,")
        tail1 = text[m.end():].split("\n", 1)[0].rstrip(" ,")
        words = head.split(" ")
        for k in range(len(words), 0, -1):
            cand = " ".join(words[:k]).strip(" ,")
            if len(cand) < 3 or not tail1.endswith(cand):
                continue
            before = tail1[: len(tail1) - len(cand)]
            if before == "" or before.endswith(" "):
                return cand
        # Basket D: the repeated name can be cut mid-word ("… Sensitive Fres"):
        # the longest leading part of the card front (≥ 20 chars) that the
        # repeated text ends with. _same_product trims it back to whole words.
        for k in range(len(head), 19, -1):
            cand = head[:k].rstrip(" ,")
            if len(cand) >= 20 and tail1.endswith(cand):
                before = tail1[: len(tail1) - len(cand)]
                if before == "" or before.endswith(" "):
                    return cand
        return _d_strip_trailing_tags(head) or head
    first = text.split("\n", 1)[0]
    return _d_strip_trailing_tags(first) or first.strip()


def _card_current_price(value: str) -> tuple[float | None, str]:
    """(current selling price, "") or (None, reason) from a store card's text.

    Prefers the explicit "Current price: $X" label (current store layout);
    otherwise the line that is only a dollar amount (older layout). Offer
    amounts ("Spend $30, save $6") and crossed-out originals are never used.
    """
    hits = _CURRENT_PRICE_RE.findall(value or "")
    if hits:
        if any(h[2].strip() for h in hits):
            return None, "variable-weight price"
        amounts = sorted({float(h[0].replace(",", "") + "." + (h[1] or "0")) for h in hits})
        if len(amounts) > 1:
            return None, f"ambiguous price {amounts}"
        return amounts[0], ""
    lines = [ln.strip() for ln in (value or "").split("\n")]
    if any(_VARIABLE_PRICE_LINE_RE.match(ln) for ln in lines):
        return None, "variable-weight price"
    found = []
    for ln in lines:
        m = _PURE_PRICE_LINE_RE.match(ln)
        if m:
            found.append(float(m.group(1).replace(",", "") + "." + (m.group(2) or "0")))
    distinct = sorted(set(found))
    if not distinct:
        return None, "no readable price"
    if len(distinct) > 1:
        return None, f"ambiguous price {distinct}"
    return distinct[0], ""


def _collect_under_max_products(
    driver, *, max_price: float | None = MAX_ITEM_PRICE
) -> list[dict]:
    """Find on-screen product nodes with a parseable price.

    If max_price is set, only keep price <= max_price; if None, any price.

    Returns dicts: {price, label, x, y, cx, cy, w, h, el}.
    """
    try:
        size = driver.get_window_size() or {}
        sh = float(size.get("height") or 844)
        sw = float(size.get("width") or 390)
    except Exception:  # noqa: BLE001
        sh, sw = 844.0, 390.0

    # Product grid sits below sticky header / filters; above Basket bar.
    # Allow partially-visible cards near the top (y can be slightly negative).
    y_min, y_max = 40.0, sh * 0.88

    candidates: list[dict] = []
    # Basket D: ONE page-source read per scan (run 20261013_122744: per-element
    # label/value/rect reads cost ~21 s per scan over ~36 scans). Same nodes as
    # the old predicate — visible, label/value/name containing "$", tree order,
    # first 60 — same filters below; the Add buttons of the SAME snapshot are
    # attached for _find_add_near_product (no second query).
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(driver.page_source or "")
    except Exception:  # noqa: BLE001
        return candidates
    els = []
    add_buttons: list[tuple[float, float, float, float, str]] = []
    for node in root.iter():
        a = node.attrib
        if a.get("visible") != "true":
            continue
        lab_a, nm_a, val_a = a.get("label") or "", a.get("name") or "", a.get("value") or ""
        if "$" in lab_a or "$" in val_a or "$" in nm_a:
            els.append(a)
        if a.get("type") == "XCUIElementTypeButton" and _d_is_add_button(lab_a, nm_a):
            try:
                add_buttons.append((float(a.get("x", 0)), float(a.get("y", 0)), float(a.get("width", 0)),
                                    float(a.get("height", 0)), (lab_a or nm_a or "+")[:60]))
            except ValueError:
                pass

    seen: set[str] = set()
    for el in els[:60]:
        try:
            label = (el.get("label") or el.get("name") or "").strip()
            value = (el.get("value") or "").strip()
            blob = f"{label}\n{value}"
            if _parse_price_usd(blob) is None:
                continue                      # not a priced node at all
            low0 = blob.lower()
            if any(bad in low0 for bad in ("service fee", "delivery fee", "bag fee", "markup",
                                           "flyer deals", "to get $", "for $0 delivery")):
                continue
            price, why = _card_current_price(blob)
            if price is None or price <= 0:
                if "variable-weight" in why:
                    print(f"  skip weighted product {label[:40]!r} — {why}")
                elif why:
                    print(f"  skip {label[:40]!r} — {why} (qty 1 could not be verified)")
                continue
            # Basket D needs exact unit pricing — skip by-weight items.
            weighted = _weighted_product_reason(value) or _weighted_product_reason(label)
            if weighted:
                print(f"  skip weighted product {label[:40]!r} — {weighted}")
                continue
            # Never add a product the store already flags as unavailable.
            unavailable = _unavailable_reason(blob)
            if unavailable:
                print(f"  skip unavailable product {label[:40]!r} — {unavailable!r}")
                continue
            if max_price is not None and price > max_price:
                continue
            # Skip fee / delivery chrome (not product pack "bags").
            low = blob.lower()
            if any(
                bad in low
                for bad in (
                    "service fee",
                    "delivery fee",
                    "bag fee",
                    "markup",
                    "flyer deals",
                )
            ):
                continue
            if re.search(r"\bbasket\b", low):
                continue
            r = el
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            h = float(r.get("height", 0))
            # Keep cards that are at least partly in the product band.
            if (y + h) < y_min or y > y_max or w < 40 or h < 20:
                continue
            if x > sw * 0.95:
                continue
            # Skip cards whose center is still under the sticky category row —
            # tapping their + often leaves Household.
            cy_mid = y + h / 2.0
            if cy_mid < 170:
                continue
            key = f"{round(x / 12)}:{round(y / 12)}:{price}:{label[:40].lower()}"
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "price": price,
                    "label": (label or value)[:160],
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                    "cx": x + w / 2.0,
                    "cy": cy_mid,
                    "add_buttons": add_buttons,
                }
            )
        except Exception:  # noqa: BLE001
            continue

    # Prefer first on screen (top → left).
    candidates.sort(key=lambda c: (c["y"], c["x"], c["price"]))
    return candidates


class _D_RectEl:
    """A page-source Add button with the element API _d_pick_add reads."""

    def __init__(self, x: float, y: float, w: float, h: float, label: str):
        self.rect = {"x": x, "y": y, "width": w, "height": h}
        self._label = label

    def get_attribute(self, k):
        return self._label if k in ("label", "name") else ""


def _d_is_add_button(label: str, name: str) -> bool:
    """Same test as the old Add-button predicate (case-insensitive where [c])."""
    lo_l, lo_n = label.lower(), name.lower()
    return (label == "Add" or name == "Add" or label == "+" or name == "+"
            or lo_l.startswith("add ") or lo_n.startswith("add ")
            or "add to" in lo_l or "add" in lo_n)


def _find_add_near_product(
    driver, product: dict
) -> tuple[float, float, str] | None:
    """Locate green + / Add control near a product card; return (cx, cy, via)."""
    px, py, pw, ph = product["x"], product["y"], product["w"], product["h"]
    if "add_buttons" in product:
        # Basket D: Add buttons from the same page-source snapshot as the card.
        buttons = [_D_RectEl(*b) for b in product["add_buttons"]]
        return _d_pick_add(driver, buttons, px, py, pw, ph)

    try:
        buttons = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Add" OR name == "Add" OR label == "+" OR name == "+" OR '
            'label BEGINSWITH[c] "Add " OR name BEGINSWITH[c] "Add " OR '
            'label CONTAINS[c] "Add to" OR name CONTAINS[c] "add")',
        )
    except Exception:  # noqa: BLE001
        buttons = []

    return _d_pick_add(driver, buttons, px, py, pw, ph)


def _d_pick_add(driver, buttons, px: float, py: float, pw: float, ph: float) -> tuple[float, float, str] | None:
    """Nearest Add control to a card (or the card's top-right + point)."""
    card_left = px - 8
    card_right = px + max(pw, 140) + 24
    card_top = py - 12
    card_bottom = py + max(ph, 80) + 40
    best = None
    for el in buttons[:40]:
        try:
            r = _safe_rect(el)
            if not r:
                continue
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            h = float(r.get("height", 0))
            if w < 18 or h < 18 or w > 160:
                continue
            cx = x + w / 2.0
            cy = y + h / 2.0
            if cy < 170:
                continue
            if not (card_left <= cx <= card_right and card_top <= cy <= card_bottom):
                # Also accept Add slightly above-right of product text.
                if not (
                    px - 20 <= cx <= px + max(pw, 160) + 40
                    and py - 90 <= cy <= py + 40
                    and cy >= 170
                ):
                    continue
            label = (el.get_attribute("label") or el.get_attribute("name") or "+")[
                :60
            ]
            # Prefer closer to top-right of card.
            score = abs(cy - py) + abs(cx - (px + max(pw, 120)))
            if best is None or score < best[0]:
                best = (score, cx, cy, label)
        except Exception:  # noqa: BLE001
            continue

    if best is not None:
        _s, cx, cy, label = best
        return cx, cy, label

    # Coord fallback: Instacart green + is typically top-right of product card.
    # Never tap above y≈180 — that hits category chips / search (leaves Household).
    try:
        size = driver.get_window_size() or {}
        sw = float(size.get("width") or 390)
    except Exception:  # noqa: BLE001
        sw = 390.0
    header_floor = 180.0
    # Two-column grid: + sits near right edge of the card.
    if pw >= 120:
        cx = px + pw - 22.0
        cy = py + 28.0
    else:
        # Narrow price/text node — assume card ~150 wide starting near px.
        cx = px + 130.0
        cy = py + 20.0
    if cy < header_floor:
        # The card's + is under the sticky header (run 20261013_065945: a
        # clamped tap at y=180 hit the card body, nothing was added) — skip.
        return None
    if cx > sw - 24.0:
        # The card's + is off-screen (clipped third card, run 20261012_125306):
        # never tap a clamped edge point — the card is skipped.
        return None
    return cx, cy, "coord_top_right"


def _basket_count(driver) -> int | None:
    """Read item count from the bottom cart badge (the number next to the cart icon).

    Examples:
      - label "5, Add $5.01 to get $0 delivery fee…"
      - promo bar "Add $89.04 to get $15 off" + separate badge text "5"
    """
    try:
        size = driver.get_window_size() or {}
        sh = float(size.get("height") or 844)
        sw = float(size.get("width") or 390)
    except Exception:  # noqa: BLE001
        sh, sw = 844.0, 390.0

    def _parse_count(blob: str) -> int | None:
        text = (blob or "").strip()
        if not text:
            return None
        # Basket D: the strict bar rule first — only the first comma field
        # counts. "Basket, Get $15 off $100" is an EMPTY bar (0), never 15;
        # "1, Get $15 off $100" → 1; "10, You've unlocked $15 off" → 10.
        state, n = _parse_basket_bar_label(text)
        if state == "empty":
            return 0
        if state == "count":
            return n
        # "5 Add $…" (no comma) — a leading count only, never a "$" amount.
        m = re.match(r"^(\d{1,3})\s+(?!\$)\S", text)
        if m:
            return int(m.group(1))
        m = re.search(r"(?<![$\d.])(\d+)\s*items?\b", text, re.I)
        if m:
            return int(m.group(1))
        # Bare badge "5"
        m = re.match(r"^(\d{1,3})$", text)
        if m:
            return int(m.group(1))
        return None

    els: list = []
    try:
        els = list(
            driver.find_elements(AppiumBy.ACCESSIBILITY_ID, "floating_cart_button")
        )
    except Exception:  # noqa: BLE001
        els = []
    if not els:
        try:
            els = list(
                driver.find_elements(
                    AppiumBy.IOS_PREDICATE,
                    'visible == 1 AND ('
                    'name == "floating_cart_button" OR '
                    'label CONTAINS[c] "Basket" OR '
                    'label CONTAINS[c] "cart" OR '
                    'label CONTAINS[c] "delivery fee" OR '
                    'label CONTAINS[c] "Min. to checkout" OR '
                    'label CONTAINS[c] "to get" OR '
                    'label CONTAINS[c] "to get $" OR '
                    'label CONTAINS[c] "off")',
                )
            )
        except Exception:  # noqa: BLE001
            els = []

    for el in els[:12]:
        try:
            blob = (
                f"{el.get_attribute('label') or ''} "
                f"{el.get_attribute('name') or ''} "
                f"{el.get_attribute('value') or ''}"
            )
            n = _parse_count(blob)
            if n is not None:
                return n
        except Exception:  # noqa: BLE001
            continue

    # Promo bar case: count is a separate "5" next to the cart icon (bottom-right).
    try:
        badge_els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label MATCHES "[0-9]{1,3}" OR value MATCHES "[0-9]{1,3}")',
        )
    except Exception:  # noqa: BLE001
        badge_els = []
    for el in badge_els[:24]:
        try:
            r = _safe_rect(el)
            if not r:
                continue
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            # Bottom green bar, right side (cart badge zone).
            if y < sh * 0.80 or x < sw * 0.55:
                continue
            blob = (
                (el.get_attribute("label") or "")
                or (el.get_attribute("value") or "")
            ).strip()
            n = _parse_count(blob)
            if n is not None and 1 <= n <= 999:
                return n
        except Exception:  # noqa: BLE001
            continue
    return None


def _item_label_key(label: str) -> str:
    """Normalize product label for duplicate skip during multi-add."""
    raw = (label or "").lower()
    # Drop price/stock suffixes so "Foil … Current price: $1.99" matches itself.
    for sep in (" current price", "\n", " best seller", " many in stock"):
        if sep in raw:
            raw = raw.split(sep, 1)[0]
    return re.sub(r"\s+", " ", raw).strip()[:48]


def _scroll_products_up(driver, *, percent: float = 0.40) -> None:
    try:
        size = driver.get_window_size()
        w, h = int(size["width"]), int(size["height"])
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.22,
                "fromX": int(w * 0.50),
                "fromY": int(h * 0.38),
                "toX": int(w * 0.50),
                "toY": int(h * (0.38 + percent)),
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _wait_basket_count_change(
    driver, before: int | None, *, tries: int = 5
) -> int | None:
    """Poll floating cart badge until count differs from `before`."""
    after = before
    for i in range(tries):
        time.sleep(0.12 if i else 0.20)
        after = _basket_count(driver)
        if after is None:
            continue
        if before is None and after > 0:
            return after
        if before is not None and after != before:
            return after
    return after


def _add_confirmed(
    pre: int | None, post: int | None, *, allow_rebase: bool = False
) -> bool:
    """True only for a real new line: badge must go exactly +1.

    Qty-bumping an already-added SKU also raises the badge — that is NOT a
    new item. Store-cart rebase (other-store badge → 1) is allowed once via
    allow_rebase.
    """
    if post is None or not isinstance(post, int):
        return False
    if pre is None or not isinstance(pre, int):
        return False
    if post == pre + 1:
        return True
    # Other-store cart cleared when entering this store → badge snaps to 1.
    if allow_rebase and pre >= 2 and post == 1:
        return True
    return False


def _tap_coord_key(x: float, y: float) -> str:
    """Bucket tap points so we never double-hit the same Add control."""
    return f"{int(round(x / 28))}:{int(round(y / 28))}"


def _dismiss_replacement_preference(driver, markers: MarkerLog) -> bool:
    """Close 'Replacement preference' sheet that sometimes opens after Add."""
    try:
        hits = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label CONTAINS[c] "Replacement preference" OR '
            'name CONTAINS[c] "Replacement preference")',
        )
    except Exception:  # noqa: BLE001
        hits = []
    if not hits:
        return False
    print("  Replacement preference sheet — tapping Close/X")
    try:
        closes = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Close" OR name == "Close")',
        )
    except Exception:  # noqa: BLE001
        closes = []
    for el in closes[:4]:
        try:
            r = _safe_rect(el)
            if r and float(r.get("y", 999)) > 140:
                continue
            el.click()
            markers.mark("replacement_preference_dismissed")
            time.sleep(0.25)
            return True
        except Exception:  # noqa: BLE001
            continue
    try:
        size = driver.get_window_size() or {}
        _tap_center(driver, 36.0, float(size.get("height") or 844) * 0.08)
        time.sleep(0.25)
        markers.mark("replacement_preference_dismissed", via="coord")
        return True
    except Exception:  # noqa: BLE001
        return False


def _price_cap_label(max_price: float | None) -> str:
    if max_price is None:
        return "any price"
    return f"≤ ${max_price:.2f}"


def _collect_visible_add_plus(
    driver,
) -> list[tuple[float, float, str]]:
    """Green + controls on the store screen: 'Add 1 ct …', 'Add 1 lb …', etc.

    Count-based items (ct/each/pk) are listed first; weight items (lb/oz) last.
    The promo bar ('Add $97.02 to get $15 off') is excluded.
    """
    try:
        size = driver.get_window_size() or {}
        sh = float(size.get("height") or 844)
        sw = float(size.get("width") or 390)
    except Exception:  # noqa: BLE001
        sh, sw = 844.0, 390.0
    out: list[tuple[int, float, float, float, str]] = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label BEGINSWITH[c] "Add " OR name BEGINSWITH[c] "Add ")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:40]:
        try:
            label = (
                el.get_attribute("label") or el.get_attribute("name") or ""
            ).strip()
            low = label.lower()
            if "$" in label or "to get" in low or "payment" in low:
                continue
            if "decrement" in low or "remove" in low or "to order" in low:
                continue
            m = _ADD_UNIT_RE.match(label)
            if not m:
                continue
            unit = m.group(1).lower()
            if _unavailable_reason(label):
                print(f"  skip unavailable Add control {label[:50]!r}")
                continue
            if unit in ("lb", "lbs", "oz"):
                # Sold by weight — stepper moves in weight increments, can't
                # reach an exact unit count (Basket D requirement).
                print(f"  skip weighted Add control {label[:50]!r}")
                continue
            r = _safe_rect(el)
            if not r:
                continue
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            h = float(r.get("height", 0))
            if w < 18 or h < 18 or w > 140:
                continue
            cy = y + h / 2.0
            # Fully on-screen product + above the Basket bar, below the header.
            if cy < 160 or cy > sh * 0.78:
                continue
            if x < 0 or x > sw * 0.95:
                continue
            short = label[m.end():].strip()[:80]
            rank = 0 if unit in ("ct", "each", "ea", "pk", "pack") else 1
            out.append((rank, cy, x + w / 2.0, cy, short or label[:80]))
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda t: (t[0], t[1], t[2]))
    deduped: list[tuple[float, float, str]] = []
    for _rank, _y, cx, cy, lab in out:
        if any(abs(cy - d[1]) < 28 and abs(cx - d[0]) < 40 for d in deduped):
            continue
        deduped.append((cx, cy, lab))
    return deduped


def _label_in_skip(key: str, skip: set[str]) -> bool:
    if not key:
        return False
    return any(key == s or key.startswith(s) or s.startswith(key) for s in skip)


def _try_add_visible_product(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    product: dict,
    max_price: float | None,
    skip: set[str],
    tapped_coords: set[str] | None = None,
    allow_rebase: bool = False,
) -> dict | None:
    """Tap Add once on one on-screen product; confirm badge exactly +1."""
    price = product["price"]
    label = product["label"]
    key = _item_label_key(label)
    if _label_in_skip(key, skip):
        return None
    print(
        f"  candidate ${price:.2f}: {label[:70]!r} "
        f"@ ({product['cx']:.0f},{product['cy']:.0f})"
    )
    add = _find_add_near_product(driver, product)
    if add is None:
        return None
    ax, ay, via = add
    if ay < 170:
        print(f"  skip Add {via!r} @ ({ax:.0f},{ay:.0f}) — header")
        return None
    coord_key = _tap_coord_key(ax, ay)
    if tapped_coords is not None and coord_key in tapped_coords:
        print(f"  skip Add @ ({ax:.0f},{ay:.0f}) — already tapped this +")
        if key:
            skip.add(key)
        return None
    before = _basket_count(driver)
    if before is None:
        # One more poll — never accept an add without a readable baseline.
        time.sleep(0.15)
        before = _basket_count(driver)
    print(f"  tapping Add via {via!r} @ ({ax:.0f},{ay:.0f}) (badge={before!r})")
    try:
        # Single tap only — a second tap bumps qty on the same SKU.
        _tap_center(driver, ax, ay)
    except Exception as exc:  # noqa: BLE001
        print(f"  Add tap failed: {exc}")
        return None
    if tapped_coords is not None:
        tapped_coords.add(coord_key)
    # Mark label attempted so we never re-hit this SKU (qty bump ≠ new item).
    if key:
        skip.add(key)
    time.sleep(0.20)
    _dismiss_replacement_preference(driver, markers)
    _d_tap_dismissibles(driver, labels=INSTACART_DISMISS_LABELS, rounds=1)
    after = _wait_basket_count_change(driver, before, tries=6)
    confirmed = _add_confirmed(before, after, allow_rebase=allow_rebase)
    if not confirmed:
        print(
            f"  Add via {via!r} not confirmed "
            f"(basket {before!r}→{after!r}) — try next"
        )
        return None
    entry = {
        "price": price,
        "label": label[:160],
        "add_x": ax,
        "add_y": ay,
        "add_via": via,
        "confirmed": True,
        "basket_before": before,
        "basket_after": after,
        "max_price": max_price,
    }
    outcome["item_added"] = entry
    outcome["item_added_to_cart"] = True
    outcome["item_price"] = price
    markers.mark(
        "item_added_under_max",
        price=price,
        label=label[:80],
        confirmed=True,
        via=via,
        basket_before=before,
        basket_after=after,
    )
    _mark_step(
        outcome,
        "add_under_max",
        status="ok",
        price=price,
        label=label[:80],
        confirmed=True,
    )
    print(f"  ✓ added ${price:.2f} item (basket {before!r}→{after!r})")
    return entry


def _add_item_under_max_price(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    max_price: float | None = MAX_ITEM_PRICE,
    max_scrolls: int = 0,
    skip_labels: set[str] | None = None,
    item_index: int | None = None,
    wait_listing: bool = True,
) -> dict:
    """Add one item from the current visible store screen only (no scroll)."""
    idx_s = f" #{item_index}" if item_index is not None else ""
    print(f"Step: add{idx_s} on-screen item ({_price_cap_label(max_price)}) …")
    if wait_listing:
        _wait_household_listing(driver, markers)
    skip = skip_labels or set()
    products = _collect_under_max_products(driver, max_price=max_price)
    for product in products[:10]:
        key = _item_label_key(product["label"])
        if key and any(
            key == s or key.startswith(s) or s.startswith(key) for s in skip
        ):
            continue
        entry = _try_add_visible_product(
            driver,
            markers,
            outcome,
            product=product,
            max_price=max_price,
            skip=skip,
            tapped_coords=set(),
            allow_rebase=True,
        )
        if entry is not None:
            return entry
    raise AssertionError(
        f"Could not add an on-screen store item ({_price_cap_label(max_price)})"
    )


_STEPPER_PRED = (
    'type == "XCUIElementTypeButton" AND visible == 1 AND '
    '(label CONTAINS[c] "Increment quantity" OR name CONTAINS[c] "Increment quantity" OR '
    'label CONTAINS[c] "Decrement quantity" OR name CONTAINS[c] "Decrement quantity" OR '
    'label CONTAINS[c] "Quantity:" OR name CONTAINS[c] "Quantity:" OR '
    'label CONTAINS[c] " ct" OR label CONTAINS[c] " lb" OR label CONTAINS[c] " oz" OR '
    'label CONTAINS[c] " each" OR name CONTAINS[c] " ct" OR name CONTAINS[c] " lb")'
)


class SessionLost(RuntimeError):
    """WDA / Appium session is dead (socket hang up etc.) — stop immediately."""


def _assert_session_alive(driver) -> None:
    """Fail fast when the WDA session is gone instead of scanning empty trees."""
    last: Exception | None = None
    for _ in range(2):
        try:
            driver.get_window_size()
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.6)
    raise SessionLost(f"Appium/WDA session unresponsive: {last}")


def _tap_with_retry(driver, cx: float, cy: float, *, tries: int = 2) -> None:
    """mobile: tap with one retry — a transient 'socket hang up' should not kill the run."""
    last: Exception | None = None
    for i in range(tries):
        try:
            _tap_center(driver, cx, cy)
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"  tap error ({i + 1}/{tries}): {exc}")
            time.sleep(0.8)
            _assert_session_alive(driver)
    raise RuntimeError(f"tap failed after {tries} tries: {last}")


def _add_plus_still_at(driver, cx: float, cy: float) -> bool:
    """True if an 'Add 1 <unit> …' control is still at ~(cx, cy)."""
    for px, py, _lab in _collect_visible_add_plus(driver):
        if abs(px - cx) < 34 and abs(py - cy) < 34:
            return True
    return False


def _stepper_near(driver, cx: float, cy: float, *, radius: float = 110.0) -> bool:
    """True if a (− N +) stepper or a collapsed '1 ct' / '1 lb' pill is near (cx, cy).

    After a successful add Instacart swaps the green + for a green pill that
    reads '1 ct' / '1 lb' (see store screenshot) — that pill IS the confirmation.
    """
    try:
        els = driver.find_elements(AppiumBy.IOS_PREDICATE, _STEPPER_PRED)
    except Exception:  # noqa: BLE001
        return False
    for el in els[:60]:
        try:
            label = (el.get_attribute("label") or el.get_attribute("name") or "").strip()
            low = label.lower()
            if low.startswith("add ") or "sponsored" in low:
                continue
            if "increment" not in low and "decrement" not in low and not _QTY_PILL_RE.search(label):
                continue
            r = _safe_rect(el)
            if not r:
                continue
            w = float(r.get("width", 0))
            if w > 160:  # product tiles / cart rows, not the small pill
                continue
            ex = float(r.get("x", 0)) + w / 2.0
            ey = float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0
            if abs(ex - cx) <= radius and abs(ey - cy) <= 60:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


# ---- Basket D: storefront tap bounds (run 20261013 run 3: + at (132,750) and
# (283,757) sat under the floating cart bar, top y 742–756 in real dumps; the
# second tap most likely opened the cart and the loop then scrolled the cart).
_D_PLUS_RADIUS = 18.0       # green + circle ≈ 36 pt (store screenshot)
_D_BAR_MARGIN = 8.0         # clearance above the floating cart bar
_D_HEADER_FLOOR = 180.0     # + centre never above this (sticky header / chips)
_D_BAR_TOP_FALLBACK = 742.0 # lowest bar top seen in dumps (19,742 352×56)


_D_BAR_RECHECKS = 3          # extra bar reads when it is present but not yet valid
_D_BAR_RECHECK_S = 0.5       # spacing between them (~1.5 s in total)


def _d_bar_read(driver) -> tuple[str, float | None]:
    """One live read of the floating cart bar: ("valid", top) | ("invalid", None)
    when it exists but has zero height / reads not visible | ("absent", None)."""
    try:
        els = driver.find_elements(AppiumBy.ACCESSIBILITY_ID, "floating_cart_button")
    except Exception:  # noqa: BLE001
        return "absent", None
    if not els:
        return "absent", None
    for el in els:
        r = _safe_rect(el) or {}
        if r and float(r.get("height", 0)) > 0:
            try:
                if el.get_attribute("visible") in ("false", False):
                    continue
            except Exception:  # noqa: BLE001
                pass
            return "valid", float(r.get("y", _D_BAR_TOP_FALLBACK))
    return "invalid", None


def _d_other_screen(driver) -> str | None:
    """'cart' / 'checkout' / 'product' when one of those screens is up, else None."""
    try:
        if driver.find_elements(AppiumBy.IOS_PREDICATE, 'name == "CartV4.CartView" AND visible == 1'):
            return "cart"
        if driver.find_elements(AppiumBy.IOS_PREDICATE, 'name == "Checkout.CheckoutView" AND visible == 1'):
            return "checkout"
    except Exception:  # noqa: BLE001
        pass
    try:
        if _on_product_detail(driver):
            return "product"
    except Exception:  # noqa: BLE001
        pass
    return None


def _d_store_state(driver) -> tuple[str, float | None]:
    """("store", bar_top) on the storefront; ("cart"|"other", None) otherwise.

    Storefront = the floating cart bar is visible and no CartV4 screen is up.
    Basket D (run 20261015_095703: the bar read zero height ~2 s after the
    store loaded): a bar that is present but not yet valid is re-read up to
    _D_BAR_RECHECKS times (~1.5 s) — each retry first rules out the cart,
    checkout and product screens, which stop at once. The returned top is
    always from the read that was accepted (never a stale position).
    """
    try:
        if driver.find_elements(AppiumBy.IOS_PREDICATE, 'name == "CartV4.CartView" AND visible == 1'):
            return "cart", None
    except Exception:  # noqa: BLE001
        pass
    state, top = _d_bar_read(driver)
    if state == "valid":
        return "store", top
    if state == "absent":
        return "other", None
    for k in range(_D_BAR_RECHECKS):
        other = _d_other_screen(driver)
        if other is not None:
            return ("cart" if other == "cart" else "other"), None
        time.sleep(_D_BAR_RECHECK_S)
        state, top = _d_bar_read(driver)
        if state == "valid":
            print(f"  floating cart bar valid again after {k + 1} re-check(s) (top {top:.0f})")
            return "store", top
        if state == "absent":
            return "other", None
    return "other", None


def _d_add_point_safe(ax: float, ay: float, bar_top: float | None, sw: float = 390.0) -> str:
    """"" when the whole + target is clear; else the obstruction."""
    top = bar_top if bar_top is not None else _D_BAR_TOP_FALLBACK
    if ay < _D_HEADER_FLOOR:
        return f"under the sticky header (y={ay:.0f} < {_D_HEADER_FLOOR:.0f})"
    if ay + _D_PLUS_RADIUS > top - _D_BAR_MARGIN:
        return f"under the floating cart bar (+ bottom {ay + _D_PLUS_RADIUS:.0f} > bar top {top:.0f} − {_D_BAR_MARGIN:.0f})"
    if ax + _D_PLUS_RADIUS > sw - 2.0 or ax - _D_PLUS_RADIUS < 2.0:
        return f"at the screen edge (x={ax:.0f})"
    return ""


def _add_n_items_under_max_price(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    count: int,
    max_price: float | None = MAX_ITEM_PRICE,
    exclude: list[str] | None = None,
) -> list[dict]:
    """Add exactly `count` distinct items from the store — scroll as needed.

    Basket D: ``exclude`` = labels already in the cart (or just removed) — never
    tapped again and never added as a look-alike (out-of-stock swap).

    Confirmation order per tap:
      1. the 'Add 1 ct …' control at that spot is gone AND a stepper appeared;
      2. else, cart badge readable both before/after and went exactly +1
         (or rebased to 1 on the very first add);
      3. else, stepper appeared alone (badge unreadable).
    """
    count = max(1, int(count))
    start_badge = _basket_count(driver)
    print(
        f"Step: add exactly {count} items from Grocery Outlet store "
        f"({_price_cap_label(max_price)}; badge={start_badge!r}) …"
    )
    _wait_household_listing(driver, markers)
    added: list[dict] = []
    skip: set[str] = set()
    prior = [{"label": lab} for lab in (exclude or []) if lab]
    for lab in exclude or []:
        k = _item_label_key(lab)
        if k:
            skip.add(k)
    tapped_coords: set[str] = set()
    scrolls_used = 0
    max_scrolls = 40   # Basket D: 25 products (the validated 10-product runs used 3–7 scrolls)
    idle_scans = 0

    pending_raw: dict = {}

    def _try_tap(cx: float, cy: float, lab: str, via: str, price: float | None = None) -> bool:
        key = _item_label_key(lab)
        if _label_in_skip(key, skip):
            return False
        # Basket D (25 products): never add a product the read-only cart check
        # could not tell apart from one already added ("X" vs "X, Minis") — the
        # row would match 2 products and the run would stop at the cart.
        twin = next((a for a in [*prior, *added] if _d_same_listing(lab, a.get("label") or "")), None)
        if twin is not None:
            print(f"  skip {lab[:40]!r} — indistinguishable from added {str(twin.get('label'))[:40]!r}")
            if key:
                skip.add(key)
            return False
        coord_key = _tap_coord_key(cx, cy)
        if coord_key in tapped_coords:
            if key:
                skip.add(key)
            return False
        before = _basket_count(driver)
        print(f"  tap Add + {lab[:50]!r} @ ({cx:.0f},{cy:.0f}) badge={before!r}")
        try:
            _tap_with_retry(driver, cx, cy)
        except SessionLost:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"  Add + failed: {exc}")
            return False
        tapped_coords.add(coord_key)
        if key:
            skip.add(key)  # never re-tap this SKU — a 2nd tap bumps qty
        time.sleep(0.25)
        _dismiss_replacement_preference(driver, markers)
        _d_tap_dismissibles(driver, labels=INSTACART_DISMISS_LABELS, rounds=1)

        # 1) UI change: Add control gone + stepper in its place.
        ui_ok = False
        for _ in range(4):
            if not _add_plus_still_at(driver, cx, cy) and _stepper_near(driver, cx, cy):
                ui_ok = True
                break
            time.sleep(0.18)

        # 2) Badge (only when readable).
        after = _wait_basket_count_change(driver, before, tries=3) if before is not None else _basket_count(driver)
        badge_ok = (
            before is not None
            and after is not None
            and _add_confirmed(before, after, allow_rebase=(len(added) == 0 and not prior))
        )
        # 3) Stepper alone when badge unreadable.
        stepper_only = (before is None or after is None) and _stepper_near(driver, cx, cy)

        confirmed = ui_ok or badge_ok or stepper_only
        how = "ui" if ui_ok else ("badge" if badge_ok else ("stepper" if stepper_only else "none"))
        if (
            confirmed
            and not badge_ok
            and isinstance(before, int)
            and isinstance(after, int)
            and after <= before
        ):
            # Badge readable and it did not go up: a nearby pill of an earlier
            # product is not proof (run 20261013_065945, badge 4→4 twice).
            time.sleep(1.0)
            late = _basket_count(driver)
            if isinstance(late, int) and _add_confirmed(before, late, allow_rebase=(len(added) == 0 and not prior)):
                after, how = late, "badge"
            else:
                print(f"  Add not confirmed — badge {before!r}→{late!r} did not rise (ui said {how})")
                return False
        if not confirmed:
            print(f"  Add not confirmed ({before!r}→{after!r}, via={how})")
            return False
        entry = {
            "price": price,
            "label": lab[:160],
            # Basket D: the full store card label — evidence for the cart name
            # check when the card's repeated name is shortened.
            "raw_label": (pending_raw.get("label") or "")[:400],
            "add_x": cx,
            "add_y": cy,
            "add_via": via,
            "confirmed": True,
            "confirmed_by": how,
            "basket_before": before,
            "basket_after": after,
        }
        added.append(entry)
        outcome["item_added"] = entry
        outcome["item_added_to_cart"] = True
        print(f"  progress {len(added)}/{count} (confirmed by {how}) badge={after!r} "
              f"— {lab[:40]!r} ${price if price is not None else '?'}")
        markers.mark(
            "basket_add_progress",
            confirmed=len(added),
            target=count,
            badge=after,
            via=via,
            confirmed_by=how,
        )
        time.sleep(0.10)
        return True

    while len(added) < count and scrolls_used <= max_scrolls:
        _assert_session_alive(driver)  # dead WDA → raise now, not after 10 empty scrolls
        # Basket D: only priced product cards are added (the price is the qty-1
        # evidence in the cart) — the button-only path cannot see the price.
        pluses: list = []
        print(f"  scan: {len(pluses)} Add + / scroll={scrolls_used}/{max_scrolls}")
        got_one = False
        for cx, cy, lab in pluses:
            if len(added) >= count:
                break
            if _try_tap(cx, cy, lab, "visible_add_plus"):
                got_one = True

        obstructed: list[str] = []
        tapped = False
        if len(added) < count and not got_one and not pluses:
            # Basket D: confirm the storefront and read the bar's real top first.
            state, bar_top = _d_store_state(driver)
            if state != "store":
                _d_stop(markers, outcome, f"add loop is not on the storefront (screen: {state}) — "
                        "stopped instead of scrolling", UnexpectedScreen)
            # Fallback (only when no Add controls were found at all): priced
            # product cards → nearest Add control.
            for product in _collect_under_max_products(driver, max_price=max_price)[:12]:
                if len(added) >= count:
                    break
                key = _item_label_key(product["label"])
                if _label_in_skip(key, skip):
                    continue
                add = _find_add_near_product(driver, product)
                if add is None:
                    continue
                ax, ay, via = add
                if ay < 170:
                    continue
                um = _ADD_UNIT_RE.match(via or "")
                if um and um.group(1).lower() in ("lb", "lbs", "oz"):
                    print(f"  skip weighted Add {via[:50]!r} near {product['label'][:30]!r}")
                    skip.add(key)
                    continue
                why = _d_add_point_safe(ax, ay, bar_top)
                if why:
                    # Not skipped for good: a short scroll brings it into the safe band.
                    obstructed.append(_card_display_name(product["label"])[:40])
                    print(f"  hold {_card_display_name(product['label'])[:40]!r} — + @ ({ax:.0f},{ay:.0f}) {why}")
                    continue
                # Re-confirm the storefront right before the tap (fresh bar top).
                state, bar_top = _d_store_state(driver)
                if state != "store":
                    _d_stop(markers, outcome, f"screen changed before tapping {product['label'][:30]!r} "
                            f"(screen: {state}) — not tapped", UnexpectedScreen)
                why = _d_add_point_safe(ax, ay, bar_top)
                if why:
                    obstructed.append(_card_display_name(product["label"])[:40])
                    print(f"  hold {_card_display_name(product['label'])[:40]!r} — + @ ({ax:.0f},{ay:.0f}) {why}")
                    continue
                tapped = True
                pending_raw["label"] = product["label"]
                if _try_tap(ax, ay, _card_display_name(product["label"]), via, price=product["price"]):
                    got_one = True
                # Any tap may move the layout or change the screen: re-scan
                # (fresh tree, fresh coordinates) before the next tap.
                break

        if len(added) >= count:
            break
        if got_one or tapped:
            idle_scans = 0
            continue  # re-scan same view first (fresh coordinates)
        idle_scans += 1
        if scrolls_used >= max_scrolls:
            break
        scrolls_used += 1
        state, _bt = _d_store_state(driver)
        if state != "store":
            _d_stop(markers, outcome, f"add loop is not on the storefront before scroll #{scrolls_used} "
                    f"(screen: {state}) — stopped instead of scrolling", UnexpectedScreen)
        # Obstructed + (under the bar): a short scroll lifts it into the safe band
        # without skipping the cards above it.
        pct = 0.20 if obstructed else 0.40
        print(f"  need more items — scroll #{scrolls_used}/{max_scrolls}"
              + (f" (short: {len(obstructed)} + held under the cart bar)" if obstructed else ""))
        _scroll_products_down(driver, percent=pct)
        markers.mark("store_scrolled_for_adds", index=scrolls_used)
        # Basket D: tap points are screen positions — only meaningful within one
        # view. After a scroll a NEW card can sit where an earlier + was tapped;
        # the name skip set (not the position) keeps every product to one tap.
        tapped_coords.clear()
        time.sleep(0.35)

    final_badge = _basket_count(driver)
    outcome["items_added"] = [
        {"label": e.get("label"), "raw_label": e.get("raw_label"), "price": e.get("price"),
         "confirmed_by": e.get("confirmed_by"),
         "basket_before": e.get("basket_before"), "basket_after": e.get("basket_after")}
        for e in added
    ]
    outcome["items_added_count"] = len(added)
    outcome["basket_count_start"] = start_badge
    outcome["basket_count_after_adds"] = final_badge
    outcome["add_mode"] = "store_scroll_ui_confirm"
    if added:
        outcome["item_price"] = added[0].get("price")
        outcome["item_added_to_cart"] = True
    if len(added) < count:
        raise AssertionError(
            f"Only confirmed {len(added)}/{count} unique store adds "
            f"(scrolls={scrolls_used}, badge={final_badge!r})"
        )
    # Badge sanity only when it is readable; never fail on an unreadable badge.
    if isinstance(final_badge, int) and isinstance(start_badge, int):
        first_after = added[0].get("basket_after")
        expected = {count, start_badge + count}
        if isinstance(first_after, int):
            expected.add(first_after + (count - 1))
        if final_badge not in expected:
            print(
                f"  WARNING: badge={final_badge} not in {sorted(expected)} "
                "(possible qty bump) — continuing; cart step will verify lines"
            )
            markers.mark("basket_badge_mismatch", badge=final_badge, expected=sorted(expected))
    print(
        f"  basket add done: {len(added)}/{count} unique "
        f"(scrolls used={scrolls_used}, badge={final_badge!r})"
    )
    return added


def _tap_basket_cart_icon(driver, markers: MarkerLog, outcome: dict) -> None:
    """Tap the cart icon on the bottom green Basket / delivery-fee bar."""
    print("Step: tap cart icon on bottom Basket bar …")
    time.sleep(_MED)

    el = None
    by = value = ""
    try:
        el, by, value = find_first(driver, BASKET_CART_STRATEGIES, timeout=6.0)
    except TimeoutError:
        el = None

    if el is not None:
        # Badge immediately before opening the cart ("10, Add $66.49 to get $15
        # off") — upper bound on cart units for the read-only qty check.
        outcome["badge_before_cart"] = None
        try:
            m_badge = re.match(r"^\s*(\d{1,3})\s*,", el.get_attribute("label") or "")
            outcome["badge_before_cart"] = int(m_badge.group(1)) if m_badge else None
        except Exception:  # noqa: BLE001
            pass
        r = _safe_rect(el) or {}
        x = float(r.get("x", 0))
        y = float(r.get("y", 0))
        w = float(r.get("width", 0))
        h = float(r.get("height", 0))
        # Cart glyph sits on the right of the green bar (badge "1").
        if w > 80:
            cx = x + w * 0.88
            cy = y + h / 2.0
        else:
            cx = x + w / 2.0
            cy = y + h / 2.0
        try:
            _tap_center(driver, cx, cy)
        except Exception:  # noqa: BLE001
            el.click()
            cx = x + w / 2.0
            cy = y + h / 2.0
        outcome.setdefault("selectors", {})["basket_cart"] = {
            "by": by,
            "value": value,
            "x": cx,
            "y": cy,
        }
        markers.mark("basket_cart_tapped", by=by, value=value, x=cx, y=cy)
        _mark_step(
            outcome,
            "basket_cart",
            status="ok",
            by=by,
            value=value,
            x=cx,
            y=cy,
        )
        outcome["basket_opened"] = True
        print(f"  Basket cart: {by} = {value!r} @ ({cx:.0f},{cy:.0f})")
        time.sleep(_SLOW)
        return

    # Coord fallback: bottom-right of green floating bar (~y 770 on 844h).
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        w, h = 390.0, 844.0
    cx, cy = w * 0.88, h * 0.91
    _tap_center(driver, cx, cy)
    markers.mark("basket_cart_tapped", via="coord", x=cx, y=cy)
    _mark_step(outcome, "basket_cart", status="ok", via="coord", x=cx, y=cy)
    outcome["basket_opened"] = True
    print(f"  Basket cart → coord @ ({cx:.0f},{cy:.0f})")
    time.sleep(_SLOW)


def _wait_for_cart_view(driver, markers: MarkerLog, *, timeout: float = 8.0) -> bool:
    """Wait until CartV4 cart sheet / screen is visible (True = confirmed)."""
    print("Step: wait for cart / Basket screen …")
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            hits = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'visible == 1 AND ('
                'name == "CartV4.CartView" OR '
                'label CONTAINS[c] "Go to checkout" OR '
                'name CONTAINS[c] "Go to checkout" OR '
                'label CONTAINS[c] "Complete your cart")',
            )
            if hits:
                markers.mark("cart_view_ready")
                print("  cart / Basket screen ready")
                time.sleep(_MED)
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.25)
    markers.mark("cart_view_ready", partial=True)
    print("  cart screen not fully confirmed — continuing")
    return False


def _parse_cart_line_qty(blob: str) -> int | None:
    """Parse cart-line qty; ignore sponsored pack sizes like '50 ct'."""
    if not blob:
        return None
    low = blob.lower()
    if "sponsored" in low or "add 1 ct" in low:
        return None
    m = _QTY_ITEM_RE.search(blob)
    if m:
        qty = int(m.group(1))
        if 1 <= qty <= _MAX_PLAUSIBLE_CART_QTY:
            return qty
    # Exact short chip only ("5 ct"), not product titles ("… 50 ct").
    for part in (blob, *(p.strip() for p in re.split(r"[\n|]", blob))):
        m = _QTY_CT_EXACT_RE.match(part.strip())
        if m:
            qty = int(m.group(1))
            if 1 <= qty <= _MAX_PLAUSIBLE_CART_QTY:
                return qty
    return None


def _scroll_cart_to_reveal_line(driver, markers: MarkerLog) -> None:
    """If the cart line scrolled under 'Complete your cart', gently pull it back."""
    try:
        size = driver.get_window_size() or {}
        sw = float(size.get("width") or 390)
        sh = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        sw, sh = 390.0, 844.0

    for i in range(2):
        line = _find_cart_line_button(driver, include_offscreen=True)
        if line is not None:
            _x, y, _w, _h, _label = line
            if 90 <= y <= 520:
                return
            # Line is above the fold — one gentle pull-down is enough.
        else:
            # Empty / no line — do not keep scrolling (can look like empty cart).
            footer = _read_cart_footer_label(driver)
            total = _parse_footer_total(footer)
            if total is not None and total < 0.01:
                print("  cart appears empty — skip scroll-to-line")
                return
        try:
            driver.execute_script(
                "mobile: dragFromToForDuration",
                {
                    "duration": 0.25,
                    "fromX": int(sw * 0.5),
                    "fromY": int(sh * 0.38),
                    "toX": int(sw * 0.5),
                    "toY": int(sh * 0.58),
                },
            )
        except Exception:  # noqa: BLE001
            pass
        markers.mark("cart_scrolled_to_line", index=i + 1)
        print(f"  cart scrolled to reveal line item #{i + 1}")
        time.sleep(0.4)


def _parse_footer_total(footer: str | None) -> float | None:
    if not footer:
        return None
    amounts = re.findall(r"\$\s*(\d+(?:\.\d{1,2})?)", footer)
    if not amounts:
        return None
    try:
        return float(amounts[-1])
    except ValueError:
        return None


def _still_on_cart_view(driver) -> bool:
    try:
        hits = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'name == "CartV4.CartView" OR '
            'label CONTAINS[c] "Go to checkout" OR '
            'name CONTAINS[c] "Go to checkout" OR '
            'label CONTAINS[c] "Min. to checkout" OR '
            'name CONTAINS[c] "Min. to checkout" OR '
            'label CONTAINS[c] "Complete your cart"',
        )
        return bool(hits)
    except Exception:  # noqa: BLE001
        return False


def _dismiss_pdp_if_open(driver, markers: MarkerLog) -> None:
    """Qty miss-taps open the product page — Back returns to CartV4."""
    if _still_on_cart_view(driver):
        return
    print("  left cart (likely PDP) — tapping Back")
    try:
        back = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(name == "Back" OR label == "Back")',
        )
        if back:
            back[0].click()
            markers.mark("cart_pdp_dismissed")
            time.sleep(_MED)
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        size = driver.get_window_size() or {}
        _tap_center(driver, 24, float(size.get("height") or 844) * 0.08)
        time.sleep(_MED)
    except Exception:  # noqa: BLE001
        pass


def _read_first_cart_line_qty(driver) -> int | None:
    """Read qty from first cart line ('5 ct' chip or Increment button label)."""
    best_y = 9999.0
    best_qty: int | None = None

    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label CONTAINS[c] "Increment quantity" OR '
            'name CONTAINS[c] "Increment quantity" OR '
            'label CONTAINS[c] "Quantity:" OR '
            'name CONTAINS[c] "Quantity:" OR '
            'label CONTAINS[c] " ct" OR '
            'name CONTAINS[c] " ct" OR '
            'value CONTAINS[c] " ct")',
        )
    except Exception:  # noqa: BLE001
        els = []

    for el in els[:32]:
        try:
            label = el.get_attribute("label") or ""
            name = el.get_attribute("name") or ""
            value = el.get_attribute("value") or ""
            # Prefer exact chip / Increment; skip sponsored product tiles.
            qty = None
            for candidate in (label, name, value, f"{label} {name} {value}"):
                qty = _parse_cart_line_qty(candidate)
                if qty is not None:
                    break
            if qty is None:
                continue
            r = _safe_rect(el)
            if not r:
                continue
            y = float(r.get("y", 9999))
            h = float(r.get("height", 0))
            # Cart line qty lives in the upper cart area (below nav); tall
            # sponsored tiles (h≈273) must be ignored.
            if y < 90 or y > 420 or h > 80:
                continue
            if y < best_y:
                best_y = y
                best_qty = qty
        except Exception:  # noqa: BLE001
            continue
    return best_qty


def _read_cart_footer_label(driver) -> str | None:
    """Read sticky cart footer CTA label ('Go to checkout' or '$10 Min…')."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label CONTAINS[c] "checkout" OR name CONTAINS[c] "checkout")',
        )
    except Exception:  # noqa: BLE001
        return None
    for el in els[:8]:
        try:
            label = el.get_attribute("label") or el.get_attribute("name") or ""
            if "checkout" in label.lower():
                return label.replace("\n", " | ")
        except Exception:  # noqa: BLE001
            continue
    return None


def _find_cart_line_button(
    driver, *, include_offscreen: bool = False
) -> tuple[float, float, float, float, str] | None:
    """Return (x, y, w, h, label) for the first CartV4 line item button.

    CartV4 exposes the whole row as one button (value includes
    'Replace with best match'). The qty pill (trash | N ct | +) is drawn on
    top of that row but is often missing from the accessibility tree.
    """
    visible_clause = "" if include_offscreen else "visible == 1 AND "
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            f'type == "XCUIElementTypeButton" AND {visible_clause}'
            '(value CONTAINS[c] "replace" OR label CONTAINS[c] "replace")',
        )
    except Exception:  # noqa: BLE001
        els = []
    best = None
    best_y = 9999.0
    for el in els[:16]:
        try:
            r = _safe_rect(el)
            if not r:
                continue
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            h = float(r.get("height", 0))
            if w < 280 or h < 70 or h > 160:
                continue
            if not include_offscreen and (y < 90 or y > 520):
                continue
            if include_offscreen and (y < -200 or y > 700):
                continue
            label = (el.get_attribute("label") or el.get_attribute("name") or "")[:80]
            # Prefer the topmost on-screen (or least-negative) cart line.
            score_y = y if y >= 0 else y + 1000
            if score_y < best_y:
                best_y = score_y
                best = (x, y, w, h, label)
        except Exception:  # noqa: BLE001
            continue
    return best


def _qty_plus_point_from_line(
    x: float, y: float, w: float, h: float, *, inset: float = 22.0
) -> tuple[float, float]:
    """Map cart-line rect → center of the '+' inside the (trash | N ct | +) pill.

    Pill layout (right side of row): [trash/minus] [N ct] [+].
    Empirically inset≈22 (x≈368 on 390-wide) hits +; inset≈14 opens PDP;
    inset≳28 drifts into minus and decreases qty.
    """
    cx = x + w - inset
    cy = y + min(36.0, max(28.0, h * 0.30))
    return cx, cy


def _find_cart_qty_plus(driver) -> tuple[float, float, str] | None:
    """Return (cx, cy, via) for + on the first cart line qty stepper (1 ct → 5 ct)."""
    try:
        size = driver.get_window_size() or {}
        sh = float(size.get("height") or 844)
        sw = float(size.get("width") or 390)
    except Exception:  # noqa: BLE001
        sh, sw = 844.0, 390.0

    candidates: list[tuple[float, float, float, str]] = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label CONTAINS[c] "Increment quantity" OR '
            'name CONTAINS[c] "Increment quantity")',
        )
    except Exception:  # noqa: BLE001
        els = []

    for el in els[:24]:
        try:
            label = (el.get_attribute("label") or el.get_attribute("name") or "")
            low = label.lower()
            if "decrement" in low or "remove" in low or low.startswith("add 1 ct"):
                continue
            r = _safe_rect(el)
            if not r:
                continue
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            h = float(r.get("height", 0))
            if w < 14 or h < 14 or w > 80:
                continue
            # CartV4 qty + is on the far right of the line (not listing-card +).
            if x < sw * 0.78 or y < 120 or y > min(420.0, sh * 0.52):
                continue
            candidates.append((y, x + w / 2.0, y + h / 2.0, label[:80]))
        except Exception:  # noqa: BLE001
            continue

    if candidates:
        candidates.sort(key=lambda t: (round(t[0] / 8.0), -t[1]))
        _y, cx, cy, label = candidates[0]
        return cx, cy, label

    line = _find_cart_line_button(driver) or _find_cart_line_button(
        driver, include_offscreen=True
    )
    if line:
        x, y, w, h, label = line
        cx, cy = _qty_plus_point_from_line(x, y, w, h)
        return cx, cy, f"cart_line_plus:{label}"

    # Last resort from typical CartV4 layout on 390×844 (inset≈22 hits +).
    return sw - 22.0, 262.0, "coord_qty_pill_plus"


def _increase_cart_qty_to(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    target_qty: int = CART_TARGET_QTY,
) -> None:
    """Tap qty-pill '+' one-at-a-time until cart total ≈ unit × target_qty.

    Verifies footer total after every tap so we never hit minus (qty drop) or
    overshoot past 5 ct. Pill is [minus/trash | N ct | +] — stay on the far right.
    """
    print(f"Step: increase cart quantity to {target_qty} (tap qty pill +) …")
    if _find_cart_line_button(driver) is None:
        _scroll_cart_to_reveal_line(driver, markers)

    footer0 = _read_cart_footer_label(driver)
    total0 = _parse_footer_total(footer0)
    if total0 is None or total0 < 0.01:
        raise AssertionError(f"Cannot raise qty — cart total unknown ({footer0!r})")

    # Count successful + taps (1→5 needs exactly 4). Do NOT derive target from
    # footer÷unit — leftover multi-item carts make that overshoot/undershoot.
    start_qty = 1
    needed_success = max(0, target_qty - start_qty)
    outcome["cart_qty_before"] = start_qty
    print(
        f"  cart qty before: ~{start_qty} ct — need {needed_success} successful "
        f"+ taps → {target_qty} ct (footer={footer0!r})"
    )

    # inset≈22 hits + on CartV4; keep within [18, 26] to avoid PDP / minus.
    inset = 22.0
    y_nudge = 0.0

    def _lock_plus() -> tuple[float, float, str]:
        line = _find_cart_line_button(driver)
        if line:
            x, y, w, h, label = line
            px, py = _qty_plus_point_from_line(x, y, w, h, inset=inset)
            return px, py + y_nudge, f"cart_line_plus:{label}"
        plus = _find_cart_qty_plus(driver)
        if not plus:
            raise AssertionError("Could not find cart quantity + (1 ct pill)")
        return plus[0], plus[1] + y_nudge, plus[2]

    cx, cy, via = _lock_plus()
    markers.mark("cart_qty_plus_found", x=cx, y=cy, via=via, inset=inset)
    print(f"  cart qty + @ ({cx:.0f},{cy:.0f}) via {via!r} inset={inset}")

    taps = 0
    successes = 0
    total = total0
    max_taps = needed_success + 8
    locked = False

    while successes < needed_success and taps < max_taps:
        _tap_center(driver, cx, cy)
        taps += 1
        markers.mark(
            "cart_qty_plus_tapped",
            index=taps,
            success_count=successes,
            x=cx,
            y=cy,
            via=via,
            inset=inset,
        )
        time.sleep(0.45)

        if not _still_on_cart_view(driver):
            print("  left cart after + tap — dismissing PDP")
            _dismiss_pdp_if_open(driver, markers)
            if not locked:
                inset = min(26.0, inset + 2.0)
                y_nudge = -4.0 if y_nudge >= 0 else 4.0
            cx, cy, via = _lock_plus()
            print(f"  retarget + @ ({cx:.0f},{cy:.0f}) inset={inset}")
            continue

        footer = _read_cart_footer_label(driver)
        new_total = _parse_footer_total(footer)

        if new_total is not None and new_total > total + 0.01:
            locked = True
            total = new_total
            successes += 1
            qty_now = start_qty + successes
            print(
                f"  + ok ({successes}/{needed_success}) → ~{qty_now} ct / ${total:.2f}"
            )
            cx, cy, via = _lock_plus()
            continue

        if new_total is not None and new_total < total - 0.01:
            # Hit minus — undo one success if we had any.
            print(
                f"  qty DROPPED ${total:.2f}→${new_total:.2f} "
                f"(hit minus) — move right toward +"
            )
            total = new_total
            if successes > 0:
                successes -= 1
            inset = max(18.0, inset - 2.0)
            cx, cy, via = _lock_plus()
            markers.mark("cart_qty_plus_retarget", x=cx, y=cy, reason="decreased")
            print(f"  retarget + @ ({cx:.0f},{cy:.0f}) inset={inset} "
                  f"(successes now {successes})")
            continue

        print(f"  total unchanged (${total:.2f}) after + — slight retarget")
        if not locked:
            inset = min(26.0, inset + 1.0)
        else:
            y_nudge = -2.0 if y_nudge >= 0 else 2.0
        cx, cy, via = _lock_plus()
        markers.mark("cart_qty_plus_retarget", x=cx, y=cy, reason="unchanged")
        time.sleep(0.2)

    footer = _read_cart_footer_label(driver)
    total = _parse_footer_total(footer) or total
    qty = start_qty + successes
    outcome["cart_qty_plus_taps"] = taps
    outcome["cart_qty_after"] = qty
    outcome["cart_footer_after_qty"] = footer
    success = successes >= needed_success

    _mark_step(
        outcome,
        "cart_qty_increase",
        status="ok" if success else "failed",
        before=start_qty,
        after=qty,
        taps=taps,
        successes=successes,
        target=target_qty,
        footer=footer,
        total=total,
    )
    if not success:
        raise AssertionError(
            f"Could not raise qty to {target_qty} ct after {taps} taps "
            f"({successes}/{needed_success} successful +, footer={footer!r})."
        )

    markers.mark(
        "cart_qty_increased",
        qty=qty,
        target=target_qty,
        taps=taps,
        successes=successes,
        footer=footer,
        total=total,
    )
    print(f"  cart qty now ~{qty} ct after {successes} successful + "
          f"(footer={footer!r})")

    # Grocery Outlet enforces a $10 checkout minimum — keep + until enabled.
    extra = 0
    while (
        footer
        and "10 min" in footer.lower()
        and (total is None or total < 10.0)
        and extra < 12
    ):
        print(f"  still under $10 min ({footer!r}) — + again")
        _tap_center(driver, cx, cy)
        taps += 1
        extra += 1
        time.sleep(0.45)
        footer = _read_cart_footer_label(driver)
        total = _parse_footer_total(footer) or total
        if total is not None:
            print(f"  cart total now ${total:.2f}")
        markers.mark("cart_qty_plus_for_minimum", index=extra, footer=footer)

    outcome["cart_qty_plus_taps"] = taps
    outcome["cart_footer_after_qty"] = footer
    if footer and "go to checkout" in footer.lower():
        qty = start_qty + successes + extra
        outcome["cart_qty_after"] = qty
        print(f"  checkout unlocked at ~{qty} ct (footer={footer!r})")
        return
    if total is not None and total >= 10.0:
        outcome["cart_qty_after"] = start_qty + successes + extra
        print(f"  total ${total:.2f} ≥ $10 — continuing")
        return
    raise AssertionError(
        f"Cart still below $10 checkout minimum after qty raise "
        f"(footer={footer!r})"
    )


def _cart_line_plus_point(
    x: float, y: float, w: float, h: float
) -> tuple[float, float]:
    """Qty pill + on CartV4 rows: [trash | N ct | +] under the thumb (LEFT).

    Empirically + sits near x≈116–133, mid-row — NOT the far-right inset used
    by older Cart layouts.
    """
    cx = x + 125.0
    cy = y + min(78.0, max(55.0, h * 0.62))
    return cx, cy


# Raw CartV4 row value per label from the latest scan, e.g.
#   "Get 1 for free,$13.41, Price before promotion: $14.90,Replace with best match,…"
_ROW_VALUE_CACHE: dict[str, str] = {}
_PRE_PROMO_RE = re.compile(r"Price before promotion:\s*\$\s*(\d+(?:\.\d{1,2})?)", re.I)
_QTY_DEAL_RE = re.compile(r"\bfree\b|\bbuy\s*\d|\bget\s*\d|\bsave\b|%\s*off|\bfor\s*\$", re.I)


def _parse_row_promo(value: str) -> dict:
    """Split a cart row value into current price, pre-promotion total and deal text.

    Simple sale ("$24.90 $49.90,$24.90, Price before promotion: $49.90,…")
    stays linear in qty → not qty-dependent. A deal ("Get 1 for free,$13.41,
    Price before promotion: $14.90,…") makes the current price non-linear; its
    "Price before promotion" is the undiscounted line total (list × qty).
    """
    value = value or ""
    lead = []
    parts = value.split(",")
    price_from = len(parts)
    for i, part in enumerate(parts):
        if part.strip().startswith("$"):
            price_from = i            # first field that IS a price
            break
        part = part.strip()
        if part and part.lower() not in ("replace with best match", "eligible items"):
            lead.append(part)
    promo_text = ", ".join(lead)
    m_cur = _PRICE_RE.search(",".join(parts[price_from:]))
    m_pre = _PRE_PROMO_RE.search(value)
    current = float(m_cur.group(1)) if m_cur else None
    pre = float(m_pre.group(1)) if m_pre else None
    qty_dependent = bool(promo_text and _QTY_DEAL_RE.search(promo_text)) or (
        current is not None and current < 0.005 and bool(pre)
    )
    return {"promo_text": promo_text, "current": current, "pre_promo": pre,
            "qty_dependent": qty_dependent}


# Basket D: a CartV4 row carries its replacement preference in its value —
# "Replace with best match", "Replace with <product>", "Choose replacement",
# or "Refund, don't replace" (Libby's row, run 20261013_101818, was skipped
# because only the first two were recognised).
_CART_ROW_MARKERS = ("replace with", "choose replacement", "don't replace", "don\u2019t replace")


def _is_cart_row_text(value: str, name: str = "") -> bool:
    blob = f"{value or ''}\n{name or ''}".lower()
    return any(mk in blob for mk in _CART_ROW_MARKERS)


def _cart_lines_from_page_source(
    driver, sw: float, sh: float, prices: dict | None = None
) -> list[tuple[float, float, float, float, str]]:
    """Parse page_source for CartV4 rows (Replace with best match).

    If ``prices`` is given, fill it with {row label: first $ amount in the row
    value} (e.g. value "$2.49,Replace with best match") — used as qty evidence.
    """
    import xml.etree.ElementTree as ET

    rows: list[tuple[float, float, float, float, str]] = []
    try:
        root = ET.fromstring(driver.page_source or "")
    except Exception:  # noqa: BLE001
        return rows
    for el in root.iter():
        if el.attrib.get("type") != "XCUIElementTypeButton":
            continue
        if el.attrib.get("visible") != "true":
            continue
        value = el.attrib.get("value") or ""
        name = el.attrib.get("name") or el.attrib.get("label") or ""
        if not _is_cart_row_text(value, name):
            continue
        try:
            x = float(el.attrib.get("x", 0))
            y = float(el.attrib.get("y", 0))
            w = float(el.attrib.get("width", 0))
            h = float(el.attrib.get("height", 0))
        except ValueError:
            continue
        if w < sw * 0.70 or h < 60 or h > 200:
            continue
        # Allow rows almost down to the checkout CTA.
        if y < 100 or y > sh * 0.82:
            continue
        label = (name or value)[:100]
        rows.append((x, y, w, h, label))
        _ROW_VALUE_CACHE[label] = value
        if prices is not None:
            m = _PRICE_RE.search(value)
            if m:
                prices[label] = float(m.group(1))
    rows.sort(key=lambda t: t[1])
    deduped: list[tuple[float, float, float, float, str]] = []
    for row in rows:
        if deduped and abs(row[1] - deduped[-1][1]) < 40:
            continue
        deduped.append(row)
    return deduped


def _find_all_cart_line_buttons(
    driver, prices: dict | None = None
) -> list[tuple[float, float, float, float, str]]:
    """All visible CartV4 line buttons, top → bottom (page_source first)."""
    try:
        size = driver.get_window_size() or {}
        sh = float(size.get("height") or 844)
        sw = float(size.get("width") or 390)
    except Exception:  # noqa: BLE001
        sh, sw = 844.0, 390.0

    rows = _cart_lines_from_page_source(driver, sw, sh, prices)
    if rows:
        return rows

    # Fallback: live predicate query.
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(value CONTAINS[c] "replace" OR label CONTAINS[c] "replace")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:30]:
        try:
            r = _safe_rect(el)
            if not r:
                continue
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            h = float(r.get("height", 0))
            if w < sw * 0.70 or h < 60 or h > 200:
                continue
            if y < 100 or y > sh * 0.82:
                continue
            label = (
                el.get_attribute("label")
                or el.get_attribute("name")
                or el.get_attribute("value")
                or ""
            )[:100]
            rows.append((x, y, w, h, label))
        except Exception:  # noqa: BLE001
            continue
    rows.sort(key=lambda t: t[1])
    deduped: list[tuple[float, float, float, float, str]] = []
    for row in rows:
        if deduped and abs(row[1] - deduped[-1][1]) < 40:
            continue
        deduped.append(row)
    return deduped


def _find_all_cart_increment_plus(
    driver,
) -> list[tuple[float, float, str, int]]:
    """Visible cart-line + as (cx, cy, label, qty). Skip 'Complete your cart' adds."""
    try:
        size = driver.get_window_size() or {}
        sh = float(size.get("height") or 844)
        sw = float(size.get("width") or 390)
    except Exception:  # noqa: BLE001
        sh, sw = 844.0, 390.0

    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label CONTAINS[c] "Increment quantity" OR '
            'name CONTAINS[c] "Increment quantity")',
        )
    except Exception:  # noqa: BLE001
        els = []

    out: list[tuple[float, float, float, str, int]] = []
    for el in els[:24]:
        try:
            label = (
                el.get_attribute("label") or el.get_attribute("name") or ""
            )
            low = label.lower()
            if "decrement" in low or "remove" in low:
                continue
            r = _safe_rect(el)
            if not r:
                continue
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            h = float(r.get("height", 0))
            if w < 14 or h < 14 or w > 90 or h > 60:
                continue
            if y < 100 or y > sh * 0.70:
                continue
            # Cart line + is left-ish (x≈100–140). Skip far-right listing +.
            if x < 40 or x > sw * 0.55:
                continue
            qty = _parse_cart_line_qty(label) or 1
            cx = x + w / 2.0
            cy = y + h / 2.0
            short = re.sub(r"(?i)^increment quantity of\s*", "", label)
            short = re.sub(r"(?i),\s*quantity:.*$", "", short).strip()
            out.append((y, cx, cy, short[:80] or label[:80], qty))
        except Exception:  # noqa: BLE001
            continue

    out.sort(key=lambda t: t[0])
    deduped: list[tuple[float, float, str, int]] = []
    for _y, cx, cy, label, qty in out:
        if deduped and abs(cy - deduped[-1][1]) < 28:
            continue
        deduped.append((cx, cy, label, qty))
    return deduped


def _scroll_cart_list(driver, *, up: bool = True) -> None:
    """Scroll the cart item list (up = reveal lower rows)."""
    try:
        size = driver.get_window_size() or {}
        sw = float(size.get("width") or 390)
        sh = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        sw, sh = 390.0, 844.0
    if up:
        fy, ty = sh * 0.62, sh * 0.32
    else:
        fy, ty = sh * 0.32, sh * 0.62
    try:
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.30,
                "fromX": int(sw * 0.50),
                "fromY": int(fy),
                "toX": int(sw * 0.50),
                "toY": int(ty),
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _increase_one_line_qty_at(
    driver,
    markers: MarkerLog,
    *,
    plus_x: float,
    plus_y: float,
    label: str,
    target_qty: int,
    line_index: int,
    start_qty: int = 1,
    line_rect: tuple[float, float, float, float] | None = None,
) -> int:
    """Tap + on one cart line until ~target_qty (verified via footer total)."""
    needed = max(0, target_qty - max(1, start_qty))
    if needed <= 0:
        print(f"  line {line_index}: already ~{start_qty} ct — skip")
        return 0
    cx, cy = plus_x, plus_y
    # Alternate left-pill vs classic right-pill if left misses.
    alt_points: list[tuple[float, float]] = [(cx, cy)]
    if line_rect is not None:
        x, y, w, h = line_rect
        alt_points.append(_cart_line_plus_point(x, y, w, h))
        alt_points.append(_qty_plus_point_from_line(x, y, w, h, inset=22.0))
    footer0 = _read_cart_footer_label(driver)
    total = _parse_footer_total(footer0) or 0.0
    successes = 0
    taps = 0
    max_taps = needed + 10
    alt_i = 0
    print(
        f"  line {line_index}: raise {start_qty}→{target_qty} ct "
        f"({needed} + taps) {label[:40]!r} @ ({cx:.0f},{cy:.0f})"
    )
    while successes < needed and taps < max_taps:
        _tap_center(driver, cx, cy)
        taps += 1
        time.sleep(0.36)
        if not _still_on_cart_view(driver):
            _dismiss_pdp_if_open(driver, markers)
            alt_i = (alt_i + 1) % len(alt_points)
            cx, cy = alt_points[alt_i]
            continue
        footer = _read_cart_footer_label(driver)
        new_total = _parse_footer_total(footer)
        if new_total is not None and new_total > total + 0.01:
            total = new_total
            successes += 1
            markers.mark(
                "cart_line_qty_plus",
                line=line_index,
                success=successes,
                of=needed,
                x=cx,
                y=cy,
            )
            continue
        if new_total is not None and new_total < total - 0.01:
            total = new_total
            # Hit trash/minus — jump to next plus candidate.
            alt_i = (alt_i + 1) % len(alt_points)
            cx, cy = alt_points[alt_i]
            print(f"    qty dropped — retarget + @ ({cx:.0f},{cy:.0f})")
            continue
        # No change — rotate plus target.
        if taps % 3 == 0:
            alt_i = (alt_i + 1) % len(alt_points)
            cx, cy = alt_points[alt_i]
    print(f"  line {line_index}: {successes}/{needed} successful + taps")
    return successes


def _pdp_action_bar_rect(driver) -> tuple[float, float, float, float] | None:
    """Bottom PDP CTA: 'Change qty' (while editing) or 'In Cart'."""
    try:
        size = driver.get_window_size() or {}
        sh = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        sh = 844.0
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Change qty" OR name == "Change qty" OR '
            'label CONTAINS[c] "Change qty" OR '
            'label == "In Cart" OR name == "In Cart" OR '
            'label CONTAINS[c] "In Cart")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:10]:
        try:
            r = _safe_rect(el)
            if not r:
                continue
            y = float(r.get("y", 0))
            if y < sh * 0.72:
                continue
            return (
                float(r.get("x", 0)),
                y,
                float(r.get("width", 0)),
                float(r.get("height", 0)),
            )
        except Exception:  # noqa: BLE001
            continue
    return None


def _pdp_in_cart_rect(driver) -> tuple[float, float, float, float] | None:
    """Alias — bottom PDP action bar (Change qty / In Cart)."""
    return _pdp_action_bar_rect(driver)


def _on_product_detail(driver) -> bool:
    """True when cart-line PDP is open.

    After the first +, Instacart swaps 'In Cart' → stepper (− N +) + 'Change qty'.
    Treat either as still on PDP — do NOT stop mid-raise.
    """
    if _pdp_action_bar_rect(driver) is not None:
        return True
    # Stepper-only: Decrement / Increment near bottom.
    try:
        size = driver.get_window_size() or {}
        sh = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        sh = 844.0
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label CONTAINS[c] "Increment quantity" OR '
            'name CONTAINS[c] "Increment quantity" OR '
            'label CONTAINS[c] "Decrement quantity" OR '
            'label == "+" OR name == "+")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:12]:
        try:
            r = _safe_rect(el)
            if r and float(r.get("y", 0)) >= sh * 0.75:
                return True
        except Exception:  # noqa: BLE001
            continue
    # Top-left cancel X on PDP (user-circled "cancel").
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Close" OR name == "Close" OR label == "cancel" OR '
            'name CONTAINS[c] "close")',
        )
        for el in els[:6]:
            r = _safe_rect(el)
            if not r:
                continue
            if float(r.get("y", 999)) < 120 and float(r.get("x", 999)) < 80:
                # Also require we're not on plain cart checkout.
                if not _still_on_cart_view(driver):
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _find_pdp_increment_el(driver):
    """Return the bottom-bar + / Increment button on PDP stepper."""
    try:
        size = driver.get_window_size() or {}
        sw = float(size.get("width") or 390)
        sh = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        sw, sh = 390.0, 844.0
    bar = _pdp_action_bar_rect(driver)
    bar_y = (bar[1] + bar[3] / 2.0) if bar else sh - 42.0

    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label CONTAINS[c] "Increment quantity" OR '
            'name CONTAINS[c] "Increment quantity" OR '
            'label == "+" OR name == "+")',
        )
    except Exception:  # noqa: BLE001
        els = []
    best = None
    best_score = 1e18
    for el in els[:24]:
        try:
            lab = (
                (el.get_attribute("label") or "")
                + " "
                + (el.get_attribute("name") or "")
            ).lower()
            if "decrement" in lab or "remove" in lab or "add 1 ct" in lab:
                continue
            r = _safe_rect(el)
            if not r:
                continue
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            h = float(r.get("height", 0))
            cy = y + h / 2.0
            if y < sh * 0.72 and abs(cy - bar_y) > 60:
                continue
            # Stepper + is left of green Change qty (not far-right).
            if x > sw * 0.62 or w > 110 or h > 80:
                continue
            score = abs(cy - bar_y) * 10 + abs(x - sw * 0.38)
            if score < best_score:
                best_score = score
                best = el
        except Exception:  # noqa: BLE001
            continue
    return best


def _find_pdp_bottom_plus(driver) -> tuple[float, float]:
    """Locate stepper + on PDP: (−  N  +) | Change qty."""
    try:
        size = driver.get_window_size() or {}
        sw = float(size.get("width") or 390)
        sh = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        sw, sh = 390.0, 844.0

    el = _find_pdp_increment_el(driver)
    if el is not None:
        r = _safe_rect(el) or {}
        return (
            float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0,
            float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0,
        )

    bar = _pdp_action_bar_rect(driver)
    if bar is not None:
        bx, by, bw, bh = bar
        # + is in the grey pill left of Change qty / In Cart.
        return max(90.0, min(bx - 36.0, sw * 0.40)), by + bh / 2.0

    return sw * 0.38, sh - 42.0


def _read_pdp_qty(driver, inc_el=...) -> int | None:
    """Read qty from PDP stepper (− N +) only — ignore pack sizes like '5 oz'.

    Pass ``inc_el`` (may be None) when the caller already resolved the
    stepper + on this screen, to skip a second lookup.
    """
    el = _find_pdp_increment_el(driver) if inc_el is ... else inc_el
    if el is not None:
        try:
            blob = (
                f"{el.get_attribute('label') or ''} "
                f"{el.get_attribute('name') or ''} "
                f"{el.get_attribute('value') or ''}"
            )
            m = re.search(r"quantity:\s*(\d+)", blob, re.I)
            if m:
                qty = int(m.group(1))
                if 1 <= qty <= _MAX_PLAUSIBLE_CART_QTY:
                    return qty
        except Exception:  # noqa: BLE001
            pass

    try:
        size = driver.get_window_size() or {}
        sh = float(size.get("height") or 844)
        sw = float(size.get("width") or 390)
    except Exception:  # noqa: BLE001
        sh, sw = 844.0, 390.0
    bar = _pdp_action_bar_rect(driver)
    bar_y = (bar[1] + bar[3] / 2.0) if bar else sh - 42.0

    # Exact short labels only inside the bottom stepper pill.
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label MATCHES "^[0-9]{1,2}$" OR '
            'value MATCHES "^[0-9]{1,2}$")',
        )
    except Exception:  # noqa: BLE001
        els = []
    best: tuple[float, int] | None = None  # (dist, qty)
    for el in els[:24]:
        try:
            r = _safe_rect(el)
            if not r:
                continue
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            h = float(r.get("height", 0))
            cy = y + h / 2.0
            # Between − and + on the left grey pill (not product title).
            if abs(cy - bar_y) > 36:
                continue
            if x < 50 or x > sw * 0.48 or w > 80:
                continue
            blob = (
                (el.get_attribute("label") or "")
                or (el.get_attribute("value") or "")
            ).strip()
            m = re.match(r"^(\d{1,2})$", blob)
            if not m:
                continue
            qty = int(m.group(1))
            if not (1 <= qty <= _MAX_PLAUSIBLE_CART_QTY):
                continue
            dist = abs(cy - bar_y) + abs(x - sw * 0.28)
            if best is None or dist < best[0]:
                best = (dist, qty)
        except Exception:  # noqa: BLE001
            continue
    return best[1] if best else None


def _tap_change_qty(driver, markers: MarkerLog) -> bool:
    """Confirm stepper with green 'Change qty' (required after raising)."""
    try:
        size = driver.get_window_size() or {}
        sh = float(size.get("height") or 844)
        sw = float(size.get("width") or 390)
    except Exception:  # noqa: BLE001
        sh, sw = 844.0, 390.0
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Change qty" OR name == "Change qty" OR '
            'label CONTAINS[c] "Change qty")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:6]:
        try:
            r = _safe_rect(el)
            if not r or float(r.get("y", 0)) < sh * 0.72:
                continue
            el.click()
            markers.mark("pdp_change_qty_tapped")
            print('  tapped "Change qty"')
            time.sleep(_MED)
            return True
        except Exception:  # noqa: BLE001
            continue
    # Coord fallback: green button right side of bottom bar.
    _tap_center(driver, sw * 0.72, sh - 42.0)
    markers.mark("pdp_change_qty_tapped", via="coord")
    print('  tapped "Change qty" (coord)')
    time.sleep(_MED)
    return True


def _close_pdp_to_cart(driver, markers: MarkerLog) -> None:
    """Close PDP via top-left cancel X, return to cart list.

    No scroll-to-top afterwards: the cart keeps its position and the next
    unprocessed row is found by scanning; _increase_basket_quantities only
    swipes when no unprocessed row is visible.
    """
    if _still_on_cart_view(driver) and not _on_product_detail(driver):
        return
    print("  closing PDP via top-left cancel X → cart …")
    for pred in (
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label == "Close" OR name == "Close")',
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label == "cancel" OR name CONTAINS[c] "close" OR '
        'label == "x" OR name == "x" OR label == "X")',
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label == "Back" OR name == "Back")',
    ):
        try:
            els = driver.find_elements(AppiumBy.IOS_PREDICATE, pred)
        except Exception:  # noqa: BLE001
            els = []
        for el in els[:6]:
            try:
                r = _safe_rect(el)
                if not r:
                    continue
                # Top-left cancel only (user-circled).
                if float(r.get("y", 999)) > 130 or float(r.get("x", 999)) > 90:
                    continue
                el.click()
                markers.mark("pdp_closed", via="cancel_x")
                time.sleep(_MED)
                if _still_on_cart_view(driver) and not _on_product_detail(driver):
                    return
            except Exception:  # noqa: BLE001
                continue
    # Explicit top-left cancel coord from screenshot.
    try:
        size = driver.get_window_size() or {}
        _tap_center(driver, 36.0, float(size.get("height") or 844) * 0.08)
        time.sleep(_MED)
        markers.mark("pdp_closed", via="coord_cancel")
    except Exception:  # noqa: BLE001
        pass
    _dismiss_pdp_if_open(driver, markers)


def _open_cart_line_pdp(
    driver, markers: MarkerLog, *, x: float, y: float, w: float, h: float, label: str
) -> bool:
    """Tap cart line product title/image to open PDP (avoid right qty pill)."""
    cx = x + min(w * 0.32, 140.0)
    cy = y + h * 0.35
    print(f"  open PDP for {label[:50]!r} @ ({cx:.0f},{cy:.0f})")
    _tap_center(driver, cx, cy)
    time.sleep(_SLOW)
    end = time.monotonic() + 6.0
    while time.monotonic() < end:
        if _on_product_detail(driver):
            markers.mark("cart_line_pdp_opened", label=label[:80], x=cx, y=cy)
            print("  PDP open (Change qty / In Cart / stepper)")
            return True
        time.sleep(0.25)
    print("  PDP bar not seen — retry tap on title")
    _tap_center(driver, cx, max(cy - 10.0, y + 8.0))
    time.sleep(_SLOW)
    ok = _on_product_detail(driver)
    if ok:
        markers.mark("cart_line_pdp_opened", label=label[:80], x=cx, y=cy, retry=True)
        print("  PDP open on retry")
    else:
        print("  PDP did not open")
    return ok


def _tap_pdp_plus(driver) -> tuple[float, float]:
    """Click stepper + if possible; else coord-tap."""
    el = _find_pdp_increment_el(driver)
    if el is not None:
        try:
            r = _safe_rect(el) or {}
            cx = float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0
            cy = float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0
            el.click()
            return cx, cy
        except Exception:  # noqa: BLE001
            pass
    px, py = _find_pdp_bottom_plus(driver)
    _tap_center(driver, px, py)
    return px, py


def _tap_pdp_minus(driver) -> tuple[float, float] | None:
    """Tap stepper − to correct an overshoot past target qty."""
    try:
        size = driver.get_window_size() or {}
        sw = float(size.get("width") or 390)
        sh = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        sw, sh = 390.0, 844.0
    bar = _pdp_action_bar_rect(driver)
    bar_y = (bar[1] + bar[3] / 2.0) if bar else sh - 42.0
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label CONTAINS[c] "Decrement quantity" OR '
            'name CONTAINS[c] "Decrement quantity" OR '
            'label == "−" OR label == "-" OR name == "-")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:12]:
        try:
            r = _safe_rect(el)
            if not r:
                continue
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            cy = y + float(r.get("height", 0)) / 2.0
            if abs(cy - bar_y) > 55 or x > sw * 0.45:
                continue
            cx = x + float(r.get("width", 0)) / 2.0
            el.click()
            return cx, cy
        except Exception:  # noqa: BLE001
            continue
    # Coord: − is left side of grey pill.
    cx, cy = sw * 0.18, bar_y
    _tap_center(driver, cx, cy)
    return cx, cy


def _raise_qty_on_pdp(
    driver,
    markers: MarkerLog,
    *,
    target_qty: int,
    line_index: int,
    label: str,
    pdp_confirmed: bool = False,
    info: dict | None = None,
    start_hint: int | None = None,
) -> int:
    """Rapid-tap stepper + to target (exactly), then 'Change qty'.

    ``pdp_confirmed``: caller just saw the PDP (skip the re-check).
    ``info``: filled with start qty (+ whether it was read or assumed) and the
    final PDP read-back, for the per-line qty evidence.
    """
    if info is None:
        info = {}
    target_qty = max(1, int(target_qty))
    if not pdp_confirmed and not _on_product_detail(driver):
        print(f"  line {line_index}: not on PDP — abort qty raise")
        return 0

    time.sleep(0.12)
    # Resolve stepper + once: used for the start read and the tap target.
    plus_el = _find_pdp_increment_el(driver)
    start = _read_pdp_qty(driver, inc_el=plus_el)
    if start is not None:
        info["start_source"] = "pdp_read"
    elif start_hint is not None:
        # Direct-cart fallback: qty inferred from the cart row (not assumed).
        info["start_source"] = "cart_inferred"
        start = int(start_hint)
    else:
        info["start_source"] = "assumed_1"
        start = 1
    if start > target_qty:
        print(
            f"  line {line_index} PDP: qty {start} > {target_qty} — "
            "decrementing back"
        )
        for _ in range(start - target_qty + 2):
            cur = _read_pdp_qty(driver)
            if cur is not None and cur <= target_qty:
                break
            _tap_pdp_minus(driver)
            time.sleep(0.10)
        start = _read_pdp_qty(driver) or target_qty
        plus_el = _find_pdp_increment_el(driver)  # stepper re-rendered by − taps
        info["start_source"] = "after_decrement"
    info["start"] = start

    print(
        f"  line {line_index} PDP: rapid raise {start}→{target_qty} ct "
        f"{label[:40]!r}"
    )
    if start >= target_qty:
        _tap_change_qty(driver, markers)
        info["final"] = start if info["start_source"] != "assumed_1" else None
        return 0

    max_plus = target_qty - start
    # Reuse + resolved above, then rapid-fire coord taps (much faster than re-query).
    if plus_el is not None:
        r = _safe_rect(plus_el) or {}
        px = float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0
        py = float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0
    else:
        px, py = _find_pdp_bottom_plus(driver)
    print(f"    + @ ({px:.0f},{py:.0f}) — {max_plus} fast taps")

    for i in range(max_plus):
        _tap_center(driver, px, py)
        # Very short gap so Instacart registers each tap without lag.
        time.sleep(0.07)
        if (i + 1) % 3 == 0:
            markers.mark(
                "pdp_qty_plus_burst",
                line=line_index,
                taps=i + 1,
                of=max_plus,
            )

    time.sleep(0.15)
    # Opt-in diagnostic (adds one page_source, ~4 s): dump the PDP stepper tree
    # once so _read_pdp_qty can be fixed — it returns None on current builds.
    dump_path = os.environ.get("BASKET_D_PDP_DUMP")
    if dump_path and line_index == 1:
        try:
            with open(dump_path, "w", encoding="utf-8") as fh:
                fh.write(driver.page_source or "")
            print(f"  PDP stepper dump → {dump_path}")
        except Exception as exc:  # noqa: BLE001
            print(f"  PDP dump failed: {exc}")
    final = _read_pdp_qty(driver)
    if final is not None and final > target_qty:
        print(f"  overshoot {final}→{target_qty} — fast decrement")
        for _ in range(final - target_qty + 2):
            q = _read_pdp_qty(driver)
            if q is None or q <= target_qty:
                break
            _tap_pdp_minus(driver)
            time.sleep(0.08)
        final = _read_pdp_qty(driver)
    elif final is not None and final < target_qty:
        # Top up a few times if some taps missed.
        miss = target_qty - final
        print(f"  short {final} — +{miss} more")
        for _ in range(miss):
            _tap_center(driver, px, py)
            time.sleep(0.07)
        time.sleep(0.12)
        final = _read_pdp_qty(driver)
        if final is not None and final > target_qty:
            for _ in range(final - target_qty + 2):
                q = _read_pdp_qty(driver)
                if q is None or q <= target_qty:
                    break
                _tap_pdp_minus(driver)
                time.sleep(0.08)
            final = _read_pdp_qty(driver)

    _tap_change_qty(driver, markers)
    info["final"] = final
    print(
        f"  line {line_index} PDP done: qty={final!r} "
        f"(target {target_qty}, +taps={max_plus})"
    )
    markers.mark(
        "pdp_qty_raise_done",
        line=line_index,
        start=start,
        final=final,
        target=target_qty,
        taps=max_plus,
    )
    return max_plus


def _raise_qty_on_cart_line_plus(
    driver,
    markers: MarkerLog,
    *,
    x: float,
    y: float,
    w: float,
    h: float,
    label: str,
    target_qty: int,
    line_index: int,
    safe_bottom: float | None = None,
) -> int:
    """Fallback: tap cart-row qty + at most (target-1) times — never past 10.

    Never taps at/under the sticky footer (``safe_bottom``), and
    stops after the first tap that leaves the cart (run 20261009_111052 hit
    "Go to checkout" 5× from (343,746)).
    """
    target_qty = max(1, int(target_qty))
    px = x + w * 0.88
    py = _cart_row_plus_y(y, h)
    needed = max(0, target_qty - 1)
    if safe_bottom is not None and py > safe_bottom:
        print(
            f"  line {line_index} cart-pill fallback skipped — + @ "
            f"({px:.0f},{py:.0f}) is under the footer (safe ≤ {safe_bottom:.0f})"
        )
        markers.mark("cart_pill_fallback_blocked", line=line_index, y=py, safe=safe_bottom)
        return 0
    print(
        f"  line {line_index} cart-pill fallback: "
        f"1→{target_qty} (max {needed} +) {label[:40]!r} @ ({px:.0f},{py:.0f})"
    )
    ok = 0
    footer0 = _parse_footer_total(_read_cart_footer_label(driver)) or 0.0
    total = footer0
    for _ in range(needed):
        _tap_center(driver, px, py)
        time.sleep(0.32)
        if _on_product_detail(driver):
            return ok + _raise_qty_on_pdp(
                driver,
                markers,
                target_qty=target_qty,
                line_index=line_index,
                label=label,
            )
        if not _still_on_cart_view(driver):
            # Left the cart (PDP or checkout): go back once and stop — never
            # keep tapping into another screen.
            _dismiss_pdp_if_open(driver, markers)
            markers.mark("cart_pill_fallback_left_cart", line=line_index)
            print(f"  line {line_index} cart-pill fallback left the cart — stop")
            break
        footer = _parse_footer_total(_read_cart_footer_label(driver))
        if footer is not None and footer > total + 0.01:
            total = footer
            ok += 1
            markers.mark("cart_pill_qty_plus", line=line_index, success=ok)
    print(f"  line {line_index} cart-pill done: {ok}/{needed} + taps")
    return ok


def _cart_line_qty_evidence(
    *,
    line: int,
    label: str,
    price_before: float | None,
    price_after: float | None,
    target_qty: int,
    start_qty: int | None,
    start_source: str,
    pdp_final: int | None,
) -> dict:
    """Classify one cart line's qty: ui_confirmed | price_inferred | unverified.

    * ui_confirmed  — PDP stepper read-back equals target (direct UI reading).
    * price_inferred — NOT a direct reading: the CartV4 row value is a line
      total, so qty ≈ price_after / (price_before / start_qty). Accepted when
      that is within 2 % of target. With an assumed start of 1 this also
      checks the assumption (a different start cannot give exactly ×target
      with +(target−1) taps at a constant unit price).
    * unverified    — anything else; the note says why (row price missing,
      unchanged, non-integer qty ⇒ unit price changed, or a different qty).
    Never fails the run.
    """
    start = start_qty if start_qty and start_qty > 0 else 1
    ev: dict = {
        "line": line,
        "label": label[:60],
        "start_qty": start,
        "start_source": start_source,
        "pdp_final": pdp_final,
        "price_before": price_before,
        "price_after": price_after,
        "unit_price_before": None,
        "inferred_qty": None,
        "status": "unverified",
    }
    if pdp_final is not None and pdp_final == target_qty:
        ev["status"] = "ui_confirmed"
        ev["note"] = f"ui_confirmed — PDP stepper reads {pdp_final}"
        return ev
    if price_before is None or price_after is None or price_before <= 0:
        ev["note"] = (
            f"unverified — row price missing (before={price_before}, "
            f"after={price_after})"
        )
        return ev
    unit = price_before / start
    inferred = price_after / unit
    ev["unit_price_before"] = round(unit, 4)
    ev["inferred_qty"] = round(inferred, 2)
    if abs(price_after - price_before) < 0.005:
        ev["note"] = (
            f"unverified — row price unchanged ${price_after:.2f} "
            "(cart not refreshed, or row shows a unit price)"
        )
    elif abs(inferred - target_qty) <= 0.02 * target_qty:
        ev["status"] = "price_inferred"
        ev["note"] = (
            f"price_inferred qty {target_qty} — ${price_before:.2f} "
            f"@ start {start} ({start_source}) → ${price_after:.2f}"
        )
    elif abs(inferred - round(inferred)) > 0.1:
        ev["note"] = (
            f"unverified — implied qty {inferred:.2f} is not whole: unit "
            f"price likely changed (${price_before:.2f} @ {start} → "
            f"${price_after:.2f})"
        )
    else:
        ev["note"] = (
            f"unverified — price implies qty {round(inferred)} "
            f"(target {target_qty})"
        )
    return ev


# Cart rows under the sticky footer ("Saving $X on this order"
# banner + "Go to checkout") swallow taps. Observed: row tap y=628 opened,
# y=724 hit the banner, (343,746) hit "Go to checkout". 0.78 × height is the
# reference; a detected footer top can only make the limit stricter.
_CART_SAFE_FRAC = 0.78
_CART_FOOTER_MARGIN = 12.0
_MAX_OBSTRUCTED_SCROLLS = 3  # per row, then reported as unreachable


def _cart_row_tap_y(y: float, h: float) -> float:
    """Y of the PDP-open tap (same point as _open_cart_line_pdp)."""
    return y + h * 0.35


def _cart_row_plus_y(y: float, h: float) -> float:
    """Y of the cart-row + tap (same point as _raise_qty_on_cart_line_plus)."""
    return y + h * 0.55


def _cart_footer_top(driver, sh: float) -> float | None:
    """Top y of the sticky cart footer: 'Go to checkout' CTA / savings banner."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            '(type == "XCUIElementTypeButton" AND '
            '(label CONTAINS[c] "Go to checkout" OR name CONTAINS[c] "Go to checkout")) OR '
            '(type == "XCUIElementTypeStaticText" AND '
            '(label CONTAINS[c] "on this order" OR label BEGINSWITH[c] "Saving ")))',
        )
    except Exception:  # noqa: BLE001
        return None
    tops: list[float] = []
    for el in els[:6]:
        r = _safe_rect(el)
        if r and float(r.get("y", 0)) >= sh * 0.60:
            tops.append(float(r.get("y", 0)))
    return min(tops) if tops else None


def _cart_safe_bottom(driver, sh: float) -> tuple[float, float | None]:
    """(lowest safe tap y, detected footer top or None)."""
    top = _cart_footer_top(driver, sh)
    ref = sh * _CART_SAFE_FRAC
    return (min(ref, top - _CART_FOOTER_MARGIN) if top is not None else ref), top


# --- Direct cart-row stepper (QTY_METHOD == "cart") ---------------------------
# CartV4 rows are one accessible Button (name=title, value="$line total,…");
# the [trash|N ct|+] pill is NOT in the a11y tree, so + is tapped by geometry
# and qty is inferred from the row's line total (price_inferred, no OCR).
# Run 20261009_122909 (video, 1 fps): the "N ct" label updates ~1–1.5 s after
# a tap, but the row price / footer total come from the server ~3–5 s later,
# and the footer shows "•••" while updates are pending. Taps sent during a
# pending server reply were lost (SimplyGO 18 expected → 13); an 8-tap burst
# after a quiet cart all landed (Daiya 2→10). Corrections computed from the
# stale price overshot (11/12/13/15). Hence: one burst, then a settle gate,
# and only then infer / correct.
_CART_PLUS_INSET = 34.0           # + centre ≈ x 356 on 390 pt (frames + run)
_CART_ROW_MOVE_TOL = 4.0
_SETTLE_TIMEOUT_S = 15.0          # per settle gate
_SETTLE_MIN_SPAN_S = 1.5          # stable total must span at least this long …
_SETTLE_MIN_POLLS = 3             # … across at least this many observations
_SETTLE_POLL_S = 0.5
_SETTLE_NO_CHANGE_QUIET_S = 6.0   # unchanged total counts as settled only this long after the last tap
_SETTLE_POLL_LOG_MAX = 40
# W3C burst timing. Daiya (run 20261009_131036): the count was overwritten
# right after the burst's longest gap (0.79 s between taps 4 and 5) — the app
# appears to send its update once taps pause, then overwrite the local count
# with the reply. Keep every gap far below that; press ≪ iOS long-press 0.5 s.
_CART_W3C_PRESS_S = CART_W3C_PRESS_MS / 1000.0
_CART_W3C_GAP_S = CART_W3C_GAP_MS / 1000.0
# How the gap is expressed. "move" (default): a zero-distance pointerMove of
# the gap's duration to the same point with the finger lifted — honoured on
# device (run 142846: gesture 4.5–4.8 s, label steps ~0.3 s apart). "pause":
# a W3C pause — NOT honoured on this stack (runs 135727 / 141235: 173 and
# 250 ms both gave a ~0.5 s 1→10 ramp on video).
_CART_W3C_SPACING = CART_W3C_SPACING
_CART_W3C_SLOW_S = 3.0            # measured sequence longer than this is flagged


def _cart_row_plus_point(
    x: float, y: float, w: float, h: float, *, inset: float = _CART_PLUS_INSET
) -> tuple[float, float]:
    """Centre of the cart-row + (pill right edge, upper part of the row)."""
    return x + w - inset, y + h * 0.36


def _cart_multitap(driver, x: float, y: float, n: int) -> dict:
    """n distinct taps at (x, y) in ONE W3C touch sequence; returns timing.

    One zero-length move, then n × (down, press, up). Between taps either a
    W3C pause ("pause") or a zero-distance pointerMove of the gap duration to
    the same point with the finger lifted ("move"). Always n separate
    touch-down/up pairs at one coordinate — never a long press or a swipe.
    elapsed_s is the measured round-trip of the single perform() call.
    """
    from selenium.webdriver.common.actions import interaction
    from selenium.webdriver.common.actions.action_builder import ActionBuilder
    from selenium.webdriver.common.actions.pointer_input import PointerInput

    xi, yi = int(round(x)), int(round(y))
    gap_ms = int(round(_CART_W3C_GAP_S * 1000))
    finger = PointerInput(interaction.POINTER_TOUCH, "finger")
    actions = ActionBuilder(driver, mouse=finger, duration=0)
    pa = actions.pointer_action
    pa.move_to_location(xi, yi)
    for i in range(n):
        pa.pointer_down()
        pa.pause(_CART_W3C_PRESS_S)
        pa.pointer_up()
        if i + 1 < n:
            if _CART_W3C_SPACING == "move":
                finger.create_pointer_move(duration=gap_ms, x=xi, y=yi, origin="viewport")
            else:
                pa.pause(_CART_W3C_GAP_S)
    t0 = time.monotonic()
    err = None
    try:
        actions.perform()
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"[:200]
    return {
        "elapsed_s": round(time.monotonic() - t0, 2),
        "n": n,
        "press_ms": int(_CART_W3C_PRESS_S * 1000),
        "gap_ms": gap_ms,
        "spacing": _CART_W3C_SPACING,
        "touch_span_s": round(n * _CART_W3C_PRESS_S + (n - 1) * _CART_W3C_GAP_S, 2),
        "error": err,
    }


def _scan_cart_row(driver, key: str):
    """One page_source scan → (row or None, row price or None, rows, prices)."""
    prices: dict[str, float] = {}
    lines = _find_all_cart_line_buttons(driver, prices=prices)
    row = next((r for r in lines if _item_label_key(r[4]) == key), None)
    return row, (prices.get(row[4]) if row else None), lines, prices


def _step_units(delta: float, unit: float) -> int | None:
    """Price change as a whole number of unit steps, else None.

    Tight tolerance (1¢ or 3 % of a unit per step) so a quantity-dependent
    price (e.g. 10 % off from qty 3: step $1.35 vs $1.50) is NOT mistaken
    for a missed tap. Estimated-each items (±1¢ rounding) still pass.
    """
    if not unit or unit <= 0:
        return None
    j = int(round(delta / unit))
    tol = max(0.011, 0.03 * unit * max(1, abs(j)))
    return j if abs(delta - j * unit) <= tol else None


def _read_cart_footer_state(driver, sh: float) -> tuple[str | None, float | None]:
    """(raw label, total) of the bottom checkout bar, found by position.

    Not filtered on "checkout": while updates are pending the bar shows
    "•••" and its a11y label may differ — every label is logged.
    """
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label CONTAINS[c] "checkout" OR name CONTAINS[c] "checkout" OR label CONTAINS "$")',
        )
    except Exception:  # noqa: BLE001
        return None, None
    for el in els[:8]:
        r = _safe_rect(el)
        if not r or float(r.get("y", 0)) < sh * 0.82 or float(r.get("width", 0)) < 300:
            continue
        try:
            label = (el.get_attribute("label") or el.get_attribute("name") or "").strip()
        except Exception:  # noqa: BLE001
            continue
        return label, _parse_footer_total(label.replace("\n", " | "))
    return None, None


def _wait_cart_settled(
    driver,
    markers: MarkerLog,
    *,
    sh: float,
    pre_total: float | None,
    last_tap_t: float,
    line_index: int,
    phase: str,
    unit: float | None = None,
    expected_steps: int | None = None,
    quiet_s: float = _SETTLE_NO_CHANGE_QUIET_S,
    timeout_s: float = _SETTLE_TIMEOUT_S,
) -> dict:
    """Wait until no cart quantity update can still be pending.

    Settled only when ALL hold:
      * the bar is not loading (label contains "Go to checkout"; logged so the
        live run shows whether "•••" is visible in a11y at all);
      * the total is identical in ≥ _SETTLE_MIN_POLLS reads spanning
        ≥ _SETTLE_MIN_SPAN_S;
      * EITHER the total moved by exactly the expected number of unit steps
        (every tap applied), OR ≥ _SETTLE_NO_CHANGE_QUIET_S passed since the
        last tap (covers dropped taps, overshoot and "nothing landed").
    A part-way plateau while taps are still queued therefore cannot pass
    early. Bounded by _SETTLE_TIMEOUT_S; the caller re-confirms the total
    after its row scan.
    """
    t0 = time.monotonic()
    polls: list[list] = []
    stable: list[tuple[float, float]] = []
    labels_seen: set[str] = set()
    result = {"settled": False, "waited_s": 0.0, "total": None, "reason": "timeout", "polls": polls}
    while True:
        now = time.monotonic()
        label, total = _read_cart_footer_state(driver, sh)
        t_rel = round(now - t0, 2)
        if len(polls) < _SETTLE_POLL_LOG_MAX:
            polls.append([t_rel, (label or "")[:40], total])
        if label:
            labels_seen.add(label.split("\n")[0][:30])
        loading = not (label and "go to checkout" in label.lower())
        if total is None or loading:
            stable = []
        elif stable and abs(total - stable[-1][1]) > 0.005:
            stable = [(now, total)]
        else:
            stable.append((now, total))
        at_target = (
            pre_total is not None and total is not None and unit and expected_steps is not None
            and _step_units(total - pre_total, unit) == expected_steps
        )
        quiet = (now - last_tap_t) >= quiet_s
        span = (stable[-1][0] - stable[0][0]) if len(stable) >= 2 else 0.0
        if len(stable) >= _SETTLE_MIN_POLLS and span >= _SETTLE_MIN_SPAN_S and (at_target or quiet):
            result.update(settled=True, total=total, reason="at_target_and_stable" if at_target else "quiet_and_stable")
            break
        if now - t0 >= timeout_s:
            result.update(total=total, reason="timeout")
            break
        time.sleep(_SETTLE_POLL_S)
    result["waited_s"] = round(time.monotonic() - t0, 1)
    result["labels_seen"] = sorted(labels_seen)
    markers.mark(
        "cart_settle", line=line_index, phase=phase, settled=result["settled"],
        reason=result["reason"], waited_s=result["waited_s"], total=result["total"],
        polls=len(polls),
    )
    print(
        f"    settle ({phase}): {result['reason']} after {result['waited_s']}s, "
        f"total={result['total']}, labels={result['labels_seen']}"
    )
    return result


def _raise_qty_on_cart_row_direct(
    driver,
    markers: MarkerLog,
    *,
    key: str,
    row: tuple[float, float, float, float, str],
    price0: float | None,
    first_seen_price: float | None,
    target_qty: int,
    line_index: int,
    sh: float,
    safe_hint: float | None = None,
    cart_settled: bool = True,
    prev_last_tap_t: float = 0.0,
) -> dict:
    """Raise one UNTOUCHED cart row from 1 to target_qty with its own + (no PDP).

    1. Burst: (target − 1) taps at the + (footer guard first).
    2. Settle gate (no qty reads before it), one row scan, footer re-read.
    3. ok ONLY when the footer total moved by exactly the burst's units and
       held — the one state with no update left in flight. Everything else
       is unverified and left alone: no correction taps, no "−", no PDP
       fallback (a long server pause is indistinguishable from "settled").
    Status: ok | unverified | obstructed | unsettled | not_untouched |
    left_cart | row_missing.
    """
    t0 = time.monotonic()
    x, y, w, h, label = row
    res: dict = {
        "method": "cart_direct", "status": "unverified", "taps": 0,
        "corrections": 0, "row_moves": 0, "settle_s": 0.0, "settles": [],
        "q_start": None, "q_after_burst": None, "q_final": None, "unit": None,
        "price_before": price0, "price_after": price0, "note": "",
        "fallback_ok": False, "q_now": None, "lines": None, "prices": None,
        "settled": False,
    }

    def _done(status: str, note: str) -> dict:
        res["status"], res["note"] = status, note
        res["elapsed_s"] = round(time.monotonic() - t0, 1)
        markers.mark(
            "cart_direct_qty_done", line=line_index, status=status,
            q_after_burst=res["q_after_burst"], q_final=res["q_final"], taps=res["taps"],
            corrections=res["corrections"], settle_s=res["settle_s"],
        )
        print(f"  line {line_index} direct-cart: {status} — {note}")
        return res

    def _relocate(new_row) -> None:
        nonlocal x, y, w, h
        if abs(new_row[1] - y) > _CART_ROW_MOVE_TOL:
            res["row_moves"] += 1
            markers.mark("cart_direct_row_moved", line=line_index, from_y=y, to_y=new_row[1])
            print(f"    row moved y {y:.0f} → {new_row[1]:.0f} — refreshed coordinates")
        x, y, w, h = new_row[0], new_row[1], new_row[2], new_row[3]

    def _settle(phase: str, pre_total: float | None, last_tap_t: float, steps: int):
        """Settle gate + row scan + footer re-confirm. Returns (ok, row, price).

        If the total moves during the row scan, updates were still pending:
        wait again (up to 3 gates, each bounded by _SETTLE_TIMEOUT_S).
        """
        for attempt in range(3):
            st = _wait_cart_settled(
                driver, markers, sh=sh, pre_total=pre_total, last_tap_t=last_tap_t,
                line_index=line_index, phase=phase if attempt == 0 else f"{phase}-retry",
                unit=unit, expected_steps=steps,
            )
            res["settle_s"] = round(res["settle_s"] + st["waited_s"], 1)
            res["settles"].append({k: st[k] for k in ("reason", "waited_s", "total", "labels_seen", "polls")})
            if not st["settled"]:
                return False, None, None, st["reason"]
            row2, p2, lines, prices = _scan_cart_row(driver, key)
            _lab, total_after = _read_cart_footer_state(driver, sh)
            if total_after is not None and st["total"] is not None and abs(total_after - st["total"]) <= 0.005:
                res["lines"], res["prices"] = lines, prices
                return True, row2, p2, st["reason"]
            res["settles"][-1]["reconfirm_moved_to"] = total_after
            print(f"    total moved during the row scan ({st['total']}→{total_after}) — still pending, waiting again")
        return False, None, None, "timeout"

    def _row_gone() -> dict:
        # Row not in a settled scan: find out where we are; never keep tapping.
        if _on_product_detail(driver):
            _close_pdp_to_cart(driver, markers)
            return _done("left_cart", "+ tap opened the product page — closed it, line left alone")
        if not _still_on_cart_view(driver):
            _dismiss_pdp_if_open(driver, markers)
            return _done("left_cart", "+ tap left the cart — went back once, stopped")
        return _done("row_missing", "row vanished from the cart after a tap — stopped")

    if price0 is None:
        return _done("unverified", "row price unreadable — not touched")
    if first_seen_price is None or abs(price0 - first_seen_price) > 0.005:
        return _done(
            "not_untouched",
            f"row price ${price0} differs from first seen ${first_seen_price} — "
            "start qty unknown, not touched",
        )
    unit = price0          # untouched row, added once this run → qty 1
    res["unit"], res["q_start"] = round(unit, 4), 1
    q = 1
    promo0 = _parse_row_promo(_ROW_VALUE_CACHE.get(label, ""))
    if promo0["qty_dependent"]:
        res["promotion"] = {"text": promo0["promo_text"], "current_qty1": promo0["current"],
                            "pre_promo_qty1": promo0["pre_promo"]}
        markers.mark("cart_promotion_detected", line=line_index, label=label[:60],
                     promo=promo0["promo_text"], current=promo0["current"],
                     pre_promo=promo0["pre_promo"])
        print(f"    promotion on {label[:40]!r}: {promo0['promo_text']!r} "
              f"(current ${promo0['current']}, before promotion ${promo0['pre_promo']})")
        if not promo0["pre_promo"] or promo0["pre_promo"] <= 0:
            # No independent qty signal: do not change the quantity at all.
            return _done("manual_verification",
                         f"promotion {promo0['promo_text']!r} and no pre-promotion price at "
                         "qty 1 — quantity cannot be verified programmatically; not tapped")
    if not cart_settled:
        # An earlier line may still have updates in flight: wait (quiet rule)
        # before sending any tap, and re-read this row from a settled scan.
        _lab, cur_total = _read_cart_footer_state(driver, sh)
        st = _wait_cart_settled(
            driver, markers, sh=sh, pre_total=cur_total, last_tap_t=prev_last_tap_t,
            line_index=line_index, phase="pre",
        )
        res["settle_s"] = st["waited_s"]
        res["settles"].append({k: st[k] for k in ("reason", "waited_s", "total", "labels_seen", "polls")})
        if not st["settled"]:
            return _done("unsettled", "cart still updating from an earlier line — not touched")
        row2, p2, _lines, _prices = _scan_cart_row(driver, key)
        if row2 is None or p2 is None or abs(p2 - price0) > 0.005:
            return _done("not_untouched", "row changed while the cart settled — not touched")
        _relocate(row2)

    # One burst; success ONLY on a confidently settled cart (see below).
    need = target_qty - q
    safe = safe_hint if safe_hint is not None else _cart_safe_bottom(driver, sh)[0]
    tx, ty = _cart_row_plus_point(x, y, w, h)
    if ty > safe:
        return _done("obstructed", f"+ y={ty:.0f} under footer (safe ≤ {safe:.0f})")
    _lab0, pre_total = _read_cart_footer_state(driver, sh)
    res["tap_mode"], res["tap_x"], res["tap_y"] = CART_TAP_MODE, round(tx), round(ty)
    t_taps = time.monotonic()
    if CART_TAP_MODE == "w3c":
        mt = _cart_multitap(driver, tx, ty, need)
        res["taps"] += need
        res["multitap"] = mt
        markers.mark("cart_direct_multitap", line=line_index, x=round(tx), y=round(ty), **mt)
        print(f"    W3C burst: {need} taps @ ({tx:.0f},{ty:.0f}) in {mt['elapsed_s']}s"
              + (f" — ERROR {mt['error']}" if mt["error"] else ""))
    else:
        mt = None
        for _ in range(need):
            _tap_center(driver, tx, ty)
            res["taps"] += 1
            time.sleep(0.07)
    res["tap_duration_s"] = round(time.monotonic() - t_taps, 2)
    last_tap_t = time.monotonic()
    res["t_taps_start"] = t_taps
    res["last_tap_t"] = last_tap_t
    ok_settle, row2, p2, reason = _settle("burst", pre_total, last_tap_t, need)
    res["settle_reason"] = reason
    # Gesture start → cart confirmed settled (incl. row scan + footer re-check).
    res["confirm_latency_s"] = round(time.monotonic() - t_taps, 2)
    if not ok_settle:
        # A timeout is never treated as settled.
        return _done("unsettled", f"cart did not settle (waited {res['settle_s']}s) — "
                                  "left alone: no corrections, no PDP fallback")
    if row2 is None:
        return _row_gone()
    _relocate(row2)
    res["price_after"] = p2
    steps = _step_units((p2 or 0.0) - price0, unit) if p2 is not None else None
    res["q_after_burst"] = (1 + steps) if steps is not None else None
    # The ONLY confidently settled state: the footer total moved by exactly the
    # burst's own units and held (no update can still be queued). Any other
    # outcome (short, over, unchanged, non-whole) may be a long server pause
    # that looks settled — indistinguishable from the footer/price — so the
    # line is left alone: no correction taps and no PDP fallback.
    if promo0["qty_dependent"]:
        return _verify_promo_line(
            driver, markers, res, _done, _row_gone, key=key, label=label, need=need,
            target_qty=target_qty, promo0=promo0, footer_total=None, sh=sh, mt=mt,
            line_index=line_index,
        )
    confident = reason == "at_target_and_stable"
    if mt is not None and mt.get("error"):
        res["settled"] = confident
        return _done("unverified", f"W3C tap sequence raised {mt['error']} — taps sent "
                                   "uncertain; line left alone")
    res["settled"] = confident
    res["increments_accounted"] = bool(confident and steps == need)
    res["loading_label_seen"] = any(
        "go to checkout" not in (lab or "").lower()
        for st_ in res["settles"] for lab in (st_.get("labels_seen") or [])
    )
    if confident and steps == need:
        res["q_final"] = target_qty
        return _done("ok", f"qty 1→{target_qty} in {res['taps']} taps, settle {res['settle_s']}s "
                           "(total moved by exactly the burst)")
    if steps is None:
        why = (f"settled price ${price0:.2f}→${p2} is not a whole number of ${unit:.2f} "
               "units (price may depend on qty)")
    elif steps == 0:
        why = "no increment observed"
    else:
        why = f"inferred qty {1 + steps} after a {need}-tap burst"
    note = (f"{why}; settle was '{reason}', not confirmed — cannot rule out pending updates, "
            "so no correction and no PDP fallback")
    print(f"    settled qty {res['q_after_burst']} (${p2}) — {why}")
    return _done("unverified", note)


def _verify_promo_line(driver, markers, res, _done, _row_gone, *, key, label, need,
                       target_qty, promo0, footer_total, sh, mt, line_index) -> dict:
    """Verify a qty-dependent-promotion row by its UNDISCOUNTED line total.

    "Price before promotion" = list price × qty, independent of the deal, so
    it must have moved by exactly `need` list-price units. That means all
    `need` taps were applied and nothing more can still be queued (only
    `need` were sent). Confirmed again by a re-scan ≥1.5 s later (same
    pre-promotion total, same footer). Anything else → manual checkpoint.
    """
    if mt is not None and mt.get("error"):
        return _done("manual_verification", f"W3C tap sequence raised {mt['error']} on a "
                                            "promotional row — manual check required")
    unit_list = promo0["pre_promo"]
    info1 = _parse_row_promo(_ROW_VALUE_CACHE.get(label, ""))
    steps1 = _step_units((info1["pre_promo"] or 0.0) - unit_list, unit_list) if info1["pre_promo"] else None
    res["promotion"].update(pre_promo_after=info1["pre_promo"], current_after=info1["current"],
                            promo_text_after=info1["promo_text"], pre_promo_steps=steps1)
    res["q_after_burst"] = (1 + steps1) if steps1 is not None else None
    if steps1 != need:
        res["settled"] = False
        return _done("manual_verification",
                     f"promotion {promo0['promo_text']!r}: pre-promotion total "
                     f"${unit_list:.2f}→${info1['pre_promo']} is not exactly {target_qty} "
                     "units — quantity not verified, manual check required")
    # Stability re-check of the independent signal.
    _lab, total_a = _read_cart_footer_state(driver, sh)
    time.sleep(1.5)
    row3, _p3, lines, prices = _scan_cart_row(driver, key)
    _lab, total_b = _read_cart_footer_state(driver, sh)
    if row3 is None:
        return _row_gone()
    info2 = _parse_row_promo(_ROW_VALUE_CACHE.get(label, ""))
    stable = (info2["pre_promo"] is not None and abs(info2["pre_promo"] - info1["pre_promo"]) < 0.005
              and total_a is not None and total_b is not None and abs(total_a - total_b) < 0.005)
    res["lines"], res["prices"] = lines, prices
    res["promotion"]["recheck_stable"] = stable
    if not stable:
        res["settled"] = False
        return _done("manual_verification",
                     f"promotion {promo0['promo_text']!r}: pre-promotion total or footer changed "
                     "on re-check — quantity not verified, manual check required")
    res["settled"] = True
    res["increments_accounted"] = True
    res["q_final"] = target_qty
    res["verified_by"] = "pre_promotion_line_total"
    res["price_after"] = info2["current"]
    markers.mark("cart_promotion_verified", line=line_index, label=label[:60],
                 pre_promo_before=unit_list, pre_promo_after=info2["pre_promo"],
                 current_after=info2["current"], promo=promo0["promo_text"])
    return _done("ok", f"promotion {promo0['promo_text']!r}: pre-promotion total "
                       f"${unit_list:.2f}→${info2['pre_promo']:.2f} = exactly {target_qty} units "
                       f"(discounted ${info2['current']}); re-check stable")


_NAME_NORM_SIZE_RE = re.compile(
    r"\([^)]*\)|,?\s*\d+(?:\.\d+)?\s*(?:fl\s*oz|oz|lb|lbs|ct|count|g|kg|ml|l|pk|pack|gal)\b\.?", re.I)


def _product_name_norm(label: str) -> str:
    raw = (label or "").lower().split("\n", 1)[0]
    raw = _NAME_NORM_SIZE_RE.sub(" ", raw)
    return re.sub(r"[^0-9a-z™®&']+", " ", raw).strip()


_CART_LABEL_CUT = 100   # _cart_lines_from_page_source keeps (name or value)[:100]
_MAX_CART_BACK_STEPS = 12      # Basket D: total step-backs per cart read (bounded)
_MAX_CART_BACK_STEPS_GAP = 4   # Basket D: step-backs to close ONE gap (reset once rows connect)
_CART_BACK_FRAC = 0.20         # step-back drag size (not halved: an up-drag moves less than a fling)


def _d_same_listing(a: str, b: str) -> bool:
    """Basket D add-time duplicate check (unchanged matcher): store label vs label ("X" vs "X (4 oz)"): the shorter normalised
    name must match the other completely (never a mere shared prefix)."""
    na, nb = _product_name_norm(a), _product_name_norm(b)
    if not na or not nb:
        return False
    if len(na) < 8 or len(nb) < 8:
        # Short names ("Limes (1 each)" vs "Limes", run 20261013_064355): only an
        # exact normalised match counts — never a prefix.
        return na == nb
    short, long_ = sorted((na, nb), key=len)
    if long_ == short or long_.startswith(short + " "):
        return True
    # Cart row name cut at 100 chars mid-word ("… Kosher, No Preserv", run
    # 20261013_071149): the cut text must be a prefix of the full store name.
    if len(a) >= _CART_LABEL_CUT and short == na and len(na) >= 60:
        return long_.startswith(na)
    return False


def _d_name_norm(text: str) -> str:
    t = (text or "").replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    t = t.replace("\u00a0", " ").replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", t).strip(" ,.").lower()



def _d_cart_name(cart_label: str) -> str:
    """Cart row name without its trailing "(size)"."""
    return re.sub(r"\s*\([^()]*\)\s*$", "", cart_label or "")



def _d_same_product(store_label: str, cart_label: str) -> bool:
    """Store card ↔ cart row: the SAME product name (strict).

    Names are compared whole after normalising case, quotes and spacing:
    store "Red Bull Sugar Free Energy Drink" (tag "Low calorie" already
    removed by _card_display_name) ↔ cart "Red Bull Sugar Free Energy Drink
    (12 fl oz)". No prefix matching — "Coke" never matches "Coke Zero Sugar".
    A store name longer than the cart name matches only when the extra words
    are storefront tags.
    """
    a = _d_name_norm(_card_display_name(store_label))
    b = _d_name_norm(_d_cart_name(cart_label))
    if not a or not b:
        return False
    if a == b:
        return True
    if a.startswith(b) and a[len(b):len(b) + 1] in (" ", ","):
        rest = a[len(b):].strip(" ,")
        return bool(rest) and _d_strip_trailing_tags(a) == b
    return False



def _d_raw_head(raw: str) -> str:
    """Front text of a store card: everything before "Current price:" (the
    full name, then the card's tags/description)."""
    m = re.search(r"\s*Current price:", raw or "", re.I)
    return (raw or "")[: m.start()] if m else ""



def _d_size_sig(text: str | None) -> tuple | None:
    """Numbers of a package size ("12 x 12 fl oz" → (12, 12); "0.75 L" → (750,)),
    ignoring wording ("ct"/"each", "fl oz"/"oz") and the card's "N sizes" note."""
    t = re.sub(r"\b\d+\s+sizes?\b", " ", (text or "").lower())
    t = re.sub(r"(\d+(?:\.\d+)?)\s*l\b", lambda mm: f"{float(mm.group(1)) * 1000:g} ml", t)
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t)]
    return tuple(sorted(round(n, 3) for n in nums)) or None



def _d_sizes_conflict(cart_label: str, raw: str | None) -> bool:
    """True when the cart row's "(size)" and the store card's size line both
    exist and their numbers differ — a different package of the product."""
    mc = re.search(r"\(([^()]*)\)\s*$", cart_label or "")
    mr = re.search(r"Current price:[^\n]*\n([^\n]+)", raw or "")
    if not mc or not mr:
        return False
    a, b = _d_size_sig(mc.group(1)), _d_size_sig(mr.group(1))
    if not (a and b):
        return False
    # Basket D: a multipack card ("64 x 8 in", "4 x 5.3 oz") vs a cart row that
    # shows only the unit size ("(8 in)", "(5.3 oz)") — same product when one
    # set of numbers is contained in the other; 12 vs 24 pack still conflicts.
    def _within(x: tuple, y: tuple) -> bool:
        rest = list(y)
        for v in x:
            if v not in rest:
                return False
            rest.remove(v)
        return True
    return not (_within(a, b) or _within(b, a))



def _same_product(a: str, b: str, raw: str | None = None) -> bool:
    """Cart label `a` vs store name `b` (and its raw card label): SAME product?

    1. Strict: whole names equal after the card's tags are removed — never a
       mere shared prefix ("Clear Carton Sealing Tape" ≠ "… Dispenser Gun").
    2. Only if 1 fails and the raw card label of the SAME card is known: the
       card's repeated name can be shortened ("… Turkey Sausage" while the
       card front and the cart say "… Turkey Sausage Patties, 26 Count",
       Basket C run 20261015_082600). Accepted only when ALL hold:
         - the raw label's cleaned name is the stored name (same card);
         - the store name (cut back to whole words) has ≥ 4 words and ≥ 20
           characters and is a word-boundary prefix of the cart name;
         - the cart name is a word-boundary prefix of the card's own front
           text (the full name the card shows before its price);
         - the cart name is longer than the store name.
       A variant name (Coke ≠ Coke Zero Sugar, 6 Pack ≠ 12 Pack, another
       flavour) fails the card-front check; anything uncertain → False.
    0. With the raw card label: the cart row's "(size)" and the card's size
       line must not disagree in their numbers (veto for every path).
    """
    if raw and _d_sizes_conflict(a, raw):
        return False                       # same name, different package size
    # Basket D: the stored name as-is (never re-cleaned: "… Certified Non-GMO"
    # must not lose "Non-GMO") equal to the cart name.
    if _d_name_norm(b) and _d_name_norm(b) == _d_name_norm(_d_cart_name(a)):
        return True
    if _d_same_product(b, a):
        return True
    # The cart reader keeps only the first _CART_LABEL_CUT characters of a row
    # label: a cut cart name matches when the store name starts with it (and
    # the cut part is long enough to be specific).
    if len(a or "") >= _CART_LABEL_CUT:
        ca = _d_name_norm(a)
        sb = _d_name_norm(_card_display_name(b))
        if len(ca) >= 60 and sb.startswith(ca):
            return True
    if not raw:
        return False
    head = _d_name_norm(_d_raw_head(raw))
    store = _d_name_norm(b)               # the stored name, as cleaned at the add
    cart = _d_name_norm(_d_cart_name(a))
    if not head or not store or not cart:
        return False
    if _d_name_norm(_card_display_name(raw)) != store:
        return False                       # raw label is not this card
    if not head.startswith(store):
        return False                       # card front does not start with the name
    if len(head) > len(store) and head[len(store)].isalnum():
        # The repeated name was cut mid-word ("… 60 Coun"): keep whole words.
        store = store.rsplit(" ", 1)[0].rstrip(" ,") if " " in store else ""
    if len(store) < 20 or len(store.split()) < 4:
        return False                       # never a short common prefix
    if not (len(cart) > len(store) and cart.startswith(store) and not cart[len(store)].isalnum()):
        return False
    if not (head.startswith(cart) and (len(head) == len(cart) or not head[len(cart)].isalnum())):
        return False                       # cart name not shown on this card
    return True



def _scroll_cart_list_small(driver, *, up: bool = True, frac: float = 0.20) -> None:
    """Short cart drag for the read-only check. The 0.30 h drag of
    _scroll_cart_list can fling past a whole screen of rows (run
    20261013_071149 skipped one row); 0.20 h + the overlap check in
    _verify_cart_qty_one keeps consecutive views overlapping (frac is
    halved there after a detected jump)."""
    try:
        size = driver.get_window_size() or {}
        sw = float(size.get("width") or 390)
        sh = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        sw, sh = 390.0, 844.0
    fy, ty = (sh * 0.62, sh * (0.62 - frac)) if up else (sh * (0.62 - frac), sh * 0.62)
    try:
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {"duration": 0.30, "fromX": int(sw * 0.50), "fromY": int(fy),
             "toX": int(sw * 0.50), "toY": int(ty)},
        )
    except Exception:  # noqa: BLE001
        pass


def _verify_cart_qty_one(driver, markers: MarkerLog, outcome: dict, *, num_items: int) -> None:
    """READ-ONLY: every product added once must be one cart row at 1 ct.

    Scrolls the cart (no taps) to read every row. Each row must match exactly
    one added product by full name; its line total must equal the price read
    on the store card (qty 1) — for a promotional row the "Price before
    promotion" may carry the undiscounted unit price instead (a discounted
    total alone never proves qty). Unavailable rows, unmatched rows, a row
    count other than num_items or any unconfirmed row stop before checkout.
    """
    print(f"Step: units_each=1 — no + / − taps; read-only check that all {num_items} rows are at 1 ct")
    added = list(outcome.get("items_added") or [])
    rows: dict[str, str] = {}
    order: list[str] = []
    prices: dict[str, float] = {}

    def _evaluate() -> tuple[list[dict], set[int]]:
        evidence: list[dict] = []
        used: set[int] = set()
        for label in order:
            value = rows[label]
            row_price = prices.get(label)
            state = _cart_row_state(value)
            promo = _parse_row_promo(value)
            cands = [i for i, a in enumerate(added) if i not in used
                     and _same_product(label, a.get("label") or "", raw=a.get("raw_label"))]
            ev = {"line": len(evidence) + 1, "label": label, "price_after": row_price, "method": "cart_row_price",
                  "row_state": state, "status": "unverified", "inferred_qty": None}
            if state == "replacement_required":
                ev["note"] = f"unavailable in the cart ({_unavailable_reason(value)!r})"
            elif len(cands) != 1:
                ev["note"] = f"row matches {len(cands)} added products — identity not confirmed"
            else:
                i = cands[0]
                used.add(i)
                want = added[i].get("price")
                ev["price_before"] = want
                by_row = want is not None and row_price is not None and abs(row_price - want) <= 0.01
                by_promo = (want is not None and promo.get("pre_promo") is not None
                            and abs(promo["pre_promo"] - want) <= 0.01)
                if by_row or by_promo:
                    ev.update(status="price_inferred", inferred_qty=1, note=(
                        f"qty 1 — row ${row_price} = store price ${want}" if by_row else
                        f"qty 1 — promotional row: pre-promotion ${promo['pre_promo']} = store price ${want} "
                        f"(shown ${row_price})"))
                else:
                    ev["note"] = f"row ${row_price} vs store price ${want} — qty 1 not confirmed"
            evidence.append(ev)
        return evidence, used

    # Early stop is allowed only with exact badge evidence: add k moved the
    # badge to exactly k (k-1 → k; the first add's "before" may be the promo
    # "$15 off" misread) and the badge read just before opening the cart is
    # exactly num_items. The badge counts UNITS, so num_items rows each proven
    # at 1 ct already account for every unit — any further row (stray,
    # duplicate, unavailable) would push the badge above num_items. It never
    # proves identity or qty by itself; those still come from the rows.
    afters = [a.get("basket_after") for a in added]
    befores = [a.get("basket_before") for a in added]
    badge_exact = (
        len(added) == num_items
        and outcome.get("badge_before_cart") == num_items
        and all(isinstance(v, int) and v == k + 1 for k, v in enumerate(afters))
        and all(isinstance(v, int) and v == k for k, v in enumerate(befores) if k >= 1)
        and len({_product_name_norm(a.get("label") or "") for a in added}) == num_items
    )
    print(f"  badge evidence: per-add {afters} · before cart {outcome.get('badge_before_cart')!r} → "
          f"{'early stop allowed' if badge_exact else 'full scan to the list end'}")
    reads = 0
    early = False
    frac = 0.20
    prev = None
    stalls = 0
    gap_steps = 0
    back_steps = 0
    after_empty_back = False
    for _ in range(3 * num_items + _MAX_CART_BACK_STEPS):   # Basket D: 25 rows, small steps (bounded)
        _ROW_VALUE_CACHE.clear()
        pr: dict[str, float] = {}
        lines = _find_all_cart_line_buttons(driver, prices=pr)
        reads += 1
        view = tuple(r[4] for r in lines)
        # Position signature: the same rows can stay in the band while the list
        # still moves (tall promo rows, 0.06 h steps) — only an unmoved list
        # means its end.
        sig = tuple((r[4], int(round(r[1]))) for r in lines)
        # Basket D (run 20261013_093332 missed 'Red Lobster…'): a new view counts
        # only if it contains a row ALREADY read, so coverage stays contiguous.
        # A view with no known row may have skipped rows: its rows are NOT
        # recorded; step back (bounded) with a halved step and re-read.
        # Up- and down-drags move the list by different amounts live, so the
        # check is against all rows read so far, not just the previous view.
        contiguous = (not rows) or any(lab in rows for lab in view)
        if view and not contiguous:
            if back_steps < _MAX_CART_BACK_STEPS and gap_steps < _MAX_CART_BACK_STEPS_GAP:
                back_steps += 1
                gap_steps += 1
                print(f"  cart read: view shares no row already read — stepping back "
                      f"(gap {gap_steps}/{_MAX_CART_BACK_STEPS_GAP}, total {back_steps}/{_MAX_CART_BACK_STEPS}), "
                      f"then {max(0.06, frac / 2):.2f} h steps")
                _scroll_cart_list_small(driver, up=False, frac=_CART_BACK_FRAC)
                frac = max(0.06, frac / 2)
                prev = None           # after a step back, an unchanged view is NOT the list end
                time.sleep(0.4)
                continue
            print("  cart read: gap could not be closed within the step-back budget — "
                  "recording this view; the row count decides")
        if not view and rows:
            # Empty view after rows were read: list end — or a fling past it.
            if not after_empty_back and back_steps < _MAX_CART_BACK_STEPS:
                back_steps += 1
                after_empty_back = True
                print(f"  cart read: empty view — stepping back (total {back_steps}/{_MAX_CART_BACK_STEPS})")
                _scroll_cart_list_small(driver, up=False, frac=_CART_BACK_FRAC)
                frac = max(0.06, frac / 2)
                prev = None
                time.sleep(0.4)
                continue
            break
        n_before = len(order)
        gap_steps = 0                 # rows connect again (or the gap budget is spent)
        for x, y, w, h, label in lines:
            if label not in rows:
                order.append(label)
            value = _ROW_VALUE_CACHE.get(label, "")
            rows[label] = value
            # Row price = the first field that IS a price ("Buy $20, get $3 off,
            # $5.99,…" → $5.99, run 20261013_093332), else the first $ amount.
            cur = _parse_row_promo(value).get("current")
            if cur is not None:
                prices[label] = cur
            elif label in pr:
                prices[label] = pr[label]
        if after_empty_back and len(order) == n_before:
            break                     # stepped back from an empty view, nothing new: list end
        after_empty_back = False
        if badge_exact and len(order) == num_items:
            ev_now, used_now = _evaluate()
            if len(used_now) == num_items and all(e["status"] == "price_inferred" for e in ev_now):
                early = True
                print(f"  cart read: all {num_items} rows proven at 1 ct and badge = {num_items} — "
                      f"stop scrolling after {reads} read(s)")
                break
        # Read to the END of the list: two FORWARD scrolls in a row that do not
        # move any row (one could be a dropped drag).
        if view and prev is not None and sig == prev:
            stalls += 1
            if stalls >= 2:
                break
        else:
            stalls = 0
        prev = sig
        _scroll_cart_list_small(driver, up=True, frac=frac)
        time.sleep(0.4)
    evidence, _used = _evaluate()
    outcome["cart_qty_scan"] = {"mode": "early_stop" if early else "full", "reads": reads,
                                "badge_exact": badge_exact, "back_steps": back_steps}
    counts = {"ui_confirmed": 0, "price_inferred": sum(e["status"] == "price_inferred" for e in evidence),
              "unverified": sum(e["status"] == "unverified" for e in evidence)}
    outcome["cart_qty_evidence"] = evidence
    outcome["cart_qty_line_counts"] = counts
    outcome["cart_qty_target"] = 1
    outcome["cart_lines_qty_raised"] = 0
    outcome["cart_qty_plus_taps"] = 0
    for ev in evidence:
        print(f"  line {ev['line']}: {ev['label'][:40]!r} — {ev['note']}")
    unavailable = [e for e in evidence if e["row_state"] == "replacement_required"]
    if unavailable:
        outcome["cart_qty_status"] = "unverified"
        outcome["cart_unavailable_item"] = {"product": unavailable[0]["label"][:80], "reason": unavailable[0]["note"]}
        raise ProductUnavailable(f"product_unavailable: {unavailable[0]['label'][:40]!r} {unavailable[0]['note']}")
    if len(order) != num_items or counts["unverified"]:
        outcome["cart_qty_status"] = "unverified"
        raise QuantityUnverified(
            f"quantity_unverified: cart shows {len(order)} row(s) (expected {num_items}); "
            f"{counts['unverified']} row(s) not confirmed at 1 ct")
    outcome["cart_qty_status"] = "price_inferred"
    outcome["cart_qty_inferred"] = 1
    markers.mark("cart_qty_verification", status="price_inferred", target=1, **counts)
    _mark_step(outcome, "cart_qty_increase", status="ok", qty_status="price_inferred",
               lines=len(order), units_each=1, method="cart_row_price", **counts)
    print(f"  qty check done: {counts['price_inferred']}/{num_items} rows confirmed at 1 ct (0 taps)")


# ---- Basket D: out-of-stock swap (approved 2026-10-13) ---------------------
# Before recording: an UNRECORDED read-only cart check. A row that the cart
# itself marks "Out of stock" / "Choose replacement" is removed with its trash
# icon, one replacement product is added from the store, and the cart is
# checked again — repeated until all rows are available (bounded by rounds / time). Then the cart is
# re-opened (top of list) and the validated RECORDED check runs as before.
# The [trash|1 ct|+] pill is not in the a11y tree (row = one Button), so the
# trash is tapped by geometry measured on the live cart (screenshot 15:08:
# trash x≈282, + x≈356 on 390 pt; pill centre = row top + (h − 25)/2 for
# 110 / 157 / 177 pt rows) — only after the row is re-identified by name and
# its "Out of stock" value at the same position, clear of header and footer.
# No fixed swap count: recovery repeats (remove every unavailable row →
# replace → re-check) until the 25 rows are all available, bounded by rounds
# and time. A stop on either bound is a clear product_unavailable failure.
_D_OOS_MAX_ROUNDS = 8           # full remove/replace/re-check cycles
_D_OOS_RECOVERY_S = 1200.0      # wall-clock budget for the whole recovery
_D_SETTLE_S = 0.8               # wait after a cart drag before trusting positions
_D_STABLE_READS = 5             # reads allowed for two consecutive agreeing positions
_D_TRASH_ATTEMPTS = 3           # re-locate attempts when the row moved before the tap
_D_TRASH_DX = 108.0      # trash centre = row right − 108 pt
_D_PILL_DY = 25.0        # pill centre = row top + (h − 25) / 2


def _d_trash_point(x: float, y: float, w: float, h: float) -> tuple[float, float]:
    return x + w - _D_TRASH_DX, y + (h - _D_PILL_DY) / 2.0


def _d_cart_rows(driver) -> list[tuple[tuple[float, float, float, float, str], str]]:
    _ROW_VALUE_CACHE.clear()
    lines = _find_all_cart_line_buttons(driver, prices={})
    return [(r, _ROW_VALUE_CACHE.get(r[4], "")) for r in lines]


def _d_stop(markers: MarkerLog, outcome: dict, reason: str, cls=None):
    cls = cls or QuantityUnverified
    if cls is UnexpectedScreen:
        outcome.setdefault("unexpected_screen", reason)
        markers.mark("d_unexpected_screen", reason=reason[:140])
        print(f"  UNEXPECTED SCREEN — {reason}")
    else:
        outcome.setdefault("oos_swap_stop", reason)
        markers.mark("d_oos_swap_stop", reason=reason[:140])
        print(f"  OUT-OF-STOCK SWAP STOPPED — {reason}")
    prefix = ("product_unavailable" if cls is ProductUnavailable
              else "unexpected_screen" if cls is UnexpectedScreen else "quantity_unverified")
    raise cls(f"{prefix}: {reason}")


def _d_stable_row(driver, label: str, hit):
    """Re-read until two consecutive reads show `label` at the same y (±tol);
    bounded by _D_STABLE_READS. Returns the newest (row, value) or None."""
    prev = hit
    for _ in range(_D_STABLE_READS):
        time.sleep(_D_SETTLE_S)
        cur = next(((r, v) for r, v in _d_cart_rows(driver) if r[4] == label), None)
        if cur is None:
            return None
        if abs(cur[0][1] - prev[0][1]) <= _CART_ROW_MOVE_TOL:
            return cur
        print(f"    row {label[:36]!r} still moving (y {prev[0][1]:.0f} → {cur[0][1]:.0f}) — reading again")
        prev = cur
    return None


def _d_locate_cart_row(driver, label: str, sh: float, *, max_steps: int = 30):
    """Bounded scroll until row `label` is on screen with its pill clear of the
    header (y ≥ 110) and the sticky footer. Searches up first (a full scan
    leaves the list at its end), then down. (row, value) or (None, "")."""
    for up in (False, True):            # up=False reveals upper rows
        last, stall = None, 0
        for _ in range(max_steps):
            rows = _d_cart_rows(driver)
            hit = next(((r, v) for r, v in rows if r[4] == label), None)
            if hit is not None:
                (x, y, w, h, _lab), val = hit
                _tx, ty = _d_trash_point(x, y, w, h)
                safe, _top = _cart_safe_bottom(driver, sh)
                if 110.0 <= ty <= safe:
                    # Basket D (run 20261015_092703: the list was still coasting
                    # after the drag): accept the position only when two
                    # consecutive reads agree within _CART_ROW_MOVE_TOL.
                    stable = _d_stable_row(driver, label, hit)
                    if stable is None:
                        return None, "unstable"
                    (_x2, y2, _w2, h2, _l2), _v2 = stable
                    _tx2, ty2 = _d_trash_point(_x2, y2, _w2, h2)
                    if 110.0 <= ty2 <= safe:
                        return stable
                    continue
                _scroll_cart_list_small(driver, up=(ty > safe), frac=0.08)
                time.sleep(_D_SETTLE_S)
                continue
            sig = tuple((r[4], int(round(r[1]))) for r, _v in rows)
            stall = stall + 1 if sig == last else 0
            if stall >= 2:
                break
            last = sig
            _scroll_cart_list_small(driver, up=up, frac=0.20)
            time.sleep(_D_SETTLE_S)
    return None, ""


def _d_remove_oos_row(driver, markers: MarkerLog, outcome: dict, label: str, sh: float) -> str:
    """Remove ONE out-of-stock cart row with its trash icon. "removed" |
    "back_in_stock"; anything uncertain stops the run (never a blind tap)."""
    for attempt in range(1, _D_TRASH_ATTEMPTS + 1):
        row, val = _d_locate_cart_row(driver, label, sh)
        if row is None:
            if val == "unstable":
                _d_stop(markers, outcome, f"row {label[:40]!r} position never stabilised after scrolling — not tapped")
            _d_stop(markers, outcome, f"out-of-stock row {label[:40]!r} not found again in the cart")
        if not _unavailable_reason(val):
            print(f"  {label[:40]!r} no longer shows out of stock — not removed")
            return "back_in_stock"
        x, y, w, h, _lab = row
        tx, ty = _d_trash_point(x, y, w, h)
        # Re-read immediately before the tap: same row, same place, still out of stock.
        rows2 = _d_cart_rows(driver)
        hit2 = next(((r, v) for r, v in rows2 if r[4] == label), None)
        if hit2 is not None and abs(hit2[0][1] - y) <= _CART_ROW_MOVE_TOL and _unavailable_reason(hit2[1]):
            break
        print(f"    row {label[:36]!r} moved/changed before the tap (attempt {attempt}/{_D_TRASH_ATTEMPTS}) — re-locating")
        markers.mark("d_oos_row_moved", label=label[:60], attempt=attempt)
    else:
        _d_stop(markers, outcome, f"row {label[:40]!r} moved or changed just before the trash tap "
                f"({_D_TRASH_ATTEMPTS} attempts) — not tapped")
    owners = [r[4] for r, _v in rows2 if r[0] <= tx <= r[0] + r[2] and r[1] <= ty <= r[1] + r[3]]
    if owners != [label]:
        _d_stop(markers, outcome, f"trash point ({tx:.0f},{ty:.0f}) is not inside exactly this row ({owners}) — not tapped")
    safe, _top = _cart_safe_bottom(driver, sh)
    if not (110.0 <= ty <= safe):
        _d_stop(markers, outcome, f"trash point y={ty:.0f} not clear of header/footer (safe ≤ {safe:.0f}) — not tapped")
    others_before = {r[4]: _parse_row_promo(v).get("current") for r, v in rows2 if r[4] != label}
    price_before = _parse_row_promo(hit2[1]).get("current")
    print(f"  remove out-of-stock {label[:50]!r}: trash tap @ ({tx:.0f},{ty:.0f}) (row y={y:.0f} h={h:.0f})")
    markers.mark("d_oos_trash_tap", label=label[:60], x=round(tx), y=round(ty), row_y=round(y), row_h=round(h))
    _tap_center(driver, tx, ty)
    for _poll in range(8):
        time.sleep(0.6)
        if not _still_on_cart_view(driver):
            _dismiss_pdp_if_open(driver, markers)
            _d_stop(markers, outcome, f"trash tap on {label[:40]!r} left the cart — went back once, stopped")
        rows3 = _d_cart_rows(driver)
        now = next((v for r, v in rows3 if r[4] == label), None)
        if now is None:
            # Basket D: the other rows still on screen must be unchanged.
            changed = [lab for (r, v) in rows3 if (lab := r[4]) in others_before
                       and others_before[lab] is not None
                       and _parse_row_promo(v).get("current") not in (None, others_before[lab])]
            if changed:
                _d_stop(markers, outcome, f"removing {label[:40]!r} changed other row(s) {changed[:3]} — stopped")
            markers.mark("d_oos_removed", label=label[:60])
            print(f"  removed {label[:50]!r} (row gone; other rows unchanged)")
            return "removed"
        cur = _parse_row_promo(now).get("current")
        if price_before is not None and cur is not None and cur > price_before + 0.005:
            _d_stop(markers, outcome, f"trash tap raised {label[:40]!r} (${price_before}→${cur}) — stopped")
    _d_stop(markers, outcome, f"{label[:40]!r} still in the cart after the trash tap — stopped")
    return "unreachable"


def _d_back_to_store(driver, markers: MarkerLog, outcome: dict) -> int | None:
    """Cart → store via the cart's own top-left Back button; returns the badge."""
    back = None
    try:
        for el in driver.find_elements(AppiumBy.IOS_PREDICATE,
                                       'type == "XCUIElementTypeButton" AND name == "Back" AND visible == 1'):
            r = _safe_rect(el) or {}
            if float(r.get("x", 999)) < 70 and float(r.get("y", 999)) < 130:
                back = (float(r["x"]) + float(r["width"]) / 2.0, float(r["y"]) + float(r["height"]) / 2.0)
                break
    except Exception:  # noqa: BLE001
        back = None
    if back is None:
        _d_stop(markers, outcome, "cart Back button not found")
    _tap_center(driver, *back)
    markers.mark("d_cart_back_to_store", x=round(back[0]), y=round(back[1]))
    end = time.monotonic() + 8.0
    while time.monotonic() < end:
        try:
            if driver.find_elements(AppiumBy.ACCESSIBILITY_ID, "floating_cart_button"):
                time.sleep(_MED)
                return _basket_count(driver)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    _d_stop(markers, outcome, "store page did not appear after Back")
    return None


def _d_reopen_cart(driver, markers: MarkerLog, outcome: dict) -> None:
    _tap_basket_cart_icon(driver, markers, outcome)
    _wait_for_cart_view(driver, markers)
    time.sleep(0.5)


def _d_precheck_and_swap(driver, markers: MarkerLog, outcome: dict, *, num_items: int) -> None:
    """UNRECORDED read-only cart check with bounded out-of-stock swaps. On
    return the cart is re-opened at the top, ready for the recorded check."""
    try:
        sh = float((driver.get_window_size() or {}).get("height") or 844)
    except Exception:  # noqa: BLE001
        sh = 844.0
    swaps: list[dict] = []
    outcome["oos_swaps"] = swaps
    all_removed: list[str] = []          # every out-of-stock row removed this run
    t_rec = time.monotonic()
    rounds = 0
    while True:
        tmp = {"items_added": list(outcome.get("items_added") or []),
               "badge_before_cart": outcome.get("badge_before_cart")}
        print(f"Step: UNRECORDED pre-check of the cart (out-of-stock swaps so far: {len(swaps)}, round {rounds}) …")
        try:
            _verify_cart_qty_one(driver, markers, tmp, num_items=num_items)
            outcome["precheck"] = {"status": "ok", "scan": tmp.get("cart_qty_scan"), "swaps": len(swaps)}
            markers.mark("d_precheck_ok", swaps=len(swaps))
            break
        except ProductUnavailable:
            for k in ("cart_qty_evidence", "cart_qty_line_counts", "cart_qty_scan"):
                outcome[f"precheck_{k}"] = tmp.get(k)
            ev = tmp.get("cart_qty_evidence") or []
            oos = [e for e in ev if e.get("row_state") == "replacement_required"]
            other = [e for e in ev if e.get("row_state") != "replacement_required" and e.get("status") != "price_inferred"]
            outcome["precheck"] = {"status": "out_of_stock", "rows": len(ev),
                                   "out_of_stock": [e["label"][:80] for e in oos]}
            if other or len(ev) != num_items:
                _d_stop(markers, outcome, f"cart has {len(ev)} row(s) and {len(other)} other unverified row(s) "
                        "besides the out-of-stock one(s) — no swap", ProductUnavailable)
            rounds += 1
            if rounds > _D_OOS_MAX_ROUNDS:
                _d_stop(markers, outcome, f"out-of-stock recovery round limit reached ({_D_OOS_MAX_ROUNDS} rounds, "
                        f"{len(swaps)} swaps, {len(oos)} still unavailable)", ProductUnavailable)
            if time.monotonic() - t_rec > _D_OOS_RECOVERY_S:
                _d_stop(markers, outcome, f"out-of-stock recovery time budget exhausted ({_D_OOS_RECOVERY_S:.0f} s, "
                        f"{len(swaps)} swaps, {len(oos)} still unavailable)", ProductUnavailable)
            print(f"  {len(oos)} out-of-stock row(s): {[e['label'][:30] for e in oos]} — recovery round {rounds}")
        except QuantityUnverified:
            # Not an out-of-stock case (e.g. row count, qty 2): keep the
            # pre-check evidence in the JSON, then stop before recording.
            for k in ("cart_qty_evidence", "cart_qty_line_counts", "cart_qty_scan"):
                outcome[f"precheck_{k}"] = tmp.get(k)
            outcome["precheck"] = {"status": "unverified", "rows": len(tmp.get("cart_qty_evidence") or [])}
            raise
        removed: list[str] = []
        for e in oos:
            if _d_remove_oos_row(driver, markers, outcome, e["label"], sh) == "removed":
                removed.append(e["label"])
        if not removed:
            _d_back_to_store(driver, markers, outcome)
            _d_reopen_cart(driver, markers, outcome)
            continue                    # the row(s) came back in stock — check again
        badge = _d_back_to_store(driver, markers, outcome)
        want = num_items - len(removed)
        if badge != want:
            _d_stop(markers, outcome, f"store badge reads {badge!r} after removing {len(removed)} — expected {want}")
        added_before = list(outcome.get("items_added") or [])
        keep = [a for a in added_before
                if not any(_same_product(lab, a.get("label") or "", raw=a.get("raw_label")) for lab in removed)]
        gone = [a for a in added_before if a not in keep]
        if len(gone) != len(removed):
            _d_stop(markers, outcome, f"removed rows {removed} match {len(gone)} added product(s) — identity unclear")
        all_removed.extend(removed)
        # Never re-add a product already in the cart or removed as out of stock.
        exclude = [a.get("label") or "" for a in added_before] + all_removed
        print(f"Step: add {len(removed)} replacement product(s) (badge {badge}) …")
        _add_n_items_under_max_price(driver, markers, outcome, count=len(removed),
                                     max_price=MAX_ITEM_PRICE, exclude=exclude)
        new = list(outcome.get("items_added") or [])
        badge2 = _basket_count(driver)
        if len(new) != len(removed) or badge2 != num_items:
            _d_stop(markers, outcome, f"replacement add left badge {badge2!r} ({len(new)} added) — expected {num_items}")
        outcome["items_added"] = keep + new
        for g, lab, n_ in zip(gone, removed, new):
            swaps.append({"removed": lab[:100], "store_label": (g.get("label") or "")[:100],
                          "replacement": (n_.get("label") or "")[:100], "price": n_.get("price")})
            print(f"  swap {len(swaps)}: {lab[:40]!r} → {(n_.get('label') or '')[:40]!r} ${n_.get('price')}")
        markers.mark("d_oos_swapped", swaps=len(swaps))
        _d_reopen_cart(driver, markers, outcome)
    # Pre-check passed: re-open the cart so the recorded check starts at the top.
    badge = _d_back_to_store(driver, markers, outcome)
    if badge != num_items:
        _d_stop(markers, outcome, f"store badge reads {badge!r} after the pre-check — expected {num_items}")
    _d_reopen_cart(driver, markers, outcome)


def _increase_basket_quantities(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    num_items: int,
    units_each: int,
) -> None:
    """Raise each cart line to units_each via: tap line → PDP bottom + → close.

    Basket D: 25 items × 1 unit — no + taps; qty 1 verified read-only from cart row prices.
    Falls back to cart-row qty + if PDP does not open / + misses.
    """
    units_each = max(1, int(units_each))
    num_items = max(1, int(num_items))
    if units_each <= 1:
        _verify_cart_qty_one(driver, markers, outcome, num_items=num_items)
        return

    print(
        f"Step: for each of {num_items} cart lines — tap item → PDP + "
        f"until {units_each} ct …"
    )
    # One gentle pull to top — no bounce loops.
    _scroll_cart_list(driver, up=False)
    time.sleep(0.20)

    processed = 0
    seen: set[str] = set()
    total_successes = 0
    stagnant_passes = 0
    # Rows beyond one screen: keep scrolling while each idle scroll
    # still changes the visible rows; stop at the list end (view unchanged)
    # or after _MAX_IDLE_CART_SCROLLS. `seen` prevents double-processing.
    _MAX_IDLE_CART_SCROLLS = 6
    idle_scrolls = 0
    try:
        sh_cart = float((driver.get_window_size() or {}).get("height") or 844)
    except Exception:  # noqa: BLE001
        sh_cart = 844.0
    obstructed_tries: dict[str, int] = {}
    unreachable: list[str] = []
    _ROW_VALUE_CACHE.clear()
    # Direct-cart bookkeeping: first price each row showed (untouched → qty 1,
    # added once this run), latest price per row, evidence index per key.
    first_seen: dict[str, float] = {}
    known_prices: dict[str, float] = {}
    ev_index: dict[str, tuple[int, dict]] = {}
    collateral: list[dict] = []
    prev_scan_settled = True  # the cart is idle when the qty phase starts
    last_cart_tap_t = 0.0

    # Cart mode: rows confirmed at 10 and their confirmed price. Any later
    # change means a server update arrived after "settled" → not verified.
    confirmed_prices: dict[str, float] = {}
    late_updates: list[dict] = []

    def _note_prices(prices: dict[str, float]) -> None:
        for lab, price in prices.items():
            k = _item_label_key(lab)
            first_seen.setdefault(k, price)
            known_prices[k] = price
            if k in confirmed_prices and abs(price - confirmed_prices[k]) > 0.005:
                late_updates.append({"label": lab[:60], "confirmed": confirmed_prices[k],
                                     "now": price, "line_in_progress": processed})
                markers.mark("cart_late_update", label=lab[:60],
                             confirmed=confirmed_prices[k], now=price)
                print(f"  LATE UPDATE: {lab[:40]!r} ${confirmed_prices[k]}→${price} after it was confirmed")
                confirmed_prices[k] = price

    def _fail_fast(reason: str, ev: dict | None, manual: bool = False,
                   unavailable: bool = False) -> None:
        """Cart mode: stop everything on the first unconfirmed quantity."""
        outcome["cart_qty_late_updates"] = late_updates
        outcome["quantity_unverified"] = {
            "reason": reason,
            "product": (ev or {}).get("label"),
            "line": (ev or {}).get("line"),
            "last_known_qty": ((ev or {}).get("direct") or {}).get("q_after_burst"),
            "taps_sent": (ev or {}).get("taps_sent"),
            "settles": ((ev or {}).get("direct") or {}).get("settles"),
            "note": (ev or {}).get("note"),
            "products_confirmed": len(confirmed_prices),
        }
        outcome["cart_qty_evidence"] = qty_evidence
        markers.mark("quantity_unverified", reason=reason, product=(ev or {}).get("label"))
        if unavailable:
            print(f"  PRODUCT UNAVAILABLE — stopping before any tap on it and before checkout: {reason}")
            raise ProductUnavailable(f"product_unavailable: {reason}")
        if manual:
            outcome["manual_verification"] = {
                "product": (ev or {}).get("label"),
                "promotion": (ev or {}).get("promotion"),
                "request": "Check this product's quantity on the phone/video; "
                           "the run stopped before checkout and did not assume 10.",
            }
            print(f"  MANUAL VERIFICATION REQUIRED — stopping before checkout: {reason}")
            raise ManualVerificationRequired(f"manual_verification_required: {reason}")
        print(f"  QUANTITY UNVERIFIED — stopping all further quantity taps and checkout: {reason}")
        raise QuantityUnverified(f"quantity_unverified: {reason}")
    prev_idle_view: frozenset | None = None
    # Rows (+ row prices) scanned right after the previous line, reused for
    # the next pass when nothing happened in between (saves one page_source).
    cached_lines: list | None = None
    cached_prices: dict[str, float] = {}
    # True when _wait_for_cart_view just confirmed CartV4 after closing a PDP.
    cart_confirmed = False
    qty_evidence: list[dict] = []
    # Lines whose row was off-screen in the follow-up scan: key → (index in
    # qty_evidence, evidence kwargs). Filled from any later scan that shows
    # the row (no extra Appium calls); a bounded sweep runs only if needed.
    pending_ev: dict[str, tuple[int, dict]] = {}

    _EV_KEEP = (
        "method", "elapsed_s", "direct", "taps_sent", "settle_s",
        "q_after_settle", "corrected", "pdp_fallback",
    )

    def _rejudge(idx: int, kw: dict, price_after: float) -> dict:
        """Re-evaluate one line's evidence, keeping its reporting fields."""
        old = qty_evidence[idx]
        ev2 = _cart_line_qty_evidence(**{**kw, "price_after": price_after})
        for f in _EV_KEEP:
            if f in old:
                ev2[f] = old[f]
        # Never upgrade a line the direct method itself left unverified.
        if old.get("status") == "unverified" and (old.get("direct") or {}).get("status") not in (None, "ok"):
            ev2["status"] = "unverified"
            ev2["note"] = old["note"]
        qty_evidence[idx] = ev2
        return ev2

    def _fill_pending(prices: dict[str, float]) -> None:
        for lab, price in prices.items():
            k = _item_label_key(lab)
            if k in pending_ev:
                idx, kw = pending_ev.pop(k)
                ev2 = _rejudge(idx, kw, price)
                ev2["price_after_source"] = "later_scan"
                print(f"  line {kw['line']} qty evidence (later scan): {ev2['note']}")

    for _pass in range(num_items * 4):
        if processed >= num_items:
            break
        if not cart_confirmed:
            if _on_product_detail(driver):
                _close_pdp_to_cart(driver, markers)
            if not _still_on_cart_view(driver):
                _close_pdp_to_cart(driver, markers)
        cart_confirmed = False

        if cached_lines is not None:
            lines, prices_now = cached_lines, cached_prices
            cached_lines, cached_prices = None, {}
        else:
            prices_now = {}
            lines = _find_all_cart_line_buttons(driver, prices=prices_now)
            _fill_pending(prices_now)
            _note_prices(prices_now)
        print(f"  scan pass {_pass + 1}: {len(lines)} cart row(s)")
        work = []
        for x, y, w, h, label in lines:
            key = _item_label_key(label)
            if not key or key in seen:
                continue
            work.append((key, x, y, w, h, label))

        if not work:
            stagnant_passes += 1
            view = frozenset(_item_label_key(row[4]) for row in lines)
            same_view = view == prev_idle_view
            prev_idle_view = view
            # Visible rows are all processed: stop only at the list end (a
            # scroll did not change the rows) or after the idle-scroll cap.
            if same_view or idle_scrolls >= _MAX_IDLE_CART_SCROLLS:
                print(
                    f"  no new cart lines (have {processed}/{num_items}; "
                    f"idle scrolls={idle_scrolls}, list end={same_view}) — stop"
                )
                break
            if processed < num_items and lines:
                idle_scrolls += 1
                markers.mark("cart_idle_scroll", n=idle_scrolls, processed=processed)
                _scroll_cart_list(driver, up=True)
                time.sleep(0.25)
            continue

        stagnant_passes = 0
        idle_scrolls = 0
        prev_idle_view = None
        key, x, y, w, h, label = work[0]
        row_value = _ROW_VALUE_CACHE.get(label, "")
        if _cart_row_state(row_value) == "replacement_required":
            seen.add(key)
            processed += 1
            reason = _unavailable_reason(row_value)
            ev = _cart_line_qty_evidence(
                line=processed, label=label, price_before=prices_now.get(label),
                price_after=None, target_qty=units_each, start_qty=None,
                start_source="not_touched", pdp_final=None,
            )
            ev["row_state"] = "replacement_required"
            ev["note"] = f"unavailable in cart ({reason!r}) — not tapped, not removed or replaced"
            qty_evidence.append(ev)
            outcome["cart_unavailable_item"] = {"product": label[:80], "reason": reason,
                                                "row_value": row_value[:160]}
            markers.mark("cart_row_unavailable", label=label[:60], reason=reason)
            _fail_fast(f"{label[:40]!r} is unavailable in the cart ({reason!r})", ev,
                       unavailable=True)
        safe_bottom, footer_top = _cart_safe_bottom(driver, sh_cart)
        tap_y = _cart_row_tap_y(y, h)
        if tap_y > safe_bottom:
            tries = obstructed_tries.get(key, 0)
            if tries < _MAX_OBSTRUCTED_SCROLLS:
                obstructed_tries[key] = tries + 1
                markers.mark(
                    "cart_row_obstructed", label=label[:60], tap_y=tap_y,
                    safe=safe_bottom, footer_top=footer_top, attempt=tries + 1,
                )
                print(
                    f"  row {label[:40]!r} tap y={tap_y:.0f} under footer "
                    f"(safe ≤ {safe_bottom:.0f}, footer top={footer_top}) — "
                    f"scroll + rescan ({tries + 1}/{_MAX_OBSTRUCTED_SCROLLS})"
                )
                _scroll_cart_list(driver, up=True)
                time.sleep(0.25)
                continue  # fresh scan next pass — no stale coordinates
            # Still under the footer after bounded scrolls: report, don't tap.
            seen.add(key)
            processed += 1
            unreachable.append(label[:60])
            ev = _cart_line_qty_evidence(
                line=processed, label=label, price_before=prices_now.get(label),
                price_after=None, target_qty=units_each, start_qty=None,
                start_source="not_opened", pdp_final=None,
            )
            ev["note"] = (
                f"unverified — row unreachable under sticky footer after "
                f"{_MAX_OBSTRUCTED_SCROLLS} scrolls (not tapped)"
            )
            qty_evidence.append(ev)
            markers.mark("cart_row_unreachable", label=label[:60], line=processed)
            if QTY_METHOD == "cart":
                _fail_fast(f"{label[:40]!r} unreachable under the sticky footer", ev)
            print(f"  line {processed}: {ev['note']}")
            continue
        seen.add(key)
        processed += 1
        price_before = prices_now.get(label)
        raise_info: dict = {}
        t_line = time.monotonic()
        method_used = "pdp"
        direct: dict | None = None
        pre_scanned = None
        start_hint: int | None = None
        ok = 0
        if QTY_METHOD == "cart":
            before_known = dict(known_prices)
            direct = _raise_qty_on_cart_row_direct(
                driver, markers, key=key, row=(x, y, w, h, label),
                price0=price_before, first_seen_price=first_seen.get(key),
                target_qty=units_each, line_index=processed, sh=sh_cart,
                safe_hint=safe_bottom,
                cart_settled=prev_scan_settled, prev_last_tap_t=last_cart_tap_t,
            )
            if direct["prices"]:
                _note_prices(direct["prices"])
            if direct["prices"] and direct.get("settled") and prev_scan_settled:
                # Both scans settled: any other row whose price changed was hit
                # by a stray tap (pending updates can no longer explain it).
                for lab2, p2 in direct["prices"].items():
                    k2 = _item_label_key(lab2)
                    if k2 != key and k2 in before_known and abs(p2 - before_known[k2]) > 0.005:
                        collateral.append({"label": lab2[:60], "before": before_known[k2],
                                           "after": p2, "during_line": processed})
                        markers.mark("cart_direct_collateral", label=lab2[:60],
                                     before=before_known[k2], after=p2)
                        print(f"  WARNING: {lab2[:40]!r} changed ${before_known[k2]}→${p2} during line {processed}")
                        if k2 in ev_index:  # already processed: re-judge its evidence
                            idx2, kw2 = ev_index[k2]
                            ev2 = _rejudge(idx2, kw2, p2)
                            ev2["note"] += " (changed by a later stray tap)"
            if direct.get("taps"):
                # Only lines that tapped can leave updates pending.
                prev_scan_settled = bool(direct.get("settled"))
                last_cart_tap_t = direct.get("last_tap_t") or time.monotonic()
            if direct["status"] == "obstructed" and obstructed_tries.get(key, 0) < _MAX_OBSTRUCTED_SCROLLS:
                obstructed_tries[key] = obstructed_tries.get(key, 0) + 1
                seen.discard(key)
                processed -= 1
                markers.mark("cart_row_obstructed", label=label[:60], via="direct_cart",
                             attempt=obstructed_tries[key])
                _scroll_cart_list(driver, up=True)
                time.sleep(0.25)
                continue  # fresh scan + fresh coordinates next pass
            if direct["status"] == "no_increment" and direct["fallback_ok"]:  # never set: fallback disabled
                method_used, start_hint = "pdp_fallback", direct["q_now"]
                print(f"  line {processed}: no increment after settling — PDP fallback from qty {start_hint}")
            else:
                method_used = "cart_direct"
                q_known = direct["q_final"] if direct["q_final"] is not None else direct["q_after_burst"]
                if direct["q_start"] is not None and q_known is not None:
                    ok = max(0, q_known - direct["q_start"])
                if direct["lines"] is not None:
                    pre_scanned = (direct["lines"], direct["prices"])
        if method_used in ("pdp", "pdp_fallback"):
            opened = _open_cart_line_pdp(
                driver, markers, x=x, y=y, w=w, h=h, label=label
            )
            if opened:
                ok = _raise_qty_on_pdp(
                    driver,
                    markers,
                    target_qty=units_each,
                    line_index=processed,
                    label=label,
                    pdp_confirmed=True,
                    info=raise_info,
                    start_hint=start_hint,
                )
                _close_pdp_to_cart(driver, markers)
            elif (
                _cart_row_plus_y(y, h) > safe_bottom
                and obstructed_tries.get(key, 0) < _MAX_OBSTRUCTED_SCROLLS
            ):
                # PDP did not open and the + is under the footer: don't tap it —
                # scroll and re-evaluate this row (bounded by the same counter).
                obstructed_tries[key] = obstructed_tries.get(key, 0) + 1
                seen.discard(key)
                processed -= 1
                if _on_product_detail(driver):
                    _close_pdp_to_cart(driver, markers)
                markers.mark(
                    "cart_row_obstructed", label=label[:60], tap_y=_cart_row_plus_y(y, h),
                    safe=safe_bottom, footer_top=footer_top,
                    attempt=obstructed_tries[key], via="fallback_unsafe",
                )
                print(
                    f"  PDP miss and + under footer — scroll + re-evaluate "
                    f"{label[:40]!r} ({obstructed_tries[key]}/{_MAX_OBSTRUCTED_SCROLLS})"
                )
                _scroll_cart_list(driver, up=True)
                time.sleep(0.25)
                continue
            else:
                print(f"  line {processed}: PDP miss — cart qty-pill fallback")
                ok = _raise_qty_on_cart_line_plus(
                    driver,
                    markers,
                    x=x,
                    y=y,
                    w=w,
                    h=h,
                    label=label,
                    target_qty=units_each,
                    line_index=processed,
                    safe_bottom=safe_bottom,
                )
                if _on_product_detail(driver):
                    _close_pdp_to_cart(driver, markers)

        total_successes += ok
        if pre_scanned is not None:
            # Direct-cart already rescanned the cart (rows present ⇒ on cart).
            ready = True
            nxt, nxt_prices = pre_scanned
            print(f"  progress lines {processed}/{num_items} (+ registered: {ok})")
        else:
            ready = _wait_for_cart_view(driver, markers, timeout=6.0)
            print(
                f"  progress lines {processed}/{num_items} "
                f"(+ taps: {ok})"
            )
            # Re-scan once: gives the next line to open AND this line's new row
            # price (qty evidence). Also runs after the last line, for evidence.
            nxt_prices = {}
            nxt = _find_all_cart_line_buttons(driver, prices=nxt_prices)
        _fill_pending(nxt_prices)
        _note_prices(nxt_prices)
        price_after = next(
            (p for lab, p in nxt_prices.items() if _item_label_key(lab) == key),
            None,
        )
        ev_kwargs = dict(
            line=processed,
            label=label,
            price_before=price_before,
            price_after=price_after,
            target_qty=units_each,
            start_qty=raise_info.get("start"),
            start_source=raise_info.get("start_source", "unknown_cart_pill"),
            pdp_final=raise_info.get("final"),
        )
        if method_used == "cart_direct" and direct is not None:
            ev_kwargs["start_qty"] = direct["q_start"]
            ev_kwargs["start_source"] = (
                "cart_untouched_qty1" if direct["q_start"] is not None else "not_raised"
            )
        ev = _cart_line_qty_evidence(**ev_kwargs)
        if direct is not None and direct.get("promotion"):
            ev["promotion"] = direct["promotion"]
        if direct is not None and direct["status"] == "ok" and direct.get("verified_by") == "pre_promotion_line_total":
            pr = direct["promotion"]
            ev["status"] = "price_inferred"
            ev["verified_by"] = "pre_promotion_line_total"
            ev["price_after"] = pr.get("pre_promo_after")
            ev["inferred_qty"] = units_each
            ev["note"] = (f"price_inferred qty {units_each} — promotion {pr['text']!r}: "
                          f"undiscounted line total ${pr['pre_promo_qty1']:.2f} → "
                          f"${pr['pre_promo_after']:.2f} (discounted ${pr.get('current_after')})")
        if method_used == "cart_direct" and direct is not None and direct["status"] != "ok":
            # Direct-cart stopped early: never report more than it proved.
            if ev["status"] == "price_inferred" and direct["q_final"] != units_each:
                ev["status"] = "unverified"
            ev["note"] = f"unverified — direct-cart {direct['status']}: {direct['note']}"
        ev["method"] = method_used
        ev["elapsed_s"] = round(time.monotonic() - t_line, 1)
        ev["pdp_fallback"] = method_used == "pdp_fallback"
        if direct is not None:
            ev["direct"] = {k: direct.get(k) for k in (
                "status", "taps", "corrections", "row_moves", "q_start",
                "q_after_burst", "q_final", "unit", "settle_s", "settles", "elapsed_s",
                "tap_mode", "tap_x", "tap_y", "tap_duration_s", "multitap",
                "confirm_latency_s")}
            ev["tap_mode"] = direct.get("tap_mode")
            ev["tap_duration_s"] = direct.get("tap_duration_s")
            ev["taps_sent"] = direct.get("taps", 0) + (ok if method_used == "pdp_fallback" else 0)
            ev["settle_s"] = direct.get("settle_s")
            ev["q_after_settle"] = direct.get("q_after_burst")
            ev["corrected"] = bool(direct.get("corrections"))
        else:
            ev["taps_sent"] = ok
        if direct is not None:
            ev["increments_accounted"] = direct.get("increments_accounted")
            ev["row_moves"] = direct.get("row_moves")
            ev["loading_label_seen"] = direct.get("loading_label_seen")
        qty_evidence.append(ev)
        ev_index[key] = (len(qty_evidence) - 1, ev_kwargs)
        if QTY_METHOD == "cart":
            if late_updates:
                _fail_fast(f"server update arrived after a product was confirmed: {late_updates[-1]}", ev)
            if direct is not None and direct["status"] == "manual_verification":
                _fail_fast(f"{label[:40]!r}: MANUAL VERIFICATION REQUIRED — {direct['note']}",
                           ev, manual=True)
            if direct is None or direct["status"] != "ok":
                _fail_fast(f"{label[:40]!r}: {(direct or {}).get('status')} — "
                           f"{(direct or {}).get('note', 'not raised by the cart method')}", ev)
            confirmed_prices[key] = direct["price_after"]
        if price_after is None and price_before is not None:
            pending_ev[key] = (len(qty_evidence) - 1, ev_kwargs)
            print(f"  line {processed}: row off-screen after raise — evidence pending")
        else:
            print(f"  line {processed} qty evidence: {ev['note']}")
        if processed >= num_items:
            break
        # Scroll once only when no unprocessed row is already visible.
        if not any(_item_label_key(lab) not in seen for *_, lab in nxt):
            _scroll_cart_list(driver, up=True)
            time.sleep(0.25)
        else:
            cached_lines, cached_prices = nxt, nxt_prices
            cart_confirmed = ready

    # Rows still missing their after-price: sweep the cart (top → bottom,
    # bounded) only to read them. Read-only; no taps.
    if pending_ev:
        print(f"  evidence sweep for {len(pending_ev)} off-screen line(s) …")
        markers.mark("cart_evidence_sweep", pending=len(pending_ev))
        for _ in range(3):  # back to the top of a 10-row cart (~2.3 rows/scroll)
            _scroll_cart_list(driver, up=False)
            time.sleep(0.25)
        prev_view: frozenset | None = None
        for _ in range(_MAX_IDLE_CART_SCROLLS + 1):
            sweep_prices: dict[str, float] = {}
            rows = _find_all_cart_line_buttons(driver, prices=sweep_prices)
            _fill_pending(sweep_prices)
            view = frozenset(_item_label_key(r[4]) for r in rows)
            if not pending_ev or view == prev_view:
                break
            prev_view = view
            _scroll_cart_list(driver, up=True)
            time.sleep(0.25)
        for k, (idx, kw) in pending_ev.items():
            qty_evidence[idx]["note"] = (
                "unverified — row never visible again after the raise"
            )
            print(f"  line {kw['line']} qty evidence: {qty_evidence[idx]['note']}")

    # Qty is reported only as far as the evidence supports it — never
    # hardcoded. ui_confirmed = PDP stepper read-back (currently unreadable);
    # price_inferred = cart row line-total ratio (inference, not a UI read).
    counts = {k: 0 for k in ("ui_confirmed", "price_inferred", "unverified")}
    for ev in qty_evidence:
        counts[ev["status"]] = counts.get(ev["status"], 0) + 1
    all_lines = processed >= num_items and len(qty_evidence) == processed
    if all_lines and counts["ui_confirmed"] == processed:
        qty_status = "ui_confirmed"
    elif all_lines and counts["unverified"] == 0:
        qty_status = "price_inferred"
    else:
        qty_status = "unverified"
    outcome["cart_qty_target"] = units_each
    outcome["cart_qty_status"] = qty_status
    # Direct UI-confirmed qty only; inferred qty is reported separately.
    outcome["cart_qty_after"] = units_each if qty_status == "ui_confirmed" else None
    outcome["cart_qty_inferred"] = (
        units_each if qty_status in ("ui_confirmed", "price_inferred") else None
    )
    outcome["cart_qty_line_counts"] = counts
    outcome["cart_qty_evidence"] = qty_evidence
    outcome["cart_lines_qty_raised"] = processed
    # Per-item accounting: which added products were never
    # processed, and which processed lines are not price-inferred.
    added_labels = [e.get("label") or "" for e in outcome.get("items_added") or []]

    def _was_processed(lab: str) -> bool:
        k = _item_label_key(lab)
        return bool(k) and any(s.startswith(k) or k.startswith(s) for s in seen)

    outcome["cart_qty_processed_items"] = [ev["label"] for ev in qty_evidence]
    outcome["cart_qty_unprocessed_items"] = [
        lab[:60] for lab in added_labels if not _was_processed(lab)
    ]
    went_unavailable = [
        lab[:60] for lab, val in _ROW_VALUE_CACHE.items()
        if _item_label_key(lab) in seen and _cart_row_state(val) == "replacement_required"
    ]
    if went_unavailable:
        outcome["cart_unavailable_item"] = {"product": went_unavailable[0],
                                            "reason": "became unavailable after being raised"}
        _fail_fast(f"{went_unavailable[0]!r} became unavailable in the cart", None,
                   unavailable=True)
    if QTY_METHOD == "cart":
        outcome["cart_qty_late_updates"] = late_updates
        if late_updates:
            _fail_fast(f"server update arrived after a product was confirmed: {late_updates[-1]}", None)
    outcome["cart_qty_unreachable_items"] = unreachable
    outcome["cart_qty_method_config"] = QTY_METHOD
    outcome["cart_qty_methods_used"] = dict(
        collections.Counter(ev.get("method", "pdp") for ev in qty_evidence)
    )
    outcome["cart_qty_direct_corrections"] = sum(
        (ev.get("direct") or {}).get("corrections") or 0 for ev in qty_evidence
    )
    outcome["cart_qty_collateral_changes"] = collateral
    outcome["cart_qty_obstructed_scrolls"] = sum(obstructed_tries.values())
    outcome["cart_qty_unverified_items"] = [
        ev["label"] for ev in qty_evidence if ev["status"] == "unverified"
    ]
    outcome["cart_qty_plus_successes"] = total_successes
    outcome["cart_qty_method"] = (
        "cart_direct" if QTY_METHOD == "cart" else "pdp_bottom_plus"
    )
    markers.mark(
        "cart_qty_verification",
        status=qty_status,
        target=units_each,
        **counts,
    )
    if processed >= num_items and total_successes >= 1:
        step_status = "ok" if qty_status != "unverified" else "unverified"
    else:
        step_status = "partial"
    _mark_step(
        outcome,
        "cart_qty_increase",
        status=step_status,
        qty_status=qty_status,
        lines=processed,
        units_each=units_each,
        successes=total_successes,
        method=outcome["cart_qty_method"],
        **counts,
    )
    print(
        f"  qty raise done: {processed}/{num_items} lines "
        f"(target {units_each} ct, {total_successes} successful +)"
    )
    if processed < 1:
        raise AssertionError(
            f"Could not find any cart lines to raise to {units_each} ct "
            f"(needed {num_items} lines)"
        )
    if processed < num_items:
        raise AssertionError(
            f"Only raised {processed}/{num_items} cart lines "
            f"(cart likely had fewer unique items); not processed: "
            f"{outcome.get('cart_qty_unprocessed_items')}"
        )
    if processed < num_items:
        print(
            f"  WARNING: only raised {processed}/{num_items} lines — continuing"
        )
    if total_successes < 1 and units_each > 1:
        raise AssertionError(
            f"Found {processed} cart line(s) but no successful + taps "
            f"toward {units_each} ct"
        )


def _tap_go_to_checkout(driver, markers: MarkerLog, outcome: dict) -> None:
    """Tap bottom green 'Go to checkout' CTA on cart screen."""
    print('Step: tap "Go to checkout" …')
    # After qty bump, Instacart may briefly keep "$10 Min. to checkout" until total updates.
    time.sleep(_MED)
    end = time.monotonic() + 12.0
    last_err: Exception | None = None
    el = by = value = None
    while time.monotonic() < end:
        try:
            el, by, value = find_first(driver, GO_TO_CHECKOUT_STRATEGIES, timeout=2.0)
            # Prefer enabled CTA (disabled "$10 Min…" must not match our strategies).
            try:
                if el.get_attribute("enabled") in ("false", "0", False):
                    time.sleep(0.4)
                    continue
            except Exception:  # noqa: BLE001
                pass
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.5)
    if el is None:
        raise TimeoutError(
            f'Could not find enabled "Go to checkout" (last: {last_err})'
        )

    r = _safe_rect(el) or {}
    cx = float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0
    cy = float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0
    try:
        _tap_center(driver, cx, cy)
    except Exception:  # noqa: BLE001
        el.click()

    outcome.setdefault("selectors", {})["go_to_checkout"] = {
        "by": by,
        "value": value,
        "x": cx,
        "y": cy,
    }
    markers.mark("go_to_checkout_tapped", by=by, value=value, x=cx, y=cy)
    _mark_step(
        outcome,
        "go_to_checkout",
        status="ok",
        by=by,
        value=value,
        x=cx,
        y=cy,
    )
    outcome["go_to_checkout_tapped"] = True
    print(f'  Go to checkout: {by} = {value!r} @ ({cx:.0f},{cy:.0f})')
    time.sleep(_SLOW)


def _tap_checkout_top_right_chevron(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """Tap '>' on the checkout 'Deliver to' address row.

    The whole address row is one button, e.g.
    '1100 University Street, Seattle, WA, Meet at the door • Add instructions'.
    Tap its right edge where the disclosure chevron sits.
    """
    print('Step: tap top-right ">" on Deliver-to address row …')
    # (No leading sleep: _tap_go_to_checkout already ends with _SLOW and the
    # address-row poll below waits for the real UI.)
    try:
        size = driver.get_window_size() or {}
        sw = float(size.get("width") or 390)
        sh = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        sw, sh = 390.0, 844.0

    def _deliver_to_rows() -> list[tuple[float, float, float, float, str]]:
        """Visible full-width Deliver-to address-row Buttons, top → bottom."""
        rows: list[tuple[float, float, float, float, str]] = []
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND visible == 1 AND ('
                'label CONTAINS[c] "Add instructions" OR '
                'name CONTAINS[c] "Add instructions" OR '
                'label CONTAINS[c] "University Street" OR '
                'name CONTAINS[c] "University Street" OR '
                'label CONTAINS[c] "Northeast 29th" OR '
                'name CONTAINS[c] "Northeast 29th" OR '
                'label CONTAINS[c] "14711" OR name CONTAINS[c] "14711" OR '
                '(label CONTAINS[c] "Meet at the door" AND '
                'label CONTAINS[c] "WA") OR '
                '(name CONTAINS[c] "Meet at the door" AND '
                'name CONTAINS[c] "WA") OR '
                '(label CONTAINS[c] "Leave at the door" AND '
                'label CONTAINS[c] "WA") OR '
                '(name CONTAINS[c] "Leave at the door" AND '
                'name CONTAINS[c] "WA"))',
            )
        except Exception:  # noqa: BLE001
            els = []
        for el in els[:16]:
            try:
                label = (
                    el.get_attribute("label") or el.get_attribute("name") or ""
                )
                r = _safe_rect(el)
                if not r:
                    continue
                x = float(r.get("x", 0))
                y = float(r.get("y", 0))
                w = float(r.get("width", 0))
                h = float(r.get("height", 0))
                # Full-width Deliver-to row (~390×58 @ y≈153).
                if w < sw * 0.55 or h < 30 or h > 120:
                    continue
                if y < 80 or y > sh * 0.45:
                    continue
                rows.append((y, x, w, h, label[:120]))
            except Exception:  # noqa: BLE001
                continue
        rows.sort(key=lambda t: t[0])
        return rows

    # Wait for the address row itself, settled (same y on two consecutive
    # polls). The old "any checkout label" wait passed before the row was
    # rendered, so the first tap was dropped (14/14 runs) and cost a 10 s retry.
    candidates: list[tuple[float, float, float, float, str]] = []
    prev_y: float | None = None
    end = time.monotonic() + 12.0
    while time.monotonic() < end:
        candidates = _deliver_to_rows()
        if candidates:
            y0 = candidates[0][0]
            if prev_y is not None and abs(y0 - prev_y) <= 2.0:
                break
            prev_y = y0
        else:
            prev_y = None
        time.sleep(0.35)
    if candidates:
        markers.mark("deliver_to_row_ready", y=candidates[0][0])
    else:
        print("  Deliver-to address row not found — coord fallback")

    if candidates:
        y, x, w, h, label = candidates[0]
        # Chevron is on the far right of the row.
        cx = x + w - 28.0
        cy = y + h / 2.0
        via = f"deliver_to_row:{label[:60]}"
    else:
        # Known layout from checkout dump: address row @ y≈153 h≈58.
        cx, cy = sw - 28.0, 182.0
        via = "coord_deliver_to_chevron"
        label = via

    _tap_center(driver, cx, cy)
    outcome.setdefault("selectors", {})["checkout_chevron"] = {
        "via": via,
        "x": cx,
        "y": cy,
    }
    markers.mark("checkout_chevron_tapped", via=via, x=cx, y=cy, label=label)
    _mark_step(
        outcome,
        "checkout_chevron",
        status="ok",
        via=via,
        x=cx,
        y=cy,
        label=label,
    )
    outcome["checkout_chevron_tapped"] = True
    print(f'  Deliver-to ">" chevron: {via!r} @ ({cx:.0f},{cy:.0f})')
    time.sleep(_SLOW)

    if not _wait_for_label(driver, contains="Delivery details", timeout=10.0):
        print("  Delivery details not open — retapping Deliver-to chevron")
        _tap_center(driver, cx, cy)
        time.sleep(_SLOW)
        if not _wait_for_label(driver, contains="Delivery details", timeout=10.0):
            raise AssertionError(
                'Could not open "Delivery details" after tapping Deliver-to ">"'
            )


def _wait_for_label(
    driver, *, contains: str, timeout: float = 10.0
) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            hits = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                f'visible == 1 AND (label CONTAINS[c] "{contains}" OR '
                f'name CONTAINS[c] "{contains}")',
            )
            if hits:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    return False


def _wait_for_label_gone(
    driver, *, contains: str, timeout: float = 3.0
) -> bool:
    """True once no visible element label/name contains ``contains``."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            hits = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                f'visible == 1 AND (label CONTAINS[c] "{contains}" OR '
                f'name CONTAINS[c] "{contains}")',
            )
            if not hits:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.25)
    return False


def _find_delivery_details_change_point(
    driver, sw: float, sh: float
) -> tuple[float, float, str] | None:
    """Locate the grey Change pill next to the address on Delivery details.

    Change is usually an unlabeled XCUIElementTypeOther at ≈ (285,113,105×62),
    immediately right of the address Other (~278×62). Returns None when only
    the coord fallback applies.
    """
    # 1) Named Change button (rare).
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Change" OR name == "Change")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:8]:
        try:
            r = _safe_rect(el)
            if not r:
                continue
            y = float(r.get("y", 9999))
            if y > sh * 0.40:
                continue
            return (
                float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0,
                y + float(r.get("height", 0)) / 2.0,
                "button:Change",
            )
        except Exception:  # noqa: BLE001
            continue

    # 2) Address Other → tap sibling Change pill to its right.
    try:
        addr_els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeOther" AND visible == 1 AND ('
            'label CONTAINS[c] "Street" OR name CONTAINS[c] "Street" OR '
            'label CONTAINS[c] "Place" OR name CONTAINS[c] "Place" OR '
            'label CONTAINS[c] "Avenue" OR name CONTAINS[c] "Avenue" OR '
            'label CONTAINS[c] "WA" OR name CONTAINS[c] "WA")',
        )
    except Exception:  # noqa: BLE001
        addr_els = []
    for el in addr_els[:12]:
        try:
            label = el.get_attribute("label") or el.get_attribute("name") or ""
            if "Add instructions" in label or "Leave at the door" in label:
                continue
            r = _safe_rect(el)
            if not r:
                continue
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            h = float(r.get("height", 0))
            # Address block under nav: ~full-left, ~62 tall, y≈113.
            if not (90 <= y <= 200 and 180 <= w <= 320 and 40 <= h <= 90):
                continue
            if x > sw * 0.25:
                continue
            # Change pill sits immediately to the right (~105×62).
            return (x + w + 52.0, y + h / 2.0, f"addr_sibling:{label[:40]}")
        except Exception:  # noqa: BLE001
            continue

    # (A former step 3 walked every accessible Other with per-element
    # label + rect reads; it matched in 0/13 runs. Callers fall back to the
    # known Change coord and verify 'Delivery address' opened.)

    return None


def _tap_delivery_details_change(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """On Delivery details sheet, tap the grey 'Change' control by the address."""
    print('Step: tap "Change" on Delivery details …')
    if not _wait_for_label(driver, contains="Delivery details", timeout=16.0):
        print("  'Delivery details' title not confirmed — looking for Change")
    time.sleep(_MED)

    try:
        size = driver.get_window_size() or {}
        sw = float(size.get("width") or 390)
        sh = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        sw, sh = 390.0, 844.0

    point = _find_delivery_details_change_point(driver, sw, sh)
    if point is None:
        # Known layout from Delivery-details dumps: Change @ (285,113,105×62).
        cx, cy, via = sw * 0.86, 144.0, "coord_change"
    else:
        cx, cy, via = point

    _tap_center(driver, cx, cy)
    markers.mark("delivery_change_tapped", x=cx, y=cy, via=via)
    _mark_step(outcome, "delivery_change", status="ok", x=cx, y=cy, via=via)
    outcome["delivery_change_tapped"] = True
    print(f'  "Change" tapped via {via!r} @ ({cx:.0f},{cy:.0f})')
    time.sleep(_SLOW)

    # Confirm Delivery address opened; retap Change once if still on details.
    if not _wait_for_label(driver, contains="Delivery address", timeout=8.0):
        print("  Delivery address not open — retapping Change")
        point2 = _find_delivery_details_change_point(driver, sw, sh)
        if point2 is None:
            cx2, cy2 = sw * 0.86, 144.0
        else:
            cx2, cy2 = point2[0], point2[1]
        _tap_center(driver, cx2, cy2)
        time.sleep(_SLOW)
        if not _wait_for_label(driver, contains="Delivery address", timeout=10.0):
            raise AssertionError(
                'Could not open "Delivery address" after tapping Change'
            )


def _select_second_delivery_address(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """On Delivery address list, tap any unselected address → Select.

    Layout (from dumps / user screenshot):
      row0 selected  @ y≈185  (filled radio)
      row1 unselected @ y≈247  ← pick this (or any later unselected)
      row2 unselected @ y≈309
      Edit pill on each row @ x≈286 w≈104
      green Select @ y≈742
    """
    print("Step: select an unselected delivery address …")
    if not _wait_for_label(driver, contains="Delivery address", timeout=12.0):
        raise AssertionError(
            'Not on "Delivery address" screen — cannot select address'
        )
    time.sleep(_MED)

    try:
        size = driver.get_window_size() or {}
        sw = float(size.get("width") or 390)
        sh = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        sw, sh = 390.0, 844.0

    def _edit_row_ys() -> list[float]:
        """Y centers of visible Edit controls on the address list."""
        ys: list[float] = []
        queries = (
            (
                AppiumBy.IOS_PREDICATE,
                'visible == 1 AND (name == "Edit" OR label == "Edit")',
            ),
            (
                AppiumBy.IOS_CLASS_CHAIN,
                '**/XCUIElementTypeOther[`name == "Edit"`]',
            ),
            (AppiumBy.ACCESSIBILITY_ID, "Edit"),
        )
        els = []
        for by, value in queries:
            try:
                els = driver.find_elements(by, value)
            except Exception:  # noqa: BLE001
                els = []
            if els:
                break
        for el in els[:12]:
            try:
                r = _safe_rect(el)
                if not r:
                    continue
                y = float(r.get("y", 0))
                h = float(r.get("height", 0))
                x = float(r.get("x", 0))
                w = float(r.get("width", 0))
                if y < 160 or y > sh * 0.72:
                    continue
                if w < 60 or h < 36 or h > 100:
                    continue
                if x < sw * 0.55:
                    continue
                ys.append(y + h / 2.0)
            except Exception:  # noqa: BLE001
                continue
        ys.sort()
        deduped: list[float] = []
        for y in ys:
            if deduped and abs(y - deduped[-1]) < 28:
                continue
            deduped.append(y)
        return deduped

    def _rows_from_page_source() -> list[tuple[float, float, float, str]]:
        """Parse page_source for address Others (predicate find is flaky here)."""
        import xml.etree.ElementTree as ET

        rows: list[tuple[float, float, float, str]] = []
        try:
            src = driver.page_source or ""
            root = ET.fromstring(src)
        except Exception:  # noqa: BLE001
            return rows
        for el in root.iter():
            name = (el.attrib.get("name") or el.attrib.get("label") or "")
            if el.attrib.get("visible") != "true":
                continue
            if not name or name.strip().lower() in ("edit", "select", "change"):
                continue
            if "Add instructions" in name or "Leave at the door" in name:
                continue
            # Address rows look like "Street…\nCity, WA ZIP" or contain ", WA".
            if ", WA" not in name and "\n" not in name:
                if "Street" not in name and "Place" not in name:
                    continue
            try:
                x = float(el.attrib.get("x", 0))
                y = float(el.attrib.get("y", 0))
                w = float(el.attrib.get("width", 0))
                h = float(el.attrib.get("height", 0))
            except ValueError:
                continue
            if y < 160 or y > sh * 0.72:
                continue
            if x > sw * 0.35 or w < 120 or w > sw * 0.85 or h < 36 or h > 100:
                continue
            rows.append((y, x + min(w * 0.40, sw * 0.35), y + h / 2.0, name[:120]))
        rows.sort(key=lambda t: t[0])
        deduped: list[tuple[float, float, float, str]] = []
        for row in rows:
            if deduped and abs(row[0] - deduped[-1][0]) < 28:
                continue
            deduped.append(row)
        return deduped

    chosen_label = "unselected_address"
    cx = cy = None
    via = "unselected"

    # 1) Prefer Edit-row anchors: first Edit = selected, tap radio of 2nd Edit.
    edit_ys: list[float] = []
    end = time.monotonic() + 8.0
    while time.monotonic() < end:
        edit_ys = _edit_row_ys()
        if len(edit_ys) >= 2:
            break
        time.sleep(0.35)

    if len(edit_ys) >= 2:
        # Tap left of 2nd Edit — empty radio / address text (not Edit itself).
        cy = edit_ys[1]
        cx = sw * 0.28
        chosen_label = f"edit_row_2_of_{len(edit_ys)}"
        via = "edit_anchor_2nd"
    else:
        # 2) Parse page_source for address Others; pick any row after the first.
        rows = _rows_from_page_source()
        if len(rows) >= 2:
            _y, cx, cy, chosen_label = rows[1]
            via = "page_source_2nd"
        elif len(edit_ys) == 1:
            # Only one Edit visible — tap below it (next unselected row ~62px).
            cy = edit_ys[0] + 62.0
            cx = sw * 0.28
            chosen_label = "below_first_edit"
            via = "below_selected"
        else:
            # 3) Known layout: 2nd row radio @ ~ (40, 278).
            cx, cy = 40.0, 278.0
            chosen_label = "coord_2nd_row"
            via = "coord_2nd_unselected"
            print("  address rows not in a11y — using 2nd-row coord fallback")

    assert cx is not None and cy is not None
    _tap_center(driver, cx, cy)
    markers.mark(
        "second_address_tapped", x=cx, y=cy, label=chosen_label, via=via
    )
    print(
        f"  unselected address tapped via {via!r}: "
        f"{chosen_label!r} @ ({cx:.0f},{cy:.0f})"
    )
    time.sleep(_MED)

    # Confirm with bottom green "Select".
    select_strategies = (
        (AppiumBy.ACCESSIBILITY_ID, "Select"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Select" OR name == "Select")',
        ),
        (
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND (label == "Select" OR name == "Select")',
        ),
    )
    sx = sy = None
    el = None
    try:
        el, _by, _value = find_first(driver, select_strategies, timeout=8.0)
        r = _safe_rect(el) or {}
        sx = float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0
        sy = float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0
        if sy < sh * 0.65:
            try:
                els = driver.find_elements(
                    AppiumBy.IOS_PREDICATE,
                    'visible == 1 AND (label == "Select" OR name == "Select")',
                )
                for cand in els[:8]:
                    cr = _safe_rect(cand)
                    if not cr:
                        continue
                    cy2 = float(cr.get("y", 0)) + float(cr.get("height", 0)) / 2.0
                    if cy2 > sy:
                        el = cand
                        r = cr
                        sx = float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0
                        sy = cy2
            except Exception:  # noqa: BLE001
                pass
    except TimeoutError:
        sx, sy = sw / 2.0, sh - 60.0
        print(f'  "Select" not in a11y — coord fallback @ ({sx:.0f},{sy:.0f})')

    assert sx is not None and sy is not None
    try:
        _tap_center(driver, sx, sy)
    except Exception:  # noqa: BLE001
        if el is not None:
            el.click()

    outcome.setdefault("selectors", {})["second_address"] = {
        "label": chosen_label,
        "via": via,
        "x": cx,
        "y": cy,
    }
    outcome["second_address_selected"] = chosen_label
    markers.mark("select_address_tapped", x=sx, y=sy)
    _mark_step(
        outcome,
        "second_address",
        status="ok",
        label=chosen_label,
        via=via,
        select_x=sx,
        select_y=sy,
    )
    print(f'  "Select" tapped @ ({sx:.0f},{sy:.0f})')
    time.sleep(_SLOW)


def _tap_delivery_details_done(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """After address Select, tap bottom green Done on Delivery details.

    After Select, Instacart saves the address and the Done CTA shows a "•••"
    spinner for a few seconds; a tap during the spinner is ignored (run
    20261009_054053). So: wait until Done looks actionable, tap, then verify
    the sheet actually closed; retry once; otherwise fail (the run's error
    handler captures page source + screenshot).
    """
    print('Step: tap "Done" on Delivery details …')
    if not _wait_for_label(driver, contains="Delivery details", timeout=12.0):
        print("  'Delivery details' title not confirmed — looking for Done")
    time.sleep(_MED)

    try:
        size = driver.get_window_size() or {}
        sw = float(size.get("width") or 390)
        sh = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        sw, sh = 390.0, 844.0

    done_strategies = (
        (AppiumBy.ACCESSIBILITY_ID, "Done"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Done" OR name == "Done")',
        ),
        (
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND (label == "Done" OR name == "Done")',
        ),
    )

    def _locate_done(timeout: float):
        """(el, dx, dy, via) — bottom Done CTA, or coord fallback with el=None."""
        try:
            el, by, value = find_first(driver, done_strategies, timeout=timeout)
            r = _safe_rect(el) or {}
            dx = float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0
            dy = float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0
            # Prefer bottom CTA if multiple Done matches.
            if dy < sh * 0.65:
                try:
                    els = driver.find_elements(
                        AppiumBy.IOS_PREDICATE,
                        'visible == 1 AND (label == "Done" OR name == "Done")',
                    )
                    for cand in els[:8]:
                        cr = _safe_rect(cand)
                        if not cr:
                            continue
                        cy2 = float(cr.get("y", 0)) + float(cr.get("height", 0)) / 2.0
                        if cy2 > dy:
                            el = cand
                            r = cr
                            dx = float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0
                            dy = cy2
                            by, value = AppiumBy.IOS_PREDICATE, "Done:bottom"
                except Exception:  # noqa: BLE001
                    pass
            return el, dx, dy, f"{by}={value!r}"
        except TimeoutError:
            # Known layout: green Done @ (16,742,358×56).
            dx, dy = sw / 2.0, sh - 60.0
            print(f'  "Done" not in a11y — coord fallback @ ({dx:.0f},{dy:.0f})')
            return None, dx, dy, "coord_done"

    def _done_actionable(el) -> tuple[bool, str]:
        """Enabled, still labelled 'Done', and no visible activity indicator."""
        if el is None:
            return True, "coord (cannot inspect)"
        try:
            if not el.is_enabled():
                return False, "disabled"
            lab = (el.get_attribute("label") or "").strip()
            if lab and lab != "Done":
                return False, f"label={lab!r}"
        except Exception as exc:  # noqa: BLE001
            return False, f"stale ({type(exc).__name__})"
        try:
            spin = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeActivityIndicator" AND visible == 1',
            )
            if spin:
                return False, "activity indicator visible"
        except Exception:  # noqa: BLE001
            pass
        return True, "enabled"

    def _wait_actionable(timeout: float):
        end = time.monotonic() + timeout
        el, dx, dy, via = _locate_done(8.0)
        ok, why = _done_actionable(el)
        while not ok and time.monotonic() < end:
            time.sleep(0.4)
            el, dx, dy, via = _locate_done(2.0)
            ok, why = _done_actionable(el)
        if not ok:
            print(f"  Done not confirmed actionable ({why}) — tapping anyway")
        return el, dx, dy, via, why

    attempts: list[dict] = []
    closed = False
    for attempt in (1, 2):
        el, dx, dy, via, why = _wait_actionable(10.0)
        try:
            _tap_center(driver, dx, dy)
        except Exception:  # noqa: BLE001
            if el is not None:
                el.click()
        markers.mark(
            "delivery_done_tapped", x=dx, y=dy, via=via, attempt=attempt, state=why
        )
        print(
            f'  "Done" tapped via {via!r} @ ({dx:.0f},{dy:.0f}) '
            f"[attempt {attempt}, {why}]"
        )
        # Sheet closed = "Delivery details" gone (absent from checkout dumps).
        closed = _wait_for_label_gone(
            driver, contains="Delivery details", timeout=8.0
        )
        attempts.append({"attempt": attempt, "via": via, "state": why, "closed": closed})
        if closed:
            markers.mark("delivery_details_closed", attempt=attempt)
            break
        print(f"  Delivery details still open after Done (attempt {attempt})")

    outcome["delivery_done_attempts"] = attempts
    if not closed:
        _mark_step(
            outcome, "delivery_done", status="failed", x=dx, y=dy, via=via,
            attempts=len(attempts),
        )
        raise AssertionError(
            'Delivery details did not close after tapping "Done" twice '
            f"(last state: {why})"
        )
    _mark_step(
        outcome, "delivery_done", status="ok", x=dx, y=dy, via=via,
        attempts=len(attempts),
    )
    outcome["delivery_done_tapped"] = True


def _tap_schedule_and_save(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """On checkout after Done, tap the Schedule and save delivery option."""
    print('Step: tap "Schedule and save" …')
    # Wait for checkout to settle after Delivery details closes.
    end = time.monotonic() + 12.0
    while time.monotonic() < end:
        try:
            hits = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'visible == 1 AND ('
                'label CONTAINS[c] "Schedule and save" OR '
                'name CONTAINS[c] "Schedule and save" OR '
                'label CONTAINS[c] "Continue" OR name == "Continue")',
            )
            if hits:
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.35)
    time.sleep(_MED)

    try:
        size = driver.get_window_size() or {}
        sw = float(size.get("width") or 390)
        sh = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        sw, sh = 390.0, 844.0

    cx = cy = None
    via = "schedule_and_save"
    strategies = (
        (AppiumBy.ACCESSIBILITY_ID, "Schedule and save"),
        (
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label CONTAINS[c] "Schedule and save" OR '
            'name CONTAINS[c] "Schedule and save")',
        ),
        (
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND ('
            'label CONTAINS[c] "As soon as" OR '
            'name CONTAINS[c] "As soon as")',
        ),
    )
    try:
        el, by, value = find_first(driver, strategies, timeout=8.0)
        r = _safe_rect(el) or {}
        x = float(r.get("x", 0))
        y = float(r.get("y", 0))
        w = float(r.get("width", 0))
        h = float(r.get("height", 0))
        # Tap the row body (not far-right price).
        cx = x + min(w * 0.40, sw * 0.45)
        cy = y + h / 2.0
        via = f"{by}={value!r}"
    except TimeoutError:
        # Typical layout: Schedule row sits under Priority/Fast (~y 320–420).
        cx, cy = sw * 0.40, sh * 0.42
        via = "coord_schedule_and_save"
        print(
            f'  "Schedule and save" not in a11y — '
            f"coord fallback @ ({cx:.0f},{cy:.0f})"
        )

    assert cx is not None and cy is not None
    _tap_center(driver, cx, cy)
    markers.mark("schedule_and_save_tapped", x=cx, y=cy, via=via)
    _mark_step(
        outcome, "schedule_and_save", status="ok", x=cx, y=cy, via=via
    )
    outcome["schedule_and_save_tapped"] = True
    print(f'  "Schedule and save" tapped via {via!r} @ ({cx:.0f},{cy:.0f})')
    time.sleep(_SLOW)


def _parse_schedule_ui_from_page_source(
    driver, sw: float, sh: float
) -> tuple[list[tuple[float, float, float, str]], list[tuple[float, float, float, str]]]:
    """One page_source parse → (day chips L→R, time slots T→B).

    Avoids slow find_elements + get_attribute loops that previously took ~100s
    and pushed the time tap past the recording window.
    """
    import xml.etree.ElementTree as ET

    days: list[tuple[float, float, float, str]] = []
    slots: list[tuple[float, float, float, str]] = []
    try:
        root = ET.fromstring(driver.page_source or "")
    except Exception:  # noqa: BLE001
        return days, slots

    month_bits = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )
    weekday_bits = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

    for el in root.iter():
        if el.attrib.get("visible") != "true":
            continue
        name = (el.attrib.get("name") or el.attrib.get("label") or "").strip()
        if not name:
            continue
        try:
            x = float(el.attrib.get("x", 0))
            y = float(el.attrib.get("y", 0))
            w = float(el.attrib.get("width", 0))
            h = float(el.attrib.get("height", 0))
        except ValueError:
            continue

        low = name.lower()
        # Day chips: "Today, $0" / "Thu, Oct 8, $0" — on-screen only.
        is_day = (
            name.startswith("Today")
            or (
                any(m in name for m in month_bits)
                and any(d in name for d in weekday_bits)
            )
        )
        if is_day and 90 <= y <= sh * 0.32 and 50 <= w <= sw * 0.55 and 36 <= h <= 120:
            if 0 <= x < sw - 8:
                days.append((x, min(x + w / 2.0, sw - 12), y + h / 2.0, name[:60]))
            continue

        # Time rows: "Noon–2pm, $0" / "6pm–8pm, $0"
        is_time = (
            ("–" in name or "-" in name)
            and any(k in low for k in ("pm", "am", "noon"))
            and "schedule" not in low
        )
        if is_time and 200 <= y <= sh * 0.88 and w >= 100 and 28 <= h <= 90:
            if 0 <= x < sw * 0.85:
                tx = max(24.0, min(x + min(w * 0.30, 90.0), sw * 0.45))
                slots.append((y, tx, y + h / 2.0, name[:80]))

    days.sort(key=lambda t: t[0])
    slots.sort(key=lambda t: t[0])

    def _dedupe(rows: list, key_i: int, gap: float) -> list:
        out: list = []
        for row in rows:
            if out and abs(row[key_i] - out[-1][key_i]) < gap:
                continue
            out.append(row)
        return out

    return _dedupe(days, 0, 36), _dedupe(slots, 0, 22)


def _pick_from_last_three(items: list, *, kind: str):
    """Return one item from the last three of a sorted list (prefer last)."""
    if not items:
        raise AssertionError(f"No {kind} options found on Schedule a time")
    band = items[-3:] if len(items) >= 3 else items
    return band[-1]


def _swipe_h(driver, *, y: float, sw: float, right_to_left: bool = True) -> None:
    fx, tx = (sw * 0.82, sw * 0.18) if right_to_left else (sw * 0.18, sw * 0.82)
    driver.execute_script(
        "mobile: dragFromToForDuration",
        {
            "duration": 0.28,
            "fromX": int(fx),
            "fromY": int(y),
            "toX": int(tx),
            "toY": int(y),
        },
    )


def _swipe_v(driver, *, sw: float, sh: float, up: bool = True) -> None:
    fy, ty = (sh * 0.75, sh * 0.40) if up else (sh * 0.40, sh * 0.75)
    driver.execute_script(
        "mobile: dragFromToForDuration",
        {
            "duration": 0.30,
            "fromX": int(sw * 0.50),
            "fromY": int(fy),
            "toX": int(sw * 0.50),
            "toY": int(ty),
        },
    )


class ScheduleSlotMismatch(AssertionError):
    """Checkout shows a different delivery slot than the one intended."""


class ScheduleSlotUnverified(AssertionError):
    """The delivery slot could not be tapped safely or read back from checkout."""


_D_SLOT_TIME_RE = re.compile(
    r"((?:\d{1,2}(?::\d{2})?\s*(?:am|pm)|noon|midnight)\s*[–\-—]\s*"
    r"(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)|noon|midnight))", re.I)


def _d_norm(text: str) -> str:
    t = (text or "").replace("—", "–").replace("-", "–")
    return re.sub(r"\s+", " ", t).strip().lower()


def _d_slot_time(label: str) -> str:
    m = _D_SLOT_TIME_RE.search(label or "")
    return re.sub(r"\s+", "", m.group(1)) if m else (label or "").split(",", 1)[0].strip()


def _d_fee(text: str) -> float | None:
    t = text or ""
    if re.search(r"\bfree\b", t, re.I):
        return 0.0
    m = re.search(r"\$\s*(\d+(?:\.\d{1,2})?)", t)
    return float(m.group(1)) if m else None


def _d_day_text(day_label: str) -> str:
    """'Sat, Oct 17, $1.99–$0' → 'Sat, Oct 17'; 'Today, $0' → 'Today'."""
    parts = [p.strip() for p in (day_label or "").split(",")]
    keep = [p for p in parts if p and "$" not in p]
    return ", ".join(keep)


def _d_day_aliases(day: str) -> set[str]:
    """Accept 'Today'/'Tomorrow' or their 'Wed, Oct 14' form."""
    d = _d_norm(day)
    out = {d}
    now = datetime.now()
    for word, delta in (("today", 0), ("tomorrow", 1)):
        if d == word:
            import datetime as _dt
            x = now + _dt.timedelta(days=delta)
            out.add(_d_norm(f"{x.strftime('%a')}, {x.strftime('%b')} {x.day}"))
    return out


def _d_live_slot_rect(driver, label: str, *, sh: float) -> dict | None:
    """Live frame of the one on-screen slot row named exactly `label`."""
    q = (label or "").replace("\\", "\\\\").replace('"', '\\"')
    try:
        els = driver.find_elements(AppiumBy.IOS_PREDICATE, f'name == "{q}" OR label == "{q}"')
    except Exception:  # noqa: BLE001
        return None
    hits = []
    for el in els[:6]:
        r = _safe_rect(el)
        if not r:
            continue
        y, h = float(r.get("y", 0)), float(r.get("height", 0))
        if 28 <= h <= 90 and 150.0 <= y and y + h <= sh - 34.0:
            hits.append({"x": float(r.get("x", 0)), "y": y, "w": float(r.get("width", 0)), "h": h})
    uniq = {round(hh["y"]) for hh in hits}
    return hits[0] if len(uniq) == 1 else None


def _d_read_checkout_slot(driver, *, sh: float, timeout: float = 6.0) -> dict | None:
    """Read the scheduled slot row on checkout → {day, time, fee, fee_text, label}."""
    end = time.monotonic() + timeout
    first = True
    while first or time.monotonic() < end:
        first = False
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeButton" AND label CONTAINS[c] "More times available"',
            )
        except Exception:  # noqa: BLE001
            els = []
        rows = []
        for el in els[:6]:
            try:
                r = _safe_rect(el)
                if r and not (0 <= float(r.get("y", -1)) <= sh):
                    continue
                lab = (el.get_attribute("label") or "").strip()
                val = (el.get_attribute("value") or "").strip()
            except Exception:  # noqa: BLE001
                continue
            m = _D_SLOT_TIME_RE.search(lab)
            if not m:
                continue
            day = lab[: m.start()].strip(" ,")
            rows.append({"label": lab, "day": day, "time": re.sub(r"\s+", "", m.group(1)),
                         "fee_text": val, "fee": _d_fee(val)})
        distinct = {(_d_norm(x["day"]), _d_norm(x["time"]), x["fee"]) for x in rows}
        if len(distinct) == 1:
            return rows[0]
        if len(distinct) > 1:
            return {"ambiguous": True, "labels": [x["label"] for x in rows]}
        time.sleep(0.5)
    return None


def _d_slot_mismatch(intended: dict, actual: dict) -> list[str]:
    problems = []
    if _d_norm(actual.get("time")) != _d_norm(intended.get("time")):
        problems.append(f"time {actual.get('time')!r} ≠ {intended.get('time')!r}")
    if _d_norm(actual.get("day")) not in _d_day_aliases(intended.get("day") or ""):
        problems.append(f"day {actual.get('day')!r} ≠ {intended.get('day')!r}")
    fi, fa = intended.get("fee"), actual.get("fee")
    if fi is not None and fa is not None and abs(fi - fa) > 0.005:
        problems.append(f"fee ${fa:.2f} ≠ ${fi:.2f}")
    return problems













def _select_schedule_day_and_time(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """On Schedule a time: pick a day from last 3, then a time from last 3."""
    print("Step: select day + time on Schedule a time …")
    if not _wait_for_label(driver, contains="Schedule a time", timeout=12.0):
        raise AssertionError(
            'Could not open "Schedule a time" after Schedule and save'
        )
    time.sleep(_MED)

    try:
        size = driver.get_window_size() or {}
        sw = float(size.get("width") or 390)
        sh = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        sw, sh = 390.0, 844.0

    day_y = sh * 0.18
    # One quick swipe to reveal later days, then a single page_source parse.
    try:
        _swipe_h(driver, y=day_y, sw=sw, right_to_left=True)
        time.sleep(0.35)
    except Exception:  # noqa: BLE001
        pass

    days, _slots0 = _parse_schedule_ui_from_page_source(driver, sw, sh)
    if len(days) < 1:
        # Basket D: no blind coordinate tap — the slot could not be verified.
        outcome["schedule_verified"] = False
        markers.mark("schedule_slot_unverified", reason="no_day_chips")
        raise ScheduleSlotUnverified(
            "schedule_slot_unverified: no day chips found on Schedule a time — stopped before payment"
        )
    _x, dx, dy, day_label = _pick_from_last_three(days, kind="day")
    dx = max(12.0, min(dx, sw - 12.0))
    dy = max(100.0, min(dy, sh * 0.30))
    _tap_center(driver, dx, dy)
    markers.mark("schedule_day_tapped", x=dx, y=dy, label=day_label)
    print(f"  day tapped: {day_label!r} @ ({dx:.0f},{dy:.0f})")
    time.sleep(0.45)

    # Two fast scrolls to the end of the time list (no a11y scans between).
    for _ in range(2):
        try:
            _swipe_v(driver, sw=sw, sh=sh, up=True)
            time.sleep(0.30)
        except Exception:  # noqa: BLE001
            break

    _days1, slots = _parse_schedule_ui_from_page_source(driver, sw, sh)
    if len(slots) < 1:
        outcome["schedule_verified"] = False
        markers.mark("schedule_slot_unverified", reason="no_time_slots")
        raise ScheduleSlotUnverified(
            "schedule_slot_unverified: no time slots found on Schedule a time — stopped before payment"
        )
    print(
        f"  visible time slots ({len(slots)}): "
        + ", ".join(s[3] for s in slots[-5:])
    )
    # Same rule as before: the last of the last three listed slots.
    _y, _tx_ps, ty_ps, time_label = _pick_from_last_three(slots, kind="time")
    intended = {
        "day": _d_day_text(day_label),
        "time": _d_slot_time(time_label),
        "fee": _d_fee(time_label),
        "slot_label": time_label,
        "day_label": day_label,
    }
    outcome["schedule_intended"] = intended
    # Basket D: re-read the intended row's frame live (the list may have
    # moved since the page source) and check it against its neighbour before
    # the one tap. Basket H run 20261014_144326 tapped (90,717) — the clamped y —
    # and hit 4pm–6pm instead of the intended 3pm–6pm.
    prev_label = slots[-2][3] if len(slots) >= 2 else None
    geo = _d_live_slot_rect(driver, time_label, sh=sh)
    if geo is None:
        outcome["schedule_verified"] = False
        markers.mark("schedule_slot_unverified", reason="slot_row_not_found_live", slot=time_label)
        raise ScheduleSlotUnverified(
            f"schedule_slot_unverified: slot row {time_label!r} not found (or not unique) on screen "
            "at tap time — stopped before tapping"
        )
    tx = max(24.0, min(geo["x"] + min(geo["w"] * 0.30, 90.0), sw * 0.45))
    ty = geo["y"] + geo["h"] / 2.0
    if prev_label:
        prev = _d_live_slot_rect(driver, prev_label, sh=sh)
        if prev is not None and not (prev["y"] + prev["h"] / 2.0 < ty - 12.0):
            outcome["schedule_verified"] = False
            markers.mark("schedule_slot_unverified", reason="row_order", slot=time_label,
                         prev=prev_label, prev_y=prev["y"], y=geo["y"])
            raise ScheduleSlotUnverified(
                f"schedule_slot_unverified: row {time_label!r} (y {geo['y']:.0f}) is not below "
                f"{prev_label!r} (y {prev['y']:.0f}) — geometry inconsistent, not tapped"
            )
    # Never clamp: the old min(ty, sh*0.85) = 717 pt moved the tap onto the
    # row above whenever the chosen row sat lower (every run since 2026-10-08).
    # The whole row must be on screen above the home indicator, else stop.
    if not (200.0 <= geo["y"] and geo["y"] + geo["h"] <= sh - 34.0):
        outcome["schedule_verified"] = False
        markers.mark("schedule_slot_unverified", reason="row_out_of_band", y=geo["y"], h=geo["h"])
        raise ScheduleSlotUnverified(
            f"schedule_slot_unverified: slot row {time_label!r} (y {geo['y']:.0f}, h {geo['h']:.0f}) is not "
            "fully on screen — not tapped (no clamped tap)"
        )
    if abs(ty - ty_ps) > 6.0:
        print(f"    slot row frame moved: page source y {ty_ps:.0f} → live y {ty:.0f} — using live frame")
        markers.mark("schedule_slot_frame_moved", slot=time_label, page_source_y=ty_ps, live_y=ty)
    # One tap only (the old double tap could land on checkout behind the
    # closing sheet).
    _tap_center(driver, tx, ty)
    markers.mark("schedule_time_tapped", x=tx, y=ty, label=time_label)
    print(f"  time tapped: {time_label!r} @ ({tx:.0f},{ty:.0f})")
    # Short pause so the selected radio is visible in the recording.
    time.sleep(0.8)

    # Some builds show Save / Done / Select after choosing a slot. One query
    # for all four (was 4 separate find_elements); same bottom-30% filter and
    # the same preference order when several match.
    confirm_order = ("Save", "Done", "Select", "Confirm")
    names = ", ".join(f'"{lab}"' for lab in confirm_order)
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            f'type == "XCUIElementTypeButton" AND visible == 1 AND '
            f"(label IN {{{names}}} OR name IN {{{names}}})",
        )
    except Exception:  # noqa: BLE001
        els = []
    best: tuple[int, float, float, str] | None = None
    for el in els[:8]:
        try:
            r = _safe_rect(el)
            if not r:
                continue
            y = float(r.get("y", 0))
            if y < sh * 0.70:
                continue
            texts = {el.get_attribute("label") or "", el.get_attribute("name") or ""}
            hits = [lab for lab in confirm_order if lab in texts]
            if not hits:
                continue
            lab = hits[0]
            rank = confirm_order.index(lab)
            if best is None or rank < best[0]:
                bx = float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0
                by = y + float(r.get("height", 0)) / 2.0
                best = (rank, bx, by, lab)
        except Exception:  # noqa: BLE001
            continue
    if best is not None:
        _rank, bx, by, lab = best
        _tap_center(driver, bx, by)
        print(f'  schedule confirm "{lab}" tapped @ ({bx:.0f},{by:.0f})')
        markers.mark("schedule_confirm_tapped", label=lab, x=bx, y=by)
        time.sleep(_SLOW)

    # Don't move on while the Schedule sheet is still up / closing: wait for
    # "Schedule a time" to leave the screen (it is absent from every checkout
    # dump), capped so a sheet that stays open behaves as before.
    if _wait_for_label_gone(driver, contains="Schedule a time", timeout=8.0):
        markers.mark("schedule_sheet_closed")
    else:
        outcome["schedule_verified"] = False
        markers.mark("schedule_slot_unverified", reason="sheet_still_open")
        raise ScheduleSlotUnverified(
            'schedule_slot_unverified: "Schedule a time" still open after 8 s — the checkout '
            "selection cannot be read; stopped before payment"
        )

    # Basket D: read the selection back from the checkout row
    # ("Sat, Oct 17, 4pm–6pm, More times available", value "$1.99").
    actual = _d_read_checkout_slot(driver, sh=sh, timeout=6.0)
    outcome["schedule_actual"] = actual
    if actual is None or actual.get("ambiguous"):
        outcome["schedule_verified"] = False
        markers.mark("schedule_slot_unverified", reason="checkout_row_unreadable" if actual is None
                     else "checkout_row_ambiguous")
        raise ScheduleSlotUnverified(
            "schedule_slot_unverified: the scheduled slot could not be read from checkout "
            f"({'not found' if actual is None else 'ambiguous: ' + str(actual.get('labels'))}) "
            "— stopped before payment"
        )
    problems = _d_slot_mismatch(intended, actual)
    print(f"  checkout shows: day={actual['day']!r} time={actual['time']!r} fee={actual['fee_text']!r}")
    if problems:
        outcome["schedule_verified"] = False
        markers.mark("schedule_slot_mismatch", intended=f"{intended['day']} {intended['time']}",
                     actual=f"{actual['day']} {actual['time']}", problems="; ".join(problems))
        raise ScheduleSlotMismatch(
            f"schedule_slot_mismatch: intended {intended['day']} {intended['time']} "
            f"(fee {intended['fee']}) but checkout shows {actual['day']} {actual['time']} "
            f"(fee {actual['fee_text']!r}) — {'; '.join(problems)}; stopped before payment"
        )
    outcome["schedule_verified"] = True
    outcome["schedule_day"] = actual["day"]
    outcome["schedule_time"] = f"{actual['time']}, {actual['fee_text']}" if actual["fee_text"] else actual["time"]
    outcome["schedule_fee"] = actual["fee"]
    markers.mark("schedule_slot_verified", day=actual["day"], time=actual["time"], fee=actual["fee"])
    _mark_step(
        outcome,
        "schedule_day_time",
        status="ok",
        day=outcome["schedule_day"],
        time=outcome["schedule_time"],
    )
    print(f"  scheduled (verified on checkout): day={outcome['schedule_day']!r} "
          f"time={outcome['schedule_time']!r}")


# ---------------------------------------------------------------------------
# Pay with — select the card that is NOT currently selected (rev 2).
#
#   * The currently selected card is whatever the sticky bottom footer says
#     ("Pay with   Visa *NNNN"). "Default" is only a label, not selection.
#   * Selection is sticky across orders, so the target is computed at runtime:
#     "the tile whose last-4 is NOT the footer's last-4" — never hardcoded.
#   * Verification = footer last-4 changed after the tap.
#   * The footer chip is not a toggle; we only swipe until the inline
#     "Pay with" carousel is on screen. Continue is never tapped.
# ---------------------------------------------------------------------------

_LAST4_RE = re.compile(r"\*\s*(\d{4})\b")


_D_PS_FOOTER: dict = {}

_ADD_PAYMENT_WORDS = ("add a payment", "add payment", "add card")


def _screen_wh(driver) -> tuple[float, float]:
    try:
        size = driver.get_window_size() or {}
        return float(size.get("width") or 390), float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        return 390.0, 844.0


def _el_blob(el) -> str:
    try:
        return (
            f"{el.get_attribute('label') or ''} "
            f"{el.get_attribute('name') or ''} "
            f"{el.get_attribute('value') or ''}"
        ).strip()
    except Exception:  # noqa: BLE001
        return ""


def _footer_last4(driver) -> str | None:
    """Last-4 of the card shown in the sticky bottom 'Pay with  Visa *NNNN' bar.

    Prefers the lowest on-screen match so the carousel tiles never win.
    """
    _sw, sh = _screen_wh(driver)
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND (label CONTAINS "*" OR name CONTAINS "*" OR '
            'value CONTAINS "*")',
        )
    except Exception:  # noqa: BLE001
        els = []
    best: tuple[float, str] | None = None
    for el in els[:40]:
        # Position first (1 call): carousel tiles above the footer band are
        # dropped before any attribute reads. Same result as before — lowest
        # matching element in the bottom ~45% — with fewer round-trips.
        r = _safe_rect(el)
        if not r:
            continue
        y = float(r.get("y", 0))
        if y < sh * 0.55:  # footer lives in the bottom ~45%
            continue
        if best is not None and y <= best[0]:
            continue  # cannot beat the current lowest match
        try:
            m = _LAST4_RE.search(el.get_attribute("label") or "")
        except Exception:  # noqa: BLE001
            m = None
        if not m:
            m = _LAST4_RE.search(_el_blob(el))
        if not m:
            continue
        best = (y, m.group(1))
    return best[1] if best else None


def _pay_with_heading_y(driver) -> float | None:
    """Y of the inline 'Pay with' heading (Button name="Pay with" value="Edit").

    Live dump: heading Button at y≈766 (h 38) before scrolling; tiles 38 px
    below it. Returns None when the heading is not on screen.
    """
    _sw, sh = _screen_wh(driver)
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND type == "XCUIElementTypeButton" AND '
            '(name == "Pay with" OR label == "Pay with")',
        )
    except Exception:  # noqa: BLE001
        els = []
    for el in els[:4]:
        r = _safe_rect(el)
        if r and 80 <= float(r.get("y", 0)) <= sh * 0.80:
            return float(r.get("y", 0))
    return None


# Sticky footer (Pay with chip + Continue) starts here on 844-pt screens.
_CHECKOUT_FOOTER_TOP_FRAC = 0.77


def _collect_payment_tiles(driver, *, pay_y: float | None = None) -> list[dict]:
    """Card tiles under Pay with, left → right, from page_source.

    Live dump structure:
      Other (16,804,110×92)   → StaticText "*5216", "Visa", "Default"
      Other (134,804,110×92)  → StaticText "*1234", "Visa"
      Other (252,804,148×92)  → StaticText "Add a payment method"
    The containers are unlabeled, so we locate the "*NNNN" text and use its
    parent container's rect as the tile. Includes off-screen tiles (y may be
    > screen) so the caller can decide whether to scroll more.
    """
    import xml.etree.ElementTree as ET

    sw, sh = _screen_wh(driver)
    tiles: list[dict] = []
    _D_PS_FOOTER.clear()
    try:
        root = ET.fromstring(driver.page_source or "")
    except Exception:  # noqa: BLE001
        return tiles
    parent = {c: p for p in root.iter() for c in p}
    # Basket D: the footer card ("Visa *NNNN", lowest visible match in the
    # bottom ~45%) from this same read — what _footer_last4 returns, without
    # its separate query + per-element reads (~8.6 s in run 20261015_073354).
    for el in root.iter():
        a = el.attrib
        if a.get("visible") != "true":
            continue
        try:
            fy = float(a.get("y", -1))
        except ValueError:
            continue
        if not (sh * 0.55 <= fy <= sh):
            continue
        fm = _LAST4_RE.search(a.get("label") or "") or _LAST4_RE.search(
            " ".join(a.get(k) or "" for k in ("label", "name", "value")))
        if fm and ("y" not in _D_PS_FOOTER or fy > _D_PS_FOOTER["y"]):
            _D_PS_FOOTER.update(y=fy, last4=fm.group(1))

    for el in root.iter():
        if el.attrib.get("type") != "XCUIElementTypeStaticText":
            continue
        text = (el.attrib.get("name") or el.attrib.get("label") or el.attrib.get("value") or "").strip()
        m = _LAST4_RE.search(text)
        if not m or len(text) > 12:   # tile text is just "*5216"; footer is "Visa *5216"
            continue
        # Walk up to the tile container (80–200 wide, 60–200 tall).
        node = el
        tile = None
        for _ in range(4):
            node = parent.get(node)
            if node is None:
                break
            try:
                w = float(node.attrib.get("width", 0))
                h = float(node.attrib.get("height", 0))
            except ValueError:
                continue
            if 80 <= w <= 200 and 60 <= h <= 200:
                tile = node
                break
        if tile is None:
            continue
        try:
            x = float(tile.attrib.get("x", 0))
            y = float(tile.attrib.get("y", 0))
            w = float(tile.attrib.get("width", 0))
            h = float(tile.attrib.get("height", 0))
        except ValueError:
            continue
        if y < 60:
            continue
        kids = " ".join(
            (k.attrib.get("name") or k.attrib.get("label") or "") for k in tile.iter()
        ).lower()
        if any(wd in kids for wd in _ADD_PAYMENT_WORDS):
            continue
        tiles.append(
            {
                "x": x,
                "w": w,
                "cx": x + w / 2.0,
                "cy": y + h / 2.0,
                "label": f"{'Visa ' if 'visa' in kids else ''}*{m.group(1)}"
                + (" (Default)" if "default" in kids else ""),
                "last4": m.group(1),
                "selected": None,
                "is_default": "default" in kids,
                "onscreen": (y + h / 2.0) < sh * _CHECKOUT_FOOTER_TOP_FRAC - 10 and x + w / 2.0 <= sw,
            }
        )
    tiles.sort(key=lambda t: t["x"])
    out: list[dict] = []
    for t in tiles:
        if out and abs(t["x"] - out[-1]["x"]) < 30:
            continue
        out.append(t)
    return out


def _count_visible_pay_with(driver) -> int:
    """How many 'Pay with' labels are on screen (1 = footer only, 2 = heading too)."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'visible == 1 AND (name == "Pay with" OR label == "Pay with")',
        )
    except Exception:  # noqa: BLE001
        return 0
    ys: list[float] = []
    for el in els[:6]:
        r = _safe_rect(el)
        if r:
            ys.append(float(r.get("y", 0)))
    ys.sort()
    n = 0
    for y in ys:
        if not n or y - ys[n - 1] > 30:
            n += 1
    return n


def _payment_carousel_visible(driver) -> bool:
    """True only when BOTH 'Pay with' labels are on screen (heading + footer)
    AND every card tile centre is above the sticky footer."""
    return _payment_carousel_state(driver)[0]


def _payment_carousel_state(driver) -> tuple[bool, int, list[dict] | None]:
    """(visible, 'Pay with' label count, tiles or None if not parsed).

    Same test as _payment_carousel_visible; also returns what it read so the
    nudge loop can log it without re-querying the same screen.
    """
    n_labels = _count_visible_pay_with(driver)
    if n_labels < 2:
        return False, n_labels, None
    tiles = _collect_payment_tiles(driver)
    return bool(tiles) and all(t["onscreen"] for t in tiles), n_labels, tiles


class PaymentCardsNotFound(AssertionError):
    """The Pay-with card tiles could not be confirmed on screen (bounded nudges)."""



def _scroll_checkout_to_pay_with(
    driver, markers: MarkerLog, *, max_swipes: int = 8
) -> list[dict]:
    """Bring the Pay-with tile row above the sticky footer; return its tiles.

    Basket B (runs 20261014_121033 / 125518): WDA's `mobile: scroll … toVisible`
    blocked ~2 min per call, so it is NOT used here. Only short `mobile: swipe`
    nudges, each followed by a visibility check: the cheap 'Pay with' label
    count first (heading + footer = 2), the page-source tile read only once the
    heading is on screen. A nudge that overshoots the heading is answered with
    one nudge back down. Stops after the cards are confirmed; raises
    PaymentCardsNotFound after `max_swipes` nudges — never a blind tap.
    """
    print('Step: scroll checkout until Pay-with cards are above the footer …')
    t0 = time.monotonic()
    sw, sh = _screen_wh(driver)
    visible, n_labels, tiles = _payment_carousel_state(driver)
    if visible:
        markers.mark("pay_with_visible", swipes=0, via="already", seconds=round(time.monotonic() - t0, 1))
        print("  Pay-with card tiles already on screen")
        return tiles or []
    seen_heading = n_labels >= 2
    for i in range(max_swipes):
        # Overshoot: the heading was on screen and is gone again → go back down.
        direction = "down" if (seen_heading and n_labels < 2) else "up"
        print(f"  nudge #{i + 1} ({direction}): pay_with_labels={n_labels} "
              f"tiles={len(tiles) if tiles is not None else '?'}")
        try:
            driver.execute_script("mobile: swipe", {"direction": direction, "velocity": 900})
        except Exception:  # noqa: BLE001
            try:
                fy, ty = (sh * 0.60, sh * 0.30) if direction == "up" else (sh * 0.30, sh * 0.60)
                driver.execute_script(
                    "mobile: dragFromToForDuration",
                    {"duration": 0.05, "fromX": int(sw * 0.5), "fromY": int(fy),
                     "toX": int(sw * 0.5), "toY": int(ty)},
                )
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.45)
        visible, n_labels, tiles = _payment_carousel_state(driver)
        seen_heading = seen_heading or n_labels >= 2
        if visible:
            secs = round(time.monotonic() - t0, 1)
            markers.mark("pay_with_visible", via="swipe", swipes=i + 1, seconds=secs)
            print(f"  Pay-with tiles on screen after nudge #{i + 1} ({secs}s)")
            return tiles or []
    secs = round(time.monotonic() - t0, 1)
    markers.mark("pay_with_visible", partial=True, swipes=max_swipes, seconds=secs)
    print(f"  PAY-WITH CARDS NOT FOUND after {max_swipes} nudges ({secs}s) — stopping, no card tapped")
    raise PaymentCardsNotFound(
        f"payment_cards_not_found: Pay-with card tiles not confirmed on screen after "
        f"{max_swipes} nudges — no card tapped")


def _select_second_payment_card(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """Tap the card tile that is NOT currently selected; verify footer last-4 changes."""
    print("Step: select the unselected payment card under Pay with …")
    t_step = time.monotonic()
    # Basket D: the tiles read by the visibility check are used directly (same
    # screen, nothing in between) — no second page-source read.
    tiles = [t for t in _scroll_checkout_to_pay_with(driver, markers) if t["onscreen"]]
    sw, sh = _screen_wh(driver)
    pay_y = None

    before = _D_PS_FOOTER.get("last4") or _footer_last4(driver)
    print(f"  footer before: *{before}"
          + (" (same page read as the tiles)" if _D_PS_FOOTER.get("last4") else ""))
    print(
        f"  tiles ({len(tiles)}): "
        + ", ".join(
            f"[{t['label'][:28]!r} last4={t['last4']} sel={t['selected']}]"
            for t in tiles
        )
    )

    # Ordered tap candidates, most likely target first: (cx, cy, label, via).
    candidates: list[tuple[float, float, str, str]] = []

    # 1) Tiles with a readable last-4 different from the footer.
    if before:
        for t in tiles:
            if t["last4"] and t["last4"] != before:
                candidates.append(
                    (t["cx"], t["cy"], t["label"], "last4_differs_from_footer")
                )
    # 2) Tiles whose a11y 'selected' is explicitly false (when some tile is true).
    if any(t["selected"] is True for t in tiles):
        for t in tiles:
            if t["selected"] is False:
                candidates.append((t["cx"], t["cy"], t["label"], "a11y_not_selected"))
    # 3) Any tile whose last-4 is unknown / not the footer card — try each in order.
    for t in tiles:
        if t["last4"] != before:
            candidates.append((t["cx"], t["cy"], t["label"], "tile_order"))
    # Basket D: no blind coordinate taps — only real tiles read on screen.
    if not before or not candidates:
        outcome["payment_before_last4"] = before
        raise PaymentCardsNotFound(
            f"payment_cards_not_found: footer card *{before} / {len(tiles)} on-screen tile(s) — "
            "no verifiable other card to tap")

    # Dedupe by position.
    seen: set[str] = set()
    ordered: list[tuple[float, float, str, str]] = []
    for c in candidates:
        k = _tap_coord_key(c[0], c[1])
        if k in seen:
            continue
        seen.add(k)
        ordered.append(c)

    verified = False
    after = before
    chosen: tuple[float, float, str, str] | None = None
    for i, (cx, cy, label, via) in enumerate(ordered[:3]):
        print(f"  tap #{i + 1} via {via!r}: {label[:40]!r} @ ({cx:.0f},{cy:.0f})")
        _tap_center(driver, cx, cy)
        time.sleep(_SLOW)
        after = _footer_last4(driver)
        if after == before:
            time.sleep(0.8)  # footer chip animates; give it one more read
            after = _footer_last4(driver)
        print(f"    footer now: *{after}")
        markers.mark(
            "payment_tile_tapped",
            index=i + 1,
            x=cx,
            y=cy,
            label=label,
            via=via,
            footer_before=before,
            footer_after=after,
        )
        if after and after != before:
            verified = True
            chosen = (cx, cy, label, via)
            break

    outcome["payment_before_last4"] = before
    outcome["payment_after_last4"] = after
    if not verified or chosen is None:
        _mark_step(
            outcome,
            "second_payment_card",
            status="failed",
            footer_before=before,
            footer_after=after,
            tiles=[t["label"] for t in tiles],
        )
        outcome["second_payment_card_selected"] = None
        outcome["second_payment_card_tapped"] = True
        outcome["second_payment_card_verified"] = False
        try:
            size = driver.get_window_size() or {}
            print(f"  (debug) window={size}  heading_y={pay_y!r}  tiles={len(tiles)}")
        except Exception:  # noqa: BLE001
            pass
        raise AssertionError(
            f"Payment card not switched — footer still *{after} (was *{before})"
        )

    cx, cy, label, via = chosen
    chosen_label = f"Visa *{after}"
    _mark_step(
        outcome,
        "second_payment_card",
        status="ok",
        label=chosen_label,
        via=via,
        x=cx,
        y=cy,
        footer_before=before,
        footer_after=after,
    )
    outcome["second_payment_card_selected"] = chosen_label
    outcome["second_payment_card_tapped"] = True
    outcome["second_payment_card_verified"] = True
    outcome["pay_with_footer"] = f"Visa *{after}"
    markers.mark(
        "second_payment_card_selected",
        x=cx,
        y=cy,
        label=chosen_label,
        via=via,
        footer_before=before,
        footer_after=after,
        verified=True,
    )
    secs = round(time.monotonic() - t_step, 1)
    outcome["payment_switch_seconds"] = secs
    print(
        f"  ✓ switched payment *{before} → *{after} via {via!r} @ ({cx:.0f},{cy:.0f}) "
        f"— card step {secs}s"
    )
    time.sleep(_SLOW)


def _basket_artifacts_dir(settings):
    """Per-basket artifacts: artifacts/basket_D/"""
    base = settings.artifacts_dir
    if base.name == f"basket_D":
        return base
    return base / f"basket_D"


def run_basket_D(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Basket D: 25 items × 1 unit = 25."""
    case_id = TEST_CASE_ID
    case_name = TEST_CASE_NAME
    num_items = NUM_ITEMS
    units_each = UNITS_EACH
    sample_letter = BASKET_LETTER
    total_units = num_items * units_each

    if retailer != "instacart":
        raise ValueError(
            f"{case_name} is Instacart-only (use --retailer instacart)."
        )

    # Scale recording with basket size (checkout edits need headroom).
    rec_limit = max(
        CART_RECORDING_SECONDS,
        min(
            int(settings.recording_time_limit_seconds),
            CART_RECORDING_SECONDS + num_items * 3 + total_units,
        ),
    )
    plan = describe_session(settings, retailer, bundle_id)
    plan["scenario"] = case_id
    plan["test_case"] = case_name
    plan["dry_run"] = dry_run
    plan["study_mode"] = "cancel_test"
    plan["place_order"] = False
    plan["max_item_price"] = MAX_ITEM_PRICE
    plan["basket_sample"] = sample_letter
    plan["num_items"] = num_items
    plan["units_each"] = units_each
    plan["total_units"] = total_units
    plan["recording"] = True
    plan["qty_method"] = QTY_METHOD
    plan["cart_tap_mode"] = CART_TAP_MODE if QTY_METHOD == "cart" else None
    plan["cart_w3c_press_ms"] = int(_CART_W3C_PRESS_S * 1000) if QTY_METHOD == "cart" else None
    plan["cart_w3c_gap_ms"] = int(round(_CART_W3C_GAP_S * 1000)) if QTY_METHOD == "cart" else None
    plan["cart_w3c_spacing"] = _CART_W3C_SPACING if QTY_METHOD == "cart" else None
    plan["recording_starts_at"] = "cart_page"
    plan["recording_duration_seconds"] = rec_limit
    add_step = (
        f"On Grocery Outlet store, add exactly {num_items} item(s) "
        f"({_price_cap_label(MAX_ITEM_PRICE)}; scroll if needed)"
    )
    qty_step = (
        f"Read-only check: all {num_items} cart rows at 1 ct (row price = store price; 0 taps)"
        if units_each <= 1
        else (
            f"For each of {num_items} cart lines: tap item → PDP bottom + "
            f"until {units_each} ct"
        )
    )
    plan["steps"] = [
        "Launch the Instacart app (cold start)",
        'On home screen, tap the Grocery Outlet icon below the "Offers" icon',
        add_step,
        "Tap the cart icon on the bottom green Basket bar",
        f"UNRECORDED read-only cart pre-check; an 'Out of stock' row is removed (trash) and replaced "
        f"from the store — as many as needed, ≤ {_D_OOS_MAX_ROUNDS} rounds / {_D_OOS_RECOVERY_S:.0f} s — then the cart is re-opened",
        f"START screen recording on cart / Basket for up to {rec_limit}s",
        qty_step,
        'Tap "Go to checkout"',
        'Tap the top-right ">" (chevron) on the checkout screen',
        'On Delivery details, tap "Change"',
        "Select an unselected delivery address → Select",
        'On Delivery details, tap bottom green "Done"',
        'On checkout, tap "Schedule and save"',
        "On Schedule a time, pick a day from the last 3 options",
        "Pick a time slot from the last 3 options",
        'Swipe up to "Pay with" and select the unselected payment card '
        "(verify footer last-4 changes; Continue is NOT tapped)",
    ]
    sample_s = (
        f" sample {sample_letter} ({num_items}×{units_each}={total_units})"
        if sample_letter
        else f" ({num_items}×{units_each})"
    )
    plan["notes"] = (
        f"{case_name}{sample_s} — Grocery Outlet → "
        f"add ({_price_cap_label(MAX_ITEM_PRICE)}) → Basket → record → "
        "read-only qty-1 check (0 taps) → checkout → Change → address → Done → Schedule and save → "
        "day/time → Pay with unselected card (no Continue)."
    )

    if dry_run:
        print(json.dumps({"status": "dry_run_ok", "plan": plan}, indent=2))
        return {"status": "dry_run_ok", "plan": plan}

    artifacts_dir = _basket_artifacts_dir(settings)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    prefix = f"{retailer}_{case_id}_{stamp}"
    result_path = artifacts_dir / f"{prefix}.json"
    page_source_path = artifacts_dir / f"{prefix}_page.xml"
    screenshot_path = artifacts_dir / f"{prefix}_blocker.png"
    video_path = artifacts_dir / f"{case_id}_{stamp}.mp4"

    markers = MarkerLog()
    appium_calls: list[dict] = []
    appium_calls_path = artifacts_dir / f"appium_calls_{stamp}.jsonl"
    driver = None
    recording = False
    outcome: dict = {
        "status": "starting",
        "test_case": case_name,
        "basket_sample": sample_letter,
        "num_items": num_items,
        "units_each": units_each,
        "total_units": total_units,
        "plan": plan,
        "markers": markers.events,
        "selectors": {},
        "checkout_steps": {},
        "grocery_outlet_tapped": False,
        "household_tapped": False,
        "item_added_to_cart": False,
        "item_price": None,
        "basket_opened": False,
        "recording_started": False,
        "cart_qty_after": None,
        "go_to_checkout_tapped": False,
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
        _install_call_timer(driver, markers, appium_calls)
        _apply_fast_wda_settings(driver)

        # 1) Launch Instacart
        print("Step: launch the Instacart app …")
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
                time.sleep(0.35)
                outcome["cold_start_terminated"] = True
            else:
                outcome["cold_start_terminated"] = False
            print(f"Activating {bundle_id} (cold start) …")
            driver.activate_app(bundle_id)
            time.sleep(1.2)
        except Exception as exc:  # noqa: BLE001
            outcome["cold_start_error"] = str(exc)
            print(f"Cold-start terminate/activate warning: {exc}")

        # Wait until Instacart is actually foreground before home taps.
        foreground = False
        for _wait_i in range(20):
            try:
                state = driver.query_app_state(bundle_id)
                outcome["app_state"] = int(state) if state is not None else None
            except Exception as exc:  # noqa: BLE001
                outcome["app_state_error"] = str(exc)
                outcome["app_state"] = None
            if outcome.get("app_state") == 4:
                foreground = True
                break
            if _wait_i in (4, 9, 14):
                try:
                    print("  re-activate Instacart (not foreground yet) …")
                    driver.activate_app(bundle_id)
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.5)
        print(
            f"App state for {bundle_id}: {outcome.get('app_state')} "
            f"(4=foreground, ready={foreground})"
        )

        markers.mark("app_opened", app_state=outcome.get("app_state"))
        _mark_step(
            outcome,
            "launch",
            status="ok",
            app_state=outcome.get("app_state"),
        )

        time.sleep(1.0)
        dismissed = _d_tap_dismissibles(
            driver, labels=INSTACART_DISMISS_LABELS, rounds=2
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
            _d_tap_dismissibles(driver, labels=INSTACART_DISMISS_LABELS, rounds=1)

        # 2) Home → Grocery Outlet below Offers
        _tap_grocery_outlet_below_offers(driver, markers, outcome)
        _wait_grocery_outlet_store(driver, markers)
        # Stop before any add / recording unless the store bar reads "Basket".
        _check_store_basket_empty(driver, markers, outcome)

        # 3) Store page → add exactly N items (no Household category step)
        if True:   # Basket D: the validated multi-item add loop (25 priced products)
            _add_n_items_under_max_price(
                driver,
                markers,
                outcome,
                count=num_items,
                max_price=MAX_ITEM_PRICE,
            )

        # 4) Bottom green bar → cart icon (Basket)
        _tap_basket_cart_icon(driver, markers, outcome)
        _wait_for_cart_view(driver, markers)
        time.sleep(0.5)

        # Refuse empty cart (Add miss) before starting the recording.
        footer_pre = _read_cart_footer_label(driver)
        total_pre = _parse_footer_total(footer_pre)
        if total_pre is not None and total_pre < 0.01:
            raise AssertionError(
                f"Cart is empty after Basket tap (footer={footer_pre!r}) — "
                "item was not added"
            )
        if _find_cart_line_button(driver) is None:
            _scroll_cart_to_reveal_line(driver, markers)

        # 4b) Basket D: unrecorded pre-check with bounded out-of-stock swaps;
        #     returns with the cart re-opened at the top.
        _d_precheck_and_swap(driver, markers, outcome, num_items=num_items)

        # 5) Cart with item(s) → START screen recording
        print(f"START RECORDING at cart / Basket for {rec_limit}s …")
        try:
            start_ios_screen_recording(driver, time_limit=rec_limit)
            recording = True
            outcome["recording_started"] = True
            markers.mark("recording_started_at_cart", duration_seconds=rec_limit)
            _mark_step(
                outcome,
                "recording_started",
                status="ok",
                duration_seconds=rec_limit,
            )
            print(f"Screen recording started — recording for up to {rec_limit}s …")
            # Let the encoder roll before UI actions (avoids cutting first frames).
            time.sleep(1.0)
        except Exception as exc:  # noqa: BLE001
            outcome["recording_started"] = False
            outcome["recording_start_error"] = str(exc)
            print(f"Screen recording failed to start: {exc}")
            raise AssertionError(
                f"Could not start screen recording on cart: {exc}"
            ) from exc

        # 6–7) For each cart line: tap item → PDP bottom + to units_each
        _increase_basket_quantities(
            driver,
            markers,
            outcome,
            num_items=num_items,
            units_each=units_each,
        )

        # 8) Cart → Go to checkout (while recording)
        _tap_go_to_checkout(driver, markers, outcome)

        # 9) Checkout screen → top-right ">" chevron (while recording)
        _tap_checkout_top_right_chevron(driver, markers, outcome)

        # 10) Delivery details → Change
        _tap_delivery_details_change(driver, markers, outcome)

        # 11) Delivery address → unselected address → Select
        _select_second_delivery_address(driver, markers, outcome)

        # 12) Delivery details → Done
        _tap_delivery_details_done(driver, markers, outcome)

        # 13) Checkout → Schedule and save
        _tap_schedule_and_save(driver, markers, outcome)

        # 14–15) Schedule a time → day (last 3) + time (last 3)
        _select_schedule_day_and_time(driver, markers, outcome)

        # 16) Checkout → swipe to Pay with → unselected card (NO Continue)
        _select_second_payment_card(driver, markers, outcome)

        # Hold so payment selection lands in the MP4.
        print(
            f"Holding {RECORDING_HOLD_AFTER_CHECKOUT_S:.0f}s after payment "
            "so recording captures the taps …"
        )
        time.sleep(RECORDING_HOLD_AFTER_CHECKOUT_S)
        markers.mark("recording_hold_after_checkout_s", s=RECORDING_HOLD_AFTER_CHECKOUT_S)

        markers.mark(
            f"{case_id}_complete",
            grocery_outlet=outcome.get("grocery_outlet_tapped"),
            household=outcome.get("household_tapped"),
            item_price=outcome.get("item_price"),
            items_added_count=outcome.get("items_added_count") or num_items,
            basket_opened=outcome.get("basket_opened"),
            recording_started=outcome.get("recording_started"),
            cart_qty=outcome.get("cart_qty_after"),
            cart_qty_status=outcome.get("cart_qty_status"),
            go_to_checkout=outcome.get("go_to_checkout_tapped"),
            checkout_chevron=outcome.get("checkout_chevron_tapped"),
            delivery_change=outcome.get("delivery_change_tapped"),
            second_address=outcome.get("second_address_selected"),
            delivery_done=outcome.get("delivery_done_tapped"),
            schedule_day=outcome.get("schedule_day"),
            schedule_time=outcome.get("schedule_time"),
            payment_card=outcome.get("second_payment_card_selected"),
            payment_before=outcome.get("payment_before_last4"),
            payment_after=outcome.get("payment_after_last4"),
            sample=sample_letter,
        )
        # A run whose quantities are not at least price-inferred completed
        # the flow but is not a verified success.
        outcome["status"] = (
            "ok"
            if outcome.get("cart_qty_status") in ("ui_confirmed", "price_inferred")
            else "completed_unverified"
        )
        price = outcome.get("item_price")
        price_s = f"${price:.2f}" if isinstance(price, (int, float)) else "?"
        n_added = outcome.get("items_added_count") or num_items
        addr = outcome.get("second_address_selected") or "unselected address"
        day = outcome.get("schedule_day") or "day"
        slot = outcome.get("schedule_time") or "time"
        pay = outcome.get("second_payment_card_selected") or "other card"
        qty_status = outcome.get("cart_qty_status")
        qty_counts = outcome.get("cart_qty_line_counts") or {}
        if qty_status == "ui_confirmed":
            qty_s = f"qty {outcome.get('cart_qty_after')} (UI-confirmed)"
        elif qty_status == "price_inferred":
            qty_s = f"qty {outcome.get('cart_qty_inferred')} (price_inferred)"
        else:
            qty_s = (
                f"qty target {outcome.get('cart_qty_target')} UNVERIFIED "
                f"({qty_counts.get('unverified', '?')}/"
                f"{outcome.get('cart_lines_qty_raised')} lines unverified)"
            )
        basket_s = (
            f" sample {sample_letter} {num_items}×{units_each}"
            if sample_letter
            else f" {num_items}×{units_each}"
        )
        outcome["message"] = (
            f"{case_name} "
            f"{'OK' if outcome['status'] == 'ok' else 'COMPLETED_UNVERIFIED'} —"
            f"{basket_s} — Grocery Outlet → "
            f"added {n_added} item(s) (first {price_s}) → Basket → recording → "
            f"{qty_s} → Go to checkout → "
            f"top-right > → Change → {addr} → Select → Done → "
            f"Schedule and save → {day} → {slot} → Pay with {pay}"
        )
        print(outcome["message"])
        return outcome
    except Exception as exc:  # noqa: BLE001
        outcome["status"] = (
            "unexpected_screen" if isinstance(exc, UnexpectedScreen)
            else "schedule_slot_mismatch" if isinstance(exc, ScheduleSlotMismatch)
            else "schedule_slot_unverified" if isinstance(exc, ScheduleSlotUnverified)
            else "payment_cards_not_found" if isinstance(exc, PaymentCardsNotFound)
            else "product_unavailable" if isinstance(exc, ProductUnavailable)
            else "manual_verification_required" if isinstance(exc, ManualVerificationRequired)
            else "quantity_unverified" if isinstance(exc, QuantityUnverified) else "error"
        )
        outcome["error_type"] = type(exc).__name__
        outcome["error"] = str(exc)
        print(f"{case_name} FAILED: {type(exc).__name__}: {exc}")
        if isinstance(exc, SessionLost):
            outcome["session_lost"] = True
            print(
                "  WDA/Appium session died mid-run (socket hang up). "
                "Check Appium server log; consider raising newCommandTimeout / "
                "wdaConnectionTimeout and disabling mjpeg screenshots."
            )
        elif driver is not None and "page_source_dump" not in outcome:
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
            recording = False
        elif recording:
            outcome["recording_saved"] = False
        if driver is not None:
            try:
                driver.quit()
                print("Appium session stopped.")
            except Exception as quit_exc:  # noqa: BLE001
                print(f"driver.quit warning: {quit_exc}")
        if appium_calls:
            try:
                appium_calls_path.write_text(
                    "\n".join(json.dumps(row) for row in appium_calls) + "\n",
                    encoding="utf-8",
                )
                outcome["appium_call_log"] = str(appium_calls_path)
                print(
                    f"Wrote {len(appium_calls)} timed Appium calls → "
                    f"{appium_calls_path}"
                )
            except Exception as calls_exc:  # noqa: BLE001
                print(f"Appium call log write error: {calls_exc}")
        try:
            result_path.write_text(
                json.dumps(outcome, indent=2, default=str), encoding="utf-8"
            )
            print(f"Wrote result → {result_path}")
        except Exception as write_exc:  # noqa: BLE001
            print(f"Result write error: {write_exc}")


def run_test_case_D_instacart(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    return run_basket_D(settings, retailer, bundle_id, dry_run=dry_run)