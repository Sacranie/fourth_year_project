from typing import List, Dict, Optional
from collections import defaultdict
import pulp
from eac.Validators import build_loop_families, validate_unit_capacity
from eac.PricingLP import GlobalPricingLP
from eac.multi_product_orders import group_multi_product_orders
from eac.solver import PulpSolverBackend

MAX_MILP_RETRIES = 50

class VolumeMILP:
    def __init__(self, pricing: Optional[GlobalPricingLP] = None, backend: Optional[PulpSolverBackend] = None, max_retries: int = MAX_MILP_RETRIES):
        self.backend = backend or PulpSolverBackend()
        self.pricing = pricing or GlobalPricingLP(self.backend)
        self.max_retries = max_retries

    def build_problem(self, products, buy_orders, sell_orders, baskets, unit_capacity_registry=None, substitutability_families_buy=None, allow_overholding_hook=None, global_loop_families=None):
        substitutability_families_buy = substitutability_families_buy or {}
        unit_capacity_registry = unit_capacity_registry or {}
        problems = validate_unit_capacity(sell_orders, unit_capacity_registry)
        if problems:
            raise ValueError("Unit capacity validation failed:\n" + "\n".join(problems))

        buy_orders_extended = [b.__dict__.copy() for b in buy_orders]
        if allow_overholding_hook:
            for p, vol in allow_overholding_hook.items():
                if vol > 0:
                    oid = f"OVERHOLD_{p}"
                    buy_orders_extended.append({"orderID": oid, "auctionProduct": p, "price": 0.0, "quantity": vol, "paradoxical": True})

        prob = pulp.LpProblem("EAC_Volume", pulp.LpMaximize)

        x_b = {}

        for b in buy_orders_extended:
            declared_min = b.get("min_acceptance_ratio", 0.0)
            declared_min = max(0.0, min(1.0, declared_min))
            bid = b.get("orderID", b.get("id", ""))
            x_b[bid] = pulp.LpVariable(f"x_b_{bid}", lowBound=0.0, upBound=1.0, cat="Continuous")
            z_var = pulp.LpVariable(f"z_b_{bid}", lowBound=0, upBound=1, cat="Binary")
            prob += x_b[bid] <= z_var, f"buy_min_cap_{bid}"
            if declared_min > 0.0:
                prob += x_b[bid] >= declared_min * z_var, f"buy_min_floor_{bid}"

        x_s = {}
        for s in sell_orders:
            if s.order_type == "parent":
                x_s[s.key] = pulp.LpVariable(f"x_s_{s.key}", lowBound=0, upBound=1, cat="Binary")
            else:
                declared_min = s.acceptance
                declared_min = max(0.0, min(1.0, declared_min))
                x_var = pulp.LpVariable(f"x_s_{s.key}", lowBound=0.0, upBound=1, cat="Continuous")
                x_s[s.key] = x_var
                z_var = pulp.LpVariable(f"z_s_{s.key}", lowBound=0, upBound=1, cat="Binary")
                prob += x_var <= z_var, f"min_accept_cap_{s.key}"
                if declared_min > 0.0:
                    prob += x_var >= declared_min * z_var, f"min_accept_floor_{s.key}"

        y_parent = {}
        all_basket_ids = set(s.basket_id for s in sell_orders)
        
        for basket_id in all_basket_ids:
            y_parent[basket_id] = pulp.LpVariable(f"y_parent_{basket_id}", lowBound=0, upBound=1, cat="Binary")

        parents_by_basket = defaultdict(list)
        for s in sell_orders:
            if s.order_type == "parent":
                parents_by_basket[s.basket_id].append(s.key)

        for basket_id, parent_ids in parents_by_basket.items():
            num_parents = len(parent_ids)
            
            for parent_id in parent_ids:
                prob += y_parent[basket_id] <= x_s[parent_id], f"y_parent_le_parent_{basket_id}_{parent_id}"
            
            prob += y_parent[basket_id] >= pulp.lpSum([x_s[pid] for pid in parent_ids]) - num_parents + 1, f"y_parent_from_all_parents_{basket_id}"

        # Child constraint: child can only be accepted if ALL parent orders in the basket are accepted
        for s in sell_orders:
            if s.order_type in ("child", "substitutable_child"):
                prob += x_s[s.key] <= y_parent[s.basket_id], f"child_requires_all_parents_{s.key}"

        subs_by_basket = defaultdict(list)
        for s in sell_orders:
            if s.order_type == "substitutable_child":
                subs_by_basket[s.basket_id].append(s.key)

        for basket_id, subs in subs_by_basket.items():
            prob += pulp.lpSum([x_s[sid] for sid in subs]) <= 1.0, f"subs_family_basket_{basket_id}"

        # Enforce mutual exclusivity: at most ONE basket from a concomitant group can be accepted
        concomitant_groups = {}
        for b in baskets:
            if len(b.concomitant) > 0:
                basket_id_key = b.id
                group_members = sorted([basket_id_key] + b.concomitant)
                group_key = tuple(group_members)
                if group_key not in concomitant_groups:
                    concomitant_groups[group_key] = group_members
        
        for group_key, group_members in concomitant_groups.items():
            baskets_in_group = [b for b in group_members if b in y_parent]
            if baskets_in_group:
                prob += pulp.lpSum([y_parent[b] for b in baskets_in_group]) <= 1.0, f"mutual_exclusive_{group_key}"

        # Product balance constraints
        for p in products:
            sell_sum = []
            buy_sum = []
            for s in sell_orders:
                for order in s.fragments:
                    if order.auctionProduct == p:
                        sell_sum.append(order.quantity * x_s[s.key])
            for b in buy_orders_extended:
                if b.get("auctionProduct") == p:
                    bid = b.get("orderID")
                    buy_sum.append(b.get("quantity") * x_b[bid])
            if sell_sum or buy_sum:
                prob += pulp.lpSum(sell_sum) == pulp.lpSum(buy_sum), f"balance_product_{p}"

        for fam_id, members in (substitutability_families_buy or {}).items():
            prob += pulp.lpSum([x_b[bid] for bid in members]) <= 1.0, f"buy_subs_family_{fam_id}"

        # Cross-window loop constraints: baskets in same loop family must have same acceptance
        if global_loop_families:
            for (loop_id, auction_id), basket_ids in global_loop_families.items():
                if len(basket_ids) > 1:
                    base_id = basket_ids[0]
                    for other_id in basket_ids[1:]:
                        if base_id in y_parent and other_id in y_parent:
                            prob += y_parent[base_id] == y_parent[other_id], f"cross_window_loop_{base_id}_{other_id}"

        # Bounds
        for bid in x_b:
            prob += x_b[bid] <= 1.0
            prob += x_b[bid] >= 0.0

        for sid in x_s:
            prob += x_s[sid] <= 1.0
            prob += x_s[sid] >= 0.0

        # Objective: Maximize welfare
        welfare_terms = []
        for b in buy_orders_extended:
            bid = b.get("orderID", b.get("id", ""))
            welfare_terms.append(b["price"] * b.get("quantity", 0) * x_b[bid])
        for s in sell_orders:
            for order in s.fragments:
                welfare_terms.append(- order.price * order.quantity * x_s[s.key])

        prob += pulp.lpSum(welfare_terms), "Welfare"
        return prob, x_b, x_s, y_parent, buy_orders_extended

    def solve_with_pricing_loop(self, products, buy_orders, sell_orders, baskets, unit_capacity_registry=None, substitutability_families_buy=None, allow_overholding_hook=None, global_loop_families=None, msg: int = 0):
        
        prob, x_b, x_s, y_parent, buy_orders_extended = self.build_problem(
            products, buy_orders, sell_orders, baskets, unit_capacity_registry, substitutability_families_buy, allow_overholding_hook, global_loop_families
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
                products, sell_orders, x_s_val, baskets, buy_orders, x_b_val
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
                bid = b.get("orderID", b.get("id", ""))
                ratio = float(x_b_val.get(bid, 0.0) or 0.0)
                if ratio <= 1e-12:
                    continue
                product = b.get("auctionProduct", b.get("product", ""))
                clearing_price = prices_unrounded.get(product, 0.0)
                surplus_per_mw = b["price"] - clearing_price
                total_surplus = surplus_per_mw * b.get("quantity", b.get("volume", 0)) * ratio
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