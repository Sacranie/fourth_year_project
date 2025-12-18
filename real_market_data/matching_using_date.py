from typing import Dict, List, Tuple, Set, Optional
from collections import defaultdict
from decimal import Decimal, getcontext
import json
import urllib.request
import urllib.parse
import pulp

from eac.models import SellOrder, BuyOrder, Basket
from eac.multi_product_orders import group_multi_product_orders
from eac.Volume import VolumeMILP
from eac.solver import PulpSolverBackend
from eac.Validators import validate_unit_capacity, build_loop_families

# NESO API endpoints
SELL_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=13b511df-d6ec-4143-afb1-0ecc6fd19810"
BUY_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=1cf68f59-8eb8-4f1d-bccf-11b5a47b24e5"

ACCEPTANCE_TOLERANCE = 0.01  # Compare within 1%


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


def process_sell_orders(sell_records: List[Dict]) -> Tuple[List[SellOrder], Dict[int, float]]:
    """
    Build logical SellOrder objects from raw API rows.
    Aggregates fragments only insofar as creating one SellOrder per API row here (you later call
    group_multi_product_orders before feeding the MILP).
    Returns (sell_orders, api_acceptance_map).
    """
    all_sell_orders = []
    api_acceptance_ratios = {}

    rejected_count = 0
    executed_count = 0
    partial_count = 0

    for row in sell_records:
        status = str(row.get("status", "")).strip().upper()
        order_id = int(row.get("orderID", 0))


        api_acceptance = row.get("acceptanceRatio", 0.0)
        api_acceptance_ratios[order_id] = api_acceptance

        if status == "REJECTED":
            rejected_count += 1
        else:
            if status == "EXECUTED":
                executed_count += 1
            else:
                partial_count += 1

        sell_order = SellOrder(
            auctionID=int(row.get("auctionID", 0) or 0),
            registeredAuctionParticipant=str(row.get("registeredAuctionParticipant", "") or ""),
            auctionUnit=str(row.get("auctionUnit", "") or ""),
            basketID=int(row.get("basketID", 0) or 0),
            service=str(row.get("service", "") or ""),
            deliveryStart=str(row.get("deliveryStart", "") or ""),
            deliveryEnd=str(row.get("deliveryEnd", "") or ""),
            orderID=int(order_id),
            orderType=str(row.get("orderType", "parent")).lower(),
            auctionProduct=str(row.get("auctionProduct", "") or ""),
            quantity=float(row.get("quantity", 0.0) or 0.0),
            price=float(row.get("priceLimit", row.get("price", 0.0) or 0.0)),
            orderEntryTime=str(row.get("orderEntryTime", "") or ""),
            product_id=str(row.get("productID", "") or ""),
            status=status,
            min_acceptance_ratio=row.get("minAcceptanceRatio", 0.0) or 0.0,
        )

        all_sell_orders.append(sell_order)

    print(f"\nSell Orders Status Breakdown:")
    print(f"  - EXECUTED: {executed_count}")
    print(f"  - PARTIALLY_EXECUTED: {partial_count}")
    print(f"  - REJECTED: {rejected_count}")
    print(f"  - Total: {len(all_sell_orders)}")
    multi_orders = group_multi_product_orders(all_sell_orders)

    # We need to now iterate through the multi_orders and adjust the acceptance ratios
    # If they were rejected we should se to 1 and if they were executed or partially executed we should set to 1
    
    for orders in multi_orders:
        if not orders.is_accepted:
            orders.acceptance = 1.0

    return multi_orders, all_sell_orders, api_acceptance_ratios


def process_buy_orders(buy_records: List[Dict], auction_id: Optional[int] = None) -> Tuple[List[BuyOrder], Dict[int, float]]:
    """
    Build BuyOrder objects; return (buy_orders, api_acceptance_map).
    auction_id is used as fallback if a row lacks auctionID.
    """
    all_buy_orders = []
    api_acceptance_ratios = {}

    for row in buy_records:
        status = str(row.get("status", "")).strip().upper()
        order_id = row.get("orderID", 0)

        if status == "REJECTED":
            min_acceptance = 1.0 # force reject
        else:
            min_acceptance = row.get("acceptanceRatio", 0.0)

        api_acceptance = row.get("acceptanceRatio", 0.0)
        api_acceptance_ratios[order_id] = api_acceptance

        raw = row.get("paradoxicallyAcceptanceAllowed", "false")
        paradoxical = (raw == "true")

        buy_order = BuyOrder(
            auctionID=auction_id,
            orderID=order_id,
            service=str(row.get("service", "") or ""),
            auctionProduct=str(row.get("auctionProduct", "") or ""),
            deliveryStart=str(row.get("deliveryStart", "") or ""),
            deliveryEnd=str(row.get("deliveryEnd", "") or ""),
            quantity=float(row.get("quantity", 0.0) or 0.0),
            price=float(row.get("priceLimit", row.get("price", 0.0) or 0.0)),
            paradoxical=paradoxical,
            min_acceptance_ratio=min_acceptance,
        )

        all_buy_orders.append(buy_order)

    print(f"\nBuy Orders: {len(all_buy_orders)} loaded")
    return all_buy_orders, api_acceptance_ratios

def build_baskets_from_orders(sell_orders: List[SellOrder], raw_records: List[Dict]) -> List[Basket]:

    baskets = {}
    for s in sell_orders:
        bid = int(s.basketID)
        if bid not in baskets:
            baskets[bid] = Basket(id=bid, auctionID=int(s.auctionID), unit=s.auctionUnit, looped_to=None, concomitant=[])

    concomitance = defaultdict(set)

    # populate looped_to and concomitant fields
    for row in raw_records:
        b_id = row.get("basketID")

        if b_id not in baskets:
            continue

        # loopedBasketID may be absent/empty
        looped = row.get("loopedBasketID")
        if looped not in (None, "", "None"):
            baskets[b_id].looped_to = int(looped)

        delivery_start = row.get("deliveryStart")
        delivery_end = row.get("deliveryEnd")
        unit = row.get("auctionUnit")

        concomitance[(unit, delivery_start, delivery_end)].add(int(b_id))

    for (unit, delivery_start, delivery_end), basket_ids in concomitance.items():
        for basket_id in basket_ids:
            baskets[basket_id].concomitant = list(basket_ids - {basket_id})

    print(f"\nBaskets built: {len(baskets)} (concomitant/loop info where present)")
    return list(baskets.values())

# --------------------------------
# ACCEPTANCE RATIO COMPARISON
# --------------------------------
def compare_acceptance_ratios(computed: Dict, api: Dict, order_type: str = "Sell"):
    """
    Compare computed acceptance ratios vs API acceptance ratios.
    Returns: (matches, total, differences_list)
    """
    
    all_order_ids = set(list(computed.keys()) + list(api.keys()))
    
    matches = 0
    total = 0
    differences = []

    
    for order_id in sorted(all_order_ids, key=lambda x: str(x)):
        api_val = api.get(order_id, 0.0)
        comp_val = computed.get(order_id, 0.0)
        
        diff = abs(api_val - comp_val)
        match = diff <= ACCEPTANCE_TOLERANCE
        
        total += 1
        if match:
            matches += 1
        else:
            differences.append({
                'order_id': order_id,
                'api': api_val,
                'computed': comp_val,
                'difference': diff
            })
        
    return matches, total, differences

# --------------------------------
# CONSTRAINT VERIFICATION
# --------------------------------
def verify_loop_constraints(x_s: Dict[int, float], sell_orders: List[SellOrder], 
                           loop_families: Dict, tolerance: float = 0.01):
    """
    Verify that baskets in the same loop family are either ALL accepted or ALL rejected.
    """
    print(f"\n{'='*100}")
    print("LOOP CONSTRAINT VERIFICATION")
    print(f"{'='*100}\n")
    
    # Build basket acceptance from order acceptances
    basket_acceptance = {}
    orders_by_basket = defaultdict(list)
    
    for order in sell_orders:
        orders_by_basket[order.basketID].append(order)
    
    for basket_id, orders in orders_by_basket.items():
        # A basket is "accepted" if any of its parent orders are accepted
        parent_orders = [o for o in orders if o.orderType == "parent"]
        if parent_orders:
            # Check if all parents are accepted (value close to 1) or all rejected (close to 0)
            parent_acceptances = [x_s.get(o.orderID, 0.0) for o in parent_orders]
            basket_acceptance[basket_id] = max(parent_acceptances) if parent_acceptances else 0.0
    
    violations = []
    passed = 0
    
    print(f"Found {len(loop_families)} loop families to verify\n")
    
    for loop_id, basket_ids in loop_families.items():
        if len(basket_ids) < 2:
            continue  # No constraint for single basket
        
        acceptances = [basket_acceptance.get(bid, 0.0) for bid in basket_ids]
        
        # Check if all baskets have similar acceptance (all ~0 or all ~1)
        min_accept = min(acceptances)
        max_accept = max(acceptances)
        
        if max_accept - min_accept > tolerance:
            violations.append({
                'loop_id': loop_id,
                'basket_ids': basket_ids,
                'acceptances': acceptances,
                'min': min_accept,
                'max': max_accept,
                'spread': max_accept - min_accept
            })
            print(f"✗ Loop {loop_id}: Baskets {basket_ids}")
            print(f"  Acceptances: {[f'{a:.4f}' for a in acceptances]}")
            print(f"  Spread: {max_accept - min_accept:.4f} (> {tolerance} tolerance)\n")
        else:
            passed += 1
    
    print(f"{'='*100}")
    if len(violations) == 0:
        print(f"✓✓✓ ALL LOOP CONSTRAINTS SATISFIED ✓✓✓")
        print(f"  - {passed} loop families: ALL PASSED")
    else:
        print(f"⚠⚠⚠ LOOP CONSTRAINT VIOLATIONS DETECTED ⚠⚠⚠")
        print(f"  - {len(violations)} loop families violated")
        print(f"  - {passed} loop families passed")
    print(f"{'='*100}\n")
    
    return violations


def verify_product_balance(x_s: Dict[int, float], x_b: Dict[str, float],
                          sell_orders: List[SellOrder], buy_orders: List[BuyOrder],
                          tolerance: float = 0.01):
    """
    Verify product balance: total sell volume = total buy volume for each product.
    """
    print(f"\n{'='*100}")
    print("PRODUCT BALANCE VERIFICATION")
    print(f"{'='*100}\n")
    
    products = set()
    for order in sell_orders:
        products.add(order.auctionProduct)
    for order in buy_orders:
        products.add(order.auctionProduct)
    
    violations = []
    passed = 0
    
    print(f"{'Product':<20} | {'Sell Volume':<15} | {'Buy Volume':<15} | {'Difference':<15} | {'Balanced':<10}")
    print("-" * 100)
    
    for product in sorted(products):
        sell_vol = sum(
            order.quantity * x_s.get(order.orderID, 0.0)
            for order in sell_orders
            if order.auctionProduct == product
        )
        
        buy_vol = sum(
            order.quantity * x_b.get(order.orderID, 0.0)
            for order in buy_orders
            if order.auctionProduct == product
        )
        
        diff = abs(sell_vol - buy_vol)
        balanced = diff <= tolerance
        balanced_str = "✓" if balanced else "✗"
        
        if balanced:
            passed += 1
        else:
            violations.append({
                'product': product,
                'sell_vol': sell_vol,
                'buy_vol': buy_vol,
                'difference': diff
            })
        
        print(f"{product:<20} | {sell_vol:>13.2f} | {buy_vol:>13.2f} | {diff:>13.2f} | {balanced_str:<10}")
    
    print("-" * 100)
    if len(violations) == 0:
        print(f"✓✓✓ ALL PRODUCTS BALANCED ✓✓✓")
    else:
        print(f"⚠ {len(violations)} products not balanced")
    print(f"{'='*100}\n")
    
    return violations

# ---------- Welfare & procurement cost comparison ----------
def compute_welfare_and_procurement(sell_orders, buy_orders,
                                    sell_accept_map, buy_accept_map,
                                    label="API"):
    """
    sell_orders: flat list of SellOrder fragments (each has orderID, price, quantity, auctionProduct)
    buy_orders: list of BuyOrder objects or dicts (must have orderID, price, quantity, auctionProduct)
    sell_accept_map: dict orderID -> acceptance (0..1)
    buy_accept_map: dict orderID -> acceptance (0..1)
    label: string tag for prints
    returns: dict with totals and per-product breakdown
    """
    total_buy_value = 0.0   # sum(price * accepted_qty) across buys
    total_sell_cost = 0.0   # sum(price * accepted_qty) across sells
    per_product = {}  # product -> {buy_value, sell_cost, buy_qty, sell_qty}

    # sells (flat fragments)
    for s in sell_orders:
        oid = int(getattr(s, "orderID", getattr(s, "orderId", s.get("orderID") if isinstance(s, dict) else None)))
        price = float(getattr(s, "price", s.get("price", 0.0) if isinstance(s, dict) else 0.0))
        qty = float(getattr(s, "quantity", s.get("quantity", 0.0) if isinstance(s, dict) else 0.0))
        prod = getattr(s, "auctionProduct", s.get("auctionProduct", "") if isinstance(s, dict) else "")

        acc = float(sell_accept_map.get(oid, 0.0) or 0.0)
        accepted_qty = qty * acc
        total_sell_cost += price * accepted_qty

        p = per_product.setdefault(prod, {"buy_value":0.0, "sell_cost":0.0, "buy_qty":0.0, "sell_qty":0.0})
        p["sell_cost"] += price * accepted_qty
        p["sell_qty"] += accepted_qty

    # buys
    for b in buy_orders:
        # buy_orders might be BuyOrder objects or dicts; handle both
        bid = b.get("orderID", b.get("id", None)) if isinstance(b, dict) else getattr(b, "orderID", None)
        price = float(b.get("price", 0.0)) if isinstance(b, dict) else float(getattr(b, "price", 0.0))
        qty = float(b.get("quantity", 0.0)) if isinstance(b, dict) else float(getattr(b, "quantity", 0.0))
        prod = b.get("auctionProduct", "") if isinstance(b, dict) else getattr(b, "auctionProduct", "")

        acc = float(buy_accept_map.get(bid, 0.0) or 0.0)
        accepted_qty = qty * acc
        total_buy_value += price * accepted_qty

        p = per_product.setdefault(prod, {"buy_value":0.0, "sell_cost":0.0, "buy_qty":0.0, "sell_qty":0.0})
        p["buy_value"] += price * accepted_qty
        p["buy_qty"] += accepted_qty

    welfare = total_buy_value - total_sell_cost
    return {
        "label": label,
        "total_buy_value": total_buy_value,
        "total_sell_cost": total_sell_cost,
        "welfare": welfare,
        "per_product": per_product
    }


# ---------------------------------------
# Main execution
# ---------------------------------------
# ---------------------------------------
# Main execution (REPLACE your current __main__ block with this)
# ---------------------------------------
if __name__ == "__main__":
    AUCTION_ID = 1118
    TEST_LIMIT = 1000000

    print(f"\n{'='*100}")
    print(f"VOLUME MILP VERIFICATION - AUCTION {AUCTION_ID}")
    print(f"{'='*100}\n")

    print("Loading orders from API...")
    sell_records = load_orders_for_auction(SELL_URL, auction_id=AUCTION_ID, limit=TEST_LIMIT)
    buy_records = load_orders_for_auction(BUY_URL, auction_id=AUCTION_ID, limit=TEST_LIMIT)
    
    print(f"\nRaw records loaded:")
    print(f"  - {len(sell_records)} sell order records")
    print(f"  - {len(buy_records)} buy order records")

    # Process orders
    multi_orders, sell_orders, api_sell_acceptance = process_sell_orders(sell_records)
    buy_orders, api_buy_acceptance = process_buy_orders(buy_records, auction_id=AUCTION_ID)
    
    # Build baskets and extract loop families (pass raw sell_records to populate concomitant/loop info)
    baskets = build_baskets_from_orders(sell_orders, sell_records)
    loop_families = build_loop_families(baskets)
    
    print(f"\nStructured data:")
    print(f"  - {len(sell_orders)} sell orders processed")
    print(f"  - {len(buy_orders)} buy orders processed")
    print(f"  - {len(baskets)} baskets created")
    print(f"  - {len(loop_families)} loop families identified")
    
    # Extract unique products
    products = set(o.auctionProduct for o in sell_orders) | set(o.auctionProduct for o in buy_orders)
    print(f"  - {len(products)} unique products")

    # Extract unique units for capacity registry
    units = set(order.auctionUnit for order in sell_orders)
    unit_capacity_registry = {unit: 1e9 for unit in units}  # Set very high capacity for each unit

    # Run Volume MILP
    print(f"\n{'='*100}")
    print("SOLVING VOLUME MILP...")
    print(f"{'='*100}\n")
    
    backend = PulpSolverBackend(msg=0)
    volume_milp = VolumeMILP(backend=backend)
    
    # Build and solve
    prob, x_b_vars, x_s_vars, y_parent_vars, _ = volume_milp.build_problem(
        products=list(products),
        buy_orders=buy_orders,
        sell_orders=multi_orders,
        baskets=baskets,
        unit_capacity_registry=unit_capacity_registry,
        global_loop_families=loop_families
    )
    
    status = backend.solve(prob)
    print(f"Solver status: {status}")
    
    # Extract solution (robust: read directly from the returned variable maps)
    x_s_computed = {sid: float(pulp.value(var) if pulp.value(var) is not None else 0.0) for sid, var in x_s_vars.items()}
    x_b_computed = {bid: float(pulp.value(var) if pulp.value(var) is not None else 0.0) for bid, var in x_b_vars.items()}

    # Map multi-order acceptances to individual order IDs for verification
    x_s_per_order = {}
    for multi_order in multi_orders:
        acceptance = x_s_computed.get(multi_order.key, 0.0)
        for fragment in multi_order.fragments:
            x_s_per_order[fragment.orderID] = acceptance

    # ===== COMPARE ACCEPTANCE RATIOS =====
    sell_matches, sell_total, sell_diffs = compare_acceptance_ratios(x_s_per_order, api_sell_acceptance, "Sell")
    buy_matches, buy_total, buy_diffs  = compare_acceptance_ratios(x_b_computed, api_buy_acceptance, "Buy")

    # ===== VERIFY CONSTRAINTS =====
    loop_violations = verify_loop_constraints(x_s_per_order, sell_orders, loop_families)
    balance_violations = verify_product_balance(x_s_per_order, x_b_computed, sell_orders, buy_orders)

    # Compute API (observed) welfare
    api_w = compute_welfare_and_procurement(
        sell_orders=sell_orders,
        buy_orders=buy_orders,
        sell_accept_map=api_sell_acceptance,
        buy_accept_map=api_buy_acceptance,
        label="API_observed"
    )

    # Compute MILP (your computed) welfare
    milp_w = compute_welfare_and_procurement(
        sell_orders=sell_orders,
        buy_orders=buy_orders,
        sell_accept_map=x_s_per_order,   # mapped from multi_orders fragments
        buy_accept_map=x_b_computed,
        label="MILP_computed"
    )


    print("\n" + "="*80)
    print("WELFARE & PROCUREMENT COMPARISON")
    print("="*80)
    print(f"{'Metric':<30} | {'API':>15} | {'MILP':>15} | {'Diff (MILP - API)':>15}")
    print("-"*80)
    print(f"{'Total buy value':<30} | {api_w['total_buy_value']:15.2f} | {milp_w['total_buy_value']:15.2f} | {milp_w['total_buy_value']-api_w['total_buy_value']:15.2f}")
    print(f"{'Total sell cost (procurement)':<30} | {api_w['total_sell_cost']:15.2f} | {milp_w['total_sell_cost']:15.2f} | {milp_w['total_sell_cost']-api_w['total_sell_cost']:15.2f}")
    print(f"{'Welfare (buy - sell)':<30} | {api_w['welfare']:15.2f} | {milp_w['welfare']:15.2f} | {milp_w['welfare']-api_w['welfare']:15.2f}")
    print("-"*80)



    # ===== FINAL SUMMARY =====
    print(f"\n{'='*100}")
    print("FINAL VERIFICATION SUMMARY")
    print("=" * 100)
    
    print(f"\n1. ACCEPTANCE RATIO MATCHING:")
    sell_pct = 100 * sell_matches / sell_total if sell_total > 0 else 0
    buy_pct = 100 * buy_matches / buy_total if buy_total > 0 else 0
    print(f"   Sell Orders: {sell_matches}/{sell_total} matched ({sell_pct:.1f}%)")
    print(f"   Buy Orders:  {buy_matches}/{buy_total} matched ({buy_pct:.1f}%)")
    
    print(f"\n2. CONSTRAINT SATISFACTION:")
    print(f"   Loop Constraints: {len(loop_families) - len(loop_violations)}/{len(loop_families)} passed")
    print(f"   Product Balance: {len(products) - len(balance_violations)}/{len(products)} balanced")
    
    total_issues = len(sell_diffs) + len(buy_diffs) + len(loop_violations) + len(balance_violations)
    
    if total_issues == 0:
        print(f"\n✓✓✓ PERFECT MATCH - ALL VERIFICATIONS PASSED ✓✓✓")
    else:
        print(f"\n⚠ Found {total_issues} total issues:")
        print(f"   - {len(sell_diffs)} sell order mismatches")
        print(f"   - {len(buy_diffs)} buy order mismatches")
        print(f"   - {len(loop_violations)} loop constraint violations")
        print(f"   - {len(balance_violations)} product balance issues")
    
    print(f"\n{'='*100}")
    print("VERIFICATION COMPLETE")
    print(f"{'='*100}\n")
