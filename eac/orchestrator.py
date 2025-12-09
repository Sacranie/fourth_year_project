from eac.PricingLP import PricingLP
from eac.Volume import VolumeMILP
from eac.solver import PulpSolverBackend
from eac.rounding import rounding_and_residual_distribution
from eac.Validators import build_loop_families
from collections import defaultdict

"""
Run the EAC market clearing process, including pricing and volume determination.
Solves per time window (deliveryStart, deliveryEnd).
"""

def _get_time_windows(sell_orders):
    """Extract unique time windows from orders based on delivery dates only."""
    windows = set()
    for s in sell_orders:
        windows.add((s.deliveryStart, s.deliveryEnd))
    return sorted(list(windows))


def _filter_orders_by_window(sell_orders, buy_orders, window):
    """Filter orders to those in a specific time window based on delivery dates."""
    delivery_start, delivery_end = window
    filtered_sells = [s for s in sell_orders 
                      if s.deliveryStart == delivery_start and s.deliveryEnd == delivery_end]
    filtered_buys = [b for b in buy_orders 
                     if b.deliveryStart == delivery_start and b.deliveryEnd == delivery_end]
    return filtered_sells, filtered_buys


def _get_baskets_for_window(baskets, sell_orders_in_window):
    """Get baskets that have sell orders in this time window."""
    basket_ids_in_window = set(s.basketID for s in sell_orders_in_window)
    # Match basket IDs - handle both string and int comparisons
    result = []
    for b in baskets:
        basket_id = b.id
        if basket_id in basket_ids_in_window:
            result.append(b)
    return result


def run_market(products, buy_orders, sell_orders, baskets,
               unit_capacity_registry=None, overholding=None, msg=0):
    """
    Run EAC market clearing per time window.
    
    Args:
        products: List of product identifiers
        buy_orders: List of BuyOrder objects
        sell_orders: List of SellOrder objects
        baskets: List of Basket objects
        unit_capacity_registry: Dict mapping unit name to capacity
        overholding: Dict mapping product to overholding volume
        msg: Message level for solver
    
    Returns:
        Dict with results for each time window and aggregated results
    """
    unit_capacity_registry = unit_capacity_registry or {}
    overholding = overholding or {}
    
    # Get unique time windows
    windows = _get_time_windows(sell_orders)
    
    # Build global loop families once (applies across all windows for same auctionID)
    global_loop_families = build_loop_families(baskets)
    
    # Solve for each time window
    window_results = {}
    aggregated_x_s = {}  # Order ID -> acceptance ratio
    aggregated_x_b = {}  # Order ID -> acceptance ratio
    aggregated_prices = {}  # Product -> price (may vary by window)
    all_final = True
    
    for window in windows:
        sell_orders_window, buy_orders_window = _filter_orders_by_window(sell_orders, buy_orders, window)
        
        if not sell_orders_window or not buy_orders_window:
            # Skip windows with missing buy or sell orders
            continue
        
        baskets_window = _get_baskets_for_window(baskets, sell_orders_window)
        
        backend = PulpSolverBackend(msg=msg)
        pricing = PricingLP(backend)
        volume = VolumeMILP(pricing, backend)
        
        res = volume.solve_with_pricing_loop(
            products, buy_orders_window, sell_orders_window, baskets_window,
            unit_capacity_registry=unit_capacity_registry,
            allow_overholding_hook=overholding,
            global_loop_families=global_loop_families,
            msg=msg
        )
        
        window_results[window] = res
        all_final = all_final and res.get("final", False)
        
        # Aggregate results
        aggregated_x_s.update(res.get("x_s", {}))
        aggregated_x_b.update(res.get("x_b", {}))
        if res.get("prices_unrounded"):
            aggregated_prices.update(res["prices_unrounded"])
    
    # Post-processing: rounding and residual distribution (aggregate level)
    final_result = {
        "window_results": window_results,
        "x_s": aggregated_x_s,
        "x_b": aggregated_x_b,
        "prices_unrounded": aggregated_prices,
        "final": all_final,
    }
    
    if all_final and aggregated_prices:
        prices_rounded, sell_round, buy_round = rounding_and_residual_distribution(
            products, aggregated_prices, aggregated_x_s,
            sell_orders, aggregated_x_b, buy_orders
        )
        final_result["prices_rounded"] = prices_rounded
        final_result["sell_round"] = sell_round
        final_result["buy_round"] = buy_round
    
    return final_result
