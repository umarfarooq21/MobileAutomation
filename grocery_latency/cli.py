from __future__ import annotations

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
from grocery_latency.scenarios.test_case_7_2 import run_test_case_7_2
from grocery_latency.scenarios.test_case_8 import run_test_case_8
from grocery_latency.scenarios.test_case_9 import run_test_case_9
from grocery_latency.scenarios.test_case_walmart_1 import run_test_case_walmart_1
from grocery_latency.scenarios.test_case_walmart_2 import run_test_case_walmart_2
from grocery_latency.scenarios.test_case_walmart_3 import run_test_case_walmart_3
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
        "test_case_walmart_2 | test_case_walmart_3 | task1_add_grocery_to_cart | "
        "task1_cart_checkout_cancel. "
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
        "Basket sample for TC7_2 or Walmart TC2/TC3 (A–H): items × units. "
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
    retailer: str, scenario: str, dry_run: bool, basket_size: str | None
) -> None:
    """Run a latency scenario against a retailer on the connected iPhone."""
    settings = load_settings()
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
    elif scenario == "test_case_7_2":
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

    if outcome.get("status") == "error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
