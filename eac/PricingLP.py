from typing import List, Dict, Tuple, Optional
from collections import defaultdict
import pulp
from .solver import PulpSolverBackend
from .Validators import build_loop_families

PRICE_MIN = -1000.0
PRICE_MAX = 10000.0


class PricingLP:
    def __init__(self, backend: Optional[PulpSolverBackend] = None,
                 price_min: float = PRICE_MIN, price_max: float = PRICE_MAX):
        self.backend = backend or PulpSolverBackend()
        self.price_min = price_min
        self.price_max = price_max

    def solve(self, products: List[str], sell_orders: List[dict], x_s_val: Dict[str, float],
              baskets: Dict[str, dict]) -> Tuple[Dict[str, float], pulp.LpProblem, str]:
        price_prob = pulp.LpProblem("EAC_Pricing", pulp.LpMinimize)
        p_vars = {p: pulp.LpVariable(f"price_{p}", lowBound=self.price_min,
                                     upBound=self.price_max, cat="Continuous") for p in products}

        procurement_terms = []
        for s in sell_orders:
            x_fixed = float(x_s_val.get(s["id"], 0.0) or 0.0)
            if x_fixed > 1e-12:
                for p in products:
                    q = s["qty"].get(p, 0.0)
                    if abs(q) > 1e-12:
                        procurement_terms.append(p_vars[p] * q * x_fixed)

        if procurement_terms:
            price_prob += pulp.lpSum(procurement_terms), "ProcurementCost"
        else:
            price_prob += 0.0, "ProcurementCost"

        # child non-negative surplus
        for s in sell_orders:
            x_fixed = float(x_s_val.get(s["id"], 0.0) or 0.0)
            if x_fixed <= 1e-12:
                continue
            total_qty = sum(s["qty"].get(p, 0.0) for p in products)
            if total_qty <= 1e-12:
                continue
            if s["type"] in ("child", "substitutable_child"):
                revenue = pulp.lpSum([p_vars[p] * s["qty"].get(p, 0.0) * x_fixed for p in products])
                required = s["price"] * total_qty * x_fixed
                price_prob += revenue >= required, f"child_nonneg_{s['id']}"

        sells_by_basket = defaultdict(list)
        for s in sell_orders:
            sells_by_basket[s["basket"]].append(s)

        loop_families = build_loop_families(baskets)
        baskets_in_loops = set().union(*loop_families) if loop_families else set()

        for basket_id, sells in sells_by_basket.items():
            if basket_id in baskets_in_loops:
                continue
            net_terms = []
            for s in sells:
                x_fixed = float(x_s_val.get(s["id"], 0.0) or 0.0)
                if x_fixed <= 1e-12:
                    continue
                revenue = pulp.lpSum([p_vars[p] * s["qty"].get(p, 0.0) * x_fixed for p in products])
                cost = s["price"] * sum(s["qty"].get(p, 0.0) for p in products) * x_fixed
                net_terms.append(revenue - cost)
            if net_terms:
                price_prob += pulp.lpSum(net_terms) >= 0.0, f"basket_net_{basket_id}"

        for fam in loop_families:
            fam_orders = []
            for b in fam:
                fam_orders.extend(sells_by_basket.get(b, []))
            net_terms = []
            for s in fam_orders:
                x_fixed = float(x_s_val.get(s["id"], 0.0) or 0.0)
                if x_fixed <= 1e-12:
                    continue
                revenue = pulp.lpSum([p_vars[p] * s["qty"].get(p, 0.0) * x_fixed for p in products])
                cost = s["price"] * sum(s["qty"].get(p, 0.0) for p in products) * x_fixed
                net_terms.append(revenue - cost)
            if net_terms:
                price_prob += pulp.lpSum(net_terms) >= 0.0, f"loop_net_{'_'.join(sorted(fam))}"

        # solve
        status = self.backend.solve(price_prob)
        status_str = pulp.LpStatus[status]
        prices_val = {p: float(pulp.value(v) if pulp.value(v) is not None else 0.0) for p, v in p_vars.items()}
        return prices_val, price_prob, status_str
