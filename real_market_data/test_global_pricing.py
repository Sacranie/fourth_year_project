#!/usr/bin/env python3
"""
Robust Global Pricing LP test script (fixed).

- Loads sell orders from NESO API for a specific auction
- Groups fragments into multi-product orders (uses group_multi_product_orders)
- Runs the GlobalPricingLP
- Compares LP prices vs API published clearing prices
- Computes procurement costs using NESO rounding (round_price_up_to_cent)
- Verifies surplus constraints using small FEAS_TOL and reports matches using PRACTICAL_TOL
- Reports counts of tight baskets/loops (surplus ≈ 0)
"""

from typing import Dict, List, Tuple, Set
from collections import defaultdict
from decimal import Decimal, getcontext
import json
import urllib.request
import urllib.parse
import logging

# Project imports (adjust module paths if needed)
from eac.models import SellOrder
from eac.PricingLP import GlobalPricingLP, group_multi_product_orders, ROUNDING_TOL_DECIMAL
from eac.solver import PulpSolverBackend
from eac.rounding import round_price_up_to_cent

getcontext().prec = 28
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Tolerances
FEAS_TOL = ROUNDING_TOL_DECIMAL       # numerical feasibility tolerance (~1e-6)
PRACTICAL_TOL = Decimal("0.01")       # 1 penny tolerance for price-match reporting

# NESO API endpoints (adjust if needed)
SELL_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=13b511df-d6ec-4143-afb1-0ecc6fd19810"
BUY_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=1cf68f59-8eb8-4f1d-bccf-11b5a47b24e5"


# -----------------------
# Helpers: robust parsing
# -----------------------
def safe_int(x, default=0):
    try:
        if x in (None, "", "null"):
            return default
        return int(x)
    except Exception:
        try:
            return int(float(x))
        except Exception:
            return default


def safe_float(x, default=0.0):
    try:
        if x in (None, "", "null"):
            return default
        return float(x)
    except Exception:
        return default


# --------------------------------
# Load & preprocess API data
# --------------------------------
def load_orders_for_auction(base_url: str, auction_id: int, limit: int) -> List[Dict]:
    """Load all orders for a specific Auction ID using the NESO datastore_search API."""
    filters = {"auctionID": auction_id}
    filters_json = json.dumps(filters)
    url = f"{base_url}&limit={limit}&filters={urllib.parse.quote(filters_json)}"
    try:
        with urllib.request.urlopen(url) as response:
            data = json.load(response)
    except Exception as e:
        logger.error("Error fetching data from API: %s", e)
        return []
    return data.get("result", {}).get("records", [])


def process_auction_data(sell_url: str, buy_url: str, auction_id: int, limit: int):
    """
    Load sell orders and build structured objects for pricing LP.

    Returns dict with:
      - sell_orders: list[SellOrder]
      - x_s_observed: dict[orderID] -> acceptance ratio
      - expected_prices: dict[(product, window)] -> clearing price (float)
      - basket_to_loop: dict[basketID] -> loop_group_id (normalized)
      - products: list of product names
    """
    sell_records = load_orders_for_auction(sell_url, auction_id=auction_id, limit=limit)
    all_sell_orders: List[SellOrder] = []
    x_s_observed: Dict[int, float] = {}
    expected_prices: Dict[Tuple[str, Tuple[str, str]], float] = {}
    basket_to_loop: Dict[int, object] = {}
    products = set()

    logger.info("Processing %d sell order rows from API...", len(sell_records))

    rejected_orders = 0
    for row in sell_records:
        status = str(row.get("status", "")).strip().upper()
        if status == "REJECTED":
            rejected_orders += 1
            continue

        delivery_start = str(row.get("deliveryStart", "")) or ""
        delivery_end = str(row.get("deliveryEnd", "")) or ""
        window = (delivery_start, delivery_end)

        # Robust parsing
        order_id = safe_int(row.get("orderID", 0))
        acceptance_ratio = safe_float(row.get("acceptanceRatio", 0.0) or 0.0)
        quantity = safe_float(row.get("quantity", 0.0))
        price_limit = safe_float(row.get("priceLimit", row.get("price", 0.0)))

        # Build SellOrder (fields used by grouping/pricing)
        sell_order = SellOrder(
            auctionID=safe_int(row.get("auctionID", 0)),
            registeredAuctionParticipant=str(row.get("registeredAuctionParticipant", "")),
            auctionUnit=str(row.get("auctionUnit", "")),
            basketID=safe_int(row.get("basketID", 0)),
            service=str(row.get("service", "")),
            deliveryStart=delivery_start,
            deliveryEnd=delivery_end,
            orderID=order_id,
            orderType=str(row.get("orderType", "parent")).lower(),
            auctionProduct=str(row.get("auctionProduct", "")),
            quantity=float(quantity),
            price=float(price_limit),
            orderEntryTime=str(row.get("orderEntryTime", "")),
            product_id=str(row.get("productID", "")),
            status=status,
            min_acceptance_ratio=0.0
        )

        all_sell_orders.append(sell_order)
        x_s_observed[order_id] = acceptance_ratio

        product = row.get("auctionProduct")
        clearing_price_raw = row.get("clearingPrice")
        if product:
            products.add(product)
            # robust convert clearing price
            if clearing_price_raw not in (None, "", "null"):
                try:
                    clearing_price = float(clearing_price_raw)
                    expected_prices[(product, window)] = clearing_price
                except Exception:
                    logger.warning("Bad clearingPrice for order %s: %r", order_id, clearing_price_raw)

        # Track loop relation: treat loopedBasketID as a loop group identifier (may not be an int)
        basket_id = safe_int(row.get("basketID", 0))
        looped_basket_id_raw = row.get("loopedBasketID", None)
        if looped_basket_id_raw in (None, "", "null"):
            looped_basket_id = None
        else:
            # try int, else keep raw string/group id
            try:
                looped_basket_id = safe_int(looped_basket_id_raw)
            except Exception:
                looped_basket_id = looped_basket_id_raw

        if looped_basket_id is not None:
            basket_to_loop[basket_id] = looped_basket_id

    if rejected_orders:
        logger.info("Skipped %d rejected sell orders", rejected_orders)

    return {
        "sell_orders": all_sell_orders,
        "x_s_observed": x_s_observed,
        "expected_prices": expected_prices,
        "basket_to_loop": basket_to_loop,
        "products": list(sorted(products)),
    }


# ---------------------------------------
# Procurement cost using NESO rounding
# ---------------------------------------
def compute_procurement_cost(grouped_orders: List, price_map: Dict[Tuple[str, Tuple[str, str]], float]) -> Tuple[Decimal, Set[Tuple[str, Tuple[str, str]]]]:
    """
    Calculate procurement cost using NESO rounding (round_price_up_to_cent).
    Returns (total_cost, missing_price_keys)
    """
    total = Decimal("0")
    missing: Set[Tuple[str, Tuple[str, str]]] = set()

    # Pre-round to pence (NESO rounding function)
    rounded_price_map: Dict[Tuple[str, Tuple[str, str]], Decimal] = {}
    for k, v in price_map.items():
        try:
            rounded = round_price_up_to_cent(float(v))
            rounded_price_map[k] = Decimal(str(rounded))
        except Exception:
            rounded_price_map[k] = None

    for order in grouped_orders:
        if not getattr(order, "is_accepted", False):
            continue
        acceptance_dec = Decimal(str(order.acceptance))
        for fragment in order.fragments:
            key = (fragment.auctionProduct, (fragment.deliveryStart, fragment.deliveryEnd))
            price_dec = rounded_price_map.get(key)
            if price_dec is None:
                missing.add(key)
                continue
            qty_dec = Decimal(str(fragment.quantity))
            total += price_dec * qty_dec * acceptance_dec

    return total, missing


# ---------------------------------------
# Surplus diagnostics & tight constraint counts
# ---------------------------------------
def analyze_api_clearing(data: Dict) -> None:
    """
    Check API published prices satisfy surplus constraints (grouped accepted orders).
    """
    grouped_orders = group_multi_product_orders(data["sell_orders"], data["x_s_observed"])
    price_map = {k: Decimal(str(v)) for k, v in data["expected_prices"].items()}

    basket_surplus = defaultdict(lambda: Decimal("0"))
    loop_surplus = defaultdict(lambda: Decimal("0"))
    child_surpluses = []
    parent_surpluses = []
    missing_price_keys: Set[Tuple[str, Tuple[str, str]]] = set()
    unresolved_orders = []

    for order in grouped_orders:
        if not order.is_accepted:
            continue

        acceptance_dec = Decimal(str(order.acceptance))
        price_limit_dec = Decimal(str(order.price_limit))
        order_surplus = Decimal("0")
        missing_price = False

        for fragment in order.fragments:
            window = (fragment.deliveryStart, fragment.deliveryEnd)
            price_key = (fragment.auctionProduct, window)
            price_dec = price_map.get(price_key)
            if price_dec is None:
                missing_price = True
                missing_price_keys.add(price_key)
                continue
            qty_dec = Decimal(str(fragment.quantity))
            order_surplus += qty_dec * acceptance_dec * (price_dec - price_limit_dec)

        if missing_price:
            unresolved_orders.append(order)
            continue

        if order.order_type == "child":
            child_surpluses.append((order, order_surplus))
        else:
            parent_surpluses.append((order, order_surplus))

        basket_surplus[order.basket_id] += order_surplus
        loop_id = data["basket_to_loop"].get(order.basket_id)
        if loop_id is not None:
            loop_surplus[loop_id] += order_surplus

    child_negative = [(o, s) for o, s in child_surpluses if s < -FEAS_TOL]
    basket_negative = [(b, s) for b, s in basket_surplus.items() if data["basket_to_loop"].get(b) is None and s < -FEAS_TOL]
    loop_negative = [(l, s) for l, s in loop_surplus.items() if s < -FEAS_TOL]

    accepted_orders = len(child_surpluses) + len(parent_surpluses)

    print("\n" + "="*100)
    print("API CLEARING PRICES - SURPLUS CONSTRAINT ANALYSIS")
    print("="*100)
    print(f"Accepted multi-product groups: {accepted_orders}")
    if missing_price_keys:
        print(f"Missing prices for {len(missing_price_keys)} product-window pairs (skipped {len(unresolved_orders)} orders)")

    print(f"\nChild orders evaluated: {len(child_surpluses)}")
    print(f"Child orders with negative surplus: {len(child_negative)} {'❌' if child_negative else '✓'}")
    if child_negative:
        print("  First 5 child violations:")
        for order, surplus in child_negative[:5]:
            print(f"    Order {order.canonical_order_id} (basket {order.basket_id}): £{float(surplus):.6f}")

    non_loop_count = sum(1 for b in basket_surplus if data["basket_to_loop"].get(b) is None)
    print(f"\nNon-loop baskets evaluated: {non_loop_count}")
    print(f"Non-loop baskets with negative surplus: {len(basket_negative)} {'❌' if basket_negative else '✓'}")
    if basket_negative:
        print("  First 5 basket violations:")
        for basket_id, surplus in basket_negative[:5]:
            print(f"    Basket {basket_id}: £{float(surplus):.6f}")

    print(f"\nLoop families evaluated: {len(loop_surplus)}")
    print(f"Loop families with negative surplus: {len(loop_negative)} {'❌' if loop_negative else '✓'}")
    if loop_negative:
        print("  First 5 loop violations:")
        for loop_id, surplus in loop_negative[:5]:
            print(f"    Loop {loop_id}: £{float(surplus):.6f}")

    print("="*100)


def count_tight_constraints(grouped_orders: List, price_map: Dict[Tuple[str, Tuple[str, str]], float], basket_to_loop: Dict) -> Tuple[int, int]:
    """
    Count baskets and loop families where surplus is within FEAS_TOL of zero.
    Uses rounded prices (NESO rounding) for consistency with procurement calculations.
    """
    # Pre-round price_map
    rounded_price_map = {}
    for k, v in price_map.items():
        try:
            rounded_price_map[k] = Decimal(str(round_price_up_to_cent(float(v))))
        except Exception:
            rounded_price_map[k] = None

    basket_surplus = defaultdict(lambda: Decimal("0"))
    family_surplus = defaultdict(lambda: Decimal("0"))

    for order in grouped_orders:
        if not getattr(order, "is_accepted", False):
            continue
        acceptance_dec = Decimal(str(order.acceptance))
        price_limit_dec = Decimal(str(order.price_limit))
        order_surplus = Decimal("0")
        for frag in order.fragments:
            key = (frag.auctionProduct, (frag.deliveryStart, frag.deliveryEnd))
            price_dec = rounded_price_map.get(key)
            if price_dec is None:
                # treat missing price as unknown — don't penalize here
                order_surplus = None
                break
            qty_dec = Decimal(str(frag.quantity))
            order_surplus += qty_dec * acceptance_dec * (price_dec - price_limit_dec)
        if order_surplus is None:
            continue
        basket_surplus[order.basket_id] += order_surplus
        loop_id = basket_to_loop.get(order.basket_id)
        if loop_id is not None:
            family_surplus[loop_id] += order_surplus

    tight_baskets = sum(1 for s in basket_surplus.values() if abs(s) <= FEAS_TOL)
    tight_loops = sum(1 for s in family_surplus.values() if abs(s) <= FEAS_TOL)
    return tight_baskets, tight_loops


# ---------------------------------------
# Price comparison & mismatch investigation
# ---------------------------------------
def print_price_comparison(computed_prices: Dict, expected_prices: Dict):
    """
    Compare LP vs API prices per window & product, using PRACTICAL_TOL for 'match'.
    Returns (matches, total_compared, mismatches_list).
    """
    print(f"\n{'='*100}")
    print("PRICE COMPARISON: LP vs API")
    print(f"{'='*100}\n")

    windows = set()
    for (product, window) in list(expected_prices.keys()) + list(computed_prices.keys()):
        windows.add(window)

    total_matches = 0
    total_compared = 0
    mismatches = []

    for window in sorted(windows):
        start, end = window
        print(f"Window: {start} → {end}")
        print("-" * 100)
        window_expected = {p: price for (p, w), price in expected_prices.items() if w == window}
        window_computed = {p: price for (p, w), price in computed_prices.items() if w == window}
        all_products = sorted(set(list(window_expected.keys()) + list(window_computed.keys())))
        if not all_products:
            continue

        header = f"{'Product':<15} | {'API Price':<12} | {'LP Price':<12} | {'Difference':<12} | {'Match':<10}"
        print(f"  {header}")
        print(f"  {'-' * len(header)}")

        for product in all_products:
            exp_price = window_expected.get(product)
            comp_price = window_computed.get(product)
            if exp_price is None and comp_price is None:
                continue
            elif exp_price is None:
                print(f"  {product:<15} | {'(no API)':<12} | £{comp_price:>10.4f} | {'-':<12} | -")
            elif comp_price is None:
                print(f"  {product:<15} | £{exp_price:>10.4f} | {'(no orders)':<12} | {'-':<12} | -")
            else:
                diff = abs(Decimal(str(exp_price)) - Decimal(str(comp_price)))
                match = diff <= PRACTICAL_TOL
                match_str = "✓" if match else "✗"
                print(f"  {product:<15} | £{exp_price:>10.4f} | £{comp_price:>10.4f} | £{float(diff):>10.4f} | {match_str:<10}")
                total_compared += 1
                if match:
                    total_matches += 1
                else:
                    mismatches.append((window, product, float(exp_price), float(comp_price), float(diff)))
        print()

    print(f"{'='*100}")
    if total_compared > 0:
        match_pct = 100 * total_matches / total_compared
        print(f"SUMMARY: {total_matches}/{total_compared} prices matched within £0.01 ({match_pct:.1f}%)")
    else:
        print("SUMMARY: No comparable prices found")
    print(f"{'='*100}\n")
    return total_matches, total_compared, mismatches


def investigate_mismatches(data: Dict, mismatches: List, computed_prices: Dict) -> None:
    """
    For a small set of mismatches, test whether the API price satisfies surplus constraints.
    """
    if not mismatches:
        print("\n✓ All prices match! No investigation needed.\n")
        return

    print("\n" + "="*100)
    print("INVESTIGATING PRICE MISMATCHES")
    print("="*100)

    grouped_orders = group_multi_product_orders(data["sell_orders"], data["x_s_observed"])
    lp = GlobalPricingLP()

    for window, product, api_price, lp_price, diff in mismatches[:10]:
        start, end = window
        print(f"\nProduct: {product} | Window: {start} → {end}")
        print(f"  API price: £{api_price:.4f}")
        print(f"  LP price:  £{lp_price:.4f}")
        print(f"  Difference: £{diff:.4f}")

        test_prices = dict(computed_prices)
        test_prices[(product, window)] = api_price

        try:
            lp._verify_surpluses(grouped_orders, test_prices, data["basket_to_loop"])
            print("  → API price satisfies all constraints (difference due to tie-break / objective).")
        except RuntimeError as exc:
            print("  → API price VIOLATES constraint:", str(exc)[:200])


# ---------------------------------------
# Run LP and orchestrate tests
# ---------------------------------------
def run_global_pricing_test(data: Dict):
    backend = PulpSolverBackend(msg=0)
    global_lp = GlobalPricingLP(backend)

    logger.info("Solving Global Pricing LP...")
    computed_prices, problem, status = global_lp.solve(
        products=data["products"],
        all_sell_orders=data["sell_orders"],
        x_s_val=data["x_s_observed"],
        all_baskets=[],
        basket_to_loop=data["basket_to_loop"],
    )

    logger.info("Solver status: %s", status)
    logger.info("Constraints: %d  Variables: %d", len(problem.constraints), len(problem.variables()))
    return computed_prices, problem, status


if __name__ == "__main__":
    AUCTION_ID = 1114
    TEST_LIMIT = 1000000

    print(f"\n{'='*100}")
    print(f"GLOBAL PRICING LP TEST - AUCTION {AUCTION_ID}")
    print(f"{'='*100}\n")

    print("Loading orders from API...")
    data = process_auction_data(SELL_URL, BUY_URL, AUCTION_ID, TEST_LIMIT)

    print("\nLoaded:")
    print(f"  - {len(data['sell_orders'])} sell order rows")
    print(f"  - {len(data['products'])} unique products")
    print(f"  - {len(data['basket_to_loop'])} baskets mapped to loop groups")
    print(f"  - {len(data['expected_prices'])} product-window pairs with API prices\n")

    # Analyze API published clearing prices
    analyze_api_clearing(data)

    # Run LP and compute LP prices
    computed_prices, problem, status = run_global_pricing_test(data)

    # Compare prices
    matches, total, mismatches = print_price_comparison(computed_prices, data["expected_prices"])

    # Compute procurement costs (use NESO rounding)
    grouped_orders = group_multi_product_orders(data["sell_orders"], data["x_s_observed"])
    api_cost, api_missing = compute_procurement_cost(grouped_orders, data["expected_prices"])
    lp_cost, lp_missing = compute_procurement_cost(grouped_orders, computed_prices)

    print("\n" + "="*100)
    print("PROCUREMENT COST COMPARISON")
    print("="*100)
    if api_missing:
        print(f"Warning: API prices missing for {len(api_missing)} product-window pairs")
    print(f"API procurement cost: £{api_cost:,.2f}")
    if lp_missing:
        print(f"Warning: LP prices missing for {len(lp_missing)} product-window pairs")
    print(f"LP procurement cost:  £{lp_cost:,.2f}")

    cost_diff = lp_cost - api_cost
    if cost_diff < 0:
        print(f"Cost savings (LP):    £{abs(cost_diff):,.2f} ✓")
    elif cost_diff > 0:
        print(f"Cost increase (LP):   £{cost_diff:,.2f}")
    else:
        print(f"Cost difference:      £0.00 (identical)")
    print("="*100)

    # Report tight constraints for diagnostic value
    tight_baskets_api, tight_loops_api = count_tight_constraints(grouped_orders, data["expected_prices"], data["basket_to_loop"])
    tight_baskets_lp, tight_loops_lp = count_tight_constraints(grouped_orders, computed_prices, data["basket_to_loop"])
    print(f"\nTIGHT-CONSTRAINTS (surplus ≈ 0 within FEAS_TOL):")
    print(f"  API - tight baskets: {tight_baskets_api}, tight loops: {tight_loops_api}")
    print(f"  LP  - tight baskets: {tight_baskets_lp}, tight loops: {tight_loops_lp}")

    print(f"\n{'='*100}")
    print("TEST COMPLETE")
    print(f"{'='*100}\n")
