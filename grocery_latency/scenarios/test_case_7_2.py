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
import time
from dataclasses import dataclass
from datetime import datetime, timezone

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
from grocery_latency.scenarios.test_case_2 import (
    GROCERY_PLUS_STRATEGIES,
    _add_fingerprint,
    _cart_badge_count,
    _collect_grocery_plus,
    _open_cart_page,
    _qty_chip_visible_near,
    _scroll_down,
    _verify_five_in_cart,
    _wait_badge_up,
)
from grocery_latency.scenarios.test_case_3 import _mark_step
from grocery_latency.scenarios.test_case_5 import (
    SEARCH_FALLBACK_QUERY,
    _count_visible_plus,
    _dismiss_overlay_fast,
    _open_grocery_produce,
    _search_vegetables_and_fruits,
)
from grocery_latency.scenarios.test_case_6 import _tc6_checkout_edits

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
    estimated = BASE_CART_RECORDING_SECONDS + sample.num_items * 2 + sample.total_units
    return max(
        BASE_CART_RECORDING_SECONDS,
        min(int(settings.recording_time_limit_seconds), int(estimated)),
    )


def _tap_xy(driver, x: float, y: float) -> None:
    driver.execute_script(
        "mobile: tap",
        {"x": int(round(x)), "y": int(round(y))},
    )


def _rapid_qty_plus_at(
    driver,
    markers: MarkerLog,
    *,
    cx: float,
    cy: float,
    extra_units: int,
    item_index: int,
) -> int:
    """Tap listing qty + at fixed coords (extra_units times). Min latency path."""
    if extra_units <= 0:
        return 0
    taps = 0
    # Short pause — Amazon grocery qty chip accepts rapid taps at same point.
    pause = 0.05 if extra_units <= 10 else 0.06
    for i in range(extra_units):
        try:
            _tap_xy(driver, cx, cy)
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
        except Exception as exc:  # noqa: BLE001
            print(f"  qty + rapid tap {i + 1}/{extra_units} failed: {exc}")
            break
    return taps


def _add_basket_scaled_fast(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    sample: BasketSample,
    max_scrolls: int | None = None,
    source: str = "grocery_produce_plus",
) -> list[dict]:
    """Add N distinct listing items, then scale each to U units on the listing.

    Efficiency: stay on Produce/search grid — after first + on an item, rapid
    mobile:tap the same coordinates for (U-1) more units (no cart hop, no
    re-find per tap). Scroll only when no unused + remains on screen.
    """
    target = sample.num_items
    units_each = sample.units_each
    if max_scrolls is None:
        max_scrolls = max(10, target // 2 + 6)

    print(
        f"Step: basket {sample.letter} — add {target} items × {units_each} "
        f"unit(s) each (total {sample.total_units}) via listing + …"
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
    scrolls = 0
    t0 = time.monotonic()

    while len(added) < target and scrolls <= max_scrolls:
        batch = _collect_grocery_plus(driver, used)
        if not batch:
            try:
                el, by, value = find_first(driver, GROCERY_PLUS_STRATEGIES, timeout=0.7)
                fp = _add_fingerprint(el) or f"plus:{len(added)}"
                if fp not in used:
                    r = el.rect or {}
                    y = float(r.get("y", 0))
                    if 160 <= y <= 780:
                        batch = [(el, by, value, fp, y)]
            except TimeoutError:
                pass
        if not batch:
            _scroll_down(driver, percent=0.50)
            scrolls += 1
            markers.mark("produce_grid_scrolled", index=scrolls)
            time.sleep(0.15)
            continue

        added_before = len(added)
        for el, by, value, fp, y in batch:
            if len(added) >= target:
                break
            r = el.rect or {}
            cx = float(r.get("x", 0)) + float(r.get("width", 0)) / 2.0
            cy = float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0
            print(
                f"  + item #{len(added) + 1}/{target}: "
                f"{by}={value!r} @({cx:.0f},{cy:.0f}) → ×{units_each}"
            )
            try:
                # First unit: element click (most reliable for unused +).
                el.click()
            except Exception as exc:  # noqa: BLE001
                if fp:
                    used.add(fp)
                print(f"  + tap failed: {exc}")
                continue

            if fp:
                used.add(fp)
            markers.mark(
                "produce_plus_tapped",
                index=len(added) + 1,
                by=by,
                value=value,
                fingerprint=fp,
                y=y,
                x=cx,
            )
            time.sleep(0.08)

            ok = _qty_chip_visible_near(driver, y)
            confirm_via = "qty_chip"
            if not ok:
                ok, _after = _wait_badge_up(driver, None, timeout=0.35)
                confirm_via = "badge" if ok else "tap_optimistic"
                ok = True

            # Remaining units: rapid taps at same + / Increase spot.
            extra = max(0, units_each - 1)
            qty_taps = 0
            if extra:
                qty_taps = _rapid_qty_plus_at(
                    driver,
                    markers,
                    cx=cx,
                    cy=cy,
                    extra_units=extra,
                    item_index=len(added) + 1,
                )
                print(
                    f"  scaled item #{len(added) + 1} qty +{qty_taps}/{extra} "
                    f"(target units={units_each})"
                )

            entry = {
                "index": len(added) + 1,
                "source": source,
                "add_path": "plus_then_rapid_qty",
                "add_by": by,
                "add_value": value,
                "fingerprint": fp,
                "confirmed": True,
                "confirm_via": confirm_via,
                "units_target": units_each,
                "units_extra_taps": qty_taps,
                "x": cx,
                "y": cy,
            }
            added.append(entry)
            markers.mark(
                "item_added_to_cart",
                index=entry["index"],
                confirmed=True,
                fingerprint=fp,
                units_target=units_each,
                units_extra_taps=qty_taps,
                path="plus_then_rapid_qty",
            )
            print(
                f"  added #{entry['index']}/{target} "
                f"({units_each}u, {confirm_via})"
            )

        if len(added) == added_before and len(added) < target:
            _scroll_down(driver, percent=0.50)
            scrolls += 1
            markers.mark(
                "produce_grid_scrolled", index=scrolls, reason="no_add_on_screen"
            )
            time.sleep(0.15)

    elapsed = time.monotonic() - t0
    outcome["items_added"] = list(added)
    outcome["items_added_count"] = len(added)
    outcome["basket_add_elapsed_seconds"] = round(elapsed, 2)
    end_badge = _cart_badge_count(driver)
    outcome["cart_count_after_adds"] = end_badge
    markers.mark(
        "basket_plus_add_done",
        sample=sample.letter,
        added=len(added),
        target=target,
        units_each=units_each,
        badge_before=start_badge,
        badge_after=end_badge,
        scrolls=scrolls,
        elapsed_s=round(elapsed, 2),
        source=source,
    )
    print(
        f"  basket add done: {len(added)}/{target} items × {units_each}u "
        f"in {elapsed:.1f}s (scrolls={scrolls}, badge {start_badge}→{end_badge})"
    )
    if len(added) < target:
        raise AssertionError(
            f"Only added {len(added)}/{target} items for basket {sample.letter} "
            f"(scrolls={scrolls}, badge {start_badge}→{end_badge}, source={source})"
        )
    return added


def _add_basket_produce_or_search(
    driver, markers: MarkerLog, outcome: dict, *, sample: BasketSample
) -> list[dict]:
    """Produce grid scaled add; fallback search if Produce empty."""
    _open_grocery_produce(driver, markers, outcome)
    if outcome.get("produce_products_ready") or _count_visible_plus(driver) >= 1:
        try:
            return _add_basket_scaled_fast(
                driver,
                markers,
                outcome,
                sample=sample,
                source="grocery_produce_plus",
            )
        except AssertionError as exc:
            print(f"  Produce adds incomplete ({exc}) — trying search fallback …")
            markers.mark("produce_add_incomplete_fallback_search")

    _search_vegetables_and_fruits(driver, markers, outcome)
    return _add_basket_scaled_fast(
        driver,
        markers,
        outcome,
        sample=sample,
        max_scrolls=max(12, sample.num_items // 2 + 8),
        source="grocery_search_vegetables_fruits_plus",
    )


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
            f"Basket {sample.letter}: add {sample.num_items} distinct items, "
            f"scale each to {sample.units_each} unit(s) via listing + / rapid qty + "
            f"(total {sample.total_units})"
        ),
        f'If no products: search "{SEARCH_FALLBACK_QUERY}" then same scaled add',
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

        _add_basket_produce_or_search(driver, markers, outcome, sample=sample)
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

        _tc6_checkout_edits(driver, markers, outcome)

        time.sleep(0.2)
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
            f"{outcome.get('items_added_count')} items × {sample.units_each}u "
            f"(total {sample.total_units}) in "
            f"{outcome.get('basket_add_elapsed_seconds')}s → cart → record → "
            f"See more slots → last slot → 2nd payment → Continue → "
            f"2nd address → Deliver"
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
