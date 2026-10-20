"""Test Case 6 (TASK 6): Produce +×5 → expand delivery slots → last slot → payment + address.

Same as Test Case 5, plus on Place Your Order:
  tap "See more delivery slots" → select the last visible delivery slot,
  then Change payment (2nd card) → Continue → Change delivery address (2nd) → Deliver.

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
from grocery_latency.scenarios.test_case_2 import _open_cart_page, _verify_five_in_cart
from grocery_latency.scenarios.test_case_3 import (
    _checkout_webview_visible,
    _detect_checkout_screen,
    _fast_scroll_find_tap,
    _handle_address_screen,
    _handle_payment_screen,
    _mark_step,
    _open_address_from_place_order,
    _open_payment_from_place_order,
    _screen_matches,
    _tap_no_thanks,
    _tap_proceed_until_left_cart,
    _wait_after_payment,
    _wait_checkout_entry,
)
from grocery_latency.scenarios.test_case_5 import (
    SEARCH_FALLBACK_QUERY,
    TARGET_ITEMS,
    _add_five_produce_or_search,
)

TEST_CASE_NAME = "Test Case 6"
TEST_CASE_ID = "test_case_6"
CART_RECORDING_SECONDS = 180

SEE_MORE_SLOTS = (
    (AppiumBy.ACCESSIBILITY_ID, "See more delivery slots"),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeStaticText[`label == "See more delivery slots"`]',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeLink[`label == "See more delivery slots"`]',
    ),
    (
        AppiumBy.IOS_PREDICATE,
        'label == "See more delivery slots" OR '
        'name == "See more delivery slots" OR '
        'label CONTAINS[c] "See more delivery slots"',
    ),
)

# Prime Shipping trial popup — prefer the top-right X, then Cancel, then No thanks.
PRIME_CLOSE_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "close"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label == "close" OR name == "close" OR '
        'name == "xmark" OR label == "xmark" OR '
        'label == "Close" OR name == "Close")',
    ),
)
PRIME_CANCEL_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Cancel"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label == "Cancel" OR name == "Cancel")',
    ),
)
PRIME_SHEET_MARKERS = (
    (AppiumBy.ACCESSIBILITY_ID, "No thanks"),
    (AppiumBy.ACCESSIBILITY_ID, "Try Prime Shipping free for 30 days"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND visible == 1 AND '
        '(label == "No thanks" OR name == "No thanks" OR '
        'label CONTAINS[c] "Try Prime Shipping" OR '
        'name CONTAINS[c] "Try Prime Shipping")',
    ),
)

# Labels often use a newline: "Tomorrow\n8 PM - 11 PM" (iOS accessibility).
_SLOT_LABEL_RE = re.compile(
    r"(?is)(tomorrow|today|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r".{0,40}?\d{1,2}\s*(am|pm)\s*[-–]\s*\d{1,2}\s*(am|pm)"
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _tap_center(driver, cx: float, cy: float) -> None:
    driver.execute_script(
        "mobile: tap",
        {"x": int(round(cx)), "y": int(round(cy))},
    )


def _prime_blocking(driver) -> bool:
    """True when the intermittent Prime trial popup is covering checkout.

    Only treat real popup CTAs as evidence — never generic Prime body copy.
    """
    return _screen_matches(driver, PRIME_SHEET_MARKERS, timeout=0.25)


def _tap_prime_close_or_cancel(driver, markers: MarkerLog, outcome: dict) -> bool:
    """Dismiss Prime popup via top-right X (close). Avoid top-nav Cancel (exits checkout)."""
    # Top-right X on the Prime sheet (live dump: close @ x≈340, y≈199).
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label == "close" OR name == "close" OR '
            'name == "xmark" OR label == "xmark" OR '
            'label == "Close" OR name == "Close")',
        )
    except Exception:  # noqa: BLE001
        els = []
    size = driver.get_window_size() or {}
    mid_x = float(size.get("width") or 390) * 0.55
    for el in els[:8]:
        try:
            if not el.is_displayed():
                continue
            r = el.rect or {}
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            # Modal X sits mid-upper / right — avoid grocery storefront close (y~47).
            if y < 120 or y > 520:
                continue
            if x < mid_x:
                continue
            el.click()
            markers.mark("prime_popup_dismissed", via="close", x=x, y=y)
            _mark_step(outcome, "prime_popup_dismiss", status="ok", via="close")
            outcome["prime_popup_dismissed"] = True
            print(f"  Prime popup: tapped close/X @ ({x:.0f},{y:.0f})")
            time.sleep(0.25)
            return True
        except Exception:  # noqa: BLE001
            continue

    # Explicit close strategies (accessibility id) — still require modal-band Y.
    for by, value in PRIME_CLOSE_STRATEGIES:
        try:
            el = driver.find_element(by, value)
            if not el.is_displayed():
                continue
            r = el.rect or {}
            y = float(r.get("y", 0))
            x = float(r.get("x", 0))
            if y < 120 or y > 520 or x < mid_x:
                continue
            el.click()
            markers.mark("prime_popup_dismissed", via="close_strategy", y=y)
            _mark_step(outcome, "prime_popup_dismiss", status="ok", via="close")
            outcome["prime_popup_dismissed"] = True
            print(f"  Prime popup: tapped close ({by}={value!r})")
            time.sleep(0.25)
            return True
        except Exception:  # noqa: BLE001
            continue

    # Cancel only if it sits on the sheet body (not the y≈51 nav chrome that exits checkout).
    for by, value in PRIME_CANCEL_STRATEGIES:
        try:
            el = driver.find_element(by, value)
            if not el.is_displayed():
                continue
            r = el.rect or {}
            y = float(r.get("y", 0))
            if y < 120 or y > 560:
                continue
            el.click()
            markers.mark("prime_popup_dismissed", via="cancel", y=y)
            _mark_step(outcome, "prime_popup_dismiss", status="ok", via="cancel")
            outcome["prime_popup_dismissed"] = True
            print(f"  Prime popup: tapped Cancel @ y={y:.0f}")
            time.sleep(0.25)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _dismiss_prime_if_blocking(
    driver, markers: MarkerLog, outcome: dict, *, why: str = ""
) -> bool:
    """Dismiss intermittent Prime trial popup (X / Cancel / No thanks)."""
    if not _prime_blocking(driver):
        return False
    note = f" ({why})" if why else ""
    print(f"  Prime sheet blocking — dismissing popup{note} …")
    if _tap_prime_close_or_cancel(driver, markers, outcome):
        if not _prime_blocking(driver):
            return True
    _tap_no_thanks(driver, markers, outcome)
    time.sleep(0.2)
    if _prime_blocking(driver):
        print("  Prime popup still detected after dismiss attempts — continuing")
    return True


def _tap_see_more_delivery_slots(driver, markers: MarkerLog, outcome: dict) -> bool:
    """Tap 'See more delivery slots' on Place Your Order (expands the slot grid)."""
    print('Step: tap "See more delivery slots" …')
    _dismiss_prime_if_blocking(driver, markers, outcome, why="before See more")

    # Fast path: accessibility id is usually already on-screen after payment.
    try:
        el = driver.find_element(AppiumBy.ACCESSIBILITY_ID, "See more delivery slots")
        if el.is_displayed():
            try:
                el.click()
            except Exception:  # noqa: BLE001
                r = el.rect or {}
                _tap_center(
                    driver,
                    float(r.get("x", 0)) + float(r.get("width", 100)) / 2.0,
                    float(r.get("y", 0)) + float(r.get("height", 20)) / 2.0,
                )
            markers.mark(
                "see_more_delivery_slots_tapped",
                by=AppiumBy.ACCESSIBILITY_ID,
                value="See more delivery slots",
            )
            _mark_step(
                outcome,
                "see_more_delivery_slots",
                status="ok",
                by=AppiumBy.ACCESSIBILITY_ID,
                value="See more delivery slots",
            )
            outcome["see_more_delivery_slots_tapped"] = True
            print(
                "  See more delivery slots: accessibility id = "
                "'See more delivery slots'"
            )
            time.sleep(0.2)
            return True
    except Exception:  # noqa: BLE001
        pass

    # Web content often reports "not displayed" while on screen, and Place Your
    # Order can still be loading — poll by position for up to ~12 s.
    try:
        h = float((driver.get_window_size() or {}).get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0
    # A long Place Your Order (big baskets, recording running) can take
    # longer than WDA's default 15 s to snapshot — it then returns an empty
    # web view. Give it more time while looking for the link.
    try:
        driver.update_settings({"customSnapshotTimeout": 60})
    except Exception:  # noqa: BLE001
        pass
    end = time.monotonic() + 45.0
    nudge = True  # first nudge goes up (the link sits near the top)
    while time.monotonic() < end:
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                # When "In 3 hours" is pre-selected, the Same Day option shows
                # "More time slots available" instead of "See more delivery slots".
                'label == "See more delivery slots" OR name == "See more delivery slots" OR '
                'label == "More time slots available" OR name == "More time slots available"',
            )
        except Exception:  # noqa: BLE001
            els = []
        if not els:
            # The session can hold a stale copy of Amazon's web content (the
            # link is on screen but searches return nothing). A page_source
            # read rebuilds the tree — take the link's position from it.
            try:
                import xml.etree.ElementTree as _ET

                root = _ET.fromstring(driver.page_source)
                for n in root.iter():
                    if (n.get("label") or n.get("name") or "") in (
                        "See more delivery slots",
                        "More time slots available",
                    ):
                        x = float(n.get("x") or 0)
                        y = float(n.get("y") or 0)
                        cy = y + float(n.get("height") or 20) / 2.0
                        if 130 <= cy <= h * 0.85:
                            _tap_center(driver, x + float(n.get("width") or 100) / 2.0, cy)
                            markers.mark("see_more_delivery_slots_tapped", via="page_source", y=cy)
                            _mark_step(outcome, "see_more_delivery_slots", status="ok", via="page_source")
                            outcome["see_more_delivery_slots_tapped"] = True
                            print(f"  See more delivery slots: tapped via page source @ y={cy:.0f}")
                            time.sleep(0.4)
                            return True
            except Exception:  # noqa: BLE001
                pass
        below = above = False
        for el in els[:4]:
            try:
                r = el.rect or {}
                cy = float(r.get("y", 0)) + float(r.get("height", 20)) / 2.0
                if cy < 130:
                    above = True
                    continue
                if cy > h * 0.85:
                    below = True
                    continue
                _tap_center(
                    driver,
                    float(r.get("x", 0)) + float(r.get("width", 100)) / 2.0,
                    cy,
                )
                markers.mark("see_more_delivery_slots_tapped", via="rect", y=cy)
                _mark_step(outcome, "see_more_delivery_slots", status="ok", via="rect")
                outcome["see_more_delivery_slots_tapped"] = True
                print(f"  See more delivery slots: tapped by position @ y={cy:.0f}")
                time.sleep(0.4)
                return True
            except Exception:  # noqa: BLE001
                continue
        if below:
            _scroll_slots_into_view(driver, direction="down")
        elif above:
            _scroll_slots_into_view(driver, direction="up")
        else:
            # Not in the tree at all: iOS refreshes a web view's accessibility
            # tree after interaction — nudge the page (down, then back up).
            nudge = not nudge
            _scroll_slots_into_view(driver, direction="down" if nudge else "up")
        time.sleep(0.5)

    # Diagnostic: keep exactly what this session sees when the link is missing.
    try:
        from grocery_latency.config import load_settings as _ls

        _dir = _ls().artifacts_dir
        _dir.mkdir(parents=True, exist_ok=True)
        _path = _dir / f"see_more_missing_{int(time.time())}.xml"
        _path.write_text(driver.page_source, encoding="utf-8")
        driver.get_screenshot_as_file(str(_path.with_suffix(".png")))
        print(f"  saved session view → {_path}")
    except Exception as _exc:  # noqa: BLE001
        print(f"  could not save session view: {_exc}")

    # One short scroll pass only if not immediately visible.
    hit = _fast_scroll_find_tap(driver, SEE_MORE_SLOTS, scrolls=1, timeout=0.55)
    if hit:
        by, value = hit
        markers.mark("see_more_delivery_slots_tapped", by=by, value=value)
        _mark_step(
            outcome,
            "see_more_delivery_slots",
            status="ok",
            by=by,
            value=value,
        )
        outcome["see_more_delivery_slots_tapped"] = True
        print(f"  See more delivery slots: {by} = {value!r}")
        time.sleep(0.2)
        return True

    # Last resort: 2 coordinate probes — only accept if chip count grows.
    before = len(_collect_expanded_slot_chips(driver))
    try:
        size = driver.get_window_size() or {}
        w = float(size.get("width") or 390)
        h = float(size.get("height") or 844)
        for cy_frac in (0.50, 0.56):
            _tap_center(driver, w * 0.30, h * cy_frac)
            time.sleep(0.4)
            after = len(_collect_expanded_slot_chips(driver))
            if after >= max(3, before + 1):
                markers.mark(
                    "see_more_delivery_slots_tapped",
                    via="coords",
                    cy_frac=cy_frac,
                    before=before,
                    after=after,
                )
                _mark_step(
                    outcome,
                    "see_more_delivery_slots",
                    status="ok",
                    via="coords",
                    cy_frac=cy_frac,
                )
                outcome["see_more_delivery_slots_tapped"] = True
                print(
                    f"  See more delivery slots: coordinate fallback "
                    f"@y={cy_frac:.2f} chips {before}→{after}"
                )
                return True
    except Exception:  # noqa: BLE001
        pass

    _mark_step(
        outcome,
        "see_more_delivery_slots",
        status="failed",
        reason="See more delivery slots not found",
    )
    markers.mark("see_more_delivery_slots_failed")
    print("  See more delivery slots not found")
    return False


def _flat_slot_label(label: str) -> str:
    return " ".join((label or "").replace("\u202f", " ").split())


def _looks_like_slot_chip(label: str) -> bool:
    if not label or len(label) < 8:
        return False
    flat = _flat_slot_label(label)
    low = flat.lower()
    if "see more" in low or "arriving" in low:
        return False
    if "delivery" in low and "$" in flat:
        return False
    if "prime" in low or "thanks" in low:
        return False
    if _SLOT_LABEL_RE.search(flat):
        return True
    return (
        ("tomorrow" in low or "today" in low)
        and ("am" in low or "pm" in low)
        and ("-" in flat or "–" in flat)
    )


def _scroll_slots_into_view(driver, *, direction: str = "up") -> None:
    """Bring delivery-slot chips back on screen (often scrolled above after expand)."""
    try:
        size = driver.get_window_size() or {}
        w = int(size.get("width") or 390)
        h = int(size.get("height") or 844)
        if direction == "up":
            # Finger down = content moves up → reveal content above.
            from_y, to_y = int(h * 0.40), int(h * 0.62)
        else:
            from_y, to_y = int(h * 0.62), int(h * 0.40)
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.18,
                "fromX": w // 2,
                "fromY": from_y,
                "toX": w // 2,
                "toY": to_y,
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _collect_expanded_slot_chips(
    driver, *, include_offscreen: bool = True
) -> list[dict]:
    """Tomorrow/day time-window chip buttons after 'See more delivery slots'.

    Keep this cheap: one find_elements + rect/name only (no double attribute fetches).
    """
    chips: list[dict] = []
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(name CONTAINS[c] "Tomorrow" OR name CONTAINS[c] "Today" OR '
            'label CONTAINS[c] "Tomorrow" OR label CONTAINS[c] "Today")',
        )
    except Exception:  # noqa: BLE001
        els = []

    for el in els[:20]:
        try:
            # `name` usually carries "Tomorrow\\n8 PM - 11 PM"; avoid a second round-trip.
            label = (el.get_attribute("name") or "").strip()
            if not label:
                label = (el.get_attribute("label") or "").strip()
            if not _looks_like_slot_chip(label):
                continue
            r = el.rect or {}
            w = float(r.get("width", 0))
            h = float(r.get("height", 0))
            y = float(r.get("y", 0))
            x = float(r.get("x", 0))
            if w < 50 or h < 22 or w > 260 or h > 120:
                continue
            if not include_offscreen and (y < 90 or y > 820):
                continue
            chips.append(
                {
                    "el": el,
                    "label": _flat_slot_label(label)[:80],
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                    "cx": x + w / 2.0,
                    "cy": y + h / 2.0,
                }
            )
        except Exception:  # noqa: BLE001
            continue

    chips.sort(key=lambda c: (round(c["y"] / 12.0), round(c["x"] / 12.0)))
    deduped: list[dict] = []
    for c in chips:
        if any(
            abs(c["cx"] - d["cx"]) < 28 and abs(c["cy"] - d["cy"]) < 22 for d in deduped
        ):
            continue
        deduped.append(c)
    return deduped


def _select_last_delivery_slot(driver, markers: MarkerLog, outcome: dict) -> bool:
    """After expand, tap the last (bottom-most / rightmost) delivery slot chip."""
    print("Step: select last delivery slot from expanded list …")

    chips: list[dict] = []
    for attempt in range(3):
        chips = _collect_expanded_slot_chips(driver, include_offscreen=True)
        onscreen = [c for c in chips if 80 <= c["y"] <= 820]
        if len(onscreen) >= 2:
            chips = onscreen
            break
        if len(chips) >= 2:
            ys = [c["y"] for c in chips]
            y_min, y_max = min(ys), max(ys)
            if y_max > 820:
                print(
                    f"  found {len(chips)} slot chips below fold "
                    f"(y={y_min:.0f}…{y_max:.0f}); scrolling …"
                )
                _scroll_slots_into_view(driver, direction="down")
            elif y_min < 80:
                print(
                    f"  found {len(chips)} slot chips above fold "
                    f"(y={y_min:.0f}…{y_max:.0f}); scrolling …"
                )
                _scroll_slots_into_view(driver, direction="up")
            else:
                chips = onscreen or chips
                break
            time.sleep(0.12)
            continue
        time.sleep(0.12)

    if len(chips) < 2:
        markers.mark(
            "last_delivery_slot_failed",
            reason="no_chips",
            found=len(chips),
        )
        _mark_step(
            outcome,
            "last_delivery_slot",
            status="failed",
            reason="no expanded slot chips",
            found=len(chips),
        )
        print(f"  no expanded delivery-slot chips found (got {len(chips)})")
        return False

    pick = chips[-1]
    print(
        f"  expanded slots={len(chips)}; tapping last "
        f"#{len(chips)} label={pick['label']!r} @ ({pick['cx']:.0f},{pick['cy']:.0f})"
    )
    # Prefer coordinate tap — avoids stale element after expand animation.
    size = driver.get_window_size() or {}
    h = float(size.get("height") or 844)
    if pick["y"] > 760:
        _scroll_slots_into_view(driver, direction="down")
        time.sleep(0.1)
        refreshed = _collect_expanded_slot_chips(driver, include_offscreen=True)
        onscreen = [c for c in refreshed if 80 <= c["y"] <= 820]
        if onscreen:
            chips = onscreen
            pick = chips[-1]
    _tap_center(driver, pick["cx"], min(pick["cy"], h * 0.88))
    time.sleep(0.12)

    markers.mark(
        "last_delivery_slot_tapped",
        label=pick["label"],
        index=len(chips) - 1,
        total=len(chips),
        x=pick["cx"],
        y=pick["cy"],
    )
    _mark_step(
        outcome,
        "last_delivery_slot",
        status="ok",
        label=pick["label"],
        index=len(chips) - 1,
        total=len(chips),
    )
    outcome["delivery_slot_changed"] = True
    outcome["delivery_slot_label"] = pick["label"]
    outcome["delivery_slot_index"] = len(chips) - 1
    outcome["delivery_slot_total"] = len(chips)
    print(f"  selected last slot: {pick['label']!r}")
    return True


def _select_last_listed_slot_option(driver, markers: MarkerLog, outcome: dict) -> bool:
    """No "See more delivery slots": use the listed delivery options
    ("Today/Tomorrow <time> … Delivery").

    One option → it is already the selected (and last) slot; accept it with
    no tap. Several → tap the bottom-most one, only once it is really on
    screen (the web view's positions can be stale — seen 19 Oct 2026: the
    option on screen at y≈420 was reported at y=1060). Taps stay in the
    middle of the page, well away from "Place your order"."""
    print("  no See more link — using the listed delivery options …")
    try:
        h = float((driver.get_window_size() or {}).get("height") or 844)
    except Exception:  # noqa: BLE001
        h = 844.0

    def _options() -> list[tuple[float, float, str]]:
        found: list[tuple[float, float, str]] = []
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                '(label BEGINSWITH[c] "Today " OR label BEGINSWITH[c] "Tomorrow ") '
                'AND (label CONTAINS "AM" OR label CONTAINS "PM")',
            )
        except Exception:  # noqa: BLE001
            els = []
        for el in els[:12]:
            try:
                lab = (el.get_attribute("label") or "").strip()
                r = el.rect or {}
                cy = float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0
                cx = float(r.get("x", 0)) + min(float(r.get("width", 0)), 200.0) / 2.0
            except Exception:  # noqa: BLE001
                continue
            # One entry per option: "Tomorrow 3 PM - 6 PM" and its
            # "… $12.99 Delivery" container are the same option.
            key = _flat_slot_label(lab).split("$")[0].strip().lower()
            if not any(_flat_slot_label(o[2]).split("$")[0].strip().lower() == key
                       for o in found):
                found.append((cx, cy, lab))
        found.sort(key=lambda o: o[1])
        return found

    options = _options()
    if not options:
        _mark_step(outcome, "last_delivery_slot", status="failed",
                   reason="no See more link and no listed delivery option")
        markers.mark("last_delivery_slot_failed", reason="no_options")
        print("  no delivery options listed")
        return False

    via = "only_option_already_selected"
    if len(options) > 1:
        via = "tapped_last_listed_option"
        for _ in range(3):
            cx, cy, lab = options[-1]
            if 120 <= cy <= h * 0.80:
                _tap_center(driver, cx, cy)
                time.sleep(0.3)
                break
            _scroll_slots_into_view(driver, direction="down" if cy > h * 0.80 else "up")
            time.sleep(0.4)
            options = _options() or options
        else:
            _mark_step(outcome, "last_delivery_slot", status="failed",
                       reason="last listed option never came on screen")
            print("  last listed delivery option never came on screen")
            return False
    lab = options[-1][2]
    flat = _flat_slot_label(lab)[:80]
    markers.mark("last_delivery_slot_tapped", label=flat, index=len(options) - 1,
                 total=len(options), via=via)
    _mark_step(outcome, "last_delivery_slot", status="ok", label=flat,
               total=len(options), via=via)
    _mark_step(outcome, "see_more_delivery_slots", status="skipped",
               reason="Amazon showed no See more link")
    outcome["delivery_slot_changed"] = True
    outcome["delivery_slot_label"] = flat
    outcome["delivery_slot_total"] = len(options)
    outcome["see_more_delivery_slots_missing"] = True
    if via == "only_option_already_selected":
        print(f"  only one delivery option listed — already selected: {flat!r}")
    else:
        print(f"  selected last listed option ({len(options)} listed): {flat!r}")
    return True


def _expand_and_select_last_slot(
    driver, markers: MarkerLog, outcome: dict
) -> bool:
    """Dismiss Prime if needed → See more delivery slots → pick the last chip."""
    if outcome.get("delivery_slot_changed"):
        return True
    # Skip pre-scroll / extra dismiss — See more is usually already visible.
    if not _tap_see_more_delivery_slots(driver, markers, outcome):
        # Amazon sometimes offers a single delivery option with no "See more"
        # link (seen 19 Oct 2026: only "Tomorrow 3 PM - 6 PM"). Pick the last
        # option that is listed instead of failing.
        return _select_last_listed_slot_option(driver, markers, outcome)
    time.sleep(0.12)
    return _select_last_delivery_slot(driver, markers, outcome)


def _place_order_surface_visible(driver) -> bool:
    """True when grocery Place Your Order content is on-screen (slots/address links).

    The page title often contains "Amazon.com Checkout", which must not be treated
    as the generic Amazon webview checkout failure.
    """
    if _screen_matches(driver, SEE_MORE_SLOTS, timeout=0.25):
        return True
    # Nav-bar title is the reliable signal: the page can open scrolled to the
    # top ("Place your order" + Prime banner) with no slot/address links yet.
    if _screen_matches(
        driver,
        (
            (
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeNavigationBar" AND '
                'name BEGINSWITH "Place Your Order"',
            ),
        ),
        timeout=0.3,
    ):
        return True
    return _screen_matches(
        driver,
        (
            (AppiumBy.ACCESSIBILITY_ID, "Change delivery address"),
            (AppiumBy.ACCESSIBILITY_ID, "See more delivery slots"),
            (
                AppiumBy.IOS_PREDICATE,
                'label == "Change delivery address" OR '
                'name == "Change delivery address" OR '
                'label CONTAINS[c] "See more delivery slots" OR '
                'label CONTAINS[c] "Arriving Today" OR '
                'label CONTAINS[c] "Arriving Tomorrow"',
            ),
        ),
        timeout=0.3,
    )


_DETOUR_MARKERS = (
    "apply now",
    "amazon.com: amazon visa",
    "offer details",
    "pricing & terms",
    "earn unlimited",
)


def _detour_screen_visible(driver) -> bool:
    """Off-flow page reached by a stray tap, e.g. the Amazon Visa sign-up."""
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            " OR ".join(
                f'label CONTAINS[c] "{m}" OR name CONTAINS[c] "{m}"'
                for m in _DETOUR_MARKERS
            ),
        )
    except Exception:  # noqa: BLE001
        return False
    return any(_safe_displayed(el) for el in els[:4])


def _safe_displayed(el) -> bool:
    try:
        return bool(el.is_displayed())
    except Exception:  # noqa: BLE001
        return False


def _go_back(driver, markers: MarkerLog, why: str) -> None:
    """Top-left back arrow; coordinate fallback where it sits on Amazon pages."""
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "Back"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND visible == 1 AND '
            '(label ==[c] "back" OR name ==[c] "back" OR '
            'label CONTAINS[c] "navigate up" OR name ==[c] "chevron.left" OR '
            'name ==[c] "arrow.left")',
        ),
    ):
        try:
            for el in driver.find_elements(by, value)[:3]:
                r = el.rect or {}
                if float(r.get("y", 0)) < 130 and float(r.get("x", 0)) < 150:
                    el.click()
                    markers.mark("went_back", why=why, via="button")
                    print(f"  ← back ({why})")
                    time.sleep(0.6)
                    return
        except Exception:  # noqa: BLE001
            continue
    _tap_center(driver, 39.0, 69.0)
    markers.mark("went_back", why=why, via="coord")
    print(f"  ← back via coord ({why})")
    time.sleep(0.6)


def _dismiss_unknown_popup(driver, markers: MarkerLog) -> bool:
    """Close / Cancel / No thanks on any sheet that is not part of the flow."""
    for label in ("Close", "close", "Cancel", "No thanks", "Not now", "Not Now", "Dismiss"):
        try:
            for el in driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                f'type == "XCUIElementTypeButton" AND visible == 1 AND '
                f'(label == "{label}" OR name == "{label}")',
            )[:2]:
                y = float((el.rect or {}).get("y", 0))
                # Skip the nav-bar Cancel (y≈51) — that exits checkout.
                if y < 110:
                    continue
                el.click()
                markers.mark("unknown_popup_dismissed", via=label, y=y)
                print(f"  dismissed unknown popup via {label!r}")
                time.sleep(0.5)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _escape_detour(driver, markers: MarkerLog, outcome: dict, *, why: str) -> bool:
    """Back out of a detour page or close an unknown popup. True if acted."""
    if _detour_screen_visible(driver):
        _go_back(driver, markers, why=f"detour page ({why})")
        outcome.setdefault("detours_escaped", []).append(why)
        return True
    if _dismiss_unknown_popup(driver, markers):
        outcome.setdefault("detours_escaped", []).append(f"popup ({why})")
        return True
    return False


def _handle_payment_screen_soft(driver, markers: MarkerLog, outcome: dict) -> bool:
    """Pick the 2nd card + Continue. If that's impossible (no 2nd saved card),
    back out of the payment picker and carry on without the payment change."""
    try:
        _handle_payment_screen(driver, markers, outcome)
        return True
    except AssertionError as exc:
        print(f"  payment change skipped: {exc}")
        markers.mark("payment_change_skipped", reason=str(exc)[:120])
        _mark_step(outcome, "payment_change", status="skipped", reason=str(exc)[:120])
        outcome["payment_change_skipped"] = str(exc)
        _escape_detour(driver, markers, outcome, why="payment")
        if _screen_matches(
            driver,
            (
                (AppiumBy.IOS_PREDICATE,
                 'name CONTAINS[c] "Select a payment method" OR '
                 'label CONTAINS[c] "Select a payment method"'),
            ),
            timeout=0.4,
        ) or _detour_screen_visible(driver):
            _go_back(driver, markers, why="leave payment picker")
        return False


def _recover_amazon_error_page(driver, markers: MarkerLog | None = None) -> bool:
    """Amazon's "UH-OH Something went wrong on our end." page (seen on a
    50-line Cart) — tap TRY AGAIN until the page loads (max 3×)."""
    hit = False
    for _ in range(3):
        try:
            els = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'label == "TRY AGAIN" OR name == "TRY AGAIN" OR '
                'label CONTAINS[c] "Something went wrong on our end"',
            )
        except Exception:  # noqa: BLE001
            return hit
        btn = None
        for el in els[:3]:
            try:
                if (el.get_attribute("label") or "").strip().upper() == "TRY AGAIN":
                    btn = el
                    break
            except Exception:  # noqa: BLE001
                continue
        if not els:
            return hit
        hit = True
        print("  Amazon error page (UH-OH) — tapping TRY AGAIN …")
        if markers is not None:
            markers.mark("amazon_error_page_try_again")
        try:
            if btn is not None:
                btn.click()
            else:
                driver.find_element(AppiumBy.ACCESSIBILITY_ID, "cartTab").click()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(4.0)
    return hit


def _confirm_first_address(driver, markers: MarkerLog) -> bool:
    """On "Select a delivery address": select the FIRST (top) address and
    tap "Deliver to this address". The address page has no order button."""
    print("  address page first — selecting the FIRST address to reach Place Your Order …")
    rows: list[tuple[float, object]] = []
    try:
        for el in driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'label CONTAINS[c] "Phone number" OR name CONTAINS[c] "Phone number"',
        )[:8]:
            try:
                y = float((el.rect or {}).get("y", 0))
            except Exception:  # noqa: BLE001
                continue
            if y > 90 and not any(abs(y - r[0]) < 24 for r in rows):
                rows.append((y, el))
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        print("  no address rows found")
        return False
    rows.sort(key=lambda t: t[0])
    try:
        rows[0][1].click()
    except Exception:  # noqa: BLE001
        return False
    time.sleep(0.8)
    hit = _fast_scroll_find_tap(
        driver,
        ((AppiumBy.ACCESSIBILITY_ID, "Deliver to this address"),
         (AppiumBy.ACCESSIBILITY_ID, "Use this address")),
        scrolls=1, timeout=2.0,
    )
    if not hit:
        print("  Deliver to this address not found for the first address")
        return False
    markers.mark("first_address_confirmed_to_reach_place_order")
    print("  first address → Deliver to this address (2nd address comes at the end)")
    time.sleep(1.0)
    return True



def _tc6_checkout_edits(driver, markers: MarkerLog, outcome: dict) -> None:
    """TC5 checkout plus expand delivery slots and select the last one."""
    _recover_amazon_error_page(driver, markers)
    _tap_proceed_until_left_cart(driver, markers, outcome)
    time.sleep(0.15)

    payment_done = False
    address_done = False
    slot_done = False
    outcome["delivery_slot_changed"] = False

    # Big baskets (50 lines) take 20 s+ to open checkout.
    first = _wait_checkout_entry(driver, markers, timeout=25.0)
    markers.mark("checkout_screen", screen=first, round=0, via="entry_wait")
    print(f"  checkout entry surface: {first}")
    if first == "webview" and not _place_order_surface_visible(driver):
        raise AssertionError(
            "Proceed opened Amazon webview checkout (not native grocery payment/"
            "Place Your Order). Clear the main Amazon cart and retry."
        )
    if first in ("webview", "unknown") and _place_order_surface_visible(driver):
        first = "place_order"
        print("  treating surface as place_order (delivery slots/address visible)")

    unknown_streak = 0
    proceed_retries = 0
    for round_i in range(30):
        if _detour_screen_visible(driver):
            # Stray page (e.g. Amazon Visa sign-up): back out and carry on.
            _escape_detour(driver, markers, outcome, why=f"round {round_i + 1}")
            first = None
            continue
        screen = first if round_i == 0 else _detect_checkout_screen(driver)
        if screen in ("unknown", "webview") and not _place_order_surface_visible(driver):
            # Unknown popup / screen: close it, or go back if it persists.
            unknown_streak += 1
            # Checkout can take 20 s+ to load after Proceed (big baskets show
            # only a spinner) — give it time, and only back out of an unknown
            # screen that persists (~6 checks × 2.5 s).
            time.sleep(2.5)
            if unknown_streak >= 6:
                if not _escape_detour(driver, markers, outcome, why="unknown screen"):
                    _go_back(driver, markers, why="unknown screen")
                unknown_streak = 0
                first = None
                continue
        else:
            unknown_streak = 0
        if screen == "cart" and proceed_retries < 2:
            # Dropped back to the Cart (slow load / backed out) — Proceed again.
            proceed_retries += 1
            print(f"  back on Cart — tapping Proceed again ({proceed_retries}/2)")
            markers.mark("checkout_proceed_retry", index=proceed_retries)
            _tap_proceed_until_left_cart(driver, markers, outcome)
            time.sleep(1.0)
            first = None
            continue
        if round_i > 0:
            markers.mark("checkout_screen", screen=screen, round=round_i + 1)
            print(f"  checkout screen detect: {screen} (round {round_i + 1})")
        else:
            print(f"  checkout screen detect: {screen} (round 1)")

        if screen in ("unknown", "webview") and _place_order_surface_visible(driver):
            screen = "place_order"
            print("  remapped → place_order (See more / address visible)")

        if screen == "no_thanks":
            _dismiss_prime_if_blocking(driver, markers, outcome, why="no_thanks screen")
            time.sleep(0.15)
            first = None
            continue

        if screen == "payment":
            if payment_done:
                screen = _wait_after_payment(driver, timeout=8.0)
                markers.mark(
                    "checkout_screen",
                    screen=screen,
                    round=round_i + 1,
                    via="post_payment",
                )
                print(f"  post-payment wait → {screen}")
                if screen in ("unknown", "webview") and _place_order_surface_visible(
                    driver
                ):
                    screen = "place_order"
                    print("  remapped post-payment → place_order")
                if screen == "payment":
                    continue
            else:
                _handle_payment_screen_soft(driver, markers, outcome)
                payment_done = True
                screen = _wait_after_payment(driver, timeout=9.0)
                markers.mark(
                    "checkout_screen",
                    screen=screen,
                    round=round_i + 1,
                    via="post_payment",
                )
                print(f"  post-payment wait → {screen}")
                if screen in ("unknown", "webview") and _place_order_surface_visible(
                    driver
                ):
                    screen = "place_order"
                    print("  remapped post-payment → place_order")

            if screen == "no_thanks":
                _dismiss_prime_if_blocking(
                    driver, markers, outcome, why="post-payment"
                )
                time.sleep(0.15)
                first = None
                continue
            if screen == "address":
                if not address_done:
                    _handle_address_screen(driver, markers, outcome)
                    address_done = True
                if slot_done:
                    break
                # Address picker came first — confirm it, then continue to
                # Place Your Order for the slot step.
                time.sleep(0.3)
                first = None
                continue
            if screen == "place_order":
                pass
            else:
                first = None
                continue

        if screen == "address":
            if not address_done and not (slot_done and payment_done):
                # Address page came FIRST (Amazon remembers the last run's
                # 2nd address). Picking the 2nd address now can make items
                # unshippable ("can't be shipped to the address you
                # selected") and Place Your Order never shows — so take the
                # FIRST address to reach Place Your Order; the switch to the
                # 2nd address happens at the end from Place Your Order.
                if not _confirm_first_address(driver, markers):
                    _handle_address_screen(driver, markers, outcome)
                    address_done = True
            elif not address_done:
                _handle_address_screen(driver, markers, outcome)
                address_done = True
            elif not (slot_done and payment_done):
                # Address already confirmed: Place Your Order is still loading
                # (spinner over the address page with a big basket).
                time.sleep(2.5)
            if slot_done and payment_done:
                break
            # Then go on to Place Your Order for the slot (and payment) step.
            first = None
            time.sleep(0.3)
            continue

        if screen == "place_order":
            # Place Your Order can sit under a reappearing Prime sheet.
            _dismiss_prime_if_blocking(
                driver, markers, outcome, why="place_order branch"
            )
            # Expand slots + pick last before payment/address (matches images).
            if not slot_done:
                if not _expand_and_select_last_slot(driver, markers, outcome):
                    raise AssertionError(
                        'Could not expand "See more delivery slots" and select last slot'
                    )
                slot_done = True
                time.sleep(0.12)
                # Expanding/selecting last slot scrolls the page; address sits above.
                _scroll_slots_into_view(driver, direction="up")
                time.sleep(0.12)
            if not payment_done:
                if _open_payment_from_place_order(driver, markers, outcome):
                    time.sleep(0.2)
                    first = None
                    continue
                payment_done = True
            if not address_done:
                # Caller work that must happen on Place Your Order BEFORE the
                # address change (e.g. TC7_2 basket C's ×25 raise — the 2nd
                # address can make items unshippable, which replaces Place
                # Your Order with "Make updates to your items").
                hook = outcome.pop("_before_address_hook", None)
                if callable(hook):
                    hook()
                if not _open_address_from_place_order(driver, markers, outcome):
                    raise AssertionError(
                        "Could not open Change delivery address on Place Your Order"
                    )
                _handle_address_screen(driver, markers, outcome)
                address_done = True
            break

        # Amazon.com Checkout appears in the Place Your Order title too — only
        # fail when the grocery place-order surface is truly absent.
        if _checkout_webview_visible(driver) and not _place_order_surface_visible(
            driver
        ):
            raise AssertionError(
                "Proceed opened Amazon webview checkout (not native grocery payment/"
                "Place Your Order). Clear the main Amazon cart and retry."
            )
        if round_i == 0:
            _dismiss_prime_if_blocking(driver, markers, outcome, why="checkout probe")
        time.sleep(0.2)
        first = None

    outcome["payment_flow_done"] = payment_done
    outcome["address_flow_done"] = address_done
    outcome["delivery_slot_flow_done"] = slot_done
    markers.mark(
        "checkout_edits_done",
        payment_done=payment_done,
        address_done=address_done,
        slot_done=slot_done,
    )
    if not slot_done:
        raise AssertionError(
            "Delivery-slot expand + last-slot select did not complete"
        )
    if not payment_done and not address_done:
        raise AssertionError(
            "After Proceed, could not complete payment or address screens"
        )
    if payment_done and not address_done:
        raise AssertionError(
            "Payment step finished but address change did not complete"
        )
    if outcome.get("payment_change_skipped"):
        print("  NOTE: payment change skipped (no 2nd saved card on the account)")
    print(
        f"  checkout edits done — slot={slot_done} payment={payment_done} "
        f"address={address_done}"
    )


def run_test_case_6(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """TASK 6: Produce +×5 → cart record → last delivery slot → payment + address."""
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
    plan["add_strategy"] = "home_grocery_produce_plus_or_search"
    plan["place_order"] = False
    plan["steps"] = [
        "Start new automation session (kill prior Amazon if running)",
        "Open Amazon App (cold start)",
        'Tap home "Grocery" tab',
        'Tap "Produce" aisle under search bar',
        f"Add {TARGET_ITEMS} different Produce items via listing +",
        f'If no products: search "{SEARCH_FALLBACK_QUERY}" then add {TARGET_ITEMS}',
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
        "Stop screen recording (do not place order)",
    ]
    plan["notes"] = (
        f"{TEST_CASE_NAME} (TASK 6) — same as TC5 plus expand delivery slots "
        "and select the last slot, then payment 2nd card → address 2nd → Deliver. "
        "Does not place the order."
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

        _add_five_produce_or_search(driver, markers, outcome)
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
        _tc6_checkout_edits(driver, markers, outcome)

        time.sleep(0.2)
        markers.mark(
            "test_case_6_stopped_after_address",
            items=outcome.get("items_added_count"),
            checkout_steps=outcome.get("checkout_steps"),
            slot=outcome.get("delivery_slot_label"),
        )
        print("Stopping process after address change (order not placed) …")

        outcome["status"] = "ok"
        outcome["message"] = (
            f"{TEST_CASE_NAME} OK — added {outcome.get('items_added_count')} "
            f"via Grocery→Produce/+; cart → record → See more slots → last slot → "
            f"2nd payment → Continue → 2nd address → Deliver"
        )
        if outcome.get("payment_change_skipped"):
            outcome["message"] = outcome["message"].replace(
                "2nd payment → Continue", "payment change SKIPPED"
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
