from collections import defaultdict, deque
from typing import Dict, List, Set
from .models import Basket, SellOrder

def build_loop_families(baskets: Dict[str, Basket]) -> List[Set[str]]:
    """
    Build connected components of looped baskets (treat looped_to links as 
    undirected).
    Returns list of sets, each set containing basket IDs of a looped family 
    (size>1 only).
    """
    adjacency = defaultdict(set)
    for b_id, info in baskets.items():
        if info.looped_to:
            adjacency[b_id].add(info.looped_to)
            adjacency[info.looped_to].add(b_id)

    visited = set()
    families = []
    for start in baskets.keys():
        if start in visited:
            continue
        q = deque([start])
        comp = set()
        while q:
            cur = q.popleft()
            if cur in comp:
                continue
            comp.add(cur)
            visited.add(cur)
            for n in adjacency[cur]:
                if n not in comp:
                    q.append(n)
        if len(comp) > 1:
            families.append(comp)
    return families

def validate_unit_capacity(
    sell_orders: List[SellOrder], 
    baskets: Dict[str, Basket], 
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

    for basket_id, sells in sells_by_basket.items():
        if basket_id not in baskets:
            problems.append(f"Undefined basket {basket_id}")
            continue
        unit = baskets[basket_id].unit
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