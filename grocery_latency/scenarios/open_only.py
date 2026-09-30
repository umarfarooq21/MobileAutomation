from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from grocery_latency.config import Settings
from grocery_latency.driver import create_driver, describe_session


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def run_open_only(
    settings: Settings,
    retailer: str,
    bundle_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Launch retailer app only; pause for human login if needed. Cancel/test mode."""
    plan = describe_session(settings, retailer, bundle_id)
    plan["scenario"] = "open_only"
    plan["dry_run"] = dry_run
    plan["study_mode"] = "cancel_test"
    plan["notes"] = (
        "open_only: create XCUITest session, activate bundleId, "
        "wait briefly for human login assist, then quit. No selectors invented."
    )

    if dry_run:
        print(json.dumps({"status": "dry_run_ok", "plan": plan}, indent=2))
        return {"status": "dry_run_ok", "plan": plan}

    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    result_path = settings.artifacts_dir / f"{retailer}_open_only_{_stamp()}.json"
    driver = None
    outcome: dict = {"status": "starting", "plan": plan}

    try:
        print(f"Creating Appium session for {retailer} ({bundle_id}) …")
        driver = create_driver(settings, bundle_id)
        outcome["session_id"] = getattr(driver, "session_id", None)
        print(f"Session started: {outcome['session_id']}")

        # Confirm app is foreground; do not invent UI selectors.
        try:
            state = driver.query_app_state(bundle_id)
            outcome["app_state"] = int(state) if state is not None else None
            print(f"App state for {bundle_id}: {outcome['app_state']} (4=running in foreground)")
        except Exception as exc:  # noqa: BLE001 — report exact Appium error
            outcome["app_state_error"] = str(exc)
            print(f"query_app_state warning: {exc}")

        pause = max(0, settings.login_pause_seconds)
        if pause:
            print(
                f"Pausing {pause}s for human login / Trust / CAPTCHA if needed. "
                "Leave the Amazon app open and complete any prompts on the phone."
            )
            time.sleep(pause)

        outcome["status"] = "ok"
        outcome["amazon_opened"] = retailer == "amazon"
        outcome["message"] = f"{retailer} session completed (open_only)."
        print(outcome["message"])
        return outcome
    except Exception as exc:  # noqa: BLE001 — capture exact session/signing errors
        outcome["status"] = "error"
        outcome["error_type"] = type(exc).__name__
        outcome["error"] = str(exc)
        print(f"LIVE RUN FAILED: {type(exc).__name__}: {exc}")
        return outcome
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception as quit_exc:  # noqa: BLE001
                outcome["quit_error"] = str(quit_exc)
        result_path.write_text(json.dumps(outcome, indent=2), encoding="utf-8")
        print(f"Wrote {result_path}")
