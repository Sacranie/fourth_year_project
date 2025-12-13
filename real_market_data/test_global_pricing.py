"""
Test the Global Pricing LP that solves all windows simultaneously.
This properly handles loop families that span multiple time windows.
"""
from decimal import Decimal
from eac.models import SellOrder, BuyOrder, Basket
from eac.PricingLP import GlobalPricingLP, group_multi_product_orders, ROUNDING_TOL_DECIMAL
from eac.solver import PulpSolverBackend
from collections import defaultdict
from typing import Dict, List, Tuple, Set
import json
import urllib.request
import urllib.parse


def load_orders_for_auction(base_url: str, auction_id: int, limit: int) -> List[Dict]:
    """Load all orders for a specific Auction ID."""
    filters = {"auctionID": auction_id}
    filters_json = json.dumps(filters)
    url = f"{base_url}&limit={limit}&filters={urllib.parse.quote(filters_json)}"
    
    try:
        with urllib.request.urlopen(url) as response:
            data = json.load(response)
    except Exception as e:
        print(f"Error fetching data: {e}")
        return []
    
    return data.get("result", {}).get("records", [])


def process_auction_data(sell_url: str, buy_url: str, auction_id: int, limit: int):
    """
    Load all orders and organize data for global pricing LP.
    """
    sell_records = load_orders_for_auction(sell_url, auction_id=auction_id, limit=limit)
    
    all_sell_orders = []
    x_s_observed = {}  # orderID -> acceptance ratio
    expected_prices = {}  # (product, window) -> expected price
    basket_to_loop = {}  # basketID -> looped_to_id
    products = set()
    
    print(f"Processing {len(sell_records)} sell orders...")
    
    rejected_orders = 0

    for row in sell_records:
        status = str(row.get("status", "")).strip().upper()
        if status == "REJECTED":
            rejected_orders += 1
            continue

        delivery_start = str(row.get("deliveryStart", ""))
        delivery_end = str(row.get("deliveryEnd", ""))
        window = (delivery_start, delivery_end)
        
        order_id = int(row.get("orderID", 0))
        acceptance_ratio = float(row.get("acceptanceRatio", 0.0) or 0.0)
        
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
            min_acceptance_ratio=0.0
        )
        
        all_sell_orders.append(sell_order)
        x_s_observed[order_id] = acceptance_ratio
        
        product = row.get("auctionProduct")
        clearing_price = row.get("clearingPrice")
        if product:
            products.add(product)
            if clearing_price is not None:
                expected_prices[(product, window)] = float(clearing_price)
        
        # Track loop relationships
        basket_id = int(row.get("basketID", 0))
        looped_basket_id = row.get("loopedBasketID")
        if looped_basket_id is not None and basket_id not in basket_to_loop:
            basket_to_loop[basket_id] = int(looped_basket_id)

    if rejected_orders:
        print(f"Skipped {rejected_orders} rejected sell orders")
    
    return {
        "sell_orders": all_sell_orders,
        "x_s_observed": x_s_observed,
        "expected_prices": expected_prices,
        "basket_to_loop": basket_to_loop,
        "products": list(products),
    }


def run_global_pricing_test(data: Dict):
    """
    Run the Global Pricing LP and compare with expected prices.
    """
    backend = PulpSolverBackend(msg=0)
    global_lp = GlobalPricingLP(backend)
    
    print("\nSolving Global Pricing LP...")
    computed_prices, problem, status = global_lp.solve(
        products=data["products"],
        all_sell_orders=data["sell_orders"],
        x_s_val=data["x_s_observed"],
        all_baskets=[],  # Not needed for pricing
        basket_to_loop=data["basket_to_loop"],
    )
    
    print(f"Solver status: {status}")
    print(f"Number of constraints: {len(problem.constraints)}")
    print(f"Number of variables: {len(problem.variables())}")
    
    return computed_prices, problem, status


def compute_procurement_cost(grouped_orders: List, price_map: Dict[Tuple[str, Tuple[str, str]], float]) -> Tuple[Decimal, Set[Tuple[str, Tuple[str, str]]]]:
    total = Decimal("0")
    missing: Set[Tuple[str, Tuple[str, str]]] = set()

    for order in grouped_orders:
        if not getattr(order, "is_accepted", False):
            continue

        acceptance_dec = Decimal(str(order.acceptance))
        for fragment in order.fragments:
            window = (fragment.deliveryStart, fragment.deliveryEnd)
            key = (fragment.auctionProduct, window)
            raw_price = price_map.get(key)
            if raw_price is None:
                missing.add(key)
                continue
            price_dec = Decimal(str(raw_price))
            qty_dec = Decimal(str(fragment.quantity))
            total += price_dec * qty_dec * acceptance_dec

    return total, missing


def print_results(computed_prices: Dict, expected_prices: Dict):
    """Compare computed vs expected prices."""
    print(f"\n{'='*100}")
    print("GLOBAL PRICING LP TEST RESULTS")
    print(f"{'='*100}\n")
    
    # Group by window
    windows = set()
    for (product, window) in expected_prices.keys():
        windows.add(window)
    
    total_matches = 0
    total_products = 0
    
    mismatches = []

    for window in sorted(windows):
        start, end = window
        print(f"Window: {start} -> {end}")
        print("-" * 100)
        
        # Get products for this window
        window_expected = {p: price for (p, w), price in expected_prices.items() if w == window}
        window_computed = {p: price for (p, w), price in computed_prices.items() if w == window}
        
        header = f"{'Product':<15} | {'Expected':<12} | {'Computed':<12} | {'Difference':<12} | {'Match':<10}"
        print(f"  {header}")
        print(f"  {'-' * len(header)}")
        
        # Only compare products that we computed a price for (have accepted orders)
        for product in sorted(set(list(window_expected.keys()) + list(window_computed.keys()))):
            exp_price = window_expected.get(product, 0.0)
            comp_price = window_computed.get(product, None)  # None if no accepted orders
            
            if comp_price is None:
                # No accepted orders for this product in this window - skip
                print(f"  {product:<15} | £{exp_price:>10.4f} | {'(no orders)':<12} | {'-':<12} | -")
                continue
            
            diff = abs(exp_price - comp_price)
            match = diff < 0.01
            match_str = "YES" if match else "NO"

            print(f"  {product:<15} | £{exp_price:>10.4f} | £{comp_price:>10.4f} | £{diff:>10.4f} | {match_str:<10}")
            
            # Count only products with computed prices (active in this window)
            total_products += 1
            if match:
                total_matches += 1
            else:
                mismatches.append((window, product, exp_price, comp_price, diff))
        
        print()
    
    print(f"{'='*100}")
    match_pct = 100 * total_matches / total_products if total_products > 0 else 0.0
    print(f"SUMMARY: {total_matches}/{total_products} prices matched ({match_pct:.1f}%)")
    print(f"{'='*100}")
    
    return total_matches, total_products, match_pct, mismatches

if __name__ == "__main__":
    SELL_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=13b511df-d6ec-4143-afb1-0ecc6fd19810"
    BUY_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=1cf68f59-8eb8-4f1d-bccf-11b5a47b24e5"
    AUCTION_ID = 1114
    TEST_LIMIT = 1000000000
    
    print(f"\n{'='*100}")
    print(f"GLOBAL PRICING LP TEST FOR AUCTION ID: {AUCTION_ID}")
    print(f"{'='*100}")
    print("This test uses the Global Pricing LP that solves ALL windows simultaneously,")
    print("properly handling loop families that span multiple time windows.")
    print(f"{'='*100}\n")
    
    print("Loading orders...")
    data = process_auction_data(SELL_URL, BUY_URL, AUCTION_ID, TEST_LIMIT)
    
    print(f"Loaded {len(data['sell_orders'])} sell orders")
    print(f"Found {len(data['products'])} products")
    print(f"Found {len(data['basket_to_loop'])} baskets in loop families")

    computed_prices, problem, status = run_global_pricing_test(data)

    grouped_orders = group_multi_product_orders(data["sell_orders"], data["x_s_observed"])
    api_cost, api_missing = compute_procurement_cost(grouped_orders, data["expected_prices"])
    lp_cost, lp_missing = compute_procurement_cost(grouped_orders, computed_prices)

    print("\nProcurement cost comparison")
    print("-" * 100)
    if api_missing:
        print(f"API MCPs missing {len(api_missing)} product-window pairs; cost computed over remaining accepted fragments")
    print(f"API MCP procurement cost: £{api_cost:.2f}")
    if lp_missing:
        print(f"LP solution missing {len(lp_missing)} product-window pairs; cost computed over remaining accepted fragments")
    print(f"LP MCP procurement cost:  £{lp_cost:.2f}")
    print(f"Cost delta (LP - API):    £{(lp_cost - api_cost):.2f}")
    print("-" * 100)
    
    
    if status == "Optimal":
        matches, total, match_pct, mismatches = print_results(computed_prices, data["expected_prices"])

        print(f"\nOverall MCP accuracy: {matches}/{total} ({match_pct:.1f}%) correct")
    else:
        print(f"\nSolver did not find optimal solution: {status}")
