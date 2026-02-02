from typing import Dict, List, Tuple, Set, Optional
from collections import defaultdict
from decimal import Decimal, getcontext
import json
import urllib.request
import urllib.parse
import pulp
import math
import numpy as np
import matplotlib.pyplot as plt

from power_export import PowerExport
from battery import VolkanBattery
from eac.models import MultiProductOrder, SellOrder, BuyOrder, Basket
from eac.multi_product_orders import group_multi_product_orders
from eac.Volume import VolumeMILP
from eac.solver import PulpSolverBackend
from eac.Validators import validate_unit_capacity, build_loop_families


# NESO API endpoints
SELL_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=13b511df-d6ec-4143-afb1-0ecc6fd19810"
BUY_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=1cf68f59-8eb8-4f1d-bccf-11b5a47b24e5"

# ELEXON API endpoints
ELEXON_BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1/system/frequency"

def frequency_data(start_time: str, end_time: str) -> List[Tuple[str, float]]:
    """
    Fetch frequency data from ELEXON API for a given time range.
    
    Args:
        start_time: ISO 8601 formatted start time (e.g., '2025-03-31T22:00:00')
        end_time: ISO 8601 formatted end time (e.g., '2025-04-07T22:00:00')
    
    Returns:
        List of (timestamp, frequency) tuples
    """
    if not start_time.endswith('Z'):
        start_time = start_time + 'Z'
    if not end_time.endswith('Z'):
        end_time = end_time + 'Z'
    
    params = urllib.parse.urlencode({
        "from": start_time,
        "to": end_time,
        "format": "json"
    })
    url = f"{ELEXON_BASE_URL}?{params}"
    
    try:
        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read().decode())
            frequencies = []
            for entry in data.get('data', []):
                timestamp = entry.get('measurementTime', '')
                freq = float(entry.get('frequency', 50.0))
                frequencies.append((timestamp, freq))
            return frequencies
    except Exception as e:
        print(f"Error fetching frequency data: {e}")
        return []

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
    # If they were rejected we should set to 1 and if they were executed or partially executed we should set to 1
    
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

def degradation_model(battery: VolkanBattery, multi_product_orders: List[MultiProductOrder], x_s_computed: Dict) -> float:
    
    power_profile = build_power_profile_from_orders(multi_product_orders, x_s_computed)
    
    if len(power_profile) == 0:
        return 0.0
    
    # Store initial state before simulation (preserving all battery state variables)
    initial_soh = battery.soh if battery.soh is not None else battery.settings['SOH0']
    initial_energy = battery.energy if battery.energy is not None else battery.settings['E0']
    initial_temp = battery.temp if battery.temp is not None else battery.settings['Tk0']
    
    # Pass all current state variables to simulate() so they don't reset
    battery.simulate(power_profile, energy=initial_energy, temp=initial_temp, soh=initial_soh)
    
    # Calculate SOH loss
    delta_soh = initial_soh - battery.soh_trajectory[-1]
    
    # Convert SOH loss to cost
    # Cost = delta_SOH * price_per_kWh_capacity * nominal_capacity
    degradation_cost = delta_soh * battery.settings['price_kWhcap'] * battery.settings['Enom']
    
    return degradation_cost


def build_power_profile_from_orders(multi_product_orders: List[MultiProductOrder], x_s_computed: Dict) -> np.ndarray:

    power_by_timestamp = {}  # timestamp -> power (kW)
    
    for mpo in multi_product_orders:
        # Use SOLVED acceptance from x_s_computed
        acceptance = x_s_computed.get(mpo.key, 0.0)
        if acceptance <= 0:
            continue
            
        for fragment in mpo.fragments:
            product = fragment.auctionProduct.upper()
            start_time = fragment.deliveryStart
            end_time = fragment.deliveryEnd
            quantity_mw = fragment.quantity * acceptance  # MW
            
            # Fetch frequency data for this delivery window
            frequencies = frequency_data(start_time, end_time)
            
            # Get power export function for this product type
            power_export = PowerExport(product)
            power_export_function = power_export.get_power_export_function()
            maximum_ratio = 0.0
            max_power = 0.0
            for timestamp, freq in frequencies:
                # Calculate export ratio based on frequency deviation
                export_ratio = power_export_function(freq - 50.0)
                maximum_ratio = max(maximum_ratio, export_ratio)
                
                # Power in kW (convert from MW)
                # DCL/DML/DRL = Low frequency -> discharge (negative power)
                # DCH/DMH/DRH = High frequency -> charge (positive power)
                power_kw = quantity_mw * 1000 * export_ratio  # Convert MW to kW
                max_power = max(max_power, power_kw)
                
                # Determine sign based on product type and frequency
                if product in {'DCL', 'DML', 'DRL'}:
                    power_kw = -power_kw
                
                # Accumulate power at this timestamp
                if timestamp in power_by_timestamp:
                    power_by_timestamp[timestamp] += power_kw
                else:
                    power_by_timestamp[timestamp] = power_kw
    
    # Convert to sorted array
    if not power_by_timestamp:
        return np.array([])
    
    sorted_timestamps = sorted(power_by_timestamp.keys())
    power_profile = np.array([power_by_timestamp[ts] for ts in sorted_timestamps])
    print(f"Maximum Ratio observed in power profile: {maximum_ratio:.4f}")
    print(f"Maximum Power observed in power profile: {max_power:.2f} kW")
    
    return power_profile

if __name__ == "__main__":
    auction_ids = [1112, 1114, 1116, 1118, 1120, 1122, 1124, 1126, 1128, 1130, 1132, 1134, 1136, 1138, 1140, 1142, 1144, 1146, 1148, 1150, 1152, 1154, 1156, 1158, 1189, 1222, 1224, 1226, 1228, 1230, 1232, 1234, 1236, 1255, 1257, 1259, 1261, 1263, 1288, 1290, 1292, 1294, 1296, 1298, 1300]
    welfare = []
    AUCTION_UNIT = "GSET-02"  # The auction unit we want to vary
    alpha = [1, 1.2, 1.4, 1.6, 1.8, 2.0]
    beta  = [1]
    profit_results = defaultdict(list)

    for a in alpha:
        for b in beta:
            # Initialize VolkanBattery with degradation model parameters
            battery = VolkanBattery()
            battery.populate_with_volkan_parameters(data_location='data/')
            for auction_index, auction_id in enumerate(auction_ids):
                print(f"\n\n=== Processing Auction ID: {auction_id} ===")
                if battery.soh is not None:
                    print(f"The Battery SOH before auction {auction_id}: {battery.soh:.4f}")
                else:
                    print(f"The Battery SOH before auction {auction_id}: Not Set")
                AUCTION_ID = auction_id
                TEST_LIMIT = 1000000

                sell_records = load_orders_for_auction(SELL_URL, auction_id=AUCTION_ID, limit=TEST_LIMIT)
                buy_records = load_orders_for_auction(BUY_URL, auction_id=AUCTION_ID, limit=TEST_LIMIT)

                # Process orders
                multi_orders, sell_orders, changing_sell_orders = process_sell_orders(sell_records, auction_unit=AUCTION_UNIT)
                buy_orders = process_buy_orders(buy_records, auction_id=AUCTION_ID)
                
                # Skip auctions with no changing orders
                if not changing_sell_orders:
                    print(f"No changing sell orders for auction unit {AUCTION_UNIT} in auction {auction_id}. Skipping.")
                    continue

                # Build baskets and extract loop families (pass raw sell_records to populate concomitant/loop info)
                baskets = build_baskets_from_orders(sell_orders, sell_records)
                
                # Extract unique products
                products = set(o.auctionProduct for o in sell_orders) | set(o.auctionProduct for o in buy_orders)

                # Extract unique units for capacity registry
                units = set(order.auctionUnit for order in sell_orders)
                unit_capacity_registry = {unit: 1e9 for unit in units}  # Set very high capacity for each unit

                # add unit capacity for the auction unit we are testing
                unit_capacity_registry[AUCTION_UNIT] = 1e9  # Set very high capacity for the test unit

                # Store original values to avoid compounding modifications
                original_values = []
                for order in changing_sell_orders:
                    order.quantity = float(order.quantity)
                    order.price = float(order.price)
                    original_values.append((order.quantity, order.price))
                
                backend = PulpSolverBackend(msg=0, time_limit=600)
                volume_milp = VolumeMILP(backend=backend)
                print(f"\nTesting parameters: alpha={a}, beta={b}")

                # Reset to original values then apply multipliers
                for idx, order in enumerate(changing_sell_orders):
                    order.quantity = original_values[idx][0] * a
                    order.price = original_values[idx][1] * b
            
                multi_changing_sell_order = group_multi_product_orders(changing_sell_orders)
                        
                # Apply the same acceptance treatment as fixed orders
                for orders in multi_changing_sell_order:
                    if not orders.is_accepted:
                        orders.acceptance = 1.0
                        
                multi_orders.extend(multi_changing_sell_order)

                changing_baskets = build_baskets_from_orders(changing_sell_orders, sell_records)

                baskets.extend(changing_baskets)
                
                # Build and solve
                data = volume_milp.solve_with_pricing_loop(
                    products=list(products),
                    buy_orders=buy_orders,
                    sell_orders=multi_orders,
                    baskets=baskets,
                    unit_capacity_registry=unit_capacity_registry
                )

                
                if not data["final"]:
                    print(f"Auction {auction_id} failed to find valid clearing: {data.get('reason', 'Unknown')}")
                    # Still need to remove the orders we added before continuing
                    multi_orders = multi_orders[:-len(multi_changing_sell_order)]
                    baskets = baskets[:-len(changing_baskets)]
                    continue  # Skip to next alpha/beta combination

                # Calculate profit for the new orders
                x_s_computed = data["x_s"]
                prices_unrounded = data["prices_unrounded"]



                degradation_cost = degradation_model(battery, multi_changing_sell_order, x_s_computed)
                print(f"Degradation cost for auction {auction_id}: £{degradation_cost:.2f}")

                auction_revenue = 0.0  # Track total revenue for this auction
                for multi in multi_changing_sell_order:
                    acceptance = x_s_computed.get(multi.key, 0.0)
                    if acceptance > 0:
                        for frag in multi.fragments:
                            # Get MCP for this product in this window
                            # prices_unrounded keys are (product, window) tuples
                            mcp = prices_unrounded.get((frag.auctionProduct, multi.window), 0.0)
                            revenue = acceptance * frag.quantity * mcp
                            auction_revenue += revenue
                auction_profit = auction_revenue - degradation_cost
                print(f"Auction {auction_id} profit for alpha={a}, beta={b}: £{auction_profit:.2f} (Revenue: £{auction_revenue:.2f}, Degradation Cost: £{degradation_cost:.2f})")
                        
                # Add cumulative profit for this beta value
                if len(profit_results[a]) == 0:
                    profit_results[a].append(auction_profit)
                else:
                    profit_results[a].append(profit_results[a][-1] + auction_profit)
                        
                multi_orders = multi_orders[:-len(multi_changing_sell_order)]
                baskets = baskets[:-len(changing_baskets)]

    for a in alpha:
        plt.plot(range(len(profit_results[a])), profit_results[a], label=f"Alpha={a}")
    plt.xlabel("Auction Index")
    plt.ylabel("Cumulative Profit (£)")
    plt.title(f"Cumulative Profit vs Auction Index for varying Alpha (Beta={beta[0]})")
    plt.legend()
    plt.show()