from typing import Dict, List, Tuple, Set
from collections import defaultdict
from decimal import Decimal, getcontext
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

# A function to calculate the total accuracy of the LP prices compared to API 
def accuracy(computed_prices: Dict, expected_prices: Dict):

    windows = set()
    for (product, window) in list(expected_prices.keys()) + list(computed_prices.keys()):
        windows.add(window)

    total_matches = 0
    total_compared = 0

    for window in sorted(windows):
        window_expected = {p: price for (p, w), price in expected_prices.items() if w == window}
        window_computed = {p: price for (p, w), price in computed_prices.items() if w == window}
        all_products = sorted(set(list(window_expected.keys()) + list(window_computed.keys())))
        if not all_products:
            continue

        for product in all_products:
            exp_price = window_expected.get(product)
            comp_price = window_computed.get(product)
            if exp_price is not None and comp_price is not None:
                diff = abs(Decimal(str(exp_price)) - Decimal(str(comp_price)))
                match = diff <= Decimal("0.01")
                total_compared += 1
                if match:
                    total_matches += 1

    if total_compared > 0:
        match_pct = 100 * total_matches / total_compared

    return match_pct

def procurement_cost_calculation(computed_prices: Dict, all_sell_orders: List[SellOrder], x_s_observed: Dict[int, float]) -> float:
    total_cost = 0.0
    for order in all_sell_orders:
        order_id = order.orderID
        acceptance_ratio = x_s_observed.get(order_id, 0.0)
        product = order.auctionProduct
        window = (order.deliveryStart, order.deliveryEnd)
        price = computed_prices.get((product, window), 0.0)
        quantity = order.quantity
        cost = acceptance_ratio * quantity * price
        total_cost += cost
    return total_cost

# calculate the procurme cost from API prices
def procurement_cost_API(all_sell_orders: List[SellOrder], expected_prices: Dict) -> float:
    total_cost = 0.0
    for order in all_sell_orders:
        status = order.status
        if status == "REJECTED":
            continue
        acceptance_ratio = order.min_acceptance_ratio
        price = expected_prices.get((order.auctionProduct, (order.deliveryStart, order.deliveryEnd)), 0.0)
        quantity = order.quantity
        cost = acceptance_ratio * quantity * price
        total_cost += cost
    return total_cost
# ---------------------------------------
# Main execution
# ---------------------------------------
if __name__ == "__main__":

    match_percentages = []
    procurement_costs = []
    auction_ids = [1112, 1114, 1116, 1118, 1120, 1122, 1124, 1126, 1128, 1130, 1132, 1134, 1136, 1138, 1140, 1142, 1144, 1146, 1148, 1150, 1152, 1154, 1156, 1158, 1189, 1222, 1224, 1226, 1228, 1230, 1232, 1234, 1236, 1255, 1257, 1259, 1261, 1263, 1288, 1290, 1292, 1294, 1296, 1298, 1300]
    auction_index = 0
    for auction_id in auction_ids:
        AUCTION_ID = auction_id
        TEST_LIMIT = 1000000

        data = process_auction_data(SELL_URL, BUY_URL, AUCTION_ID, TEST_LIMIT)

        # Group orders
        grouped_orders = group_multi_product_orders(data["sell_orders"])
        
        # Organize orders by basket (needed for surplus analysis)
        orders_by_basket = defaultdict(list)
        for order in grouped_orders:
            orders_by_basket[order.basket_id].append(order)
        
        # Run LP to get computed prices
        backend = PulpSolverBackend(msg=0)
        global_lp = GlobalPricingLP(backend)
        computed_prices, problem, status = global_lp.solve(
            all_sell_orders=data["sell_orders"],
            x_s_val=data["x_s_observed"],
            basket_to_loop=data["basket_to_loop"],
        )

        # ===== PRICE COMPARISON =====
        match_pct = accuracy(computed_prices, data["expected_prices"])
        procurement_cost_API_value = procurement_cost_API(data["sell_orders"], data["expected_prices"])
        print(procurement_cost_API_value)
        procurement_cost_LP = procurement_cost_calculation(computed_prices, data["sell_orders"], data["x_s_observed"])
        print(procurement_cost_LP)
        match_percentages.append((auction_index, match_pct))
        procurement_costs.append((auction_index, procurement_cost_API_value, procurement_cost_LP))
        auction_index += 1
        if AUCTION_ID == 1146:
            print_price_comparison(computed_prices, data["expected_prices"])


    # Plot a graph of accuracy over auction IDs
    import matplotlib.pyplot as plt
    auction_ids = [item[0] for item in match_percentages]
    accuracies = [item[1] for item in match_percentages]
    plt.figure(figsize=(12, 6))
    plt.plot(auction_ids, accuracies, marker='o')
    plt.title('LP Price Accuracy vs Auction ID')
    plt.xlabel('Auction ID')
    plt.ylabel('Accuracy (%)')
    plt.grid(True)
    plt.ylim(0, 100)
    plt.xticks(auction_ids, rotation=45)
    plt.tight_layout()
    plt.show()

    # Plot a graph of procurement costs over auction IDs
    procurement_auction_ids = [item[0] for item in procurement_costs]
    procurement_api_costs = [item[1] for item in procurement_costs]
    procurement_lp_costs = [item[2] for item in procurement_costs]
    percentage = [(api - lp) / api * 100 if api != 0 else 0.0 for api, lp in zip(procurement_api_costs, procurement_lp_costs)]
    plt.figure(figsize=(12, 6))
    plt.plot(procurement_auction_ids, percentage, marker='x', label='API Procurement Cost')
    plt.title('Procurement Cost Reduction vs Auction ID')
    plt.xlabel('Auction ID')
    plt.ylabel('Reduction (%)')
    plt.grid(True)
    plt.xticks(procurement_auction_ids, rotation=45)
    plt.legend()
    plt.tight_layout()
    plt.show()

    # percentage already computed as: (lp - api) / api * 100
    # since LP < API, values will be negative — flip sign for readability if you want
    percentage_reduction = [p for p in percentage if p is not None]

    plt.figure(figsize=(8, 5))
    plt.hist(percentage_reduction, bins=25, edgecolor='black')
    plt.title('Distribution of Procurement Cost Reduction (API → LP)')
    plt.xlabel('Procurement Cost Reduction (%)')
    plt.ylabel('Frequency')
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.show()