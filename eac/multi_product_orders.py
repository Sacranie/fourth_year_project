from typing import List, Dict, Tuple
from collections import defaultdict
from eac.models import SellOrder, MultiProductOrder

def group_multi_product_orders(all_sell_orders: List[SellOrder],
                                x_s_val: Dict[int, float]) -> List[MultiProductOrder]:
    grouped = defaultdict(list)
    for order in all_sell_orders:
        order_type = getattr(order, "orderType", "parent")
        order_entry_time = getattr(order, "orderEntryTime", "") or ""
        delivery_window = (order.deliveryStart, order.deliveryEnd)
        product_marker = getattr(order, "product_id", "")
        acceptance_ratio = float(x_s_val.get(order.orderID, 0.0) or 0.0)
        status = order.status

        key = (order.basketID, product_marker, order_entry_time, order_type, delivery_window, acceptance_ratio, status)
        grouped[key].append(order)

    multi_orders: List[MultiProductOrder] = []
    for key, fragments in grouped.items():
        if not fragments:
            continue

        acceptance_values = [float(x_s_val.get(f.orderID, 0.0) or 0.0) for f in fragments]

        label = "accepted" if all(a > 0.0 for a in acceptance_values) else "rejected"
        
        multi_orders.append(
            create_multi_product_order(key, label, fragments, acceptance_values)
        )

    return multi_orders

def create_multi_product_order(base_key: Tuple,
                                label: str,
                                fragments: List[SellOrder],
                                acceptance_values: List[float]) -> MultiProductOrder:
    canonical_acceptance = acceptance_values[0] if acceptance_values else 0.0
    canonical_fragment_id = fragments[0].orderID if fragments else 0

    price_limit = fragments[0].price
    window = (fragments[0].deliveryStart, fragments[0].deliveryEnd)

    return MultiProductOrder(
        key=base_key + (label,),
        fragments=fragments,
        acceptance=canonical_acceptance,
        price_limit=price_limit,
        basket_id=fragments[0].basketID,
        order_type=getattr(fragments[0], "orderType", "parent"),
        window=window,
        canonical_order_id=canonical_fragment_id,
    )

