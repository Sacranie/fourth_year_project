from typing import Dict, List, Tuple, Set, Optional
from collections import defaultdict
from decimal import Decimal, getcontext
import json
import urllib.request
import urllib.parse
import pulp
import matplotlib.pyplot as plt
import numpy as np
import pickle
from concurrent.futures import ProcessPoolExecutor, as_completed

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


def process_sell_orders(sell_records: List[Dict], auction_unit: str) -> Tuple[List, List[SellOrder], List[SellOrder]]:
    """
    Build logical SellOrder objects from raw API rows.
    Separates orders into fixed orders and changing orders (those matching auction_unit).
    
    Returns:
        (multi_orders, all_sell_orders, changing_sell_orders)
    """
    all_sell_orders = []
    changing_sell_orders = []
    rejected_count = 0
    executed_count = 0
    partial_count = 0

    for row in sell_records:
        status = str(row.get("status", "")).strip().upper()
        order_id = int(row.get("orderID", 0))

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
        
        if auction_unit != sell_order.auctionUnit:
            all_sell_orders.append(sell_order)
        else:
            changing_sell_orders.append(sell_order)

    multi_orders = group_multi_product_orders(all_sell_orders)

    # We need to now iterate through the multi_orders and adjust the acceptance ratios
    # If they were rejected we should se to 1 and if they were executed or partially executed we should set to 1
    
    for orders in multi_orders:
        if not orders.is_accepted:
            orders.acceptance = 1.0
    


    return multi_orders, all_sell_orders, changing_sell_orders


def process_buy_orders(buy_records: List[Dict], auction_id: Optional[int] = None) -> List[BuyOrder]:
    """
    Build BuyOrder objects from raw API records.
    
    Args:
        buy_records: Raw records from the NESO API
        auction_id: Fallback auction ID if row lacks auctionID
    
    Returns:
        List of BuyOrder objects
    """
    all_buy_orders = []

    for row in buy_records:
        status = str(row.get("status", "")).strip().upper()
        order_id = row.get("orderID", 0)

        if status == "REJECTED":
            min_acceptance = 1.0 # force reject
        else:
            min_acceptance = row.get("acceptanceRatio", 0.0)

        raw = row.get("paradoxicallyAcceptanceAllowed", "false")
        paradoxical = (str(raw).lower() == "true")

        buy_order = BuyOrder(
            auctionID=auction_id,
            orderID=order_id,
            service=str(row.get("service", "") or ""),
            auctionProduct=str(row.get("auctionProduct", "") or ""),
            deliveryStart=str(row.get("deliveryStart", "") or ""),
            deliveryEnd=str(row.get("deliveryEnd", "") or ""),
            quantity=float(row.get("quantity", 0.0) or 0.0),
            price=float(row.get("price", 0.0) or 0.0),
            paradoxical=paradoxical,
            min_acceptance_ratio=min_acceptance,
        )

        all_buy_orders.append(buy_order)

    print(f"\nBuy Orders: {len(all_buy_orders)} loaded")
    return all_buy_orders

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


def process_single_auction(auction_id: int, auction_unit: str, alpha_vals: List[float], beta_vals: List[float]) -> Tuple[set, dict]:
    """
    Process a single auction and return the products and price differences.
    This function is designed to be run in parallel.
    
    Returns:
        (products_set, price_diffs_dict) for this auction
    """
    print(f"\n\n=== Processing Auction ID: {auction_id} ===")
    TEST_LIMIT = 1000000
    
    # Local price_diffs for this auction
    local_price_diffs = defaultdict(list)
    local_products = set()

    sell_records = load_orders_for_auction(SELL_URL, auction_id=auction_id, limit=TEST_LIMIT)
    buy_records = load_orders_for_auction(BUY_URL, auction_id=auction_id, limit=TEST_LIMIT)

    # Process orders
    multi_orders, sell_orders, changing_sell_orders = process_sell_orders(sell_records, auction_unit=auction_unit)
    buy_orders = process_buy_orders(buy_records, auction_id=auction_id)

    if not changing_sell_orders:
        print(f"No changing sell orders for auction unit {auction_unit} in Auction {auction_id}, skipping.")
        return local_products, dict(local_price_diffs)
    
    # Build baskets and extract loop families
    baskets = build_baskets_from_orders(sell_orders, sell_records)
    
    # Extract unique products
    products = set(o.auctionProduct for o in sell_orders) | set(o.auctionProduct for o in buy_orders)
    local_products.update(products)

    products_sold = set(o.auctionProduct for o in changing_sell_orders)

    # Extract unique units for capacity registry
    units = set(order.auctionUnit for order in sell_orders)
    unit_capacity_registry = {unit: 1e9 for unit in units}
    unit_capacity_registry[auction_unit] = 1e9

    # Store original values to avoid compounding modifications
    original_values = [(float(order.quantity), float(order.price)) for order in changing_sell_orders]

    backend = PulpSolverBackend(msg=0, time_limit=600)
    volume_milp = VolumeMILP(backend=backend)

    original_mcp = {}

    # Iterate through alpha/beta combinations
    for a in alpha_vals:
        for b in beta_vals:
            print(f"[Auction {auction_id}] Testing parameters: alpha={a}, beta={b}")

            # Apply multipliers to changing orders
            for i, order in enumerate(changing_sell_orders):
                order.quantity = original_values[i][0] * a
                order.price = original_values[i][1] * b
            
            multi_changing_sell_order = group_multi_product_orders(changing_sell_orders)
            for orders in multi_changing_sell_order:
                if not orders.is_accepted:
                    orders.acceptance = 1.0
            
            # Combine with fixed orders
            test_multi_orders = multi_orders + multi_changing_sell_order
            changing_baskets = build_baskets_from_orders(changing_sell_orders, sell_records)
            test_baskets = baskets + changing_baskets
    
            # Solve
            data = volume_milp.solve_with_pricing_loop(
                products=list(products),
                buy_orders=buy_orders,
                sell_orders=test_multi_orders,
                baskets=test_baskets,
                unit_capacity_registry=unit_capacity_registry
            )

            if not data["final"]:
                print(f"Auction {auction_id} failed to find valid clearing for alpha={a}, beta={b}")
                continue

            prices_unrounded = data["prices_unrounded"]

            # Store baseline MCP at (1,1)
            if a == 1 and b == 1:
                original_mcp = dict(prices_unrounded)
                print(f"[Auction {auction_id}] Baseline MCP captured for {len(original_mcp)} product-windows")
                continue

            # Calculate absolute price difference for other combinations
            for key, mcp in prices_unrounded.items():
                product = key[0]
                is_accepted_product = any(
                    multi.is_accepted and any(frag.auctionProduct == product for frag in multi.fragments)
                    for multi in multi_changing_sell_order
                )
                if product in products_sold and is_accepted_product:
                    original_price = original_mcp.get(key, 0.0)
                    price_diff = mcp - original_price
                    local_price_diffs[(product, a, b)].append(price_diff)
    
    return local_products, dict(local_price_diffs)


if __name__ == "__main__":
    auction_ids = [1112, 1114, 1116, 1118, 1120, 1122, 1124, 1126, 1128, 1130, 1132, 1134, 1136, 1138, 1140, 1142, 1144, 1146, 1148, 1150, 1152, 1154, 1156, 1158, 1189, 1222, 1224, 1226, 1228, 1230, 1232, 1234, 1236, 1255, 1257, 1259, 1261, 1263, 1288, 1290, 1292, 1294, 1296, 1298, 1300]
    AUCTION_UNIT = "GSET-02"  # The auction unit we want to vary
    
    alpha = [1, 1.5, 2, 2.5, 3.0, 3.5, 4.0]
    beta  = [1, 1.2, 1.4, 1.6, 2]
    
    # Global tracking across all auctions
    all_products = set()
    price_diffs = defaultdict(list)
    
    # Number of parallel workers (adjust based on your CPU cores)
    MAX_WORKERS = 4  # Set to number of CPU cores, or fewer if memory is a concern
    
    print(f"Processing {len(auction_ids)} auctions with {MAX_WORKERS} parallel workers...")
    
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit all auction jobs
        future_to_auction = {
            executor.submit(process_single_auction, auction_id, AUCTION_UNIT, alpha, beta): auction_id
            for auction_id in auction_ids
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_auction):
            auction_id = future_to_auction[future]
            try:
                local_products, local_price_diffs = future.result()
                
                # Merge results into global tracking
                all_products.update(local_products)
                for key, diffs in local_price_diffs.items():
                    price_diffs[key].extend(diffs)
                
                print(f"Completed auction {auction_id}")
            except Exception as e:
                print(f"Auction {auction_id} generated an exception: {e}")
    
    print(f"\nAll auctions processed. Total products: {len(all_products)}")
    
    # Save results to pickle file for later use
    with open("price_diffs_cache.pkl", "wb") as f:
        pickle.dump({
            'all_products': all_products,
            'price_diffs': dict(price_diffs)
        }, f)
    print("Results saved to price_diffs_cache.pkl")
    
    # Now plot the results as 2D heatmaps for each product
    # This shows the feasible regions where alpha/beta changes have little effect on MCP
    
    alpha_vals = [1, 1.5, 2, 2.5, 3.0, 3.5, 4.0]
    beta_vals = [1, 1.2, 1.4, 1.6, 2]
    
    for product in all_products:
        # Build a matrix of percentage of times being a price maker (|MCP change| >= £0.50)
        heatmap_data = np.zeros((len(alpha_vals), len(beta_vals)))
        
        for i, a in enumerate(alpha_vals):
            for j, b in enumerate(beta_vals):
                if a == 1 and b == 1:
                    heatmap_data[i, j] = 0  # Baseline
                    continue
                key = (product, a, b)
                if price_diffs[key]:
                    # Count how many times |MCP change| >= £0.50 (price maker)
                    price_maker_count = sum(1 for diff in price_diffs[key] if abs(diff) >= 0.5)
                    total_count = len(price_diffs[key])
                    heatmap_data[i, j] = (price_maker_count / total_count) * 100  # Percentage
                else:
                    heatmap_data[i, j] = np.nan
        
        plt.figure(figsize=(10, 8))
        
        # Create heatmap with sequential colormap (0% to 100%)
        im = plt.imshow(heatmap_data, cmap='YlOrRd', aspect='auto', origin='lower',
                        vmin=0, vmax=100)
        plt.colorbar(im, label='Price Maker Frequency (%)')
        
        # Set axis labels
        plt.xticks(range(len(beta_vals)), beta_vals)
        plt.yticks(range(len(alpha_vals)), alpha_vals)
        plt.xlabel('Beta (Price Multiplier)')
        plt.ylabel('Alpha (Quantity Multiplier)')
        plt.title(f'Price Maker Frequency for Product: {product}\n(|MCP Change| >= £0.50)')
        
        # Add text annotations
        for i in range(len(alpha_vals)):
            for j in range(len(beta_vals)):
                if not np.isnan(heatmap_data[i, j]):
                    text = plt.text(j, i, f'{heatmap_data[i, j]:.1f}%',
                                   ha='center', va='center', color='black', fontsize=8)
        
        plt.tight_layout()
        safe_product_name = product.replace('/', '_').replace('\\', '_').replace(' ', '_')
        plt.savefig(f"mcp_heatmap_{safe_product_name}.png", dpi=150)
        plt.close()
        print(f"Saved heatmap for product: {product}")
    
    # Also create a scatter plot showing all products together
    plt.figure(figsize=(12, 8))
    
    for product in all_products:
        xs = []
        ys = []
        for (prod, a, b), diffs in price_diffs.items():
            if prod == product and diffs:
                xs.append(a * b)  # Combined multiplier effect
                # Calculate price maker percentage
                price_maker_count = sum(1 for diff in diffs if abs(diff) >= 0.5)
                ys.append((price_maker_count / len(diffs)) * 100)
        if xs:
            plt.scatter(xs, ys, label=product, alpha=0.7)
    
    plt.axhline(y=50, color='red', linestyle=':', alpha=0.5, label='50% threshold')
    plt.xlabel('Alpha × Beta (Combined Multiplier)')
    plt.ylabel('Price Maker Frequency (%)')
    plt.title('Price Maker Frequency vs Bid Aggressiveness\n(Price Maker = |MCP Change| >= £0.50)')
    plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize=8)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    
    plt.savefig("mcp_sensitivity_all_products.png", dpi=150)
    plt.close()
    print("Saved combined scatter plot")
