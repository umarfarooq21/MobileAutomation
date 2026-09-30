from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone

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
    find_first,
    looks_like_login_wall,
    tap_dismissibles,
)
from grocery_latency.markers import MarkerLog

SEARCH_QUERY = "Groceries"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def run_search_groceries(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Open Amazon, tap search, type Groceries. Selection-only — no order."""
    if retailer != "amazon":
        raise ValueError(
            "search_groceries is Amazon-only for now (do not invent Walmart/Instacart)."
        )

    plan = describe_session(settings, retailer, bundle_id)
    plan["scenario"] = "search_groceries"
    plan["dry_run"] = dry_run
    plan["study_mode"] = "cancel_test"
    plan["query"] = SEARCH_QUERY
    plan["steps"] = [
        "Open Amazon App",
        f"Tap on Search Bar and Type `{SEARCH_QUERY}`",
    ]
    plan["notes"] = (
        "search_groceries: open Amazon, dismiss light blockers, tap search, "
        "type Groceries. No cart/checkout/order."
    )

    if dry_run:
        print(json.dumps({"status": "dry_run_ok", "plan": plan}, indent=2))
        return {"status": "dry_run_ok", "plan": plan}

    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    result_path = settings.artifacts_dir / f"{retailer}_search_groceries_{stamp}.json"
    page_source_path = settings.artifacts_dir / f"{retailer}_search_groceries_{stamp}_page.xml"
    video_path = settings.artifacts_dir / f"{retailer}_search_groceries_{stamp}.mp4"

    markers = MarkerLog()
    driver = None
    recording = False
    outcome: dict = {"status": "starting", "plan": plan, "markers": markers.events}

    try:
        print(f"Creating Appium session for {retailer} ({bundle_id}) …")
        driver = create_driver(settings, bundle_id)
        outcome["session_id"] = getattr(driver, "session_id", None)
        print(f"Session started: {outcome['session_id']}")

        try:
            state = driver.query_app_state(bundle_id)
            outcome["app_state"] = int(state) if state is not None else None
            print(f"App state for {bundle_id}: {outcome['app_state']} (4=foreground)")
        except Exception as exc:  # noqa: BLE001
            outcome["app_state_error"] = str(exc)

        markers.mark("app_opened", app_state=outcome.get("app_state"))

        try:
            driver.start_recording_screen(timeLimit=180, forceRestart=True)
            recording = True
            outcome["recording_started"] = True
        except Exception as exc:  # noqa: BLE001 — optional
            outcome["recording_started"] = False
            outcome["recording_start_error"] = str(exc)
            print(f"Screen recording skipped: {exc}")

        time.sleep(2.0)
        dismissed = tap_dismissibles(driver)
        outcome["dismissed"] = dismissed
        if dismissed:
            print(f"Dismissed: {dismissed}")

        if looks_like_login_wall(driver):
            pause = max(0, settings.login_pause_seconds)
            print(
                f"Login wall suspected — pausing {pause}s for human assist on the phone."
            )
            markers.mark("login_pause_start", seconds=pause)
            time.sleep(pause)
            markers.mark("login_pause_end")
            tap_dismissibles(driver)

        print("Looking for Amazon search field …")
        search_el, tap_by, tap_value = find_first(
            driver, SEARCH_TAP_STRATEGIES, timeout=25.0
        )
        outcome["search_tap_selector"] = {"by": tap_by, "value": tap_value}
        print(f"Search tap target: {tap_by} = {tap_value!r}")
        search_el.click()
        markers.mark("search_tapped", by=tap_by, value=tap_value)
        time.sleep(1.0)

        type_el, type_by, type_value = find_first(
            driver, SEARCH_TYPE_STRATEGIES, timeout=20.0
        )
        outcome["search_type_selector"] = {"by": type_by, "value": type_value}
        print(f"Search type target: {type_by} = {type_value!r}")
        type_into_search(driver, type_el, SEARCH_QUERY)
        time.sleep(1.2)
        markers.mark("groceries_typed", query=SEARCH_QUERY)

        ok, evidence = field_contains_query(driver, SEARCH_QUERY)
        outcome["query_confirmed"] = ok
        outcome["query_evidence"] = evidence
        if not ok:
            dump_page_source(driver, page_source_path)
            outcome["page_source_dump"] = str(page_source_path)
            raise AssertionError(
                f"Could not confirm typed query {SEARCH_QUERY!r} in search field/UI"
            )

        outcome["status"] = "ok"
        outcome["message"] = (
            f"Amazon search_groceries OK — typed {SEARCH_QUERY!r}; evidence={evidence!r}"
        )
        print(outcome["message"])
        return outcome
    except Exception as exc:  # noqa: BLE001
        outcome["status"] = "error"
        outcome["error_type"] = type(exc).__name__
        outcome["error"] = str(exc)
        print(f"LIVE RUN FAILED: {type(exc).__name__}: {exc}")
        if driver is not None and "page_source_dump" not in outcome:
            try:
                dump_page_source(driver, page_source_path)
                outcome["page_source_dump"] = str(page_source_path)
                print(f"Wrote page source → {page_source_path}")
            except Exception as dump_exc:  # noqa: BLE001
                outcome["page_source_dump_error"] = str(dump_exc)
        return outcome
    finally:
        if driver is not None and recording:
            try:
                video_b64 = driver.stop_recording_screen()
                if video_b64:
                    video_path.write_bytes(base64.b64decode(video_b64))
                    outcome["video"] = str(video_path)
                    print(f"Wrote video → {video_path}")
            except Exception as rec_exc:  # noqa: BLE001
                outcome["recording_stop_error"] = str(rec_exc)
        if driver is not None:
            try:
                driver.quit()
            except Exception as quit_exc:  # noqa: BLE001
                outcome["quit_error"] = str(quit_exc)
        outcome["markers"] = markers.events
        result_path.write_text(json.dumps(outcome, indent=2), encoding="utf-8")
        print(f"Wrote {result_path}")
