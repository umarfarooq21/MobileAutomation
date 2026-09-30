from __future__ import annotations

import time
from typing import Iterable, Sequence

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.support.ui import WebDriverWait


# Dismissible chrome that often blocks the Amazon home search field.
DISMISS_LABELS = (
    "Not Now",
    "Not now",
    "No Thanks",
    "No thanks",
    "Maybe Later",
    "Close",
    "Dismiss",
    "Got It",
    "Got it",
    "Continue",
    "Allow",
    "Allow While Using App",
    "Don’t Allow",
    "Don't Allow",
)


def wait_until(driver, predicate, timeout: float = 20.0, poll: float = 0.25):
    end = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < end:
        try:
            value = predicate()
            if value:
                return value
        except (NoSuchElementException, StaleElementReferenceException) as exc:
            last_exc = exc
        time.sleep(poll)
    if last_exc:
        raise TimeoutError(f"wait_until timed out after {timeout}s: {last_exc}") from last_exc
    raise TimeoutError(f"wait_until timed out after {timeout}s")


def find_first(driver, strategies: Sequence[tuple[str, str]], timeout: float = 15.0):
    """Try locator strategies in order until one finds a displayed element."""

    def _attempt():
        for by, value in strategies:
            try:
                el = driver.find_element(by, value)
                if el.is_displayed():
                    return el, by, value
            except NoSuchElementException:
                continue
        return None

    return wait_until(driver, _attempt, timeout=timeout)


def tap_dismissibles(driver, labels: Iterable[str] = DISMISS_LABELS, rounds: int = 2) -> list[str]:
    """Best-effort tap on common dismiss / cookie / permission buttons."""
    tapped: list[str] = []
    for _ in range(rounds):
        hit = False
        for label in labels:
            strategies = [
                (AppiumBy.ACCESSIBILITY_ID, label),
                (AppiumBy.IOS_PREDICATE, f'label == "{label}" OR name == "{label}"'),
                (
                    AppiumBy.IOS_CLASS_CHAIN,
                    f'**/XCUIElementTypeButton[`label == "{label}" OR name == "{label}"`]',
                ),
            ]
            for by, value in strategies:
                try:
                    el = driver.find_element(by, value)
                    if el.is_displayed():
                        el.click()
                        tapped.append(label)
                        hit = True
                        time.sleep(0.6)
                        break
                except NoSuchElementException:
                    continue
            if hit:
                break
        if not hit:
            break
    return tapped


def looks_like_login_wall(driver) -> bool:
    # Prefer cheap element probes — full page_source on Amazon iOS is very slow.
    probes = (
        (AppiumBy.ACCESSIBILITY_ID, "Sign in"),
        (AppiumBy.ACCESSIBILITY_ID, "Sign In"),
        (AppiumBy.ACCESSIBILITY_ID, "Create account"),
        (
            AppiumBy.IOS_PREDICATE,
            'label == "Sign in" OR label == "Sign In" OR '
            'label == "Create account" OR name == "Sign in"',
        ),
    )
    for by, value in probes:
        try:
            for el in driver.find_elements(by, value)[:2]:
                if el.is_displayed():
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


def element_textish(el) -> str:
    bits = []
    for attr in ("value", "text", "label", "name"):
        try:
            v = el.get_attribute(attr)
        except Exception:  # noqa: BLE001
            v = None
        if v:
            bits.append(str(v))
    return " | ".join(bits)


def dump_page_source(driver, path) -> None:
    path.write_text(driver.page_source or "", encoding="utf-8")
