from typing import Dict, List, Tuple, Set
from collections import defaultdict
from decimal import Decimal, getcontext
import json
import urllib.request
import urllib.parse

from eac.models import SellOrder
from eac.PricingLP import GlobalPricingLP, group_multi_product_orders, ROUNDING_TOL_DECIMAL
from eac.solver import PulpSolverBackend

# Set decimal precision
getcontext().prec = 28

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
    """Load sell orders and build structured objects for pricing LP."""
    sell_records = load_orders_for_auction(sell_url, auction_id=auction_id, limit=limit)
    all_sell_orders: List[SellOrder] = []
    x_s_observed: Dict[int, float] = {}
    expected_prices: Dict[Tuple[str, Tuple[str, str]], float] = {}
    basket_to_loop = defaultdict(list)
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
            min_acceptance_ratio=acceptance_ratio,
        )

        all_sell_orders.append(sell_order)
        x_s_observed[order_id] = acceptance_ratio

        product = row.get("auctionProduct")
        clearing_price_raw = row.get("clearingPrice")
        if product:
            products.add(product)
            if clearing_price_raw is not None:
                expected_prices[(product, window)] = float(clearing_price_raw)

        basket_id = int(row.get("basketID", 0))
        looped_basket_id = row.get("loopedBasketID")
        
        if looped_basket_id is not None and basket_id not in basket_to_loop:
            basket_to_loop[looped_basket_id].append(basket_id)

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


# --------------------------------
# CONSTRAINT VERIFICATION
# --------------------------------
def compute_order_surplus(order, price_map: Dict[Tuple[str, Tuple[str, str]], float]) -> Tuple[Decimal, Set]:
    """
    Compute surplus for a single multi-product order.
    Surplus = Revenue - Cost = sum(price * quantity * acceptance) - (price_limit * total_quantity * acceptance)
    
    Returns: (surplus, missing_price_keys)
    """
    if not order.is_accepted:
        return Decimal("0"), set()
    
    revenue = Decimal("0")
    cost = Decimal("0")
    missing_keys = set()
    
    for fragment in order.fragments:
        key = (fragment.auctionProduct, (fragment.deliveryStart, fragment.deliveryEnd))
        price_raw = price_map.get(key)
        
        if price_raw is None:
            missing_keys.add(key)
            continue
        
        quantity_accepted = Decimal(str(fragment.quantity)) * Decimal(str(order.acceptance))
        revenue += Decimal(str(price_raw)) * quantity_accepted
        cost += Decimal(str(order.price_limit)) * quantity_accepted
    
    surplus = revenue - cost
    return surplus, missing_keys


def verify_all_constraints(grouped_orders: List, price_map: Dict, basket_to_loop: Dict, 
                          price_source: str = "API"):
    """
    Verify ALL constraints:
    1. Child orders: individual surplus >= 0
    2. Non-looped baskets: total basket surplus >= 0
    3. Loop families: total surplus across all baskets in family >= 0
    
    Returns: (violations_dict, summary_stats)
    """
    print(f"\n{'='*100}")
    print(f"CONSTRAINT VERIFICATION - {price_source} PRICES")
    print(f"{'='*100}\n")
    
    # Organize orders by basket
    orders_by_basket = defaultdict(list)
    for order in grouped_orders:
        orders_by_basket[order.basket_id].append(order)
    
    # Identify baskets in loops
    baskets_in_loops = set()
    for loop_id, basket_ids in basket_to_loop.items():
        baskets_in_loops.update(basket_ids)
    
    # Track violations
    child_violations = []
    basket_violations = []
    loop_violations = []
    total_missing_prices = set()
    
    # ===== CONSTRAINT 1: Child Order Surplus >= 0 =====
    print("CONSTRAINT 1: Child Order Surplus")
    print("-" * 100)
    child_count = 0
    child_passed = 0
    
    for order in grouped_orders:
        if order.order_type == 'child' and order.is_accepted:
            child_count += 1
            surplus, missing = compute_order_surplus(order, price_map)
            total_missing_prices.update(missing)
            
            if surplus < Decimal("-0.01"):  # Allow 1 penny tolerance
                child_violations.append({
                    'order_id': order.canonical_order_id,
                    'basket_id': order.basket_id,
                    'surplus': float(surplus),
                    'missing_prices': len(missing)
                })
                print(f"  ✗ Child Order {order.canonical_order_id} (Basket {order.basket_id}): "
                      f"Surplus = £{surplus:.4f} (NEGATIVE)")
            else:
                child_passed += 1
    
    print(f"\n  Summary: {child_passed}/{child_count} child orders passed")
    if child_violations:
        print(f"  ⚠ {len(child_violations)} child orders have negative surplus!")
    else:
        print(f"  ✓ All child orders have non-negative surplus")
    
    # ===== CONSTRAINT 2: Non-Looped Basket Surplus >= 0 =====
    print(f"\n{'='*100}")
    print("CONSTRAINT 2: Non-Looped Basket Surplus")
    print("-" * 100)
    non_looped_count = 0
    non_looped_passed = 0
    
    for basket_id, orders in orders_by_basket.items():
        if basket_id not in baskets_in_loops:
            non_looped_count += 1
            basket_surplus = Decimal("0")
            basket_missing = set()
            
            for order in orders:
                surplus, missing = compute_order_surplus(order, price_map)
                basket_surplus += surplus
                basket_missing.update(missing)
            
            total_missing_prices.update(basket_missing)
            
            if basket_surplus < Decimal("-0.01"):
                basket_violations.append({
                    'basket_id': basket_id,
                    'surplus': float(basket_surplus),
                    'order_count': len(orders),
                    'missing_prices': len(basket_missing)
                })
                print(f"  ✗ Basket {basket_id} ({len(orders)} orders): "
                      f"Surplus = £{basket_surplus:.4f} (NEGATIVE)")
            else:
                non_looped_passed += 1
    
    print(f"\n  Summary: {non_looped_passed}/{non_looped_count} non-looped baskets passed")
    if basket_violations:
        print(f"  ⚠ {len(basket_violations)} non-looped baskets have negative surplus!")
    else:
        print(f"  ✓ All non-looped baskets have non-negative surplus")
    
    # ===== CONSTRAINT 3: Loop Family Surplus >= 0 =====
    print(f"\n{'='*100}")
    print("CONSTRAINT 3: Loop Family Surplus")
    print("-" * 100)
    loop_count = len(basket_to_loop)
    loop_passed = 0
    
    for loop_id, basket_ids in basket_to_loop.items():
        loop_surplus = Decimal("0")
        loop_missing = set()
        loop_order_count = 0
        
        for basket_id in basket_ids:
            basket_orders = orders_by_basket.get(basket_id, [])
            loop_order_count += len(basket_orders)
            
            for order in basket_orders:
                surplus, missing = compute_order_surplus(order, price_map)
                loop_surplus += surplus
                loop_missing.update(missing)
        
        total_missing_prices.update(loop_missing)
        
        if loop_surplus < Decimal("-0.01"):
            loop_violations.append({
                'loop_id': loop_id,
                'basket_ids': basket_ids,
                'surplus': float(loop_surplus),
                'basket_count': len(basket_ids),
                'order_count': loop_order_count,
                'missing_prices': len(loop_missing)
            })
            print(f"  ✗ Loop {loop_id} ({len(basket_ids)} baskets, {loop_order_count} orders): "
                  f"Surplus = £{loop_surplus:.4f} (NEGATIVE)")
        else:
            loop_passed += 1
    
    print(f"\n  Summary: {loop_passed}/{loop_count} loop families passed")
    if loop_violations:
        print(f"  ⚠ {len(loop_violations)} loop families have negative surplus!")
    else:
        print(f"  ✓ All loop families have non-negative surplus")
    
    # ===== OVERALL SUMMARY =====
    print(f"\n{'='*100}")
    print(f"OVERALL SUMMARY - {price_source} PRICES")
    print("=" * 100)
    
    total_violations = len(child_violations) + len(basket_violations) + len(loop_violations)
    total_checks = child_count + non_looped_count + loop_count
    
    if total_violations == 0:
        print(f"✓✓✓ ALL CONSTRAINTS SATISFIED ✓✓✓")
        print(f"  - {child_count} child orders: ALL PASSED")
        print(f"  - {non_looped_count} non-looped baskets: ALL PASSED")
        print(f"  - {loop_count} loop families: ALL PASSED")
    else:
        print(f"⚠⚠⚠ CONSTRAINT VIOLATIONS DETECTED ⚠⚠⚠")
        print(f"  - {len(child_violations)} child order violations")
        print(f"  - {len(basket_violations)} non-looped basket violations")
        print(f"  - {len(loop_violations)} loop family violations")
        print(f"  - {total_violations}/{total_checks} total violations")
    
    if total_missing_prices:
        print(f"\n⚠ Warning: {len(total_missing_prices)} product-window pairs missing prices")
    
    print("=" * 100 + "\n")
    
    return {
        'child_violations': child_violations,
        'basket_violations': basket_violations,
        'loop_violations': loop_violations,
        'missing_prices': total_missing_prices
    }, {
        'child_count': child_count,
        'child_passed': child_passed,
        'non_looped_count': non_looped_count,
        'non_looped_passed': non_looped_passed,
        'loop_count': loop_count,
        'loop_passed': loop_passed,
        'total_violations': total_violations
    }


# ---------------------------------------
# Main execution
# ---------------------------------------
if __name__ == "__main__":
    AUCTION_ID = 1114
    TEST_LIMIT = 1000000

    print(f"\n{'='*100}")
    print(f"COMPREHENSIVE CONSTRAINT VERIFICATION - AUCTION {AUCTION_ID}")
    print(f"{'='*100}\n")

    print("Loading orders from API...")
    data = process_auction_data(SELL_URL, BUY_URL, AUCTION_ID, TEST_LIMIT)

    print("\nLoaded:")
    print(f"  - {len(data['sell_orders'])} sell order rows")
    print(f"  - {len(data['products'])} unique products")
    print(f"  - {len(data['basket_to_loop'])} loop families")
    print(f"  - {len(data['expected_prices'])} product-window pairs with API prices\n")

    # Group orders
    grouped_orders = group_multi_product_orders(data["sell_orders"], data["x_s_observed"])
    
    # Organize orders by basket (needed for surplus analysis)
    orders_by_basket = defaultdict(list)
    for order in grouped_orders:
        orders_by_basket[order.basket_id].append(order)
    
    # Run LP to get computed prices
    print("Solving Global Pricing LP...")
    backend = PulpSolverBackend(msg=0)
    global_lp = GlobalPricingLP(backend)
    computed_prices, problem, status = global_lp.solve(
        all_sell_orders=data["sell_orders"],
        x_s_val=data["x_s_observed"],
        basket_to_loop=data["basket_to_loop"],
    )
    print(f"Solver status: {status}\n")

    # ===== VERIFY API PRICES =====
    api_violations, api_stats = verify_all_constraints(
        grouped_orders, 
        data["expected_prices"], 
        data["basket_to_loop"],
        price_source="API"
    )

    # ===== VERIFY LP PRICES =====
    lp_violations, lp_stats = verify_all_constraints(
        grouped_orders,
        computed_prices,
        data["basket_to_loop"],
        price_source="LP"
    )

    # ===== PRICE COMPARISON =====
    matches, total_compared, price_mismatches = print_price_comparison(computed_prices, data["expected_prices"])

    # ===== SURPLUS COMPARISON FOR MISMATCHED PRICES =====
    if price_mismatches:
        print(f"\n{'='*100}")
        print("SURPLUS IMPACT ANALYSIS - WHERE PRICES DIFFER")
        print("=" * 100)
        print("\nAnalyzing how price differences affect surplus for baskets/loops with mismatched prices...\n")
        
        # Get product-windows with price mismatches
        mismatched_product_windows = set((window, product) for window, product, _, _, _ in price_mismatches)
        
        # Find which baskets/loops are affected by these mismatches
        affected_baskets = set()
        affected_loops = set()
        
        for order in grouped_orders:
            if not order.is_accepted:
                continue
            for fragment in order.fragments:
                window = (fragment.deliveryStart, fragment.deliveryEnd)
                if (window, fragment.auctionProduct) in mismatched_product_windows:
                    affected_baskets.add(order.basket_id)
                    # Check if this basket is in a loop
                    for loop_id, basket_ids in data['basket_to_loop'].items():
                        if order.basket_id in basket_ids:
                            affected_loops.add(loop_id)
        
        print(f"Found {len(mismatched_product_windows)} product-window pairs with price differences")
        print(f"These affect {len(affected_baskets)} baskets and {len(affected_loops)} loop families\n")
        
        # Compare surplus for affected child orders
        print("="*100)
        print("CHILD ORDERS WITH MISMATCHED PRICES")
        print("-"*100)
        child_surplus_diffs = []
        for order in grouped_orders:
            if order.order_type == 'child' and order.is_accepted and order.basket_id in affected_baskets:
                api_surplus, _ = compute_order_surplus(order, data["expected_prices"])
                lp_surplus, _ = compute_order_surplus(order, computed_prices)
                surplus_diff = lp_surplus - api_surplus
                
                if abs(surplus_diff) > Decimal("0.01"):
                    child_surplus_diffs.append({
                        'order_id': order.canonical_order_id,
                        'basket_id': order.basket_id,
                        'api_surplus': float(api_surplus),
                        'lp_surplus': float(lp_surplus),
                        'difference': float(surplus_diff)
                    })
        
        if child_surplus_diffs:
            # Sort by absolute difference
            child_surplus_diffs.sort(key=lambda x: abs(x['difference']), reverse=True)
            print(f"\n{'Order ID':<15} | {'Basket':<10} | {'API Surplus':<15} | {'LP Surplus':<15} | {'Difference':<15}")
            print("-"*100)
            for item in child_surplus_diffs[:20]:  # Show top 20
                print(f"{item['order_id']:<15} | {item['basket_id']:<10} | £{item['api_surplus']:>12.4f} | £{item['lp_surplus']:>12.4f} | £{item['difference']:>12.4f}")
            if len(child_surplus_diffs) > 20:
                print(f"\n... and {len(child_surplus_diffs) - 20} more child orders with surplus differences")
        else:
            print("\nNo child orders found with surplus differences > £0.01")
        
        # Compare surplus for affected non-looped baskets
        print(f"\n{'='*100}")
        print("NON-LOOPED BASKETS WITH MISMATCHED PRICES")
        print("-"*100)
        baskets_in_loops = set(b_id for _, basket_ids in data['basket_to_loop'].items() for b_id in basket_ids)
        basket_surplus_diffs = []
        
        for basket_id in affected_baskets:
            if basket_id not in baskets_in_loops:
                orders = orders_by_basket[basket_id]
                
                api_basket_surplus = Decimal("0")
                lp_basket_surplus = Decimal("0")
                
                for order in orders:
                    api_surp, _ = compute_order_surplus(order, data["expected_prices"])
                    lp_surp, _ = compute_order_surplus(order, computed_prices)
                    api_basket_surplus += api_surp
                    lp_basket_surplus += lp_surp
                
                surplus_diff = lp_basket_surplus - api_basket_surplus
                
                if abs(surplus_diff) > Decimal("0.01"):
                    basket_surplus_diffs.append({
                        'basket_id': basket_id,
                        'order_count': len(orders),
                        'api_surplus': float(api_basket_surplus),
                        'lp_surplus': float(lp_basket_surplus),
                        'difference': float(surplus_diff)
                    })
        
        if basket_surplus_diffs:
            basket_surplus_diffs.sort(key=lambda x: abs(x['difference']), reverse=True)
            print(f"\n{'Basket ID':<12} | {'Orders':<10} | {'API Surplus':<15} | {'LP Surplus':<15} | {'Difference':<15}")
            print("-"*100)
            for item in basket_surplus_diffs[:20]:
                print(f"{item['basket_id']:<12} | {item['order_count']:<10} | £{item['api_surplus']:>12.4f} | £{item['lp_surplus']:>12.4f} | £{item['difference']:>12.4f}")
            if len(basket_surplus_diffs) > 20:
                print(f"\n... and {len(basket_surplus_diffs) - 20} more baskets with surplus differences")
        else:
            print("\nNo non-looped baskets found with surplus differences > £0.01")
        
        # Compare surplus for affected loop families
        print(f"\n{'='*100}")
        print("LOOP FAMILIES WITH MISMATCHED PRICES")
        print("-"*100)
        loop_surplus_diffs = []
        
        for loop_id in affected_loops:
            basket_ids = data['basket_to_loop'][loop_id]
            
            api_loop_surplus = Decimal("0")
            lp_loop_surplus = Decimal("0")
            total_orders = 0
            
            for basket_id in basket_ids:
                basket_orders = orders_by_basket.get(basket_id, [])
                total_orders += len(basket_orders)
                
                for order in basket_orders:
                    api_surp, _ = compute_order_surplus(order, data["expected_prices"])
                    lp_surp, _ = compute_order_surplus(order, computed_prices)
                    api_loop_surplus += api_surp
                    lp_loop_surplus += lp_surp
            
            surplus_diff = lp_loop_surplus - api_loop_surplus
            
            if abs(surplus_diff) > Decimal("0.01"):
                loop_surplus_diffs.append({
                    'loop_id': loop_id,
                    'basket_count': len(basket_ids),
                    'order_count': total_orders,
                    'basket_ids': basket_ids,
                    'api_surplus': float(api_loop_surplus),
                    'lp_surplus': float(lp_loop_surplus),
                    'difference': float(surplus_diff)
                })
        
        if loop_surplus_diffs:
            loop_surplus_diffs.sort(key=lambda x: abs(x['difference']), reverse=True)
            print(f"\n{'Loop ID':<12} | {'Baskets':<10} | {'Orders':<10} | {'API Surplus':<15} | {'LP Surplus':<15} | {'Difference':<15}")
            print("-"*100)
            for item in loop_surplus_diffs:
                print(f"{item['loop_id']:<12} | {item['basket_count']:<10} | {item['order_count']:<10} | £{item['api_surplus']:>12.4f} | £{item['lp_surplus']:>12.4f} | £{item['difference']:>12.4f}")
                # Show which baskets are in this loop
                print(f"  └─ Baskets: {', '.join(map(str, item['basket_ids']))}")
        else:
            print("\nNo loop families found with surplus differences > £0.01")
        
        # Summary statistics
        print(f"\n{'='*100}")
        print("SURPLUS DIFFERENCE SUMMARY")
        print("="*100)
        total_child_diff = sum(abs(x['difference']) for x in child_surplus_diffs)
        total_basket_diff = sum(abs(x['difference']) for x in basket_surplus_diffs)
        total_loop_diff = sum(abs(x['difference']) for x in loop_surplus_diffs)
        
        print(f"\nChild Orders:       {len(child_surplus_diffs)} with differences, total absolute difference: £{total_child_diff:,.2f}")
        print(f"Non-Looped Baskets: {len(basket_surplus_diffs)} with differences, total absolute difference: £{total_basket_diff:,.2f}")
        print(f"Loop Families:      {len(loop_surplus_diffs)} with differences, total absolute difference: £{total_loop_diff:,.2f}")
        print("="*100)
    
    # ===== FINAL COMPARISON =====
    print(f"\n{'='*100}")
    print("FINAL COMPARISON: API vs LP")
    print("=" * 100)
    print(f"\n{'Constraint Type':<30} | {'API Violations':<20} | {'LP Violations':<20}")
    print("-" * 100)
    print(f"{'Child Orders':<30} | {len(api_violations['child_violations']):<20} | {len(lp_violations['child_violations']):<20}")
    print(f"{'Non-Looped Baskets':<30} | {len(api_violations['basket_violations']):<20} | {len(lp_violations['basket_violations']):<20}")
    print(f"{'Loop Families':<30} | {len(api_violations['loop_violations']):<20} | {len(lp_violations['loop_violations']):<20}")
    print("-" * 100)
    print(f"{'TOTAL':<30} | {api_stats['total_violations']:<20} | {lp_stats['total_violations']:<20}")
    print("=" * 100)
    
    if lp_stats['total_violations'] == 0:
        print("\n✓✓✓ SUCCESS: LP prices satisfy ALL constraints! ✓✓✓")
    else:
        print(f"\n⚠⚠⚠ WARNING: LP prices have {lp_stats['total_violations']} constraint violations ⚠⚠⚠")
    
    if api_stats['total_violations'] == 0:
        print("✓✓✓ API prices also satisfy ALL constraints ✓✓✓")
    else:
        print(f"⚠ API prices have {api_stats['total_violations']} constraint violations")
    
    print(f"\n{'='*100}")
    print("VERIFICATION COMPLETE")
    print(f"{'='*100}\n")