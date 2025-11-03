from typing import List, Dict, Optional
from collections import defaultdict
import pulp
from .Validators import build_loop_families, validate_unit_capacity
from .PricingLP import PricingLP
from .solver import PulpSolverBackend

MAX_MILP_RETRIES = 50

class VolumeMILP:
    def __init__(self, pricing: Optional[PricingLP] = None, backend: Optional[PulpSolverBackend] = None, max_retries: int = MAX_MILP_RETRIES):
        self.backend = backend or PulpSolverBackend()
        self.pricing = pricing or PricingLP(self.backend)
        self.max_retries = max_retries

    def build_problem(self, products, buy_orders, sell_orders, baskets, unit_capacity_registry=None, substitutability_families_buy=None, allow_overholding_hook=None):
        substitutability_families_buy = substitutability_families_buy or {}
        unit_capacity_registry = unit_capacity_registry or {}
        problems = validate_unit_capacity([s for s in sell_orders], baskets, unit_capacity_registry)
        if problems:
            raise ValueError("Unit capacity validation failed:\n" + "\n".join(problems))

        buy_orders_extended = [dict(b) for b in buy_orders]
        if allow_overholding_hook:
            for p, vol in allow_overholding_hook.items():
                if vol > 0:
                    oid = f"OVERHOLD_{p}"
                    buy_orders_extended.append({"id": oid, "product": p, "price": 0.0, "volume": vol, "family": None, "paradoxical": True})

        prob = pulp.LpProblem("EAC_Volume", pulp.LpMaximize)

        # x_b
        x_b = {}
        for b in buy_orders_extended:
            low = float(b.get("min_acceptance_ratio", 0.0))
            low = max(0.0, min(1.0, low))
            x_b[b["id"]] = pulp.LpVariable(f"x_b_{b['id']}", lowBound=low, upBound=1, cat="Continuous")

        # x_s
        x_s = {}
        for s in sell_orders:
            if s["type"] == "parent":
                x_s[s["id"]] = pulp.LpVariable(f"x_s_{s['id']}", lowBound=0, upBound=1, cat="Binary")
            else:
                low = float(s.get("min_acceptance_ratio", 0.0))
                low = max(0.0, min(1.0, low))
                x_s[s["id"]] = pulp.LpVariable(f"x_s_{s['id']}", lowBound=low, upBound=1, cat="Continuous")

        # y_parent
        y_parent = {}
        for b_id in baskets.keys():
            y_parent[b_id] = pulp.LpVariable(f"y_parent_{b_id}", lowBound=0, upBound=1, cat="Binary")

        parents_by_basket = {}
        for s in sell_orders:
            if s["type"] == "parent":
                parents_by_basket[s["basket"]] = s["id"]

        for basket_id, parent_order_id in parents_by_basket.items():
            prob += x_s[parent_order_id] == y_parent[basket_id], f"parent_accept_equals_y_{basket_id}"

        for s in sell_orders:
            if s["type"] in ("child", "substitutable_child"):
                prob += x_s[s["id"]] <= y_parent[s["basket"]], f"child_less_than_parent_{s['id']}"

        subs_by_basket = defaultdict(list)
        for s in sell_orders:
            if s["type"] == "substitutable_child":
                subs_by_basket[s["basket"]].append(s["id"])

        for basket_id, subs in subs_by_basket.items():
            prob += pulp.lpSum([x_s[sid] for sid in subs]) <= 1.0, f"subs_family_basket_{basket_id}"

        for b_id, info in baskets.items():
            for other in info.get("concomitant", []):
                if b_id < other:
                    prob += y_parent[b_id] + y_parent[other] <= 1.0, f"mutual_exclusive_{b_id}_{other}"

        loop_families = build_loop_families(baskets)
        for fam in loop_families:
            fam = list(fam)
            base = fam[0]
            for other in fam[1:]:
                prob += y_parent[base] == y_parent[other], f"loop_eq_{base}_{other}"

        for p in products:
            sell_sum = []
            buy_sum = []
            for s in sell_orders:
                q = s["qty"].get(p, 0.0)
                if abs(q) > 1e-12:
                    sell_sum.append(q * x_s[s["id"]])
            for b in buy_orders_extended:
                if b["product"] == p:
                    buy_sum.append(b["volume"] * x_b[b["id"]])
            prob += pulp.lpSum(sell_sum) == pulp.lpSum(buy_sum), f"balance_product_{p}"

        for fam_id, members in (substitutability_families_buy or {}).items():
            prob += pulp.lpSum([x_b[bid] for bid in members]) <= 1.0, f"buy_subs_family_{fam_id}"

        for b in buy_orders_extended:
            prob += x_b[b["id"]] <= 1.0
            prob += x_b[b["id"]] >= 0.0

        for s in sell_orders:
            prob += x_s[s["id"]] <= 1.0
            prob += x_s[s["id"]] >= 0.0

        welfare_terms = []
        for b in buy_orders_extended:
            welfare_terms.append(b["price"] * b["volume"] * x_b[b["id"]])
        for s in sell_orders:
            total_qty = sum(s["qty"].get(p, 0.0) for p in products)
            welfare_terms.append(- s["price"] * total_qty * x_s[s["id"]])

        prob += pulp.lpSum(welfare_terms), "Welfare"
        return prob, x_b, x_s, y_parent, buy_orders_extended

    def solve_with_pricing_loop(self, products, buy_orders, sell_orders, baskets, unit_capacity_registry=None, substitutability_families_buy=None, allow_overholding_hook=None, msg: int = 0):
        prob, x_b, x_s, y_parent, buy_orders_extended = self.build_problem(
            products, buy_orders, sell_orders, baskets, unit_capacity_registry, substitutability_families_buy, allow_overholding_hook
        )
        nogood_counter = 0
        seen_parent_patterns = set()
        final_solution = None
        price_problem = None
        prices_unrounded = None
        price_status = None
        milp_status = None

        for iteration in range(1, self.max_retries + 1):
            prob.solve(pulp.PULP_CBC_CMD(msg=msg))
            milp_status = pulp.LpStatus[prob.status]
            x_b_val = {bid: float(pulp.value(var) if pulp.value(var) is not None else 0.0) for bid, var in x_b.items()}
            x_s_val = {sid: float(pulp.value(var) if pulp.value(var) is not None else 0.0) for sid, var in x_s.items()}
            y_parent_val = {bid: float(pulp.value(var) if pulp.value(var) is not None else 0.0) for bid, var in y_parent.items()}

            if milp_status not in ("Optimal", "Feasible"):
                return {
                    "x_b": x_b_val,
                    "x_s": x_s_val,
                    "y_parent": y_parent_val,
                    "prices_unrounded": None,
                    "prices_status": None,
                    "milp_status": milp_status,
                    "final": False,
                    "iterations": iteration,
                    "vol_problem": prob,
                    "price_problem": None,
                }

            accepted_parents = frozenset([b for b, v in y_parent_val.items() if v > 0.5])
            if accepted_parents in seen_parent_patterns:
                nogood_counter += 1
                prob += pulp.lpSum([y_parent[b] for b in accepted_parents]) <= max(0, len(accepted_parents) - 1), f"nogood_repeat_{nogood_counter}"
                continue

            seen_parent_patterns.add(accepted_parents)
            prices_unrounded_candidate, price_problem_candidate, price_status_candidate = self.pricing.solve(
                products, sell_orders, x_s_val, baskets
            )
            price_problem = price_problem_candidate
            prices_unrounded = prices_unrounded_candidate
            price_status = price_status_candidate

            if price_status != "Optimal":
                nogood_counter += 1
                if len(accepted_parents) == 0:
                    prob += pulp.lpSum([y_parent[b] for b in y_parent.keys()]) >= 1, f"nogood_nonzero_{nogood_counter}"
                else:
                    prob += pulp.lpSum([y_parent[b] for b in accepted_parents]) <= max(0, len(accepted_parents) - 1), f"nogood_cut_{nogood_counter}"
                continue

            buy_problematic = False
            violating_buys = []
            for b in buy_orders_extended:
                bid = b["id"]
                ratio = float(x_b_val.get(bid, 0.0) or 0.0)
                if ratio <= 1e-12:
                    continue
                product = b["product"]
                clearing_price = prices_unrounded.get(product, 0.0)
                surplus_per_mw = b["price"] - clearing_price
                total_surplus = surplus_per_mw * b["volume"] * ratio
                if total_surplus < -1e-9 and not bool(b.get("paradoxical", True)):
                    buy_problematic = True
                    violating_buys.append((bid, total_surplus))

            if buy_problematic:
                nogood_counter += 1
                if len(accepted_parents) == 0:
                    prob += pulp.lpSum([y_parent[b] for b in y_parent.keys()]) >= 1, f"nogood_nonzero_par_{nogood_counter}"
                else:
                    prob += pulp.lpSum([y_parent[b] for b in accepted_parents]) <= max(0, len(accepted_parents) - 1), f"nogood_paradox_buy_{nogood_counter}"
                continue

            final_solution = {
                "x_b": x_b_val,
                "x_s": x_s_val,
                "y_parent": y_parent_val,
                "prices_unrounded": prices_unrounded,
                "prices_status": price_status,
                "milp_status": milp_status,
                "final": True,
                "iterations": iteration,
                "vol_problem": prob,
                "price_problem": price_problem,
            }
            return final_solution

        return {
            "x_b": x_b_val,
            "x_s": x_s_val,
            "y_parent": y_parent_val,
            "prices_unrounded": prices_unrounded,
            "prices_status": price_status,
            "milp_status": milp_status,
            "final": False,
            "iterations": self.max_retries,
            "vol_problem": prob,
            "price_problem": price_problem,
        }