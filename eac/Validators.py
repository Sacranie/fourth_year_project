from collections import defaultdict, deque
from typing import Dict, List, Set
from eac.models import Basket, SellOrder

def build_loop_families(baskets: List[Basket]) -> Dict:
    """
    We need to determine the loop families in the basket graph.
    A loop family is a connected component in the undirected graph formed by
    the baskets and their looped_to relationships.
    We have to make sure that we are taking into account that baskets belong to a specific auctionID.
    """
    adjacency = defaultdict(list)
    for b in baskets:
        if b.looped_to and b.auctionID:
            adjacency[(b.looped_to, b.auctionID)].append(b.id)

    return adjacency

def validate_unit_capacity(
    sell_orders: List[SellOrder], 
    unit_capacity_registry: Dict[str, float]
) -> List[str]:
    """
    For each unit in a specific time window, check: parent_qty + sum(child_qty) + 
    max(substitutable_child_qty) <= unit_capacity
    Groups orders by (auctionUnit, deliveryStart, deliveryEnd) to represent a specific unit in a time window.
    """
    problems = []
    sells_by_unit_window = defaultdict(list)
    for s in sell_orders:
        key = (s.auctionUnit, s.deliveryStart, s.deliveryEnd)
        sells_by_unit_window[key].append(s)

    for (unit, delivery_start, delivery_end), sells in sells_by_unit_window.items():
        cap = unit_capacity_registry.get(unit)
        if cap is None:
            problems.append(f"Unit capacity not registered for unit {unit}")
            continue

        parent_total = 0.0
        child_total = 0.0
        max_sub = 0.0
        for s in sells:
            qty = s.qty
            if s.orderType == "parent":
                parent_total += qty
            elif s.orderType == "child":
                child_total += qty
            elif s.orderType == "substitutable_child":
                if qty > max_sub:
                    max_sub = qty

        total_energy = parent_total + child_total + max_sub
        if total_energy > cap + 1e-9:
            problems.append(f"Unit {unit} (delivery {delivery_start} to {delivery_end}) violates capacity: {total_energy} > {cap}")
    return problems