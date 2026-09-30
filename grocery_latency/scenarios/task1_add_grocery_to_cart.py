"""TASK 1: cold-start Amazon → open master ASIN PDP → add to cart."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from grocery_latency.amazon_task1_common import (
    add_master_asin_to_cart,
    cold_start_amazon,
    dismiss_and_login_pause,
)
from grocery_latency.config import Settings
from grocery_latency.driver import create_driver, describe_session
from grocery_latency.ios_helpers import dump_page_source
from grocery_latency.markers import MarkerLog


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def run_task1_add_grocery_to_cart(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """TASK 1: cold-start Amazon → deep-link master ASIN → add to cart. No checkout."""
    if retailer != "amazon":
        raise ValueError("task1_add_grocery_to_cart is Amazon-only.")

    plan = describe_session(settings, retailer, bundle_id)
    plan["scenario"] = "task1_add_grocery_to_cart"
    plan["dry_run"] = dry_run
    plan["study_mode"] = "cancel_test"
    plan["recording"] = False
    plan["master_asins"] = list(settings.amazon_asins)
    plan["steps"] = [
        "Start new automation session (kill prior if running)",
        "Open Amazon App (cold start — kill if already running)",
        "Open product directly from master ASIN list (deep link)",
        "Add item to cart on PDP",
    ]
    plan["notes"] = (
        "TASK 1 cancel/test: stop after add-to-cart. No checkout/order. "
        "No screen recording. Uses pre-built master ASIN list — no grocery search."
    )

    if dry_run:
        print(json.dumps({"status": "dry_run_ok", "plan": plan}, indent=2))
        return {"status": "dry_run_ok", "plan": plan}

    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    prefix = f"{retailer}_task1_add_grocery_to_cart_{stamp}"
    result_path = settings.artifacts_dir / f"{prefix}.json"
    page_source_path = settings.artifacts_dir / f"{prefix}_page.xml"
    screenshot_path = settings.artifacts_dir / f"{prefix}_blocker.png"

    markers = MarkerLog()
    driver = None
    outcome: dict = {
        "status": "starting",
        "plan": plan,
        "markers": markers.events,
        "selectors": {},
        "item_added_to_cart": False,
        "recording_started": False,
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

        cold_start_amazon(driver, bundle_id, markers, outcome)
        dismiss_and_login_pause(driver, settings, markers, outcome)
        chosen = add_master_asin_to_cart(
            driver,
            bundle_id,
            settings.amazon_asins,
            markers,
            outcome,
            skip_if_cart_nonempty=settings.skip_add_if_cart_nonempty,
            cart_add_url_only=settings.cart_add_url_only,
        )

        outcome["status"] = "ok"
        outcome["message"] = (
            f"TASK 1 OK — master ASIN {chosen.get('asin')} added to cart "
            f"via {chosen.get('open_method')}"
        )
        print(outcome["message"])
        return outcome
    except Exception as exc:  # noqa: BLE001
        outcome["status"] = "error"
        outcome["error_type"] = type(exc).__name__
        outcome["error"] = str(exc)
        print(f"LIVE RUN FAILED: {type(exc).__name__}: {exc}")
        if driver is not None and "page_source_dump" not in outcome:
            _dump_ui("error")
        return outcome
    finally:
        if driver is not None:
            try:
                driver.quit()
                print("Appium session stopped.")
            except Exception as quit_exc:  # noqa: BLE001
                outcome["quit_error"] = str(quit_exc)
        outcome["markers"] = markers.events
        result_path.write_text(json.dumps(outcome, indent=2), encoding="utf-8")
        print(f"Wrote {result_path}")
