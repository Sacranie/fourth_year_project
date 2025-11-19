from eac.models import SellOrder, BuyOrder, Basket
from collections import defaultdict
from typing import Dict, List, Tuple
import json
import urllib.request
from eac.orchestrator import run_market


def load_orders_for_period(
    base_url: str,
    delivery_start: str,
    delivery_end: str,
    limit: int,
) -> List[Dict]:
    """
    Load all orders for a specific delivery period.
    
    Args:
        base_url: Base URL with resource_id
        delivery_start: Delivery start time (e.g., "2024-01-15T00:00:00")
        delivery_end: Delivery end time (e.g., "2024-01-15T01:00:00")
        limit: Max records to fetch
        
    Returns:
        List of records matching the delivery period
    """

    filters = json.dumps({
        "deliveryStart": delivery_start,
        "deliveryEnd": delivery_end
    })
    
    url = f"{base_url}&limit={limit}&filters={urllib.parse.quote(filters)}"
    
    try:
        with urllib.request.urlopen(url) as response:
            data = json.load(response)
    except Exception as e:
        print(f"Error fetching data: {e}")
        return []
    
    records = data.get("result", {}).get("records", [])
    
    return records


def process_orders(sell_url: str, buy_url: str, delivery_start: str, delivery_end: str, test_limit: int) -> None:

    sell_records = load_orders_for_period(sell_url, limit=test_limit, delivery_start = delivery_start, delivery_end = delivery_end)
    buy_records = load_orders_for_period(buy_url, limit=test_limit, delivery_start = delivery_start, delivery_end = delivery_end)


    # Initialize data structures
    buys = []
    sells = []
    basket_registry = {}
    products = set()
    expected_prices = {}
    loop_baskets = {}
    unit_capacity_registry = {}
    overholding = {}
    wellfare_sell_expected = 0.0
    welfare_buy_expected = 0.0

    # Process sell orders
    print("\nProcessing sell orders...")
    for row in sell_records:
        sell_order = SellOrder(
            id=str(row.get("orderID")),
            basket=str(row.get("basketID")),
            qty={row.get("auctionProduct"): float(row.get("quantity", 0.0))},
            price=float(row.get("clearingPrice", 0.0)),
            type=row.get("orderType", "parent").lower(),
            min_acceptance_ratio=float(row.get("acceptanceRatio", 0.0))
        )

        sells.append(sell_order)

        wellfare_sell_expected -= (float(row.get("acceptanceRatio", 0.0)) * float(row.get("quantity", 0.0)) * float(row.get("priceLimit", 0.0)))

        basket_id = str(row.get("basketID"))
        if basket_id not in basket_registry:
            
            if row.get("loopedBasketID") and row.get("loopedBasketID") in loop_baskets:
                loop_basket_id = loop_baskets[row.get("loopedBasketID")]
            else:
                loop_baskets[row.get("loopedBasketID")] = basket_id
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

        # Store expected clearing price
        product = row.get("auctionProduct")
        clearing_price = row.get("clearingPrice")
        if product and clearing_price is not None:
            expected_prices[product] = float(clearing_price)
        
        # Track product
        if product:
            products.add(product)
    
    # Process buy orders
    print("Processing buy orders...")
    for row in buy_records:
        buy_order = BuyOrder(
            id=str(row["orderID"]),
            product=str(row["auctionProduct"]),
            price=float(row["price"]),
            volume=float(row["quantity"]),
            family=str(row["substitutabilityFamily"]),
            paradoxical=bool(row.get("paradoxicallyAcceptanceAllowed", True)),
            min_acceptance_ratio=float(row.get("acceptanceRatio", 0.0))
        )

        # Add to time bucket
        buys.append(buy_order)

        welfare_buy_expected += (float(row.get("acceptanceRatio", 0.0)) * float(row.get("quantity", 0.0)) * float(row.get("price", 0.0)))
        
        # Track product
        product = row.get("auctionProduct")
        if product:
            products.add(product)

        if product:
            if product in overholding and 0.5 * float(row["quantity"]) > overholding[product]:
                overholding[product] = 0.5 * float(row["quantity"])
            elif product not in overholding:
                overholding[product] = 0.5 * float(row["quantity"])
    
    return buys, sells, basket_registry, products, expected_prices, unit_capacity_registry, overholding, wellfare_sell_expected, welfare_buy_expected
