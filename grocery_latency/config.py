from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from grocery_latency.amazon_task1_common import DEFAULT_MASTER_ASINS

RETAILERS = {
    "amazon": "AMAZON_BUNDLE_ID",
    "walmart": "WALMART_BUNDLE_ID",
    "instacart": "INSTACART_BUNDLE_ID",
}

SCENARIOS = (
    "open_only",
    "search_groceries",
    "test_case_1",
    "select_grocery_under_4",  # alias for test_case_1
    "test_case_2",
    "test_case_3",
    "test_case_4",
    "test_case_5",
    "test_case_6",
    "test_case_7",
    "test_case_7_2",
    "test_case_8",
    "test_case_9",
    "test_case_walmart_1",
    "test_case_walmart_2",
    "test_case_walmart_3",
    "task1_add_grocery_to_cart",
    "task1_cart_checkout_cancel",
)


@dataclass(frozen=True)
class Settings:
    device_udid: str
    ios_version: str
    device_name: str
    appium_url: str
    new_command_timeout: int
    wda_launch_timeout: int
    wda_startup_retries: int
    no_reset: bool
    xcode_org_id: str | None
    xcode_signing_id: str | None
    updated_wda_bundle_id: str | None
    artifacts_dir: Path
    login_pause_seconds: int
    assigned_quantity: int
    recording_time_limit_seconds: int
    skip_add_if_cart_nonempty: bool
    cart_add_url_only: bool
    amazon_asins: tuple[str, ...]
    amazon_bundle_id: str
    walmart_bundle_id: str
    instacart_bundle_id: str

    def bundle_id_for(self, retailer: str) -> str:
        key = RETAILERS[retailer]
        value = getattr(self, key.lower())
        if not value:
            raise ValueError(f"Missing bundle id env for retailer={retailer} ({key})")
        return value


def _bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_asins(raw: str | None) -> tuple[str, ...]:
    if not raw or not raw.strip():
        return tuple(DEFAULT_MASTER_ASINS)
    parts = [p.strip().upper() for p in re_split_asins(raw) if p.strip()]
    return tuple(parts) if parts else tuple(DEFAULT_MASTER_ASINS)


def re_split_asins(raw: str) -> list[str]:
    import re

    return re.split(r"[\s,;]+", raw.strip())


def load_settings(env_file: Path | None = None) -> Settings:
    root = Path.cwd()
    candidate = env_file or (root / ".env")
    if candidate.exists():
        load_dotenv(candidate)
    else:
        example = root / ".env.example"
        if example.exists():
            load_dotenv(example)

    # Prefer ACTIVE_DEVICE profile (iphone16 / iphone14); fall back to DEVICE_*.
    active = (os.getenv("ACTIVE_DEVICE") or "").strip().lower()
    profile_map = {
        "iphone16": ("IPHONE16", "iPhone16"),
        "iphone14": ("IPHONE14", "iPhone"),
        "16": ("IPHONE16", "iPhone16"),
        "14": ("IPHONE14", "iPhone"),
    }
    if active in profile_map:
        prefix, default_name = profile_map[active]
        udid = os.getenv(f"{prefix}_UDID", "").strip()
        ios = os.getenv(f"{prefix}_IOS_VERSION", "").strip()
        device_name = (
            os.getenv(f"{prefix}_DEVICE_NAME", "").strip()
            or default_name
        )
    else:
        udid = os.getenv("DEVICE_UDID", "").strip()
        ios = os.getenv("IOS_VERSION", "").strip()
        device_name = os.getenv("DEVICE_NAME", "iPhone").strip() or "iPhone"
    if not udid or not ios:
        raise SystemExit(
            "Set ACTIVE_DEVICE=iphone16|iphone14 (with IPHONE16_*/IPHONE14_* "
            "in .env) or DEVICE_UDID + IOS_VERSION (see .env.example)."
        )

    asin_file = (os.getenv("AMAZON_ASIN_LIST_FILE") or "").strip()
    if asin_file:
        path = Path(asin_file)
        if not path.is_absolute():
            path = root / path
        if path.exists():
            asin_raw = path.read_text(encoding="utf-8")
        else:
            asin_raw = os.getenv("AMAZON_ASINS", "")
    else:
        asin_raw = os.getenv("AMAZON_ASINS", "")

    return Settings(
        device_udid=udid,
        ios_version=ios,
        device_name=device_name,
        appium_url=os.getenv("APPIUM_URL", "http://127.0.0.1:4723").strip(),
        new_command_timeout=int(os.getenv("NEW_COMMAND_TIMEOUT", "300")),
        wda_launch_timeout=int(os.getenv("WDA_LAUNCH_TIMEOUT", "120000")),
        wda_startup_retries=int(os.getenv("WDA_STARTUP_RETRIES", "4")),
        no_reset=_bool(os.getenv("NO_RESET"), True),
        xcode_org_id=(os.getenv("XCODE_ORG_ID") or "").strip() or None,
        xcode_signing_id=(os.getenv("XCODE_SIGNING_ID") or "").strip() or None,
        updated_wda_bundle_id=(os.getenv("UPDATED_WDA_BUNDLE_ID") or "").strip() or None,
        artifacts_dir=Path(os.getenv("ARTIFACTS_DIR", "artifacts")),
        login_pause_seconds=int(os.getenv("LOGIN_PAUSE_SECONDS", "90")),
        assigned_quantity=max(1, int(os.getenv("ASSIGNED_QUANTITY", "2"))),
        recording_time_limit_seconds=max(
            60, int(os.getenv("RECORDING_TIME_LIMIT_SECONDS", "600"))
        ),
        skip_add_if_cart_nonempty=_bool(
            os.getenv("SKIP_ADD_IF_CART_NONEMPTY"), False
        ),
        cart_add_url_only=_bool(os.getenv("AMAZON_CART_ADD_URL_ONLY"), False),
        amazon_asins=_parse_asins(asin_raw),
        amazon_bundle_id=os.getenv("AMAZON_BUNDLE_ID", "com.amazon.Amazon").strip(),
        walmart_bundle_id=os.getenv("WALMART_BUNDLE_ID", "com.walmart.electronics").strip(),
        instacart_bundle_id=os.getenv("INSTACART_BUNDLE_ID", "com.instacart.Instacart").strip(),
    )
