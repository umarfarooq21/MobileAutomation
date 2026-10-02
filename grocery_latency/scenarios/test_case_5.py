"""Test Case 5 (TASK 5): Grocery → Produce → +×5 → payment + address edits.

Grocery tab → Produce aisle (yellow + ×5) → Cart → record → Proceed →
No thanks → Change payment method → 2nd card → Continue →
Change delivery or pickup address → 2nd address → Deliver to this address → stop.

If Produce has no + chips, search "vegetables and fruits" then add 5.
Does not place the order.
"""

from __future__ import annotations

import json
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
    find_first,
    looks_like_login_wall,
    tap_dismissibles,
)
from grocery_latency.markers import MarkerLog
from grocery_latency.recording import save_recording_bytes, start_ios_screen_recording
from grocery_latency.scenarios.test_case_2 import (
    GROCERY_HOME_TAB_STRATEGIES,
    GROCERY_PLUS_STRATEGIES,
    _add_fingerprint,
    _cart_badge_count,
    _collect_grocery_plus,
    _dismiss_rufus,
    _open_cart_page,
    _qty_chip_visible_near,
    _scroll_down,
    _try_tap_first,
    _verify_five_in_cart,
    _wait_badge_up,
)
from grocery_latency.scenarios.test_case_3 import _tc3_checkout_edits
from grocery_latency.scenarios.test_case_4 import (
    GROCERY_SEARCH_HINT_STRATEGIES,
    _dismiss_overlay_fast,
    _submit_search,
)

TEST_CASE_NAME = "Test Case 5"
TEST_CASE_ID = "test_case_5"
TARGET_ITEMS = 5
SEARCH_FALLBACK_QUERY = "vegetables and fruits"
# Cap only — stop_recording runs after last address action.
CART_RECORDING_SECONDS = 180

PRODUCE_TAB_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "Produce"),
    (
        AppiumBy.IOS_PREDICATE,
        'label == "Produce" OR name == "Produce"',
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


def _swipe_aisle_rail(driver, *, direction: str = "left") -> None:
    """Nudge the Grocery aisle rail (For You / Produce / Dairy / …)."""
    try:
        size = driver.get_window_size()
        w, h = int(size["width"]), int(size["height"])
        y = max(140, min(220, int(h * 0.22)))
        if direction == "left":
            from_x, to_x = int(w * 0.88), int(w * 0.18)
        else:
            from_x, to_x = int(w * 0.18), int(w * 0.88)
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.22,
                "fromX": from_x,
                "fromY": y,
                "toX": to_x,
                "toY": y,
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _count_visible_plus(driver) -> int:
    try:
        return len(_collect_grocery_plus(driver, set()))
    except Exception:  # noqa: BLE001
        return 0


def _wait_produce_products_ready(
    driver, markers: MarkerLog, outcome: dict, *, attempts: int = 4
) -> bool:
    """Return True when Produce grid shows yellow + chips."""
    for i in range(attempts):
        try:
            el, by, value = find_first(driver, RELOAD_PAGE, timeout=0.4)
            if el.is_displayed():
                el.click()
                markers.mark("produce_reload_tapped", by=by, value=value, attempt=i + 1)
                print(f"  Produce restocking — Reload ({i + 1}/{attempts})")
                time.sleep(0.8)
                _try_tap_first(driver, PRODUCE_TAB_STRATEGIES, timeout=0.6, scrolls=0)
                time.sleep(0.35)
        except Exception:  # noqa: BLE001
            pass

        n = _count_visible_plus(driver)
        if n >= 1:
            markers.mark("produce_products_ready", count=n, attempt=i + 1)
            outcome["produce_products_ready"] = True
            return True
        try:
            find_first(driver, GROCERY_PLUS_STRATEGIES, timeout=0.7)
            markers.mark("produce_products_ready", via="plus_strategy", attempt=i + 1)
            outcome["produce_products_ready"] = True
            return True
        except TimeoutError:
            print(f"  waiting for Produce products ({i + 1}/{attempts}) …")
            time.sleep(0.45)

    outcome["produce_products_ready"] = False
    markers.mark("produce_products_not_ready")
    return False


def _open_grocery_produce(driver, markers: MarkerLog, outcome: dict) -> None:
    """Tap home Grocery, then Produce aisle (as in the Produce grid screenshot)."""
    print('Step: tap home "Grocery" tab …')
    hit = None
    try:
        el = driver.find_element(AppiumBy.ACCESSIBILITY_ID, "Grocery")
        el.click()
        hit = (AppiumBy.ACCESSIBILITY_ID, "Grocery")
    except Exception:  # noqa: BLE001
        hit = _try_tap_first(
            driver, GROCERY_HOME_TAB_STRATEGIES, timeout=2.5, scrolls=0
        )
    if not hit:
        raise AssertionError('Home "Grocery" tab not found')
    by, value = hit
    outcome.setdefault("selectors", {})["grocery_home_tab"] = {"by": by, "value": value}
    markers.mark("grocery_home_tab_tapped", by=by, value=value)
    print(f"  Grocery tab: {by} = {value!r}")
    time.sleep(0.35)
    _dismiss_rufus(driver, markers)
    _dismiss_overlay_fast(driver)

    print('Step: tap "Produce" aisle …')
    produce = _try_tap_first(driver, PRODUCE_TAB_STRATEGIES, timeout=0.7, scrolls=0)
    if not produce:
        # Produce is usually near the left; swipe right-to-left then left-to-right.
        for swipe_i in range(3):
            _swipe_aisle_rail(driver, direction="right" if swipe_i == 0 else "left")
            markers.mark("produce_aisle_swiped", index=swipe_i + 1)
            produce = _try_tap_first(
                driver, PRODUCE_TAB_STRATEGIES, timeout=0.45, scrolls=0
            )
            if produce:
                break

    if not produce:
        raise AssertionError('Grocery "Produce" tab not found under search bar')

    outcome["selectors"]["produce_tab"] = {
        "by": produce[0],
        "value": produce[1],
    }
    markers.mark("produce_tab_tapped", by=produce[0], value=produce[1])
    print(f"  Produce: {produce[0]} = {produce[1]!r}")
    time.sleep(0.4)
    _dismiss_overlay_fast(driver)
    _wait_produce_products_ready(driver, markers, outcome, attempts=3)


def _search_vegetables_and_fruits(
    driver, markers: MarkerLog, outcome: dict
) -> None:
    """Fallback: search groceries for vegetables and fruits."""
    query = SEARCH_FALLBACK_QUERY
    print(f'Step: no Produce + chips — search "{query}" …')
    outcome["search_query"] = query
    outcome["produce_fallback_search"] = True
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
    markers.mark("search_tapped", by=tap_by, value=tap_value, query=query)
    time.sleep(0.15)

    type_el, type_by, type_value = find_first(
        driver, SEARCH_TYPE_STRATEGIES + GROCERY_SEARCH_HINT_STRATEGIES, timeout=2.5
    )
    outcome["selectors"]["search_type"] = {"by": type_by, "value": type_value}
    print(f"  Search type: {type_by} = {type_value!r}")
    type_into_search(driver, type_el, query)
    markers.mark("query_typed", query=query)
    outcome["query_confirmed"] = True

    _submit_search(driver, markers, query)
    markers.mark("produce_fallback_searched", query=query)
    time.sleep(0.35)
    print(f'  Search submitted for "{query}"')
    _dismiss_overlay_fast(driver)


def _add_n_produce_fast(
    driver,
    markers: MarkerLog,
    outcome: dict,
    *,
    target: int,
    max_scrolls: int = 8,
    source: str = "grocery_produce_plus",
) -> list[dict]:
    """Tap distinct listing yellow + / Add chips (never invent grid taps / PDP)."""
    print(f"Step: add {target} different items via listing + …")
    _dismiss_overlay_fast(driver)
    start_badge = _cart_badge_count(driver)
    outcome["cart_count_before_adds"] = start_badge
    outcome["asin_add_strategy"] = source
    markers.mark("produce_plus_add_start", target=target, badge=start_badge, source=source)

    used: set[str] = set()
    added: list[dict] = []
    scrolls = 0

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
            time.sleep(0.2)
            continue

        added_before = len(added)
        for el, by, value, fp, y in batch:
            if len(added) >= target:
                break
            print(
                f"  + candidate #{len(added) + 1}/{target}: "
                f"{by}={value!r} y={y:.0f} fp={fp}"
            )
            try:
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
            )
            time.sleep(0.14)

            ok = _qty_chip_visible_near(driver, y)
            confirm_via = "qty_chip"
            after: int | None = len(added) + 1 if ok else None
            if not ok:
                ok, after = _wait_badge_up(driver, None, timeout=0.4)
                confirm_via = "badge"
            if not ok:
                # Optimistic count — listing add often has slow badge.
                ok = True
                after = len(added) + 1
                confirm_via = "tap_optimistic"

            entry = {
                "index": len(added) + 1,
                "source": source,
                "add_path": "plus",
                "add_by": by,
                "add_value": value,
                "fingerprint": fp,
                "confirmed": True,
                "confirm_via": confirm_via,
                "cart_before": None,
                "cart_after": after,
            }
            added.append(entry)
            markers.mark(
                "item_added_to_cart",
                index=entry["index"],
                confirmed=True,
                fingerprint=fp,
                path="plus",
                confirm_via=confirm_via,
            )
            print(
                f"  added #{entry['index']}/{target} via + "
                f"@ y={y:.0f} ({confirm_via})"
            )

        if len(added) == added_before and len(added) < target:
            _scroll_down(driver, percent=0.50)
            scrolls += 1
            markers.mark(
                "produce_grid_scrolled", index=scrolls, reason="no_add_on_screen"
            )
            time.sleep(0.2)

    outcome["items_added"] = list(added)
    outcome["items_added_count"] = len(added)
    end_badge = _cart_badge_count(driver)
    outcome["cart_count_after_adds"] = end_badge
    markers.mark(
        "produce_plus_add_done",
        added=len(added),
        target=target,
        badge_before=start_badge,
        badge_after=end_badge,
        scrolls=scrolls,
        source=source,
    )
    if len(added) < target:
        raise AssertionError(
            f"Only added {len(added)}/{target} via listing + "
            f"(scrolls={scrolls}, badge {start_badge}→{end_badge}, source={source})"
        )
    return added


def _add_five_produce_or_search(
    driver, markers: MarkerLog, outcome: dict
) -> list[dict]:
    """Produce grid +×5; if empty, search vegetables and fruits then +×5."""
    _open_grocery_produce(driver, markers, outcome)
    if outcome.get("produce_products_ready") or _count_visible_plus(driver) >= 1:
        try:
            return _add_n_produce_fast(
                driver,
                markers,
                outcome,
                target=TARGET_ITEMS,
                max_scrolls=8,
                source="grocery_produce_plus",
            )
        except AssertionError as exc:
            print(f"  Produce adds incomplete ({exc}) — trying search fallback …")
            markers.mark("produce_add_incomplete_fallback_search")

    _search_vegetables_and_fruits(driver, markers, outcome)
    return _add_n_produce_fast(
        driver,
        markers,
        outcome,
        target=TARGET_ITEMS,
        max_scrolls=10,
        source="grocery_search_vegetables_fruits_plus",
    )


def run_test_case_5(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """TASK 5: Grocery → Produce → +×5 → cart record → payment + address edits."""
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
        'Tap "No thanks" if shown',
        'Tap "Change payment method"',
        "Select 2nd payment card → Continue",
        'Tap "Change delivery or pickup address"',
        'Select 2nd address → "Deliver to this address"',
        "Stop screen recording (do not place order)",
    ]
    plan["notes"] = (
        f"{TEST_CASE_NAME} (TASK 5) — Grocery → Produce → yellow + ×{TARGET_ITEMS}; "
        f'fallback search "{SEARCH_FALLBACK_QUERY}"; cart record → payment 2nd card → '
        "address 2nd → Deliver. Does not place the order."
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
        _tc3_checkout_edits(driver, markers, outcome)

        time.sleep(0.4)
        markers.mark(
            "test_case_5_stopped_after_address",
            items=outcome.get("items_added_count"),
            checkout_steps=outcome.get("checkout_steps"),
        )
        print("Stopping process after address change (order not placed) …")

        outcome["status"] = "ok"
        outcome["message"] = (
            f"{TEST_CASE_NAME} OK — added {outcome.get('items_added_count')} "
            f"via Grocery→Produce/+; cart → record → checkout → No thanks → "
            f"2nd payment → Continue → 2nd address → Deliver"
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
