from __future__ import annotations

import importlib
import re
import sys
from contextlib import nullcontext
from datetime import datetime

import click

from grocery_latency import __version__
from grocery_latency.config import RETAILERS, SCENARIOS, load_settings
from grocery_latency.scenarios.open_only import run_open_only
from grocery_latency.scenarios.search_groceries import run_search_groceries
from grocery_latency.scenarios.test_case_1 import run_test_case_1
from grocery_latency.scenarios.test_case_2 import run_test_case_2
from grocery_latency.scenarios.test_case_3 import run_test_case_3
from grocery_latency.scenarios.test_case_4 import run_test_case_4
from grocery_latency.scenarios.test_case_5 import run_test_case_5
from grocery_latency.scenarios.test_case_6 import run_test_case_6
from grocery_latency.scenarios.test_case_7 import run_test_case_7
from grocery_latency.scenarios.test_case_amazon_final import run_test_case_7_2
from grocery_latency.scenarios.test_case_8 import run_test_case_8
from grocery_latency.scenarios.test_case_9 import run_test_case_9
from grocery_latency.scenarios.test_case_walmart_1 import run_test_case_walmart_1
from grocery_latency.scenarios.test_case_walmart_2 import run_test_case_walmart_2
from grocery_latency.scenarios.test_case_walmart_3 import run_test_case_walmart_3
from grocery_latency.scenarios.test_case_walmart_4 import run_test_case_walmart_4
from grocery_latency.scenarios.test_case_walmart_5 import run_test_case_walmart_5
from grocery_latency.scenarios.test_case_wallmart_final import run_test_case_walmart_6
from grocery_latency.scenarios.task1_add_grocery_to_cart import (
    run_task1_add_grocery_to_cart,
)
from grocery_latency.scenarios.task1_cart_checkout_cancel import (
    run_task1_cart_checkout_cancel,
)


@click.group()
@click.version_option(__version__, prog_name="grocery_latency")
def main() -> None:
    """Grocery large-order latency harness (iOS Appium / XCUITest)."""


@main.command("run")
@click.option(
    "--retailer",
    type=click.Choice(sorted(RETAILERS.keys()), case_sensitive=False),
    required=True,
    help="Retailer app to exercise.",
)
@click.option(
    "--scenario",
    type=click.Choice(SCENARIOS, case_sensitive=False),
    required=True,
    help=(
        "Scenario: open_only | search_groceries | test_case_1 | test_case_2 | "
        "test_case_3 | test_case_4 | test_case_5 | test_case_6 | test_case_7 | "
        "test_case_7_2 | test_case_8 | test_case_9 | test_case_walmart_1 | "
        "test_case_walmart_2 | test_case_walmart_3 | test_case_walmart_4 | "
        "test_case_walmart_5 | test_case_walmart_6 | "
        "test_case_amazon_final | test_case_wallmart_final | "
        "task1_add_grocery_to_cart | task1_cart_checkout_cancel. "
        "(select_grocery_under_4 is an alias for test_case_1.)"
    ),
)
@click.option(
    "--basket-size",
    type=click.Choice(
        ["A", "B", "C", "D", "E", "F", "G", "H"], case_sensitive=False
    ),
    default=None,
    help=(
        "Basket sample for TC7_2 or Walmart TC2–TC6 (A–H): items × units. "
        "Default: env BASKET_SIZE or A."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print planned capabilities / steps without starting an Appium session.",
)
def run_cmd(
    retailer: str,
    scenario: str,
    dry_run: bool,
    basket_size: str | None,
) -> None:
    """Run a latency scenario against a retailer on the connected iPhone."""
    settings = load_settings()
    log_ctx = (
        nullcontext()
        if dry_run
        else _RunLog(settings.artifacts_dir, retailer, scenario, basket_size)
    )
    with log_ctx:
        outcome = _run_scenario(settings, retailer, scenario, dry_run, basket_size)
    if not dry_run and outcome:
        _copy_to_submission(settings, retailer, scenario, basket_size, outcome,
                            getattr(log_ctx, "path", None))
    if (outcome or {}).get("status") == "error":
        raise SystemExit(1)


_SUBMISSION_SCENARIOS = {
    # scenario → retailer folder under artifacts/Submission/
    "test_case_7_2": "Amazon",
    "test_case_amazon_final": "Amazon",
}


def _copy_to_submission(settings, retailer, scenario, basket, outcome, log_path) -> None:
    """Exact passes → artifacts/Submission/<Retailer>/Basket <X>/ (video
    only). The dated folders keep every run and its log; Submission holds
    only the videos to hand in."""
    import shutil
    from pathlib import Path

    folder_name = _SUBMISSION_SCENARIOS.get(scenario.lower())
    if not folder_name or not basket:
        return
    if outcome.get("status") != "ok" or outcome.get("basket_exact") is not True:
        print(f"Submission: not copied (status={outcome.get('status')}, "
              f"exact={outcome.get('basket_exact')})")
        return
    dest = Path(settings.artifacts_dir).parent / "Submission" / folder_name / f"Basket {basket.upper()}"
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for src in (outcome.get("video"),):  # videos only — logs stay in the dated folders
        if src and Path(src).exists():
            shutil.copy2(src, dest / Path(src).name)
            copied.append(Path(src).name)
    print(f"Submission: copied {copied} → {dest}")


class _Tee:
    def __init__(self, *streams) -> None:
        self._streams = streams

    def write(self, data: str) -> int:
        for s in self._streams:
            s.write(data)
            s.flush()
        return len(data)

    def flush(self) -> None:
        for s in self._streams:
            s.flush()


class _RunLog:
    """Copy the run's console output into artifacts/<date>/<run>.log."""

    def __init__(self, folder, retailer: str, scenario: str, basket: str | None):
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = f"_{basket.upper()}" if basket else ""
        self.path = folder / f"{retailer.lower()}_{scenario.lower()}{tag}_{stamp}.log"

    def __enter__(self):
        self._file = open(self.path, "w", encoding="utf-8")
        self._out, self._err = sys.stdout, sys.stderr
        sys.stdout = _Tee(self._out, self._file)
        sys.stderr = _Tee(self._err, self._file)
        print(f"Run log → {self.path}")
        return self

    def __exit__(self, *exc) -> None:
        sys.stdout, sys.stderr = self._out, self._err
        self._file.close()


def _run_scenario(
    settings,
    retailer: str,
    scenario: str,
    dry_run: bool,
    basket_size: str | None,
) -> None:
    retailer = retailer.lower()
    scenario = scenario.lower()
    bundle_id = settings.bundle_id_for(retailer)

    if scenario == "open_only":
        outcome = run_open_only(settings, retailer, bundle_id, dry_run=dry_run)
    elif scenario == "search_groceries":
        outcome = run_search_groceries(settings, retailer, bundle_id, dry_run=dry_run)
    elif scenario in {"test_case_1", "select_grocery_under_4"}:
        outcome = run_test_case_1(settings, retailer, bundle_id, dry_run=dry_run)
    elif scenario == "test_case_2":
        outcome = run_test_case_2(settings, retailer, bundle_id, dry_run=dry_run)
    elif scenario == "test_case_3":
        outcome = run_test_case_3(settings, retailer, bundle_id, dry_run=dry_run)
    elif scenario == "test_case_4":
        outcome = run_test_case_4(settings, retailer, bundle_id, dry_run=dry_run)
    elif scenario == "test_case_5":
        outcome = run_test_case_5(settings, retailer, bundle_id, dry_run=dry_run)
    elif scenario == "test_case_6":
        outcome = run_test_case_6(settings, retailer, bundle_id, dry_run=dry_run)
    elif scenario == "test_case_7":
        outcome = run_test_case_7(settings, retailer, bundle_id, dry_run=dry_run)
    elif scenario in ("test_case_7_2", "test_case_amazon_final"):
        outcome = run_test_case_7_2(
            settings,
            retailer,
            bundle_id,
            dry_run=dry_run,
            basket_size=basket_size,
        )
    elif scenario == "test_case_8":
        outcome = run_test_case_8(settings, retailer, bundle_id, dry_run=dry_run)
    elif scenario == "test_case_9":
        outcome = run_test_case_9(settings, retailer, bundle_id, dry_run=dry_run)
    elif scenario == "test_case_walmart_1":
        outcome = run_test_case_walmart_1(
            settings, retailer, bundle_id, dry_run=dry_run
        )
    elif scenario == "test_case_walmart_2":
        outcome = run_test_case_walmart_2(
            settings,
            retailer,
            bundle_id,
            dry_run=dry_run,
            basket_size=basket_size,
        )
    elif scenario == "test_case_walmart_3":
        outcome = run_test_case_walmart_3(
            settings,
            retailer,
            bundle_id,
            dry_run=dry_run,
            basket_size=basket_size,
        )
    elif scenario == "test_case_walmart_4":
        outcome = run_test_case_walmart_4(
            settings,
            retailer,
            bundle_id,
            dry_run=dry_run,
            basket_size=basket_size,
        )
    elif scenario == "test_case_walmart_5":
        outcome = run_test_case_walmart_5(
            settings,
            retailer,
            bundle_id,
            dry_run=dry_run,
            basket_size=basket_size,
        )
    elif re.fullmatch(r"basket_[a-h]|test_case_[a-h]_instacart", scenario):
        # Instacart baskets (grocery_latency/scenarios/basket_X.py), loaded on
        # demand so one broken basket file can't stop the CLI. (scenario is
        # lower-cased above; the files use an upper-case letter.)
        letter = (scenario[7] if scenario.startswith("basket_") else scenario[10]).upper()
        module = importlib.import_module(f"grocery_latency.scenarios.basket_{letter}")
        outcome = getattr(module, f"run_basket_{letter}")(
            settings, retailer, bundle_id, dry_run=dry_run
        )
    elif scenario in ("test_case_walmart_6", "test_case_wallmart_final"):
        outcome = run_test_case_walmart_6(
            settings,
            retailer,
            bundle_id,
            dry_run=dry_run,
            basket_size=basket_size,
        )
    elif scenario == "task1_add_grocery_to_cart":
        outcome = run_task1_add_grocery_to_cart(
            settings, retailer, bundle_id, dry_run=dry_run
        )
    elif scenario == "task1_cart_checkout_cancel":
        outcome = run_task1_cart_checkout_cancel(
            settings, retailer, bundle_id, dry_run=dry_run
        )
    else:
        raise click.ClickException(f"Unsupported scenario: {scenario}")

    return outcome


if __name__ == "__main__":
    main()
