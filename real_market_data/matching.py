from eac.models import SellOrder, BuyOrder, Basket
from collections import defaultdict
from typing import Dict, List, Tuple
import json
import urllib.request
from eac.orchestrator import run_market
import time


def load_sample_records(base_url: str, limit: int = 500, sort_field: str = "deliveryStart") -> List[Dict]:
    """
    Load a sample of records from the API, sorted by delivery start date.
    
    Args:
        base_url: Base URL with resource_id already included
        limit: Number of records to fetch (default 500)
        sort_field: Field to sort by (default "deliveryStart")
        
    Returns:
        List of records from the API
    """
    # Add sort parameter to get earliest delivery dates first
    # URL encode the space as %20
    url = f"{base_url}&limit={limit}&sort={sort_field}%20asc"
    
    try:
        with urllib.request.urlopen(url) as response:
            data = json.load(response)
    except Exception as e:
        print(f"Error fetching data: {e}")
        return []
    
    records = data.get("result", {}).get("records", [])
    print(f"Loaded {len(records)} records (sorted by {sort_field})")
    
    return records


# API endpoints

SELL_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=13b511df-d6ec-4143-afb1-0ecc6fd19810"
BUY_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=1cf68f59-8eb8-4f1d-bccf-11b5a47b24e5"

# Configuration: adjust limit for testing
TEST_LIMIT = 100  # Change this to test with more/fewer records

# Load sample records sorted by delivery date
print(f"Loading first {TEST_LIMIT} sell orders (sorted by delivery date)...")
sell_records = load_sample_records(SELL_URL, limit=TEST_LIMIT)

print(f"\nLoading first {TEST_LIMIT} buy orders (sorted by delivery date)...")
buy_records = load_sample_records(BUY_URL, limit=TEST_LIMIT)

# Initialize data structures
time_graph = defaultdict(lambda: {
    "buys": [],
    "sells": [],
    "baskets": {},
    "products": set(),
    "expected_prices": {},
    "loop_baskets" : {}
})

unit_capacity_registry = {}

# Process sell orders
print("\nProcessing sell orders...")
for row in sell_records:
    sell_order = SellOrder(
        id=str(row.get("orderID")),
        basket=str(row.get("basketID")),
        qty={row.get("auctionProduct"): float(row.get("quantity", 0.0))},
        price=float(row.get("priceLimit", 0.0)),
        type=row.get("orderType", "parent").lower(),
        min_acceptance_ratio=float(row.get("acceptanceRatio", 0.0))
    )

    key = (row.get("deliveryStart"), row.get("deliveryEnd"))

    time_graph[key]["sells"].append(sell_order)

    basket_registry = time_graph[key]["baskets"]

    basket_id = str(row.get("basketID"))
    if basket_id not in basket_registry:
        
        if row.get("loopedBasketID") and row.get("loopedBasketID") in time_graph[key]["loop_baskets"]:
            loop_basket_id = time_graph[key]["loop_baskets"][row.get("loopedBasketID")]
        else:
            time_graph[key]["loop_baskets"][row.get("loopedBasketID")] = basket_id
            loop_basket_id = None

        basket = Basket(
            id=basket_id,
            unit=str(row.get("auctionUnit")),
            concomitant=[],
            looped_to= loop_basket_id
        )
        basket_registry[basket_id] = basket
        # Register unit capacity to a default high value if not already set
        if str(row.get("auctionUnit")) not in unit_capacity_registry:
            unit_capacity_registry[str(row.get("auctionUnit"))] = 100000
    time_graph[key]["baskets"][basket_id] = basket_registry[basket_id]
    
        
    
    # Store expected clearing price
    product = row.get("auctionProduct")
    clearing_price = row.get("clearingPrice")
    if product and clearing_price is not None:
        time_graph[key]["expected_prices"][product] = float(clearing_price)
    
    # Track product
    if product:
        time_graph[key]["products"].add(product)

    
# Process buy orders
print("Processing buy orders...")
for row in buy_records:
    buy_order = BuyOrder(
        id=str(row["orderID"]),
        product=str(row["auctionProduct"]),
        price=float(row["clearingPrice"]),
        volume=float(row["quantity"]),
        family=str(row["substitutabilityFamily"]),
        paradoxical=bool(row.get("paradoxicallyAcceptanceAllowed", True)),
        min_acceptance_ratio=float(row.get("acceptanceRatio", 0.0))
    )
    
    # Get time key
    key = (row.get("deliveryStart"), row.get("deliveryEnd"))

    # Add to time bucket
    time_graph[key]["buys"].append(buy_order)
    
    # Track product
    product = row.get("auctionProduct")
    if product:
        time_graph[key]["products"].add(product)

# Run market clearing for each time period
print("\n" + "="*80)
print("RUNNING MARKET CLEARING")
print("="*80 + "\n")

for (start, end), bucket in sorted(time_graph.items()):
    buys = bucket["buys"]
    sells = bucket["sells"]
    baskets = bucket["baskets"]
    products = bucket["products"]
    expected_prices = bucket["expected_prices"]
    
    # Skip if no buy orders
    if not buys:
        print(f"Skipping period {start} to {end} - no buy orders")
        continue
    
    print(f"\n{'='*80}")
    print(f"Auction for delivery: {start} to {end}")
    print(f"{'='*80}")
    print(f"Buy orders: {len(buys)}")
    print(f"Sell orders: {len(sells)}")
    print(f"Baskets: {len(baskets)}")
    print(f"Products: {len(products)}")
    
    

    # Run market clearing
    results = run_market(
        buy_orders=buys,
        sell_orders=sells,
        products=products,
        baskets=baskets,
        unit_capacity_registry=unit_capacity_registry,
    )
    
    print(f"\nStatus: {results.get('milp_status')} / {results.get('prices_status')}")
    print(f"Final: {results.get('final')}")

    if results.get("prices_rounded"):
        computed_prices = results["prices_rounded"]
        
        print(f"\n{'='*80}")
        print("PRICE COMPARISON")
        print(f"{'='*80}")
        print(f"{'Product':<40} {'Expected':>12} {'Computed':>12} {'Match':>8}")
        print("-"*80)
        
        matches = 0
        total = 0
        
        for product in sorted(products):
            expected = expected_prices.get(product)
            computed = computed_prices.get(product)
            
            if expected is not None and computed is not None:
                match = "✓" if abs(expected - computed) < 0.01 else "✗"
                if abs(expected - computed) < 0.01:
                    matches += 1
                total += 1
                print(f"{product:<40} £{expected:>11.2f} £{computed:>11.2f} {match:>8}")
        
        print("-"*80)
        print(f"Match rate: {matches}/{total} ({100*matches/total if total > 0 else 0:.1f}%)")

    print()
    print("="*80)
    print("MARKET CLEARING COMPLETE")
    print("="*80)