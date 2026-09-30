# Grocery Latency (iOS Appium)

Python harness for cancel/test grocery latency runs on a real iPhone via Appium 2 + XCUITest.

## Setup

```bash
cd ~/Desktop/AgentApps/WebAgent-grocery-latency
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set DEVICE_UDID + IOS_VERSION
```

Host tooling (once):

```bash
export PATH="$HOME/.local/bin:$PATH"   # appium + ffmpeg symlink used on this Mac
appium driver install xcuitest@7.35.1  # if needed
appium --address 127.0.0.1 --port 4723
```

## Run

```bash
python -m grocery_latency --help
python -m grocery_latency run --retailer amazon --scenario open_only --dry-run
python -m grocery_latency run --retailer amazon --scenario open_only
python -m grocery_latency run --retailer amazon --scenario search_groceries --dry-run
python -m grocery_latency run --retailer amazon --scenario search_groceries
python -m grocery_latency run --retailer amazon --scenario test_case_1 --dry-run
python -m grocery_latency run --retailer amazon --scenario test_case_1
python -m grocery_latency run --retailer amazon --scenario test_case_2 --dry-run
python -m grocery_latency run --retailer amazon --scenario test_case_2
python -m grocery_latency run --retailer amazon --scenario task1_add_grocery_to_cart --dry-run
python -m grocery_latency run --retailer amazon --scenario task1_add_grocery_to_cart
python -m grocery_latency run --retailer amazon --scenario task1_cart_checkout_cancel --dry-run
python -m grocery_latency run --retailer amazon --scenario task1_cart_checkout_cancel
```

**Test Case 1** (`test_case_1`): Groceries → Shop groceries → tap **+** → Cart → **start recording** → Proceed to checkout → 2nd address radio → **Deliver to this address** → stop at **1 minute**.  
(`select_grocery_under_4` is kept as an alias for the same flow.)

**Test Case 2** (`test_case_2` / TASK 2): Groceries → Shop groceries → add **5 different** items **under $5** → Cart → verify all 5 added. No place-order / no recording.

| Scenario | What it does |
| --- | --- |
| `open_only` | Launch retailer app only (smoke / login assist pause) |
| `search_groceries` | Amazon: open app → tap search → type `Groceries` (no order) |
| `test_case_1` | **Test Case 1**: Groceries → + → Cart → record → checkout → 2nd address → Deliver → Place Order qty **+3** → stop |
| `test_case_2` | **Test Case 2 / TASK 2**: Groceries → add **5** under-**$5** items → Cart verify |
| `task1_add_grocery_to_cart` | Amazon TASK 1: cold start → open master ASIN PDP → add to cart |
| `task1_cart_checkout_cancel` | TASK 1 (ASIN) + Cart → **start recording** → checkout → place order → Path A cancel |

Master ASINs (override with `AMAZON_ASINS` or `AMAZON_ASIN_LIST_FILE`): `B001W3T2E4`, `B000NOGKN4`, `B003AYKYIG`, `B00HT7U55W`, `B000P6G12U`, `B000NSFQIU`.

Add-to-cart uses the fastest path per ASIN: **direct cart-add URL** → **PDP deep link + Add/+** → **SERP inline Add**. Set `SKIP_ADD_IF_CART_NONEMPTY=true` to skip add when the cart already has items (useful for checkout-only reruns).

Env knobs for checkout recording: `ASSIGNED_QUANTITY` (default `2`), `RECORDING_TIME_LIMIT_SECONDS` (default `600`). Prefer `NEW_COMMAND_TIMEOUT=600+` for the long recorded run. iOS screen-recording length may be capped by the device/Appium.

## Notes

- Recreated locally when Origin clone was unavailable without `origin auth login`.
- Amazon on the test iPhone: `com.amazon.Amazon` (set in `.env`).
- Real-device WDA on this Mac needed `XCODE_ORG_ID` + `UPDATED_WDA_BUNDLE_ID` in `.env`.
- Keep Appium up: `export PATH="$HOME/.local/bin:$PATH" && appium --address 127.0.0.1 --port 4723`
