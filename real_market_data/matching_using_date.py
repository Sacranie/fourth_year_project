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


if __name__ == "__main__":
    auction_ids = [1112, 1114, 1116, 1118, 1120, 1122, 1124, 1126, 1128, 1130, 1132, 1134, 1136, 1138, 1140, 1142, 1144, 1146, 1148, 1150, 1152, 1154, 1156, 1158, 1189, 1222, 1224, 1226, 1228, 1230, 1232, 1234, 1236, 1255, 1257, 1259, 1261, 1263, 1288, 1290, 1292, 1294, 1296, 1298, 1300]
    match_percentages = []
    welfare = []
    auction_index = 0
    for auction_id in auction_ids:
        print(f"\n\n=== Processing Auction ID: {auction_id} ===")
        AUCTION_ID = auction_id
        TEST_LIMIT = 1000000

        sell_records = load_orders_for_auction(SELL_URL, auction_id=AUCTION_ID, limit=TEST_LIMIT)
        buy_records = load_orders_for_auction(BUY_URL, auction_id=AUCTION_ID, limit=TEST_LIMIT)

        # Process orders
        multi_orders, sell_orders, api_sell_acceptance = process_sell_orders(sell_records)
        buy_orders, api_buy_acceptance = process_buy_orders(buy_records, auction_id=AUCTION_ID)
        
        # Build baskets and extract loop families (pass raw sell_records to populate concomitant/loop info)
        baskets = build_baskets_from_orders(sell_orders, sell_records)
        
        # Extract unique products
        products = set(o.auctionProduct for o in sell_orders) | set(o.auctionProduct for o in buy_orders)

        # Extract unique units for capacity registry
        units = set(order.auctionUnit for order in sell_orders)
        unit_capacity_registry = {unit: 1e9 for unit in units}  # Set very high capacity for each unit

        
        backend = PulpSolverBackend(msg=0, time_limit=600)
        volume_milp = VolumeMILP(backend=backend)
        
        # Build and solve
        prob, x_b_vars, x_s_vars, y_parent_vars, _, _ = volume_milp.build_problem(
            products=list(products),
            buy_orders=buy_orders,
            sell_orders=multi_orders,
            baskets=baskets,
            unit_capacity_registry=unit_capacity_registry
        )
        
        status = backend.solve(prob)
        
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

        match_percentage = (sell_matches + buy_matches) / (sell_total + buy_total) * 100.0
        print(match_percentage)
        match_percentages.append(match_percentage)
        welfare.append((api_w, milp_w))
        auction_index += 1
    
    import matplotlib.pyplot as plt

    # Plot acceptance ratio match percentages
    plt.figure(figsize=(10, 6))
    plt.plot(range(len(auction_ids)), match_percentages, marker='o')
    plt.title('Acceptance Ratio Match Percentages per Auction')
    plt.xlabel('Auction Index')
    plt.ylabel('Match Percentage (%)')
    plt.ylim(0, 100)
    plt.grid(True)
    plt.show()

    # Plot percentage welfare change per auction (MILP vs API)
    welfare_changes = []

    for api_w, milp_w in welfare:
        if api_w['welfare'] != 0:
            change_pct = 100.0 * (milp_w['welfare'] - api_w['welfare']) / api_w['welfare']
        else:
            change_pct = 0.0
        welfare_changes.append(change_pct)

    plt.figure(figsize=(10, 6))
    plt.plot(range(len(auction_ids)), welfare_changes, marker='o')
    plt.axhline(0, linestyle='--')  # reference line
    plt.title('Percentage Welfare Change per Auction (MILP vs API)')
    plt.xlabel('Auction Index')
    plt.ylabel('Welfare Change (%)')
    plt.grid(True)
    plt.show()

    # Histogram of percentage welfare change
    plt.figure(figsize=(10, 6))
    plt.hist(welfare_changes, bins=20, edgecolor='black')
    plt.axvline(0, linestyle='--')
    plt.title('Histogram of Percentage Welfare Change (MILP vs API)')
    plt.xlabel('Welfare Change (%)')
    plt.ylabel('Frequency')
    plt.grid(True)
    plt.show()
