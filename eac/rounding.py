import math
from typing import List, Dict, Tuple
from collections import defaultdict


def round_price_up_to_cent(price: float) -> float:
    cents = math.ceil(price * 100.0)
    return cents / 100.0

def rounding_and_residual_distribution(products, mcp_prices_val_unrounded, x_s_val, sell_orders, x_b_val, buy_orders):
    """
    Implements EAC Section 8 rounding:
      - prices rounded up to nearest £0.01.
      - accepted sell volumes rounding: parent/child nearest integer; substitutable_child floor.
      - buy volumes rounded to nearest integer
      - compute rounding residual per product and adjust buyer rounded volumes only by distributing ±1 MW ticks until residual is zero
    Returns: prices_rounded, accepted_sell_rounded, accepted_buy_rounded
    """

    # Round the market clearing prices up to nearest penny
    prices_rounded = {p: round_price_up_to_cent(mcp_prices_val_unrounded[p]) for p in products}

    # How much of each sell order is accepted unrounded
    accepted_unrounded_sell = {}
    for s in sell_orders:
        ratio = float(x_s_val.get(s.orderID, 0.0) or 0.0)
        # SellOrder has qty (scalar), not per-product qty dictionary
        total_qty = s.quantity
        accepted_unrounded_sell[s.orderID] = total_qty * ratio

    # Round accepted sell volumes according to type
    accepted_sell_rounded = {}
    for s in sell_orders:
        unrounded = accepted_unrounded_sell[s.orderID]
        if unrounded <= 0:
            accepted_sell_rounded[s.orderID] = 0
            continue
        if s.orderType == "substitutable_child":
            accepted_sell_rounded[s.orderID] = int(math.floor(unrounded + 1e-9))
        else:
            accepted_sell_rounded[s.orderID] = int(round(unrounded + 1e-9))

    # Within each sell order, distribute rounded accepted volume to products proportionally, adjusting for rounding errors
    # Note: SellOrder has a single qty, not per-product quantities
    total_rounded_sells_by_product = {p: 0 for p in products}
    for s in sell_orders:
        rounded_total = accepted_sell_rounded[s.orderID]
        # For single-product orders, all rounded volume goes to that product
        if rounded_total > 0:
            total_rounded_sells_by_product[s.auctionProduct] += rounded_total

    
    accepted_buy_rounded = {}
    for b in buy_orders:
        ratio = float(x_b_val.get(b.orderID, 0.0) or 0.0)
        # BuyOrder has field 'quantity'
        unrounded = b.quantity * ratio
        accepted_buy_rounded[b.orderID] = int(round(unrounded + 1e-9))

    buys_by_product = defaultdict(list)
    for b in buy_orders:
        buys_by_product[b.auctionProduct].append(b)

    # Now adjust buy rounded volumes to fix residuals per product
    for p in products:
        rounded_buys_sum = sum(accepted_buy_rounded[b.orderID] for b in buys_by_product.get(p, []))
        rounded_sells_sum = total_rounded_sells_by_product.get(p, 0)
        residual = rounded_buys_sum - rounded_sells_sum  # positive -> too many buys
        if residual == 0:
            continue
        if rounded_buys_sum < rounded_sells_sum:
            need = rounded_sells_sum - rounded_buys_sum
            candidates = sorted(buys_by_product.get(p, []), key=lambda b: (b.price, b.orderID))
            if not candidates:
                continue
            idx = 0
            while need > 0:
                b = candidates[idx % len(candidates)]
                accepted_buy_rounded[b.orderID] += 1
                need -= 1
                idx += 1
        else:
            need = rounded_buys_sum - rounded_sells_sum
            candidates = sorted(buys_by_product.get(p, []), key=lambda b: (-b.price, b.orderID))
            if not candidates:
                continue
            idx = 0
            while need > 0:
                b = candidates[idx % len(candidates)]
                if accepted_buy_rounded[b.orderID] > 0:
                    accepted_buy_rounded[b.orderID] -= 1
                    need -= 1
                idx += 1

    return prices_rounded, accepted_sell_rounded, accepted_buy_rounded