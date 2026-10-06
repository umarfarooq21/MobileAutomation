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
python -m grocery_latency run --retailer amazon --scenario test_case_3 --dry-run
python -m grocery_latency run --retailer amazon --scenario test_case_3
python -m grocery_latency run --retailer amazon --scenario test_case_4 --dry-run
python -m grocery_latency run --retailer amazon --scenario test_case_4
python -m grocery_latency run --retailer amazon --scenario test_case_5 --dry-run
python -m grocery_latency run --retailer amazon --scenario test_case_5
python -m grocery_latency run --retailer amazon --scenario test_case_6 --dry-run
python -m grocery_latency run --retailer amazon --scenario test_case_6
python -m grocery_latency run --retailer amazon --scenario test_case_7 --dry-run
python -m grocery_latency run --retailer amazon --scenario test_case_7
python -m grocery_latency run --retailer amazon --scenario test_case_7_2 --dry-run
python -m grocery_latency run --retailer amazon --scenario test_case_7_2 --basket-size A
python -m grocery_latency run --retailer amazon --scenario test_case_7_2 --basket-size E
python -m grocery_latency run --retailer amazon --scenario test_case_8 --dry-run
python -m grocery_latency run --retailer amazon --scenario test_case_8
python -m grocery_latency run --retailer amazon --scenario test_case_9 --dry-run
python -m grocery_latency run --retailer amazon --scenario test_case_9
python -m grocery_latency run --retailer walmart --scenario test_case_walmart_1 --dry-run
python -m grocery_latency run --retailer walmart --scenario test_case_walmart_1
python -m grocery_latency run --retailer walmart --scenario test_case_walmart_2 --dry-run
python -m grocery_latency run --retailer walmart --scenario test_case_walmart_2 --basket-size A
python -m grocery_latency run --retailer walmart --scenario test_case_walmart_2 --basket-size B
python -m grocery_latency run --retailer walmart --scenario test_case_walmart_2 --basket-size C
python -m grocery_latency run --retailer walmart --scenario test_case_walmart_2 --basket-size D
python -m grocery_latency run --retailer walmart --scenario test_case_walmart_2 --basket-size E
python -m grocery_latency run --retailer walmart --scenario test_case_walmart_2 --basket-size H
python -m grocery_latency run --retailer amazon --scenario task1_add_grocery_to_cart --dry-run
python -m grocery_latency run --retailer amazon --scenario task1_add_grocery_to_cart
python -m grocery_latency run --retailer amazon --scenario task1_cart_checkout_cancel --dry-run
python -m grocery_latency run --retailer amazon --scenario task1_cart_checkout_cancel
```

**Test Case 1** (`test_case_1`): Groceries → Shop groceries → tap **+** → Cart → **start recording** → Proceed to checkout → 2nd address radio → **Deliver to this address** → stop at **1 minute**.  
(`select_grocery_under_4` is kept as an alias for the same flow.)

**Test Case 2** (`test_case_2` / TASK 2): Groceries → add **5** items → Cart → **start recording** → verify. No place-order.

**Test Case 3** (`test_case_3` / TASK 3): Grocery → Household → add **5** items → Cart → record → payment 2nd card → address 2nd → Deliver. No place-order.

**Test Case 4** (`test_case_4` / TASK 4): Grocery search **under 5 dollar** → real +×5 → Cart → record → Proceed → change delivery-slot radio → **Change payment method** → select alternate card → Continue → stop. No place-order.

**Test Case 5** (`test_case_5` / TASK 5): Grocery → **Produce** → +×5 → Cart → record → Proceed → No thanks → Change payment (2nd card) → Continue → Change delivery address (2nd) → Deliver. Fallback search **vegetables and fruits** if Produce is empty. No place-order.

**Test Case 6** (`test_case_6` / TASK 6): Same as TC5, plus on Place Your Order tap **See more delivery slots** and select the **last** slot, then payment + address edits. No place-order.

**Test Case 7** (`test_case_7` / TASK 7): Same as TC6, plus after Cart recording starts increase the **1st product** quantity by tapping **+** three times, then See more slots → last slot → payment + address. No place-order.

**Test Case 7_2** (`test_case_7_2`): Scaled baskets **A–H** (items × units). Adds on Produce listing with rapid qty taps to minimize add time, then same TC6 checkout edits. Select with `--basket-size A|B|…|H` or `BASKET_SIZE`. Matrix: A 1×1, B 10×1, C 1×25, D 25×1, E 5×10, F 50×1, G 50×5, H 10×10. No place-order.

**Test Case 8** (`test_case_8` / TASK 8): Same as TC7, then on the final Place Your Order screen tap **Place your order**; stop recording only after order success confirmation.

**Test Case 9** (`test_case_9` / TASK 9): Same as TC8 place-order, then Back (Authorize Payment if shown) → Home → Menu → **Customer Service** → order card → **Track package** (twice) → **Cancel items** (all, reason **Order Created by Mistake**) → **View your orders**; stop recording after cancel.

**Test Case Walmart 1** (`test_case_walmart_1`): Walmart → **My Items** → **Fresh Produce** → blue **+ Add** ×5 → Cart → **start recording** → **Change** (delivery) → **Change** (address, 2nd) → day/slot → **Reserve** → 1st item qty **+4** → **Continue to checkout** → **Continue** on Missing anything? → stop on **Checkout**. No place-order.

**Test Case Walmart 2** (`test_case_walmart_2`): Same checkout path as Walmart TC1, but basket size follows study samples **A–H** (select with `--basket-size` / `BASKET_SIZE`): **A** 1×1, **B** 10×1, **C** 1×25, **D** 25×1, **E** 5×10, **F** 50×1, **G** 50×5, **H** 10×10. My Items uses **+ Add once** (qty 1); unit scaling is done on **Cart** (qty + on every line). Day/slot toggles last↔2nd-last. No place-order.

| Scenario | What it does |
| --- | --- |
| `open_only` | Launch retailer app only (smoke / login assist pause) |
| `search_groceries` | Amazon: open app → tap search → type `Groceries` (no order) |
| `test_case_1` | **Test Case 1**: Groceries → + → Cart → record → checkout → 2nd address → Deliver → Place Order qty **+3** → stop |
| `test_case_2` | **Test Case 2 / TASK 2**: Groceries → add **5** under-**$5** items → Cart → **record** → verify |
| `test_case_3` | **Test Case 3 / TASK 3**: Grocery → Household → +×5 → cart record → payment + address edits |
| `test_case_4` | **Test Case 4 / TASK 4**: Grocery search → +×5 → cart record → delivery slot + Change payment method → stop |
| `test_case_5` | **Test Case 5 / TASK 5**: Grocery → Produce → +×5 → cart record → payment + address edits |
| `test_case_6` | **Test Case 6 / TASK 6**: TC5 + See more delivery slots → last slot → payment + address |
| `test_case_7` | **Test Case 7 / TASK 7**: TC6 + Cart 1st item qty **+3** after recording starts |
| `test_case_7_2` | **Test Case 7_2**: Basket samples **A–H** (scaled items×units) + TC6 checkout |
| `test_case_8` | **Test Case 8 / TASK 8**: TC7 + **Place your order**; stop recording after success |
| `test_case_9` | **Test Case 9 / TASK 9**: TC8 + CS **Track package** → **Cancel all items** → View orders; stop after |
| `test_case_walmart_1` | **Walmart TC1**: My Items → Fresh Produce → +Add×5 → cart record → Change delivery/address → Reserve → qty+ → checkout Continue → stop |
| `test_case_walmart_2` | **Walmart TC2**: baskets **A–H** (scaled items×units) + same checkout path as TC1 |
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
