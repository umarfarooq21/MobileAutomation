"""Test Case Walmart 6: fast copy of Walmart TC5 (same flow, less wall-clock).

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

Same steps and recorded window as TC5. Speed-ups are all in the un-recorded
basket fill:
  - one page_source per screen (attributes trimmed), popups detected from it
    instead of per-tap find_elements helpers (~1.3–1.8 s each)
  - every visible Add on a screen tapped in ONE W3C action chain, then every
    row's + taps in ONE chain (a single mobile: tap costs ~0.5 s)
  - after a scroll, wait only until two snapshots agree (the list re-lays out
    for ~1–2 s while rows load; tapping earlier opens the PDP or does nothing)
  - header cart opened directly from the snapshot
  - cart clear driven by snapshots: tap the first visible Remove, wait for the
    "Cart (N items)" count to drop (shared clear is ~10 s/line, 45 s budget)

Basket matrix (``--basket-size`` / ``BASKET_SIZE``):
  A 1×1, B 10×1, C 1×25, D 25×1, E 5×10, G 50×5, H 10×10.

Does not place the order.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.actions import interaction
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput

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
    _collapse_cart_fulfillment_if_expanded,
    _dismiss_ask_sparky_if_shown,
    _dismiss_unable_to_remove_sheet,
    _mark_step,
    _open_cart,
    _return_to_my_items_fast,
    _scroll_down,
    _scroll_up,
    _dismiss_subscription_sheet,
    _tap_missing_anything_continue,
    _tap_my_items_tab,
    _wait_cart_screen,
    _wait_checkout_screen,
)
from grocery_latency.scenarios.test_case_walmart_2 import BASKET_SAMPLES
from grocery_latency.scenarios.test_case_walmart_4 import (
    ItemDetailsClosed,
    _dismiss_tech_issue_alert,
    _item_details_sheet_visible,
    _dismiss_walmart_plus_checkout_splash,
    _tap_cart_change_delivery_fast,
    _tap_checkout_item_details_view,
    _tap_continue_to_checkout_tc4,
    _tap_item_details_done,
)
from grocery_latency.scenarios.test_case_walmart_5 import (
    _confirm_delivery_address,
    ALLOWED_BASKETS,
    SEARCH_QUERY,
    _CART_BADGE_RE,
    _cart_badge_units,
    _clear_popups,
    _collect_result_adds,
    _on_results_page,
    _resolve_tc5_basket,
    _search_my_items,
    _snapshot,
    _result_steppers,
    _missing_anything_visible,
    _stepper_near,
)

TEST_CASE_NAME = "Test Case Walmart 6"
TEST_CASE_ID = "test_case_walmart_6"

# Drop attributes we never read: page_source ~1.6 s → ~1.2 s on results.
_PAGE_SOURCE_EXCLUDED = "enabled,accessible,index,value"
_QTY_TAP_GAP = 0.12
_QTY_MAX_ROUNDS = 4
_ADD_REGISTER_TIMEOUT = 4.5
# Below the cart's sticky header / "Pickup and delivery options" bar.
_REMOVE_MIN_Y = 300.0
_QTY_READ_DELAY = 0.8
# A row's stepper can vanish / show a spinner while Walmart syncs the qty.
_QTY_READ_TIMEOUT = 4.0
# Keep new Adds above the floating tab bar / Sparky pill: rows lower than
# this re-render under the overlay and their + taps go missing.
_ADD_MAX_Y_FRAC = 0.72
_CART_COUNT_RE = re.compile(r"^Cart \((\d+) items?\)$", re.I)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _w3c_taps(driver, points: list[tuple[float, float]], *, gap: float) -> None:
    """Tap every point in ONE W3C actions request (one HTTP round trip)."""
    if not points:
        return
    finger = PointerInput(interaction.POINTER_TOUCH, "finger")
    actions = ActionBuilder(driver, mouse=finger)
    for x, y in points:
        finger.create_pointer_move(duration=0, x=int(round(x)), y=int(round(y)))
        finger.create_pointer_down()
        finger.create_pause(0.04)
        finger.create_pointer_up(0)
        finger.create_pause(gap)
    actions.perform()


def _row_ys(root, h: float) -> list[int]:
    ys = [round(t[1]) for t in _collect_result_adds(root, set(), h)]
    ys += [round(st[0]) for st in _result_steppers(root)]
    return sorted(ys)


def _scroll_and_settle(driver, markers: MarkerLog, w: float, h: float):
    """Scroll, then snapshot until row positions agree twice (list re-layout)."""
    _scroll_down(driver, percent=0.45)
    root = _clear_popups(driver, markers, w, h)
    for _ in range(5):
        if root is None:
            return root
        again = _clear_popups(driver, markers, w, h)
        if again is None:
            return root
        same = _row_ys(root, h) == _row_ys(again, h)
        root = again
        if same:
            break
    return root


def _main_column(adds: list[tuple], w: float) -> list[tuple]:
    """Main list Adds sit in the right column (~x 203–374 on 390 wide).

    Horizontal multipack carousels expose Adds at other x (often off-screen)
    and nearly always answer "Unable to add to cart".
    """
    return [a for a in adds if w * 0.55 <= a[0] <= w * 0.95]


def _steppers_by_title(root) -> dict[str, tuple]:
    """In-cart steppers keyed by product title (lower-case).

    Each result Cell holds "Add to favorites, <title>" plus the stepper, so
    rows are matched by product, not y — rows shift after rapid Adds.
    """
    out: dict[str, tuple] = {}
    for cell in root.iter("XCUIElementTypeCell"):
        title = None
        for node in cell.iter("XCUIElementTypeButton"):
            name = node.get("name") or ""
            if name.startswith("Add to favorites, "):
                title = name[len("Add to favorites, "):].strip().lower()
                break
        if not title:
            continue
        for st in _result_steppers(cell):
            out[title] = st
            break
    return out


def _minus_for(root, title: str) -> tuple[float, float] | None:
    """Stepper − of the result cell whose favorites button names ``title``."""
    key = title.lower()
    for cell in root.iter("XCUIElementTypeCell"):
        names = [n.get("name") or "" for n in cell.iter("XCUIElementTypeButton")]
        fav = next((n for n in names if n.startswith("Add to favorites, ")), None)
        if fav is None:
            continue
        t = fav[len("Add to favorites, "):].strip().lower()
        if not (t == key or t.startswith(key) or key.startswith(t)):
            continue
        for node in cell.iter("XCUIElementTypeButton"):
            if node.get("name") == "stepper.decrementButton":
                x = float(node.get("x") or 0) + float(node.get("width") or 0) / 2.0
                y = float(node.get("y") or 0) + float(node.get("height") or 0) / 2.0
                return x, y
    return None


def _stepper_for(root, title: str, row_cy: float):
    key = title.lower()
    by_title = _steppers_by_title(root)
    st = by_title.get(key)
    if st is None:
        for t, cand in by_title.items():
            if t.startswith(key) or key.startswith(t):
                st = cand
                break
    return st if st is not None else _stepper_near(root, row_cy)


class ProbeExhausted(AssertionError):
    """No product in this search allows the per-item target (Max caps)."""


# Basket C needs ×25 of one item; "low cost products" items all cap at Max
# 12/24. "Fresh Banana, Each" allows ×25 (checked 15 Oct 2026); the rest are
# fallbacks, tried in this order.
C_SEARCH_TERMS = (
    "banana",
    "low cost products",
    "lime",
    "lemon",
    "avocado",
    "yellow onion",
    "roma tomato",
    "jalapeno pepper",
)


def _add_basket_fast(
    driver, markers: MarkerLog, outcome: dict, *, sample,
    listing_units: int | None = None,
    probe_units: int | None = None,
    query: str | None = None,
    max_probe_misses: int | None = None,
) -> list[dict]:
    """Per screen: 1 snapshot → Adds in 1 chain → 1 snapshot → + in 1 chain."""
    target = sample.num_items
    # Units are raised later on Checkout → Item details, so add ×1 here.
    units_each = listing_units or sample.units_each
    max_scrolls = max(15, target * 2 + 10)
    print(
        f"Step: basket {sample.letter} — FAST add {target} result(s) "
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
    replaced: list[str] = []
    badge_fixes = 0
    scrolls = 0
    recoveries = 0
    snapshots = 1
    tap_chains = 0
    t0 = time.monotonic()
    probe_misses = 0

    while len(added) < target and scrolls <= max_scrolls:
        if root is None or not _on_results_page(root):
            recoveries += 1
            if recoveries > 3 + target // 10:
                raise AssertionError(
                    f"Lost the search results page ({recoveries - 1} recoveries)"
                )
            print("  not on search results — re-running search …")
            markers.mark("search_results_recover", index=recoveries)
            if query:
                _search_my_items(driver, markers, outcome, query)
            else:
                _search_my_items(driver, markers, outcome)
            root = _clear_popups(driver, markers, w, h)
            snapshots += 1
            continue

        take = [
            a for a in _main_column(_collect_result_adds(root, used, h), w)
            if a[1] <= h * _ADD_MAX_Y_FRAC
        ]
        take = take[: target - len(added)]
        if not take:
            root = _scroll_and_settle(driver, markers, w, h)
            scrolls += 1
            snapshots += 2
            markers.mark("search_results_scrolled", index=scrolls)
            continue

        for _cx, _cy, label in take:
            used.add(label.lower())
        names = [label[len("Add to cart "):].strip() or label for _, _, label in take]
        print(
            f"  Add ×{len(take)} (#{len(added) + 1}–{len(added) + len(take)}"
            f"/{target}): " + " | ".join(n[:28] for n in names)
        )
        # One Add at a time: a 2nd Add ~0.3 s after the 1st makes Walmart drop
        # the 1st. Each stepper shows ~1.5–1.8 s after its Add (network).
        found: dict[int, tuple] = {}
        for i, (cx, cy, _label) in enumerate(take):
            _w3c_taps(driver, [(cx, cy)], gap=0.0)
            tap_chains += 1
            deadline = time.monotonic() + _ADD_REGISTER_TIMEOUT
            while time.monotonic() < deadline:
                root = _clear_popups(driver, markers, w, h)
                snapshots += 1
                if root is None:
                    continue
                st = _stepper_for(root, names[i], cy)
                if st is not None:
                    found[i] = st
                    break
        if root is None:
            continue

        # rows: [cy, name, plus_x, plus_y, qty, stalls, taps]
        rows: list[list] = []
        late: list[tuple[float, str]] = []
        for i, ((cx, cy, _label), name) in enumerate(zip(take, names)):
            st = found.get(i)
            if st is None:
                # First Add in a chain can confirm late — keep checking by title.
                late.append((cy, name))
                continue
            rows.append([cy, name, st[1], st[2], st[3], 0, 0])

        def _pick_up_late(snap) -> None:
            for cy_l, name_l in list(late):
                st_l = _stepper_for(snap, name_l, cy_l)
                if st_l is not None:
                    late.remove((cy_l, name_l))
                    rows.append([st_l[0], name_l, st_l[1], st_l[2], st_l[3], 0, 0])
                    print(f"  late Add confirmed: {name_l[:48]!r}")

        def _read_row(r):
            """Poll until the row's stepper shows a number, the same number
            twice in a row (the label lags taps). None = row really gone."""
            nonlocal root, snapshots
            last = None
            end = time.monotonic() + _QTY_READ_TIMEOUT
            while time.monotonic() < end:
                root = _clear_popups(driver, markers, w, h)
                snapshots += 1
                st = _stepper_for(root, r[1], r[0]) if root is not None else None
                if st is not None and st[3] is not None:
                    if last is not None and last[3] == st[3]:
                        return st
                    last = st
                time.sleep(0.3)
            return last

        def _stable_badge():
            """Header cart badge, read until two reads agree (it lags taps)."""
            nonlocal root, snapshots
            last = None
            for _ in range(6):
                root = _clear_popups(driver, markers, w, h)
                snapshots += 1
                b = _cart_badge_units(root) if root is not None else None
                if b is not None and b == last:
                    return b
                last = b
                time.sleep(0.35)
            return last

        def _set_row_qty(r, goal: int | None = None) -> None:
            """One row at a time: multi-row + chains lose taps, because each
            qty update re-renders the list and swallows taps in flight."""
            nonlocal tap_chains
            goal = goal or units_each
            if r[4] is None:
                st = _read_row(r)
                if st is None:
                    return
                r[0], r[2], r[3], r[4] = st[0], st[1], st[2], st[3]
            for _ in range(_QTY_MAX_ROUNDS):
                cur = r[4]
                if cur == goal:
                    return
                if cur < goal:
                    pts = [(r[2], r[3])] * (goal - cur)
                    r[6] += len(pts)
                else:
                    m = _minus_for(root, r[1]) if root is not None else None
                    if m is None:
                        return
                    pts = [m] * (cur - goal)
                _w3c_taps(driver, pts, gap=_QTY_TAP_GAP)
                tap_chains += 1
                markers.mark("listing_qty_chain", label=r[1][:60], taps=len(pts))
                time.sleep(_QTY_READ_DELAY)
                st = _read_row(r)
                if st is None:
                    print(f"  stepper lost for {r[1][:40]!r} after + taps")
                    markers.mark("listing_qty_row_lost", label=r[1][:60])
                    return
                prev = r[4]
                r[0], r[2], r[3], r[4] = st[0], st[1], st[2], st[3]
                if r[4] == prev:
                    r[5] += 1
                    if r[5] >= 2:
                        print(f"  qty stuck at {r[4]} for {r[1][:40]!r} (Max cap?)")
                        return

        if units_each > 1:
            for r in rows:
                _set_row_qty(r)

        if probe_units and probe_units > units_each:
            # Big targets (C ×25): most items cap at "Max 12". Check each one
            # can reach the target now (before recording), then lower it back
            # to ×1; a capped item is removed and another product is used.
            fit: list[list] = []
            for r in rows:
                r[5] = 0
                _set_row_qty(r, probe_units)
                if r[4] == probe_units:
                    r[5] = 0
                    _set_row_qty(r, units_each)
                    print(f"  {r[1][:40]!r} allows ×{probe_units} — back to ×{r[4]}")
                    markers.mark("probe_ok", label=r[1][:60], max_ok=probe_units, now=r[4])
                    fit.append(r)
                    continue
                cap = r[4]
                # Take it out completely: slow − taps, re-read, repeat — a
                # fast burst drops taps and left capped items in the cart.
                gone = False
                for _round in range(5):
                    st_rm = _read_row(r)
                    if st_rm is None or not st_rm[3]:
                        gone = True
                        break
                    m = _minus_for(root, r[1]) if root is not None else None
                    if m is None:
                        break
                    _w3c_taps(driver, [m] * int(st_rm[3]), gap=0.55)
                    tap_chains += 1
                    time.sleep(_QTY_READ_DELAY)
                    r[4] = st_rm[3]
                print(
                    f"  {r[1][:40]!r} caps at ×{cap} (< ×{probe_units}) — "
                    f"{'removed' if gone else 'REMOVE FAILED'}, next product"
                )
                markers.mark("probe_capped_removed", label=r[1][:60], cap=cap, gone=gone)
                replaced.append(r[1])
                probe_misses += 1
            rows = fit
            if not rows and max_probe_misses and probe_misses >= max_probe_misses \
                    and not added:
                raise ProbeExhausted(
                    f"{probe_misses} product(s) in this search cap below "
                    f"×{probe_units}"
                )

        if late:
            root = _clear_popups(driver, markers, w, h)
            snapshots += 1
            if root is not None:
                n_before = len(rows)
                _pick_up_late(root)
                if units_each > 1:
                    for r in rows[n_before:]:
                        _set_row_qty(r)
        for _cy, name in late:
            print(f"  Add did not register: {name[:48]!r} — skip")
            markers.mark("item_add_not_registered", label=name[:80])

        # A row that can't reach ×units_each (item Max cap, lost stepper) is
        # taken back out with − so another product replaces it; the basket
        # total then stays exact instead of silently short.
        kept: list[list] = []
        for r in rows:
            if r[4] == units_each or units_each == 1 or root is None:
                kept.append(r)
                continue
            m = _minus_for(root, r[1])
            if m is None or not r[4]:
                kept.append(r)
                continue
            _w3c_taps(driver, [m] * r[4], gap=_QTY_TAP_GAP)
            tap_chains += 1
            time.sleep(_QTY_READ_DELAY)
            root = _clear_popups(driver, markers, w, h)
            snapshots += 1
            print(f"  removed short item {r[1][:40]!r} (×{r[4]}) — will replace")
            markers.mark("short_item_removed", label=r[1][:80], qty=r[4])
            replaced.append(r[1])
        rows = kept

        # Ground truth: the header cart badge. "N in cart" on results can show
        # a qty Walmart never saved (and stray taps can land on a neighbour),
        # so after each screen correct the running total on a visible row.
        if rows and units_each > 1:
            expected = (badge_before or 0) + sum(
                (e["units_on_listing"] or 0) for e in added
            ) + sum((r[4] or 0) for r in rows)
            for _fix in range(3):
                b = _stable_badge()
                if b is None or b == expected:
                    break
                r = rows[-1]
                diff = expected - b
                if diff > 0:
                    pts = [(r[2], r[3])] * diff
                else:
                    m = _minus_for(root, r[1]) if root is not None else None
                    if m is None:
                        break
                    pts = [m] * (-diff)
                print(
                    f"  badge {b} vs expected {expected} — "
                    f"{'+' if diff > 0 else '−'}{abs(diff)} on {r[1][:36]!r}"
                )
                markers.mark("badge_correction", badge=b, expected=expected, diff=diff)
                badge_fixes += 1
                _w3c_taps(driver, pts, gap=_QTY_TAP_GAP)
                tap_chains += 1
                time.sleep(_QTY_READ_DELAY)
                st = _read_row(r)
                if st is not None:
                    r[0], r[2], r[3] = st[0], st[1], st[2]

        for cy, name, _px, _py, qty, _stalls, taps in rows:
            entry = {
                "index": len(added) + 1,
                "source": "my_items_search_add",
                "query": SEARCH_QUERY,
                "label": name[:120],
                "y": cy,
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
        print(f"  … {len(added)}/{target} in {time.monotonic() - t0:.1f}s")

        if len(added) < target:
            root = _scroll_and_settle(driver, markers, w, h)
            scrolls += 1
            snapshots += 2
            markers.mark("search_results_scrolled", index=scrolls)

    elapsed = time.monotonic() - t0
    # Badge lags the last + taps — read until two reads agree.
    badge = None
    for _ in range(6):
        time.sleep(0.4)
        root = _clear_popups(driver, markers, w, h)
        snapshots += 1
        b = _cart_badge_units(root) if root is not None else None
        if b is not None and b == badge:
            break
        badge = b
    short = [e["index"] for e in added if (e["units_on_listing"] or 0) < units_each]
    outcome["items_added"] = list(added)
    outcome["items_added_count"] = len(added)
    outcome["items_qty_short"] = short
    outcome["cart_badge_units_after_add"] = badge
    outcome["cart_badge_units_added"] = (
        badge - (badge_before or 0) if badge is not None else None
    )
    outcome["items_replaced_short"] = replaced
    outcome["basket_exact"] = (
        outcome["cart_badge_units_added"] == target * units_each and not short
    )
    if not outcome["basket_exact"]:
        print(
            f"  WARNING: basket not exact — cart badge added "
            f"{outcome['cart_badge_units_added']} vs {target * units_each}; "
            f"short items {short}"
        )
    outcome["basket_add_elapsed_seconds"] = round(elapsed, 2)
    outcome["fill_summary"] = {
        "snapshots": snapshots,
        "tap_chains": tap_chains,
        "scrolls": scrolls,
        "badge_corrections": badge_fixes,
        "seconds_per_item": round(elapsed / max(1, len(added)), 2),
    }
    markers.mark(
        "walmart_basket_add_done",
        sample=sample.letter,
        added=len(added),
        target=target,
        units_each=units_each,
        qty_short=short,
        cart_badge_units=badge,
        elapsed_s=round(elapsed, 2),
        **outcome["fill_summary"],
    )
    print(
        f"  basket {sample.letter} add done: {len(added)}/{target} items "
        f"×{units_each} in {elapsed:.1f}s "
        f"({outcome['fill_summary']['seconds_per_item']}s/item; "
        f"snapshots={snapshots}, chains={tap_chains}, scrolls={scrolls}; "
        f"cart badge={badge} vs expected {target * units_each})"
    )
    if len(added) < target:
        raise AssertionError(
            f"Only added {len(added)}/{target} search results for basket "
            f"{sample.letter} (scrolls={scrolls})"
        )
    return added


def _cart_line_count(root) -> int | None:
    for node in root.iter("XCUIElementTypeStaticText"):
        m = _CART_COUNT_RE.match(node.get("label") or "")
        if m:
            return int(m.group(1))
    return None


def _cart_empty(root) -> bool:
    for node in root.iter():
        if "your cart is empty" in (node.get("label") or "").lower():
            return True
    return _cart_line_count(root) == 0


def _cart_signature(root) -> tuple:
    """All visible-text labels: changes when a line goes, even if the next
    line slides into the exact same layout (Remove ys alone stay identical)."""
    return tuple(sorted(
        n.get("label") or ""
        for n in root.iter("XCUIElementTypeStaticText")
    ))


def _remove_ys(root) -> list[int]:
    return sorted(
        round(float(n.get("y") or 0))
        for n in root.iter("XCUIElementTypeStaticText")
        if n.get("name") == "Remove"
    )


def _first_remove(
    root, h: float, skip: set[str]
) -> tuple[tuple[float, float] | None, int, str]:
    """Topmost cart-line Remove between the sticky header and the sticky
    Estimated total / CTA bar (taps outside that band hit the overlays).

    Uses the per-line button ``CartItemTileActionsView.remove`` labelled
    "Remove, <product>"; lines whose product is in ``skip`` are left alone.
    Returns (point, scroll_hint, product): hint -1 = nearest usable Remove is
    above the band (scroll up), +1 = below (scroll down), 0 = none seen.
    """
    best = None
    above = below = False
    for node in root.iter("XCUIElementTypeButton"):
        if (node.get("name") or "") != "CartItemTileActionsView.remove":
            continue
        product = (node.get("label") or "")[len("Remove, "):].strip()
        if product in skip:
            continue
        cx, cy = _node_center(node)
        if cy < _REMOVE_MIN_Y:
            above = True
            continue
        if cy > h * 0.80:
            below = True
            continue
        if best is None or cy < best[0][1]:
            best = ((cx, cy), product)
    if best is not None:
        return best[0], 0, best[1]
    return None, (-1 if above else (1 if below else 0)), ""


def _unable_to_remove_visible(root) -> bool:
    return any(
        (n.get("label") or "") == "Unable to remove" for n in root.iter()
    )


def _node_center(node) -> tuple[float, float]:
    return (
        float(node.get("x") or 0) + float(node.get("width") or 0) / 2.0,
        float(node.get("y") or 0) + float(node.get("height") or 0) / 2.0,
    )


def _line_present(root, product: str) -> bool:
    """True while the cart still shows a line for ``product``."""
    want = f"Remove, {product}"
    return any(
        (n.get("name") or "") == "CartItemTileActionsView.remove"
        and (n.get("label") or "").strip() == want
        for n in root.iter("XCUIElementTypeButton")
    )


def _save_for_later_button(root, product: str):
    want = f"Save for later, {product}"
    return next(
        (
            n for n in root.iter("XCUIElementTypeButton")
            if (n.get("name") or "") == "CartItemTileActionsView.saveForLater"
            and (n.get("label") or "").strip() == want
        ),
        None,
    )


def _wait_line_gone(driver, product: str, timeout: float = 5.0):
    """Poll until ``product``'s line leaves the cart. Returns (gone, root,
    blocked) — blocked=True when "Unable to remove" shows instead."""
    root = None
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        root = _snapshot(driver)
        if root is None:
            continue
        if _unable_to_remove_visible(root):
            return False, root, True
        if _cart_empty(root) or not _line_present(root, product):
            return True, root, False
        time.sleep(0.2)
    return False, root, False


def _close_unable_to_remove(driver, markers: MarkerLog, root) -> None:
    got = next(
        (
            n for n in root.iter("XCUIElementTypeButton")
            if (n.get("name") or "") == "Got it"
        ),
        None,
    )
    if got is not None:
        _w3c_taps(driver, [_node_center(got)], gap=0.0)
    else:
        _dismiss_unable_to_remove_sheet(driver, markers)
    time.sleep(0.5)


def _clear_cart_fast(driver, markers: MarkerLog, outcome: dict) -> None:
    """Remove every cart line: tap first visible Remove, wait for it to go.

    A product Walmart refuses to remove ("Unable to remove — technical issue")
    is left in the cart: Got it, remember it, skip its line from then on.
    """
    print("Step: FAST clear existing cart items (if any) …")
    skip: set[str] = set(outcome.get("cart_unremovable") or [])
    if _cart_icon_looks_empty(driver):
        outcome["cart_items_cleared_total"] = 0
        outcome["cart_empty_before_add"] = True
        markers.mark("cart_cleared", removed=0, reason="already_empty")
        print("  cart already empty — skip clear")
        return
    _open_cart(driver, markers, outcome)
    _collapse_cart_fulfillment_if_expanded(driver)
    try:
        h = float((driver.get_window_size() or {}).get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    try:
        driver.update_settings({"pageSourceExcludedAttributes": _PAGE_SOURCE_EXCLUDED})
    except Exception:  # noqa: BLE001
        pass
    removed = 0
    misses = 0
    fails = 0
    empty = False
    only_skipped_left = False
    t0 = time.monotonic()
    deadline = t0 + 600.0
    root = _snapshot(driver)
    while time.monotonic() < deadline:
        if root is None:
            root = _snapshot(driver)
            continue
        if _unable_to_remove_visible(root):
            _close_unable_to_remove(driver, markers, root)
            root = _snapshot(driver)
            continue
        if _cart_empty(root):
            empty = True
            break
        if fails >= 6:
            print("  Remove keeps failing — stopping fast clear")
            break
        if fails and fails % 2 == 0:
            # Same dead spot twice: move the list so a different Remove is used.
            _scroll_down(driver, percent=0.25)
            time.sleep(0.3)
            root = _snapshot(driver)
            if root is None:
                continue
        hit, hint, product = _first_remove(root, h, skip)
        if hit is None:
            misses += 1
            if misses > 8:
                only_skipped_left = bool(skip)
                print(
                    "  no reachable Remove after 8 scrolls"
                    + (" — only unremovable item(s) left" if skip else "")
                )
                break
            if hint < 0:
                _scroll_up(driver, percent=0.20)
            else:
                _scroll_down(driver, percent=0.25)
            time.sleep(0.3)
            root = _snapshot(driver)
            continue
        misses = 0
        _w3c_taps(driver, [hit], gap=0.0)
        tap_t = time.monotonic()
        # Count it only once THIS product's line is gone — any-text-changed
        # gave false "removed" while the count went up.
        changed, root, blocked = _wait_line_gone(driver, product)
        if changed:
            removed += 1
        if blocked:
            # Walmart can't remove this product: Got it → Save for later on
            # the same line (moves it out of the cart).
            _close_unable_to_remove(driver, markers, root)
            root = _snapshot(driver)
            btn = _save_for_later_button(root, product) if root is not None else None
            saved = False
            if btn is not None:
                _w3c_taps(driver, [_node_center(btn)], gap=0.0)
                saved, root, again_blocked = _wait_line_gone(driver, product)
                if again_blocked:
                    _close_unable_to_remove(driver, markers, root)
                    root = _snapshot(driver)
            if saved:
                removed += 1
                changed = True
                markers.mark("cart_line_saved_for_later", product=product[:80])
                print(f"  Unable to remove {product[:45]!r} → Save for later")
            else:
                skip.add(product)
                markers.mark("cart_line_unremovable", product=product[:80])
                print(
                    f"  Unable to remove {product[:45]!r} and Save for later "
                    "failed — leaving it in the cart"
                )
                fails = 0
                continue
        fails = 0 if changed else fails + 1
        if changed and root is not None:
            # Lines slide up after a removal; tapping mid-animation hits a
            # dead spot ("no change" streaks). Wait for Remove ys to settle.
            for _ in range(3):
                again = _snapshot(driver)
                if again is None:
                    break
                same = _remove_ys(again) == _remove_ys(root)
                root = again
                if same:
                    break
        print(
            f"  Remove @y={hit[1]:.0f}: "
            + (
                f"removed in {time.monotonic() - tap_t:.1f}s "
                f"(count={_cart_line_count(root) if root is not None else '?'})"
                if changed
                else "no change in 4s"
            )
        )
        if removed and removed % 10 == 0:
            print(
                f"  … removed {removed} line(s) in "
                f"{time.monotonic() - t0:.0f}s (count={_cart_line_count(root)})"
            )
    try:
        driver.update_settings({"pageSourceExcludedAttributes": ""})
    except Exception:  # noqa: BLE001
        pass
    outcome["cart_items_cleared_total"] = removed
    outcome["cart_empty_before_add"] = empty
    outcome["cart_unremovable"] = sorted(skip)
    outcome["cart_only_unremovable_left"] = only_skipped_left
    markers.mark(
        "cart_cleared",
        removed=removed,
        empty=empty,
        unremovable=len(skip),
        elapsed_s=round(time.monotonic() - t0, 2),
    )
    print(
        f"  cleared {removed} line(s) in {time.monotonic() - t0:.1f}s "
        f"(empty={empty}; left unremovable: {len(skip)})"
    )
    _return_to_my_items_fast(driver, markers)


def _clear_cart_until_empty(
    driver, markers: MarkerLog, outcome: dict, *, max_passes: int = 3
) -> None:
    """Fast passes (re-open Cart each time). Stops once only unremovable
    products are left — those stay; the badge baseline accounts for them."""
    total = 0
    for i in range(max_passes):
        _clear_cart_fast(driver, markers, outcome)
        total += int(outcome.get("cart_items_cleared_total") or 0)
        if outcome.get("cart_empty_before_add") or _cart_icon_looks_empty(driver):
            outcome["cart_empty_before_add"] = True
            break
        if outcome.get("cart_only_unremovable_left"):
            print(
                f"  leaving {len(outcome['cart_unremovable'])} unremovable "
                "product(s) in the cart"
            )
            break
        print(f"  cart not empty after fast pass {i + 1} — again …")
        markers.mark("cart_clear_repass", index=i + 1)
    outcome["cart_items_cleared_total"] = total


def _on_checkout_page(driver) -> bool:
    """Checkout's nav bar is named "Checkout" (the Cart's is a search bar).
    Don't test for a missing "Continue to checkout" — the Cart stays in the
    navigation stack, so that button is still in the tree on Checkout."""
    try:
        return bool(
            driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeNavigationBar" AND name == "Checkout"',
            )
        )
    except Exception:  # noqa: BLE001
        return False


def _go_to_checkout(driver, markers: MarkerLog, outcome: dict) -> None:
    """After Continue to checkout: on "Missing anything?" tap its Continue
    (its X cancels back to the Cart). Checkout can take 10+ s to load, so
    wait patiently; retry Continue to checkout only if the Cart is showing."""
    for attempt in range(3):
        end = time.monotonic() + 25.0
        while time.monotonic() < end:
            if _on_checkout_page(driver):
                markers.mark("checkout_reached", attempt=attempt + 1)
                print("  on Checkout")
                return
            if _missing_anything_visible(driver):
                _tap_missing_anything_continue(driver, markers, outcome)
                continue
            if _confirm_delivery_address(driver, markers):
                continue
            time.sleep(0.5)
        cart_cta = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "Continue to checkout" OR name == "Continue to checkout")',
        )
        if attempt < 2 and cart_cta:
            print("  still on Cart — tapping Continue to checkout again …")
            markers.mark("continue_to_checkout_retry", attempt=attempt + 1)
            _dismiss_subscription_sheet(driver)
            _tap_continue_to_checkout_tc4(driver, markers, outcome)
    raise AssertionError("Could not reach the Checkout screen")


def _close_checkout_popups(driver) -> None:
    """Close info sheets opened on Checkout (e.g. "Delivery from store" fee)."""
    for label in ("Got it", "Got It", "OK", "Close", "close"):
        try:
            for el in driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                f'type == "XCUIElementTypeButton" AND visible == 1 AND '
                f'(label == "{label}" OR name == "{label}")',
            )[:2]:
                el.click()
                time.sleep(0.6)
                return
        except Exception:  # noqa: BLE001
            continue


_ID_INC_RE = re.compile(
    r"^Increase quantity of (.+), The current quantity is (\d+)$", re.S
)


def _id_lines(driver, h: float) -> list[dict]:
    """On-screen Item details lines: {"name", "qty", "el"} (above Done)."""
    band = f" AND rect.y > 150 AND rect.y < {int(h * 0.78)}"
    out = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            'label BEGINSWITH "Increase quantity of "' + band,
        )
    except Exception:  # noqa: BLE001
        return out
    for el in els[:8]:
        try:
            m = _ID_INC_RE.match(el.get_attribute("label") or "")
            if m:
                out.append({"name": m.group(1), "qty": int(m.group(2)), "el": el})
        except Exception:  # noqa: BLE001
            continue
    return out


def _id_line_btn(driver, name: str, *, minus: bool = False):
    """Fresh + (or −) element for one line by its exact label; (el, qty)."""
    verb = "Decrease" if minus else "Increase"
    esc = name.replace('"', '\\"')
    try:
        for el in driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            f'type == "XCUIElementTypeButton" AND '
            f'label BEGINSWITH "{verb} quantity of {esc}, The current quantity is"',
        )[:2]:
            m = re.search(r"current quantity is (\d+)$", el.get_attribute("label") or "")
            if m:
                return el, int(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    return None, None


def _id_set_line(driver, markers: MarkerLog, name: str, target: int, h: float) -> int:
    """Raise/lower ONE line to target, one confirmed tap at a time (element
    clicks — coordinate bursts drifted onto the neighbouring line)."""
    _el, qty = _id_line_btn(driver, name)
    if qty is None:
        return -1
    stalls = 0
    while qty != target and stalls < 4:
        if not _item_details_sheet_visible(driver):
            raise ItemDetailsClosed(f"Item details sheet closed on {name[:30]!r}")
        _dismiss_tech_issue_alert(driver)
        el, cur = _id_line_btn(driver, name, minus=qty > target)
        if el is None:
            return qty
        try:
            r = el.rect or {}
            if not (150 <= float(r.get("y", 0)) <= h * 0.78):
                return qty  # scrolled out of the safe band — next sweep
            el.click()
        except Exception:  # noqa: BLE001
            stalls += 1
            continue
        end = time.monotonic() + 4.0
        moved = False
        while time.monotonic() < end:
            time.sleep(0.25)
            if _dismiss_tech_issue_alert(driver):
                break
            _e, now = _id_line_btn(driver, name)
            if now is not None and now != qty:
                qty, moved = now, True
                break
        stalls = 0 if moved else stalls + 1
    markers.mark("item_details_line_set", product=name[:60], qty=qty, target=target)
    return qty


def _id_drag(driver, h: float, w: float, *, up: bool) -> None:
    y0, y1 = (h * 0.45, h * 0.70) if up else (h * 0.70, h * 0.45)
    try:
        driver.execute_script(
            "mobile: dragFromToWithVelocity",
            {"pressDuration": 0.05, "holdDuration": 0.2, "velocity": 500,
             "fromX": int(w * 0.25), "fromY": int(y0),
             "toX": int(w * 0.25), "toY": int(y1)},
        )
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.5)


def _raise_item_details_precise(
    driver, markers: MarkerLog, outcome: dict, *, sample
) -> None:
    """Every Item details line to exactly ×units_each (sweep down + up)."""
    target = sample.units_each
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
    except Exception:  # noqa: BLE001
        w, h = 390.0, 844.0
    print(f"Step: on Item details set every line to ×{target} (precise) …")
    final: dict[str, int] = {}
    t0 = time.monotonic()
    for sweep in range(6):
        if sweep > 0:
            # Back to the top: Done + View again. (A downward drag on a sheet
            # that is already at its top closes the sheet.)
            _tap_item_details_done(driver, markers, outcome)
            time.sleep(0.8)
            _tap_checkout_item_details_view(driver, markers, outcome)
        worked = False
        idle = 0
        for _step in range(sample.num_items * 3 + 10):
            if not _item_details_sheet_visible(driver):
                raise ItemDetailsClosed("Item details sheet closed while scanning")
            lines = _id_lines(driver, h)
            for ln in lines:
                final[ln["name"]] = ln["qty"]
            todo = [ln for ln in lines if ln["qty"] != target]
            if todo:
                name = todo[0]["name"]
                q = _id_set_line(driver, markers, name, target, h)
                final[name] = q if q >= 0 else final.get(name, 0)
                print(f"  {name[:40]!r}: ×{final[name]}")
                worked = True
                idle = 0
                continue
            if len(final) >= sample.num_items and all(
                q == target for q in final.values()
            ):
                break
            idle += 1
            if idle >= 4:
                break  # bottom of the list
            _id_drag(driver, h, w, up=False)
        if len(final) >= sample.num_items and all(q == target for q in final.values()):
            break
        if not worked and sweep > 0:
            break
    lines_ok = sum(1 for q in final.values() if q == target)
    outcome["item_details_qty"] = final
    outcome["checkout_item_details_lines_increased"] = lines_ok
    outcome["checkout_item_details_qty_increased_by"] = target - 1
    print(
        f"  Item details: {lines_ok}/{sample.num_items} line(s) at ×{target} "
        f"in {time.monotonic() - t0:.0f}s"
    )
    if lines_ok < sample.num_items:
        raise AssertionError(
            f"Item details: only {lines_ok}/{sample.num_items} line(s) reached ×{target}"
        )


def _raise_on_item_details_with_recovery(
    driver, markers: MarkerLog, outcome: dict, *, sample, attempts: int = 3
) -> None:
    """Raise every Item details line to ×units_each. If the sheet closes
    mid-way (taps would land on Checkout), close popups, re-open View and
    resume — lines already at target are only verified."""
    for attempt in range(attempts):
        try:
            _raise_item_details_precise(driver, markers, outcome, sample=sample)
            return
        except ItemDetailsClosed as exc:
            print(f"  {exc} — recovering (attempt {attempt + 1}/{attempts}) …")
            markers.mark("item_details_reopen", attempt=attempt + 1)
            _close_checkout_popups(driver)
            if not _on_checkout_page(driver):
                raise
            _tap_checkout_item_details_view(driver, markers, outcome)
    raise AssertionError("Item details kept closing — qty raise incomplete")


def _open_cart_fast(driver, markers: MarkerLog, outcome: dict) -> None:
    """Tap the header cart straight from a snapshot; shared _open_cart fallback.

    Shared _open_cart first runs the PDP check, which matches "bought since
    yesterday" on search results and taps Back — slow and leaves results.
    """
    root = _snapshot(driver)
    if root is not None:
        for node in root.iter("XCUIElementTypeButton"):
            if not _CART_BADGE_RE.search(node.get("name") or ""):
                continue
            x = float(node.get("x") or 0) + float(node.get("width") or 0) / 2.0
            y = float(node.get("y") or 0) + float(node.get("height") or 0) / 2.0
            _w3c_taps(driver, [(x, y)], gap=0.0)
            if _wait_cart_screen(driver, timeout=8.0):
                outcome["cart_page_opened"] = True
                markers.mark("cart_opened", by="snapshot", value="header_cart")
                _mark_step(outcome, "cart_opened", status="ok", by="snapshot")
                print("  Cart: header cart (snapshot)")
                return
            break
    _open_cart(driver, markers, outcome)


def run_test_case_walmart_6(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
    basket_size: str | None = None,
) -> dict:
    """Walmart TC6: TC5 flow with a fast basket fill (batched taps/snapshots)."""
    if retailer != "walmart":
        raise ValueError(
            f"{TEST_CASE_NAME} is Walmart-only (use --retailer walmart)."
        )

    sample = _resolve_tc5_basket(basket_size)
    plus_taps = sample.num_items * max(0, sample.units_each - 1)
    # ~6.5 s per confirmed + on Item details (H measured) — keep headroom.
    rec_limit = min(1800, max(CART_RECORDING_SECONDS, 300 + plus_taps * 10))
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
            f"Basket {sample.letter}: Add to cart on {sample.num_items} "
            "result(s) ×1"
        ),
        "Open Cart screen",
        f"START screen recording on Cart (limit {rec_limit}s)",
        'Tap "Change" on Cart → Reserve a time (day + time slots)',
        'Change delivery day + time slot → tap "Reserve"',
        'Tap "Continue to checkout" (close "Missing anything?" if shown)',
        'On Checkout tap Item details "View"',
        (
            f"On Item details raise every item to ×{sample.units_each} with +"
            if sample.units_each > 1
            else "On Item details: quantities already ×1"
        ),
        'Tap "Done" → back on Checkout → stop recording (do not place order)',
    ]
    plan["notes"] = (
        f"{TEST_CASE_NAME} — basket {sample.letter} "
        f"({sample.num_items}×{sample.units_each}={sample.total_units}): "
        f'My Items search "{SEARCH_QUERY}"; units set on the search listing. '
        "Fast copy of Walmart TC5 (batched W3C taps, 1 snapshot per step). "
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
        _clear_cart_until_empty(driver, markers, outcome)
        phases["cart_clear"] = time.monotonic() - t

        t = time.monotonic()
        big = sample.units_each > 12
        terms = C_SEARCH_TERMS if big else (None,)
        try:
            driver.update_settings(
                {"pageSourceExcludedAttributes": _PAGE_SOURCE_EXCLUDED}
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            for ti, term in enumerate(terms):
                if term:
                    _search_my_items(driver, markers, outcome, term)
                else:
                    _search_my_items(driver, markers, outcome)
                try:
                    _add_basket_fast(
                        driver, markers, outcome, sample=sample, listing_units=1,
                        # Walmart's usual per-item cap is 12: pre-check bigger
                        # targets, and move on to the next search term after
                        # a few capped products.
                        probe_units=sample.units_each if big else None,
                        query=term,
                        max_probe_misses=3 if big else None,
                    )
                    outcome["search_query_used"] = term or "low cost products"
                    break
                except ProbeExhausted as exc:
                    print(f'  "{term}": {exc} — trying the next search term')
                    markers.mark("search_term_exhausted", query=term)
                    if ti == len(terms) - 1:
                        raise AssertionError(
                            f"No product allows ×{sample.units_each} in any of "
                            f"{len(terms)} searches"
                        ) from exc
        finally:
            try:
                driver.update_settings({"pageSourceExcludedAttributes": ""})
            except Exception:  # noqa: BLE001
                pass
        phases["search_and_add_basket"] = time.monotonic() - t

        t = time.monotonic()
        _open_cart_fast(driver, markers, outcome)
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
        # Units are raised on Checkout → Item details "View" (every basket).
        _go_to_checkout(driver, markers, outcome)
        _dismiss_walmart_plus_checkout_splash(driver, markers)
        _tap_checkout_item_details_view(driver, markers, outcome)
        if sample.units_each > 1:
            _raise_on_item_details_with_recovery(
                driver, markers, outcome, sample=sample
            )
        _tap_item_details_done(driver, markers, outcome)
        _wait_checkout_screen(driver, markers, outcome)
        lines_ok = sample.units_each == 1 or (
            int(outcome.get("checkout_item_details_lines_increased") or 0)
            >= sample.num_items
        )
        outcome["basket_exact"] = bool(outcome.get("basket_exact")) and lines_ok
        phases["recorded_checkout_steps"] = time.monotonic() - rec_t0

        markers.mark(
            "test_case_walmart_6_stopped_after_continue",
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
            f'via search "{outcome.get("search_query_used") or SEARCH_QUERY}" '
            f"(add {outcome.get('basket_add_elapsed_seconds')}s) → cart record → "
            "Change → day/slot → Reserve → Continue to checkout → "
            f"Item details View → ×{sample.units_each} → Done"
        )
        if outcome.get("items_qty_short"):
            outcome["message"] += (
                f" (qty short on items {outcome['items_qty_short']})"
            )
        if not outcome.get("basket_exact"):
            outcome["message"] += (
                f" [NOT EXACT: cart badge added "
                f"{outcome.get('cart_badge_units_added')}/{sample.total_units}]"
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
