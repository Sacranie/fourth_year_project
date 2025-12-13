from typing import Dict, List, Tuple, Set
from decimal import Decimal
import json
import urllib.request
import urllib.parse

from eac.models import SellOrder
from eac.PricingLP import GlobalPricingLP, group_multi_product_orders, ROUNDING_TOL_DECIMAL
from eac.solver import PulpSolverBackend


# NESO API endpoints
SELL_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=13b511df-d6ec-4143-afb1-0ecc6fd19810"
BUY_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=1cf68f59-8eb8-4f1d-bccf-11b5a47b24e5"


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
        print(f"Error loading data: {e}")
        return []
    return data.get("result", {}).get("records", [])


def process_auction_data(sell_url: str, buy_url: str, auction_id: int, limit: int):
    """
    Load sell orders and build structured objects for pricing LP.

    Returns dict with:
      - sell_orders: list[SellOrder]
      - x_s_observed: dict[orderID] -> acceptance ratio
      - expected_prices: dict[(product, window)] -> clearing price (float)
      - basket_to_loop: dict[basketID] -> loop_group_id
      - products: list of product names
    """
    sell_records = load_orders_for_auction(sell_url, auction_id=auction_id, limit=limit)
    all_sell_orders: List[SellOrder] = []
    x_s_observed: Dict[int, float] = {}
    expected_prices: Dict[Tuple[str, Tuple[str, str]], float] = {}
    basket_to_loop: Dict[int, int] = {}
    products = set()

    rejected_orders = 0
    for row in sell_records:
        status = str(row.get("status", "")).strip().upper()
        if status == "REJECTED":
            rejected_orders += 1
            continue

        delivery_start = str(row.get("deliveryStart", ""))
        delivery_end = str(row.get("deliveryEnd", ""))
        window = (delivery_start, delivery_end)

        # CRITICAL FIX 1: Use "orderID" not "order_id"
        order_id = int(row.get("orderID", 0))
        acceptance_ratio = float(row.get("acceptanceRatio", 0.0) or 0.0)

        # Build SellOrder
        # Note: min_acceptance_ratio is a constraint on the order (if it exists in API)
        # For the pricing LP, we're taking acceptances as given from the API,
        # so this field doesn't affect our pricing calculation
        sell_order = SellOrder(
            auctionID=int(row.get("auctionID", 0)),
            registeredAuctionParticipant=str(row.get("registeredAuctionParticipant", "")),
            auctionUnit=str(row.get("auctionUnit", "")),
            basketID=int(row.get("basketID", 0)),
            service=str(row.get("service", "")),
            deliveryStart=delivery_start,
            deliveryEnd=delivery_end,
            orderID=order_id,
            orderType=str(row.get("orderType", "parent")).lower(),
            auctionProduct=str(row.get("auctionProduct", "")),
            quantity=float(row.get("quantity", 0.0)),
            price=float(row.get("priceLimit", 0.0)),
            orderEntryTime=str(row.get("orderEntryTime", "")),
            product_id=str(row.get("productID", "")),
            status=status,
            min_acceptance_ratio=acceptance_ratio,
        )

        all_sell_orders.append(sell_order)
        x_s_observed[order_id] = acceptance_ratio  

        product = row.get("auctionProduct")
        clearing_price_raw = row.get("clearingPrice")
        if product:
            products.add(product)
            # FIXED: Check for None before converting to float
            if clearing_price_raw is not None:
                expected_prices[(product, window)] = float(clearing_price_raw)

        # Track loop relation
        basket_id = int(row.get("basketID", 0))
        looped_basket_id = row.get("loopedBasketID")
        
        # FIXED: Convert to int and only add if not already present
        if looped_basket_id is not None and basket_id not in basket_to_loop:
            basket_to_loop[basket_id] = int(looped_basket_id)

    if rejected_orders:
        print(f"Skipped {rejected_orders} rejected orders")

    return {
        "sell_orders": all_sell_orders,
        "x_s_observed": x_s_observed,
        "expected_prices": expected_prices,
        "basket_to_loop": basket_to_loop,
        "products": list(sorted(products)),
    }


# ---------------------------------------
# Procurement cost calculation
# ---------------------------------------
def compute_procurement_cost(grouped_orders: List, price_map: Dict[Tuple[str, Tuple[str, str]], float]) -> Tuple[Decimal, Set[Tuple[str, Tuple[str, str]]]]:
    """
    Calculate procurement cost using prices as provided (LP already rounds to NESO standard).
    Returns (total_cost, missing_price_keys)
    """
    total = Decimal("0")  # FIXED: Use Decimal for precision
    missing: Set[Tuple[str, Tuple[str, str]]] = set()

    for order in grouped_orders:
        if not order.is_accepted:
            continue
        for fragment in order.fragments:
            key = (fragment.auctionProduct, (fragment.deliveryStart, fragment.deliveryEnd))
            price_raw = price_map.get(key)
            if price_raw is None:
                missing.add(key)
                continue
            # Use Decimal arithmetic for precision
            total += Decimal(str(price_raw)) * Decimal(str(fragment.quantity)) * Decimal(str(order.acceptance))

    return total, missing


# ---------------------------------------
# Price comparison & mismatch investigation
# ---------------------------------------
def print_price_comparison(computed_prices: Dict, expected_prices: Dict):
    """
    Compare LP vs API prices per window & product.
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
                # FIXED: Use 0.01 for proper penny matching (was 0.1)
                match = diff <= Decimal("0.01")
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


# ---------------------------------------
# Run LP and orchestrate tests
# ---------------------------------------
def run_global_pricing_test(data: Dict):
    backend = PulpSolverBackend(msg=0)
    global_lp = GlobalPricingLP(backend)

    print("Solving Global Pricing LP...")
    computed_prices, problem, status = global_lp.solve(
        products=data["products"],
        all_sell_orders=data["sell_orders"],
        x_s_val=data["x_s_observed"],
        all_baskets=[],
        basket_to_loop=data["basket_to_loop"],
    )
    
    print(f"Solver status: {status}")
    print(f"Number of constraints: {len(problem.constraints)}")
    print(f"Number of variables: {len(problem.variables())}")

    return computed_prices, problem, status


if __name__ == "__main__":
    AUCTION_ID = 1112
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

    # Run LP and compute LP prices
    computed_prices, problem, status = run_global_pricing_test(data)

    # Compare prices
    matches, total, mismatches = print_price_comparison(computed_prices, data["expected_prices"])

    # Compute procurement costs
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
    if abs(cost_diff) < Decimal("0.01"):
        print(f"Cost difference:      £0.00 (identical)")
    elif cost_diff < 0:
        print(f"Cost savings (LP):    £{abs(cost_diff):,.2f} ✓")
    else:
        print(f"Cost increase (LP):   £{cost_diff:,.2f}")
    print("="*100)

    print(f"\n{'='*100}")
    print("TEST COMPLETE")
    print(f"{'='*100}\n")