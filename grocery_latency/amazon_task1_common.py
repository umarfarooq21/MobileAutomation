"""Shared Amazon TASK 1 helpers: cold start → open master ASIN → add to cart → cart."""

from __future__ import annotations

import re
import time

from appium.webdriver.common.appiumby import AppiumBy

from grocery_latency.amazon_search import (
    SEARCH_TAP_STRATEGIES,
    SEARCH_TYPE_STRATEGIES,
    type_into_search,
)
from grocery_latency.ios_helpers import (
    element_textish,
    find_first,
    looks_like_login_wall,
    tap_dismissibles,
)
from grocery_latency.markers import MarkerLog

# Pre-built master list (stable ASINs). Prefer deep-link over fuzzy grocery search.
DEFAULT_MASTER_ASINS = (
    "B001W3T2E4",
    "B000NOGKN4",
    "B003AYKYIG",
    "B00HT7U55W",
    "B000P6G12U",
    "B000NSFQIU",
)

ADD_TO_CART_BUTTON = (
    (AppiumBy.ACCESSIBILITY_ID, "Add to cart"),
    (AppiumBy.ACCESSIBILITY_ID, "Add to Cart"),
    (AppiumBy.ACCESSIBILITY_ID, "Add to Basket"),
    (AppiumBy.ACCESSIBILITY_ID, "Add"),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(name == "Add to cart" OR label == "Add to cart" OR '
        'name == "Add to Cart" OR label == "Add to Cart" OR '
        'label CONTAINS[c] "Add to Basket" OR '
        'label CONTAINS[c] "Add to cart" OR name CONTAINS[c] "Add to cart" OR '
        'label == "Add" OR name == "Add")',
    ),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(name == "+" OR label == "+" OR label CONTAINS[c] "Increase quantity")',
    ),
)

ADDED_CONFIRM_NEEDLES = (
    "added to cart",
    "added to basket",
    "1 in cart",
    "cart 1",
    "go to cart",
    "view cart",
    "cart subtotal",
)

CART_TAB_STRATEGIES = (
    (AppiumBy.ACCESSIBILITY_ID, "cartTab"),
    (
        AppiumBy.IOS_PREDICATE,
        'name == "cartTab" OR label CONTAINS[c] "Cart"',
    ),
    (
        AppiumBy.IOS_PREDICATE,
        'type == "XCUIElementTypeButton" AND '
        '(label CONTAINS[c] "Cart" OR name CONTAINS[c] "Cart")',
    ),
    (AppiumBy.ACCESSIBILITY_ID, "Go to cart"),
    (AppiumBy.ACCESSIBILITY_ID, "View cart"),
)


def cart_badge_count(driver) -> int | None:
    try:
        els = driver.find_elements(
            AppiumBy.IOS_PREDICATE,
            'label CONTAINS[c] "Cart" OR name CONTAINS[c] "Cart"',
        )
    except Exception:  # noqa: BLE001
        return None
    for el in els:
        try:
            blob = element_textish(el)
            m = re.search(r"Cart\s*(\d+)", blob, re.I)
            if m:
                return int(m.group(1))
        except Exception:  # noqa: BLE001
            continue
    return None


def looks_added(driver, before_count: int | None) -> bool:
    after = cart_badge_count(driver)
    if before_count is not None and after is not None and after > before_count:
        return True
    try:
        src = (driver.page_source or "").lower()
    except Exception:  # noqa: BLE001
        return False
    return any(n in src for n in ADDED_CONFIRM_NEEDLES)


def cold_start_amazon(driver, bundle_id: str, markers: MarkerLog, outcome: dict) -> None:
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
            time.sleep(1.0)
            outcome["cold_start_terminated"] = True
        else:
            outcome["cold_start_terminated"] = False
        print(f"Activating {bundle_id} (cold start) …")
        driver.activate_app(bundle_id)
        time.sleep(2.0)
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


def dismiss_and_login_pause(driver, settings, markers: MarkerLog, outcome: dict) -> None:
    time.sleep(2.0)
    dismissed = tap_dismissibles(driver)
    outcome["dismissed"] = dismissed
    if dismissed:
        print(f"Dismissed: {dismissed}")

    if looks_like_login_wall(driver):
        pause = max(0, settings.login_pause_seconds)
        print(f"Login wall suspected — pausing {pause}s for human assist.")
        markers.mark("login_pause_start", seconds=pause)
        time.sleep(pause)
        markers.mark("login_pause_end")
        tap_dismissibles(driver)


def scroll_down(driver, percent: float = 0.55) -> None:
    try:
        driver.execute_script(
            "mobile: swipe",
            {"direction": "up", "percent": percent},
        )
    except Exception:  # noqa: BLE001
        try:
            size = driver.get_window_size()
            w, h = size["width"], size["height"]
            driver.execute_script(
                "mobile: dragFromToForDuration",
                {
                    "duration": 0.4,
                    "fromX": w // 2,
                    "fromY": int(h * 0.72),
                    "toX": w // 2,
                    "toY": int(h * 0.28),
                },
            )
        except Exception:  # noqa: BLE001
            pass


def _cart_add_urls(asin: str) -> tuple[str, ...]:
    """Web cart-add URLs — add without opening PDP when Amazon accepts the deep link."""
    # Correct query form is ASIN.1=<asin>&Quantity.1=1 (not ASIN.<asin>=1).
    # Prefer shopping.web scheme — plain https deep links are often ignored by Amazon iOS.
    return (
        f"com.amazon.mobile.shopping.web://www.amazon.com/gp/aws/cart/add.html"
        f"?ASIN.1={asin}&Quantity.1=1",
        f"com.amazon.mobile.shopping://www.amazon.com/gp/aws/cart/add.html"
        f"?ASIN.1={asin}&Quantity.1=1",
        f"https://www.amazon.com/gp/aws/cart/add.html?ASIN.1={asin}&Quantity.1=1",
        f"https://www.amazon.com/gp/cart/add.html?ASIN.1={asin}&Quantity.1=1",
    )


def _try_safari_cart_add(
    driver,
    bundle_id: str,
    asin: str,
    url: str,
    markers: MarkerLog,
) -> None:
    """Open cart-add URL in Safari (Amazon iOS often ignores native cart-add deep links)."""
    print(f"Safari cart-add fallback: {url}")
    markers.mark("asin_cart_add_safari", asin=asin, url=url)
    try:
        driver.execute_script(
            "mobile: launchApp",
            {"bundleId": "com.apple.mobilesafari"},
        )
    except Exception:  # noqa: BLE001
        driver.activate_app("com.apple.mobilesafari")
    time.sleep(1.5)
    # Prefer openUrl / get — whichever the session supports.
    opened = False
    for opener in (
        lambda: driver.execute_script("mobile: openUrl", {"url": url}),
        lambda: driver.get(url),
    ):
        try:
            opener()
            opened = True
            break
        except Exception as exc:  # noqa: BLE001
            print(f"Safari open attempt failed: {exc}")
    if not opened:
        # Last resort: type URL into Safari address bar.
        try:
            field, _, _ = find_first(
                driver,
                (
                    (AppiumBy.ACCESSIBILITY_ID, "TabBarItemTitle"),
                    (AppiumBy.ACCESSIBILITY_ID, "URL"),
                    (
                        AppiumBy.IOS_PREDICATE,
                        'type == "XCUIElementTypeTextField" OR '
                        'type == "XCUIElementTypeSearchField"',
                    ),
                ),
                timeout=8.0,
            )
            field.click()
            time.sleep(0.4)
            field.clear()
            field.send_keys(url + "\n")
        except Exception as exc:  # noqa: BLE001
            print(f"Safari address-bar typing failed: {exc}")
            raise
    time.sleep(4.0)
    tap_dismissibles(driver)
    # If Safari prompts to open Amazon, tap it.
    for by, value in (
        (AppiumBy.ACCESSIBILITY_ID, "Open"),
        (AppiumBy.ACCESSIBILITY_ID, "Open in the Amazon app"),
        (
            AppiumBy.IOS_PREDICATE,
            'type == "XCUIElementTypeButton" AND '
            '(label == "Open" OR label CONTAINS[c] "Amazon" OR '
            'label CONTAINS[c] "Open in")',
        ),
    ):
        try:
            for el in driver.find_elements(by, value)[:3]:
                if el.is_displayed():
                    el.click()
                    time.sleep(2.0)
                    break
        except Exception:  # noqa: BLE001
            continue
    # Return to Amazon app to read cart badge.
    try:
        driver.activate_app(bundle_id)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(2.0)
    tap_dismissibles(driver)


def try_add_asin_via_cart_url(
    driver,
    bundle_id: str,
    asin: str,
    markers: MarkerLog,
    outcome: dict,
) -> bool:
    """Try direct cart-add deep links (native, then Safari). Returns True on success."""
    before = cart_badge_count(driver)
    outcome["cart_count_before"] = before
    print(f"Cart count before cart-add URL: {before}")

    urls = _cart_add_urls(asin)
    # Pass 1: native deepLink into Amazon (fast when supported).
    for url in urls:
        try:
            print(f"Direct cart-add ASIN {asin}: {url}")
            driver.execute_script(
                "mobile: deepLink",
                {"url": url, "bundleId": bundle_id},
            )
            markers.mark("asin_cart_add_url", asin=asin, url=url)
            time.sleep(3.0)
            tap_dismissibles(driver)
            if _cart_add_confirmed(driver, before, asin):
                return _mark_cart_add_success(
                    driver, markers, outcome, before, url, method="cart_add_url"
                )
            print(f"Direct cart-add did not confirm for {asin} via {url}")
        except Exception as exc:  # noqa: BLE001
            print(f"Cart-add URL failed ({url}): {exc}")

    # Pass 2: Safari opens the https cart-add URL (works when native deep link is ignored).
    https_urls = [u for u in urls if u.startswith("https://")]
    for url in https_urls:
        try:
            _try_safari_cart_add(driver, bundle_id, asin, url, markers)
            if _cart_add_confirmed(driver, before, asin):
                return _mark_cart_add_success(
                    driver,
                    markers,
                    outcome,
                    before,
                    url,
                    method="cart_add_url_safari",
                )
            print(f"Safari cart-add did not confirm for {asin} via {url}")
        except Exception as exc:  # noqa: BLE001
            print(f"Safari cart-add failed ({url}): {exc}")
            try:
                driver.activate_app(bundle_id)
            except Exception:  # noqa: BLE001
                pass

    return False


def _cart_add_confirmed(driver, before: int | None, asin: str) -> bool:
    """Require badge increase, or Cart page evidence for this ASIN."""
    after = cart_badge_count(driver)
    if before is not None and after is not None and after > before:
        print(f"Cart-add confirmed by badge: {before} → {after}")
        return True

    # Open Cart and look for this ASIN / added chrome (handles already-in-cart).
    try:
        el, _, _ = find_first(driver, CART_TAB_STRATEGIES, timeout=8.0)
        el.click()
        time.sleep(2.5)
        tap_dismissibles(driver)
    except Exception as exc:  # noqa: BLE001
        print(f"Cart open during confirm failed: {exc}")

    after = cart_badge_count(driver)
    if before is not None and after is not None and after > before:
        print(f"Cart-add confirmed by badge after Cart open: {before} → {after}")
        return True

    try:
        src = (driver.page_source or "").lower()
    except Exception:  # noqa: BLE001
        return False

    if asin.lower() in src:
        print(f"Cart-add confirmed: ASIN {asin} visible on Cart page")
        return True

    strong = (
        "added to cart",
        "was added to",
        "proceed to checkout",
        "cart subtotal",
    )
    if any(n in src for n in strong) and (
        "shopping cart" in src or "your amazon cart" in src or "subtotal" in src
    ):
        # Cart page open but ASIN text absent — only accept if badge moved.
        print("Cart page open but ASIN not found and badge unchanged — not confirmed")
        return False
    return False


def _mark_cart_add_success(
    driver,
    markers: MarkerLog,
    outcome: dict,
    before: int | None,
    url: str,
    *,
    method: str,
) -> bool:
    after = cart_badge_count(driver)
    outcome["cart_count_after"] = after
    outcome["add_confirmed_in_ui"] = True
    outcome["item_added_to_cart"] = True
    outcome["asin_open_method"] = url
    markers.mark(
        "item_added_to_cart",
        method=method,
        confirmed=True,
        cart_before=before,
        cart_after=after,
    )
    print(f"Direct cart-add succeeded ({method})")
    return True


def try_serp_add_for_asin(driver, markers: MarkerLog, outcome: dict) -> bool:
    """Tap inline Add to cart on ASIN search results (skip full PDP scroll)."""
    before = cart_badge_count(driver)
    for by, value in ADD_TO_CART_BUTTON:
        try:
            els = driver.find_elements(by, value)
        except Exception:  # noqa: BLE001
            continue
        for el in els[:6]:
            try:
                if not el.is_displayed():
                    continue
                print(f"SERP add for ASIN: {by} = {value!r}")
                el.click()
                markers.mark("serp_add_tapped", by=by, value=value)
                time.sleep(2.0)
                tap_dismissibles(driver)
                if looks_added(driver, before):
                    outcome["cart_count_after"] = cart_badge_count(driver)
                    outcome["add_confirmed_in_ui"] = True
                    outcome["item_added_to_cart"] = True
                    markers.mark("item_added_to_cart", method="serp_add")
                    return True
            except Exception:  # noqa: BLE001
                continue
    return False


def open_product_by_asin(driver, bundle_id: str, asin: str, markers: MarkerLog) -> str:
    """Open a PDP directly via deep link. Returns method used."""
    urls = (
        f"https://www.amazon.com/dp/{asin}",
        f"https://www.amazon.com/gp/product/{asin}",
        f"com.amazon.mobile.shopping://www.amazon.com/dp/{asin}",
    )
    last_err = None
    for url in urls:
        try:
            print(f"Deep-link open ASIN {asin}: {url}")
            driver.execute_script(
                "mobile: deepLink",
                {"url": url, "bundleId": bundle_id},
            )
            markers.mark("asin_deeplink", asin=asin, url=url)
            time.sleep(3.5)
            tap_dismissibles(driver)
            return url
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"Deep-link failed ({url}): {exc}")

    # Last resort: exact ASIN search (still deterministic vs fuzzy grocery browse).
    print(f"Deep-link unavailable — falling back to ASIN search for {asin}")
    search_el, tap_by, tap_value = find_first(
        driver, SEARCH_TAP_STRATEGIES, timeout=20.0
    )
    search_el.click()
    time.sleep(0.8)
    type_el, type_by, type_value = find_first(
        driver, SEARCH_TYPE_STRATEGIES, timeout=15.0
    )
    type_into_search(driver, type_el, asin)
    markers.mark(
        "asin_search_fallback",
        asin=asin,
        search_tap=tap_value,
        search_type=type_value,
        by_tap=tap_by,
        by_type=type_by,
    )
    time.sleep(1.0)
    try:
        submit, _, _ = find_first(
            driver,
            (
                (AppiumBy.ACCESSIBILITY_ID, asin),
                (
                    AppiumBy.IOS_PREDICATE,
                    f'name == "{asin}" OR label == "{asin}" OR '
                    'label == "Search" OR name == "Search"',
                ),
            ),
            timeout=8.0,
        )
        submit.click()
    except TimeoutError:
        try:
            driver.execute_script("mobile: keys", {"keys": ["\n"]})
        except Exception:  # noqa: BLE001
            pass
    time.sleep(2.5)
    tap_dismissibles(driver)
    try:
        find_first(
            driver,
            (
                (AppiumBy.ACCESSIBILITY_ID, "Add to cart"),
                (AppiumBy.ACCESSIBILITY_ID, "Add to Cart"),
                (
                    AppiumBy.IOS_PREDICATE,
                    'label CONTAINS[c] "Add to cart" OR name CONTAINS[c] "Add to cart"',
                ),
            ),
            timeout=5.0,
        )
    except TimeoutError:
        try:
            results = driver.find_elements(
                AppiumBy.IOS_PREDICATE,
                'type == "XCUIElementTypeLink" OR type == "XCUIElementTypeCell" OR '
                'type == "XCUIElementTypeButton"',
            )
            for el in results[:12]:
                try:
                    if not el.is_displayed():
                        continue
                    blob = element_textish(el).lower()
                    if "search" in blob or "camera" in blob or "cart" in blob:
                        continue
                    el.click()
                    time.sleep(2.5)
                    tap_dismissibles(driver)
                    break
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
    if last_err:
        markers.mark("asin_deeplink_all_failed", asin=asin, error=str(last_err))
    return f"search:{asin}"


def _tap_add_to_cart_on_pdp(driver, markers: MarkerLog, outcome: dict) -> bool:
    before = cart_badge_count(driver)
    outcome["cart_count_before"] = before

    for scroll_i in range(6):
        for by, value in ADD_TO_CART_BUTTON:
            try:
                els = driver.find_elements(by, value)
            except Exception:  # noqa: BLE001
                continue
            for el in els[:10]:
                try:
                    if not el.is_displayed():
                        continue
                    print(f"Add to cart on PDP: {by} = {value!r}")
                    outcome.setdefault("selectors", {})["add_to_cart"] = {
                        "by": by,
                        "value": value,
                    }
                    el.click()
                    markers.mark("add_to_cart_tapped", by=by, value=value)
                    time.sleep(2.5)
                    tap_dismissibles(driver)
                    confirmed = looks_added(driver, before)
                    after = cart_badge_count(driver)
                    outcome["cart_count_after"] = after
                    outcome["add_confirmed_in_ui"] = confirmed
                    outcome["item_added_to_cart"] = True
                    markers.mark(
                        "item_added_to_cart",
                        confirmed=confirmed,
                        cart_before=before,
                        cart_after=after,
                    )
                    return True
                except Exception:  # noqa: BLE001
                    continue
        scroll_down(driver)
        markers.mark("pdp_scrolled_for_add", index=scroll_i)
        time.sleep(0.7)
    return False


def skip_add_if_cart_nonempty(
    driver,
    markers: MarkerLog,
    outcome: dict,
) -> dict | None:
    """If cart already has items, skip add step (fast path for checkout-only runs)."""
    count = cart_badge_count(driver)
    if count is None or count < 1:
        return None
    print(f"Cart already has {count} item(s) — skipping add-to-cart step.")
    markers.mark("add_skipped_existing_cart", cart_count=count)
    chosen = {
        "asin": None,
        "source": "existing_cart",
        "open_method": "skip",
        "confirmed": True,
        "cart_count": count,
    }
    outcome["chosen_item"] = chosen
    outcome["add_skipped"] = True
    outcome["item_added_to_cart"] = True
    return chosen


def add_master_asin_to_cart(
    driver,
    bundle_id: str,
    asins: list[str] | tuple[str, ...],
    markers: MarkerLog,
    outcome: dict,
    *,
    skip_if_cart_nonempty: bool = False,
    cart_add_url_only: bool = False,
) -> dict:
    """Add an item to cart using the fastest available path for each ASIN."""
    if not asins:
        raise AssertionError("Master ASIN list is empty")

    if skip_if_cart_nonempty:
        existing = skip_add_if_cart_nonempty(driver, markers, outcome)
        if existing is not None:
            return existing

    outcome["master_asins"] = list(asins)
    outcome["cart_add_url_only"] = cart_add_url_only
    errors: list[dict] = []

    for asin in asins:
        asin = (asin or "").strip().upper()
        if not asin:
            continue
        print(
            f"Step: add master ASIN {asin} to cart "
            f"({'cart-add URL only' if cart_add_url_only else 'fast paths first'}) …"
        )
        markers.mark("asin_attempt", asin=asin)
        try:
            # 1) Direct cart-add URL — no PDP (fastest when Amazon accepts it).
            if try_add_asin_via_cart_url(driver, bundle_id, asin, markers, outcome):
                chosen = {
                    "asin": asin,
                    "source": "master_asin_list",
                    "open_method": outcome.get("asin_open_method", "cart_add_url"),
                    "confirmed": True,
                    "add_path": "cart_add_url",
                }
                outcome["chosen_item"] = chosen
                print(f"Added master ASIN {asin} via direct cart-add URL")
                return chosen

            if cart_add_url_only:
                errors.append(
                    {"asin": asin, "error": "Direct cart-add URL did not confirm"}
                )
                markers.mark("asin_add_failed", asin=asin, reason="cart_add_url_only")
                continue

            # 2) Open PDP via deep link, then tap Add (or +) on product page.
            method = open_product_by_asin(driver, bundle_id, asin, markers)
            outcome["asin_open_method"] = method
            if _tap_add_to_cart_on_pdp(driver, markers, outcome):
                chosen = {
                    "asin": asin,
                    "source": "master_asin_list",
                    "open_method": method,
                    "confirmed": outcome.get("add_confirmed_in_ui"),
                    "add_path": "pdp",
                }
                outcome["chosen_item"] = chosen
                print(f"Added master ASIN {asin} to cart via PDP ({method})")
                return chosen

            # 3) Inline Add on search results if deep link landed on SERP.
            if try_serp_add_for_asin(driver, markers, outcome):
                chosen = {
                    "asin": asin,
                    "source": "master_asin_list",
                    "open_method": method,
                    "confirmed": outcome.get("add_confirmed_in_ui"),
                    "add_path": "serp",
                }
                outcome["chosen_item"] = chosen
                print(f"Added master ASIN {asin} via SERP inline add")
                return chosen

            errors.append({"asin": asin, "error": "Add to cart not found (all paths)"})
            markers.mark("asin_add_failed", asin=asin, reason="no_add_button")
        except Exception as exc:  # noqa: BLE001
            errors.append({"asin": asin, "error": str(exc)})
            markers.mark("asin_attempt_error", asin=asin, error=str(exc))
            print(f"ASIN {asin} failed: {exc}")

    outcome["asin_errors"] = errors
    raise AssertionError(
        f"Could not add any master-list ASIN to cart. Tried={list(asins)} errors={errors}"
    )


def open_cart_page(driver, markers: MarkerLog, outcome: dict) -> None:
    """Navigate to the Cart tab / cart page."""
    print("Step: open Cart page …")
    el, by, value = find_first(driver, CART_TAB_STRATEGIES, timeout=20.0)
    outcome.setdefault("selectors", {})["cart_tab"] = {"by": by, "value": value}
    print(f"Cart: {by} = {value!r}")
    el.click()
    markers.mark("cart_opened", by=by, value=value)
    time.sleep(2.5)
    tap_dismissibles(driver)
    outcome["cart_page_opened"] = True
