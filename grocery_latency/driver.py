from __future__ import annotations

from appium import webdriver
from appium.options.ios import XCUITestOptions

from grocery_latency.config import Settings


def build_options(settings: Settings, bundle_id: str) -> XCUITestOptions:
    opts = XCUITestOptions()
    opts.platform_name = "iOS"
    opts.automation_name = "XCUITest"
    opts.device_name = settings.device_name
    opts.udid = settings.device_udid
    opts.platform_version = settings.ios_version
    opts.bundle_id = bundle_id
    opts.no_reset = settings.no_reset
    opts.new_command_timeout = settings.new_command_timeout
    opts.wda_launch_timeout = settings.wda_launch_timeout
    opts.set_capability("appium:wdaStartupRetries", settings.wda_startup_retries)
    opts.set_capability("appium:showXcodeLog", True)
    if settings.xcode_org_id:
        opts.xcode_org_id = settings.xcode_org_id
    if settings.xcode_signing_id:
        opts.xcode_signing_id = settings.xcode_signing_id
    if settings.updated_wda_bundle_id:
        opts.updated_wda_bundle_id = settings.updated_wda_bundle_id
    return opts


def create_driver(settings: Settings, bundle_id: str):
    opts = build_options(settings, bundle_id)
    return webdriver.Remote(settings.appium_url, options=opts)


def describe_session(settings: Settings, retailer: str, bundle_id: str) -> dict:
    return {
        "retailer": retailer,
        "bundle_id": bundle_id,
        "device_udid": settings.device_udid,
        "ios_version": settings.ios_version,
        "device_name": settings.device_name,
        "appium_url": settings.appium_url,
        "no_reset": settings.no_reset,
        "new_command_timeout": settings.new_command_timeout,
        "wda_launch_timeout": settings.wda_launch_timeout,
        "wda_startup_retries": settings.wda_startup_retries,
        "xcode_org_id_set": bool(settings.xcode_org_id),
        "xcode_signing_id": settings.xcode_signing_id,
        "updated_wda_bundle_id_set": bool(settings.updated_wda_bundle_id),
    }
