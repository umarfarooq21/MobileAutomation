"""Shared Amazon iOS search locators and typing helpers (live-inspected)."""

from __future__ import annotations

import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import InvalidElementStateException

from grocery_latency.ios_helpers import element_textish

# Amazon iOS (test iPhone): home search → focused TextView name=searchTextView.
SEARCH_TAP_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "searchTextView"),
    (AppiumBy.ACCESSIBILITY_ID, "search text field"),
    (AppiumBy.ACCESSIBILITY_ID, "Search Amazon"),
    (AppiumBy.ACCESSIBILITY_ID, "Search"),
    (
        AppiumBy.IOS_PREDICATE,
        'name == "searchTextView" OR type == "XCUIElementTypeSearchField" OR '
        '(type == "XCUIElementTypeTextField" AND '
        '(name CONTAINS[c] "search" OR label CONTAINS[c] "search" OR value CONTAINS[c] "search")) OR '
        '(type == "XCUIElementTypeTextView" AND name CONTAINS[c] "search")',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeTextView[`name == "searchTextView"`]',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        "**/XCUIElementTypeSearchField",
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeTextField[`name CONTAINS[c] "search" OR label CONTAINS[c] "search"`]',
    ),
    (
        AppiumBy.IOS_PREDICATE,
        'label CONTAINS[c] "Search Amazon" OR name CONTAINS[c] "Search Amazon" '
        'OR value CONTAINS[c] "Search Amazon"',
    ),
)

SEARCH_TYPE_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "searchTextView"),
    (
        AppiumBy.IOS_PREDICATE,
        'name == "searchTextView"',
    ),
    (
        AppiumBy.IOS_CLASS_CHAIN,
        '**/XCUIElementTypeTextView[`name == "searchTextView"`]',
    ),
    (AppiumBy.ACCESSIBILITY_ID, "search text field"),
    (AppiumBy.CLASS_NAME, "XCUIElementTypeSearchField"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeSearchField" OR '
        '(type == "XCUIElementTypeTextView" AND name CONTAINS[c] "search")',
    ),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeTextField" AND '
        '(name CONTAINS[c] "search" OR label CONTAINS[c] "search")',
    ),
)


def type_into_search(driver, el, query: str) -> None:
    try:
        el.click()
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.3)
    try:
        el.clear()
    except Exception:  # noqa: BLE001 — some search fields reject clear
        pass
    try:
        el.set_value(query)
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        el.send_keys(query)
        return
    except InvalidElementStateException:
        pass
    try:
        driver.execute_script("mobile: type", {"text": query})
        return
    except Exception:  # noqa: BLE001
        pass
    el.send_keys(query)


def field_contains_query(driver, query: str) -> tuple[bool, str]:
    q = query.lower()
    strategies = SEARCH_TYPE_STRATEGIES + (
        (
            AppiumBy.IOS_PREDICATE,
            f'value CONTAINS[c] "{query}" OR label CONTAINS[c] "{query}" OR name == "{query.lower()}"',
        ),
        (
            AppiumBy.IOS_CLASS_CHAIN,
            f'**/XCUIElementTypeButton[`name == "{query.lower()}" OR label == "{query.lower()}"`]',
        ),
    )
    for by, value in strategies:
        try:
            els = driver.find_elements(by, value)
        except Exception:  # noqa: BLE001
            continue
        for el in els:
            try:
                blob = element_textish(el).lower()
                if q in blob:
                    return True, blob
            except Exception:  # noqa: BLE001
                continue
    try:
        src = (driver.page_source or "").lower()
        if q in src:
            return True, "page_source_contains_query"
    except Exception:  # noqa: BLE001
        pass
    return False, ""
