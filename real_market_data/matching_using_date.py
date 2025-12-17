from eac.models import SellOrder, BuyOrder, Basket
from collections import defaultdict
from typing import Dict, List, Tuple, Set
import json
import urllib.request
from eac.orchestrator import run_market
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


def process_auction(sell_url: str, buy_url: str, auction_id: int, test_limit: int) -> Tuple[List[BuyOrder], List[SellOrder], Dict, Set, Dict, Dict, Dict, float, float, Dict, Dict, Dict]:
    """Load and process all orders for a specific auction ID."""
    sell_records = load_orders_for_auction(sell_url, auction_id=auction_id, limit=test_limit)
    buy_records = load_orders_for_auction(buy_url, auction_id=auction_id, limit=test_limit)

    buys = []
    sells = []
    basket_registry = {}
    products = set()
    expected_prices = {}
    expected_prices_by_window = defaultdict(dict)  # window -> product -> price
    unit_capacity_registry = {}
    overholding = {}
    welfare_sell_expected = 0.0
    welfare_buy_expected = 0.0
    welfare_sell_by_window = defaultdict(float)  # window -> welfare
    welfare_buy_by_window = defaultdict(float)  # window -> welfare

    unit_to_basket = defaultdict(list)
    basket_unit_mapping = {}
    basket_looped_to_mapping = defaultdict(list)

    # PASS 1: Process sell orders
    print("\nProcessing sell orders...")
    for row in sell_records:
        min_acceptance_ratio = float(row.get("acceptanceRatio", 0.0))
        if row.get("status") == "REJECTED":
            min_acceptance_ratio = 1.0  # Force rejection
        sell_order = SellOrder(
            auctionID=int(row.get("auctionID", 0)),
            registeredAuctionParticipant=str(row.get("registeredAuctionParticipant", "")),
            auctionUnit=str(row.get("auctionUnit", "")),
            basketID=int(row.get("basketID", 0)),
            service=str(row.get("service", "")),
            deliveryStart=str(row.get("deliveryStart", "")),
            deliveryEnd=str(row.get("deliveryEnd", "")),
            orderID=int(row.get("orderID", 0)),
            orderType=str(row.get("orderType", "parent")).lower(),
            auctionProduct=str(row.get("auctionProduct", "")),
            quantity=float(row.get("quantity", 0.0)),
            price=float(row.get("priceLimit", 0.0)),
            orderEntryTime=str(row.get("orderEntryTime", "")),
            product_id=str(row.get("productID", "")),
            min_acceptance_ratio=min_acceptance_ratio
        )

        sells.append(sell_order)
        acceptance_ratio = float(row.get("acceptanceRatio", 0.0))
        quantity = float(row.get("quantity", 0.0))
        price_limit = float(row.get("priceLimit", 0.0))
        welfare_sell_expected -= (acceptance_ratio * quantity * price_limit)

        basket_id = row.get("basketID")
        unit = row.get("auctionUnit")
        delivery_start = str(row.get("deliveryStart", ""))
        delivery_end = str(row.get("deliveryEnd", ""))
        window = (delivery_start, delivery_end)
        welfare_sell_by_window[window] -= (acceptance_ratio * quantity * price_limit)
        
        if basket_id not in basket_registry:
            if basket_id not in basket_unit_mapping:
                basket_unit_mapping[basket_id] = unit
                unit_to_basket[unit].append(basket_id)
                
                looped_basket_id = row.get("loopedBasketID")
                if looped_basket_id:
                    basket_looped_to_mapping[str(looped_basket_id)].append(basket_id)
                
                basket = Basket(
                    id=basket_id,
                    auctionID=int(row.get("auctionID", 0)),
                    unit=unit,
                    concomitant=[],
                    looped_to=None
                )
                basket_registry[basket_id] = basket
            
            if unit not in unit_capacity_registry:
                unit_capacity_registry[unit] = 100000

        product = row.get("auctionProduct")
        clearing_price = row.get("clearingPrice")
        if product and clearing_price is not None:
            expected_prices[product] = float(clearing_price)
            expected_prices_by_window[window][product] = float(clearing_price)
        
        if product:
            products.add(product)
    
    # PASS 2: Build concomitant relationships
    for unit, basket_ids in unit_to_basket.items():
        for basket_id in basket_ids:
            concomitant = [b for b in basket_ids if b != basket_id]
            basket_registry[basket_id].concomitant = concomitant
    
    # PASS 3: Build looped_to relationships
    for looped_to_id, basket_ids in basket_looped_to_mapping.items():
        for basket_id in basket_ids:
            if basket_id in basket_registry:
                # looped_to should be a single integer ID, not a list
                # Set it to the first looped_to_id in the group
                basket_registry[basket_id].looped_to = int(looped_to_id) if looped_to_id else None
                
    # Process buy orders
    print("Processing buy orders...")
    for row in buy_records:
        min_acceptance_ratio = float(row.get("acceptanceRatio", 0.0))
        if row.get("status") == "REJECTED":
            min_acceptance_ratio = 1.0  # Force rejection
        raw = str(row.get("paradoxicallyAcceptanceAllowed", "")).strip().lower()
        paradoxical = raw == "true"
        buy_order = BuyOrder(
            auctionID=int(row.get("auctionID", 0)),
            orderID=int(row.get("orderID", 0)),
            service=str(row.get("service", "")),
            auctionProduct=str(row.get("auctionProduct", "")),
            deliveryStart=str(row.get("deliveryStart", "")),
            deliveryEnd=str(row.get("deliveryEnd", "")),
            quantity=float(row.get("quantity", 0.0)),
            price=float(row.get("price", 0.0)),
            paradoxical=paradoxical,
            min_acceptance_ratio=min_acceptance_ratio
        )

        buys.append(buy_order)
        acceptance_ratio = float(row.get("acceptanceRatio", 0.0))
        quantity = float(row.get("quantity", 0.0))
        buy_price = float(row.get("price", 0.0))
        welfare_buy_expected += (acceptance_ratio * quantity * buy_price)
        
        delivery_start = str(row.get("deliveryStart", ""))
        delivery_end = str(row.get("deliveryEnd", ""))
        window = (delivery_start, delivery_end)
        welfare_buy_by_window[window] += (acceptance_ratio * quantity * buy_price)
        
        product = row.get("auctionProduct")
        if product:
            products.add(product)
    
    return buys, sells, basket_registry, products, expected_prices, unit_capacity_registry, overholding, welfare_sell_expected, welfare_buy_expected, dict(welfare_sell_by_window), dict(welfare_buy_by_window), dict(expected_prices_by_window)


if __name__ == "__main__":
    SELL_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=13b511df-d6ec-4143-afb1-0ecc6fd19810"
    BUY_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=1cf68f59-8eb8-4f1d-bccf-11b5a47b24e5"
    AUCTION_ID = 1112
    TEST_LIMIT = 1000000000
    
    print(f"\n{'='*100}")
    print(f"MARKET CLEARING FOR AUCTION ID: {AUCTION_ID}")
    print(f"{'='*100}\n")
    
    print(f"Loading orders for Auction {AUCTION_ID}...")
    buys, sells, basket_registry, products, expected_prices, unit_capacity_registry, overholding, welfare_sell_expected, welfare_buy_expected, welfare_sell_by_window, welfare_buy_by_window, expected_prices_by_window = process_auction(
        SELL_URL,
        BUY_URL,
        AUCTION_ID,
        TEST_LIMIT
    )
    
    print(f"✓ Loaded {len(buys)} buy orders and {len(sells)} sell orders")
    
    if not buys or not sells:
        print("No orders found for this auction")
        exit(1)
    
    # Extract unique delivery windows from loaded orders
    windows = set()
    for s in sells:
        windows.add((s.deliveryStart, s.deliveryEnd))
    windows = sorted(list(windows))
    
    print(f"✓ Found {len(windows)} delivery time window(s):\n")
    for idx, (start, end) in enumerate(windows, 1):
        print(f"  {idx}. {start} → {end}")
    
    print(f"\n{'='*100}")
    print(f"RUNNING MARKET CLEARING")
    print(f"(Orchestrator handles time window separation automatically)")
    print(f"{'='*100}\n")
    
    # Run market clearing - the orchestrator will handle time window separation automatically
    results = run_market(
        products=products,
        buy_orders=buys,
        sell_orders=sells,
        baskets=list(basket_registry.values()),
        unit_capacity_registry=unit_capacity_registry,
        overholding=overholding,
        msg=0
    )
    
    print(f"\n{'='*100}")
    print(f"MARKET CLEARING RESULTS")
    print(f"{'='*100}\n")
    
    # Extract window-specific results
    window_results = results.get("window_results", {})
    all_products_sorted = sorted(products)
    
    print(f"Overall Status: {'✓ FINAL' if results.get('final') else '✗ NOT FINAL'}\n")
    
    if not window_results:
        print("No window results found")
        exit(1)
    
    print(f"MARKET CLEARING PRICES (MCP) BY DELIVERY TIME WINDOW")
    print("-" * 100)
    
    if all_products_sorted and window_results:
        # Header
        header = "Delivery Window".ljust(50)
        for product in all_products_sorted:
            header += f" | {product:>8}"
        print(header)
        print("=" * len(header))
        
        # Data rows
        for window, window_res in sorted(window_results.items()):
            start, end = window
            row = f"{start} → {end}".ljust(50)
            
            computed_prices = window_res.get("prices_unrounded") or {}
            for product in all_products_sorted:
                price = (computed_prices.get(product) if computed_prices else None) or 0.0
                row += f" | £{price:>7.2f}"
            
            print(row)
        
        print("=" * len(header))
        
        # Final rounded prices per window
        if results.get("prices_rounded"):
            print(f"\nFINAL ROUNDED PRICES (per window):")
            final_prices_dict = results.get("prices_rounded") or {}
            for window in sorted(final_prices_dict.keys()):
                start, end = window
                print(f"  Window {start} → {end}:")
                for product in all_products_sorted:
                    window_prices = final_prices_dict.get(window, {})
                    price = window_prices.get(product, 0.0) if isinstance(window_prices, dict) else 0.0
                    print(f"    {product}: £{price:>7.2f}")
    
    # Detailed window analysis
    if window_results:
        print(f"\n\n{'='*100}")
        print("DETAILED WINDOW ANALYSIS")
        print(f"{'='*100}\n")
        
        for window, window_res in sorted(window_results.items()):
            start, end = window
            print(f"Window: {start} → {end}")
            print("-" * 100)
            
            milp_status = window_res.get("milp_status", "Unknown")
            prices_status = window_res.get("prices_status", "Unknown")
            is_final = window_res.get("final", False)
            
            print(f"Status: MILP={milp_status}, Pricing={prices_status}, Final={is_final}\n")
            
            # Welfare analysis for this window
            expected_welfare_sell = welfare_sell_by_window.get(window, 0.0)
            expected_welfare_buy = welfare_buy_by_window.get(window, 0.0)
            expected_total_welfare = expected_welfare_sell + expected_welfare_buy
            
            # Compute welfare from actual acceptance ratios and BID/ASK PRICES (not MCP!)
            x_s = window_res.get("x_s", {})
            x_b = window_res.get("x_b", {})
            
            computed_welfare_sell = 0.0
            computed_welfare_buy = 0.0
            
            # Calculate computed welfare from sell orders in this window using SELL PRICES (ask)
            for sell in sells:
                if (sell.deliveryStart, sell.deliveryEnd) == window:
                    acceptance_ratio = x_s.get(sell.orderID, 0.0)
                    # Use the sell order's price limit, NOT the market clearing price!
                    computed_welfare_sell -= (acceptance_ratio * sell.quantity * sell.price)
            
            # Calculate computed welfare from buy orders in this window using BUY PRICES (bid)
            for buy in buys:
                if (buy.deliveryStart, buy.deliveryEnd) == window:
                    acceptance_ratio = x_b.get(buy.orderID, 0.0)
                    # Use the buy order's price (bid), NOT the market clearing price!
                    computed_welfare_buy += (acceptance_ratio * buy.quantity * buy.price)
            
            computed_total_welfare = computed_welfare_sell + computed_welfare_buy
            
            print(f"Welfare Analysis:")
            print(f"  Expected Total Welfare: £{expected_total_welfare:>12.2f}")
            print(f"  Computed Total Welfare: £{computed_total_welfare:>12.2f}")
            print(f"  Difference:             £{abs(expected_total_welfare - computed_total_welfare):>12.2f}")
            print(f"  Match: {'✓ YES' if abs(expected_total_welfare - computed_total_welfare) < 0.01 else '✗ NO'}\\n")
            
            # Price comparison
            print(f"Market Clearing Price (MCP) Comparison:")
            expected_window_prices = expected_prices_by_window.get(window, {})
            window_computed_prices = window_res.get("prices_unrounded") or {}
            header = f"{'Product':<15} | {'Expected':<12} | {'Computed':<12} | {'Match':<10}"
            print(f"  {header}")
            print(f"  {'-' * len(header)}")
            
            for product in all_products_sorted:
                expected_price = expected_window_prices.get(product, 0.0)
                computed_price = window_computed_prices.get(product, 0.0)
                price_diff = abs(expected_price - computed_price)
                match_str = "✓ YES" if price_diff < 0.01 else f"✗ NO (Δ£{price_diff:.4f})"
                print(f"  {product:<15} | £{expected_price:>10.4f} | £{computed_price:>10.4f} | {match_str}")
            print()
    
    # Summary
    print(f"\n{'='*100}")
    print("SUMMARY")
    print(f"{'='*100}\n")
    
    print(f"Auction ID: {AUCTION_ID}")
    print(f"Total Orders: {len(buys)} buy + {len(sells)} sell = {len(buys) + len(sells)} orders")
    print(f"Delivery Time Windows: {len(windows)}")
    print(f"Products: {', '.join(sorted(products))}")
    print(f"Market Clearing Status: {'✓ FINAL' if results.get('final') else '✗ NOT FINAL'}")
    
    print(f"\n{'='*100}")
    print("MARKET CLEARING COMPLETE")
    print("="*100)
