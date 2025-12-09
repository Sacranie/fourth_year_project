from typing import List, Dict, Tuple, Optional
from collections import defaultdict
import pulp
from eac.solver import PulpSolverBackend
from eac.Validators import build_loop_families

"""
What does this do?
Implements the pricing linear program for the EAC market clearing process.
It sets up and solves a linear program to determine market clearing prices
"""

PRICE_MIN = -1000.0
PRICE_MAX = 10000.0


class PricingLP:
    def __init__(self, backend: Optional[PulpSolverBackend] = None,
                 price_min: float = PRICE_MIN, price_max: float = PRICE_MAX):
        self.backend = backend or PulpSolverBackend()
        self.price_min = price_min
        self.price_max = price_max

    def solve(self, products: List[str], sell_orders: List, x_s_val: Dict[str, float],
              baskets: List) -> Tuple[Dict[str, float], pulp.LpProblem, str]:
        
        price_prob = pulp.LpProblem("EAC_Pricing", pulp.LpMinimize)
        p_vars = {p: pulp.LpVariable(f"price_{p}", lowBound=self.price_min,
                                     upBound=self.price_max, cat="Continuous") for p in products}

        # Objective: Minimize procurement cost
        procurement_terms = []
        for s in sell_orders:
            s_id = s.orderID
            x_fixed = float(x_s_val.get(s_id, 0.0))
            if x_fixed > 1e-12:
                if s.auctionProduct in p_vars:
                    q = s.qty
                    if abs(q) > 1e-12:
                        procurement_terms.append(p_vars[s.auctionProduct] * q * x_fixed)

        if procurement_terms:
            price_prob += pulp.lpSum(procurement_terms), "ProcurementCost" 
        else:
            price_prob += 0.0, "ProcurementCost"

        # Constraints: Non-negative profit for sell orders         
        for s in sell_orders:
            s_id = s.orderID
            s_price = s.price
            x_fixed = float(x_s_val.get(s_id, 0.0) or 0.0)
            if x_fixed <= 1e-12:
                continue
            if s.quantity <= 1e-12:
                continue
            if s.auctionProduct not in p_vars:
                continue
            revenue = p_vars[s.auctionProduct] * s.qty * x_fixed
            required = s_price * s.qty * x_fixed
            price_prob += revenue >= required, f"sell_nonneg_{s_id}"

        sells_by_basket = defaultdict(list)
        for s in sell_orders:
            sells_by_basket[s.basketID].append(s)

        # Build loop families - filter to baskets in this window (window being auctionID)
        basket_ids_in_window = set(s.basketID for s in sell_orders)
        baskets_in_window = [b for b in baskets if b.id in basket_ids_in_window]
        loop_families = build_loop_families(baskets_in_window)
        
        # Get basket IDs that are in loops
        baskets_in_loops = set()
        for (loop_id, auction_id), basket_ids in loop_families.items():
            baskets_in_loops.update(basket_ids)

        # Constraints: Non-negative net profit for baskets NOT in loops
        for basket_id, sells in sells_by_basket.items():
            if basket_id in baskets_in_loops:
                continue
            net_terms = []
            for s in sells:
                s_id = s.orderID
                s_price = s.price
                x_fixed = float(x_s_val.get(s_id, 0.0) or 0.0)
                if x_fixed <= 1e-12:
                    continue
                if s.auctionProduct not in p_vars:
                    continue
                revenue = p_vars[s.auctionProduct] * s.qty * x_fixed
                cost = s_price * s.qty * x_fixed
                net_terms.append(revenue - cost)
            if net_terms:
                price_prob += pulp.lpSum(net_terms) >= 0.0, f"basket_net_{basket_id}"

        # Constraints: Non-negative net profit for loop families
        for (loop_id, auction_id), basket_ids in loop_families.items():
            fam_orders = []
            for b_id in basket_ids:
                fam_orders.extend(sells_by_basket.get(b_id, []))
            net_terms = []
            for s in fam_orders:
                s_id = s.orderID
                s_price = s.price
                x_fixed = float(x_s_val.get(s_id, 0.0) or 0.0)
                if x_fixed <= 1e-12:
                    continue
                if s.auctionProduct not in p_vars:
                    continue
                revenue = p_vars[s.auctionProduct] * s.qty * x_fixed
                cost = s_price * s.qty * x_fixed
                net_terms.append(revenue - cost)
            if net_terms:
                loop_label = '_'.join(sorted([str(bid) for bid in basket_ids]))
                price_prob += pulp.lpSum(net_terms) >= 0.0, f"loop_net_{loop_label}"

        # solve
        status = self.backend.solve(price_prob)
        status_str = pulp.LpStatus[status]
        prices_val = {p: float(pulp.value(v) if pulp.value(v) is not None else 0.0) for p, v in p_vars.items()}
        return prices_val, price_prob, status_str
