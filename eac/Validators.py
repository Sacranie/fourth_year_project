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
    baskets: List[Basket], 
    unit_capacity_registry: Dict[str, float]
) -> List[str]:
    """
    For each basket (unit), check: parent_qty + sum(child_qty) + 
    max(substitutable_child_qty) <= unit_capacity
    """
    problems = []
    sells_by_basket = defaultdict(list)
    for s in sell_orders:
        sells_by_basket[s.basket].append(s)

    baskets_dict = {b.id: b for b in baskets}

    for basket_id, sells in sells_by_basket.items():
        if basket_id not in baskets_dict:
            problems.append(f"Undefined basket {basket_id}")
            continue
        unit = baskets_dict[basket_id].unit
        cap = unit_capacity_registry.get(unit)
        if cap is None:
            problems.append(f"Unit capacity not registered for unit {unit} (basket {basket_id})")
            continue

        parent_total = 0.0
        child_total = 0.0
        max_sub = 0.0
        for s in sells:
            total_qty = sum(s.qty.values())
            if s.type == "parent":
                parent_total += total_qty
            elif s.type == "child":
                child_total += total_qty
            elif s.type == "substitutable_child":
                if total_qty > max_sub:
                    max_sub = total_qty

        total_energy = parent_total + child_total + max_sub
        if total_energy > cap + 1e-9:
            problems.append(f"Basket {basket_id} for unit {unit} violates capacity: {total_energy} > {cap}")
    return problems