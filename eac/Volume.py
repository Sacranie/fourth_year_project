from typing import List, Dict, Optional
from collections import defaultdict
import pulp
from eac.Validators import build_loop_families, validate_unit_capacity
from eac.PricingLP import GlobalPricingLP, PRICE_MAX
from eac.solver import PulpSolverBackend

MAX_MILP_RETRIES = 50

class VolumeMILP:
    def __init__(self, pricing: Optional[GlobalPricingLP] = None, backend: Optional[PulpSolverBackend] = None, max_retries: int = MAX_MILP_RETRIES, time_limit: int = 300):
        self.backend = backend or PulpSolverBackend(time_limit=time_limit)
        self.pricing = pricing or GlobalPricingLP(self.backend)
        self.max_retries = max_retries

    def build_problem(self, products, buy_orders, sell_orders, baskets, unit_capacity_registry=None, substitutability_families_buy=None, allow_overholding_hook=None):
        substitutability_families_buy = substitutability_families_buy or {}
        unit_capacity_registry = unit_capacity_registry or {}
        problems = validate_unit_capacity(sell_orders, unit_capacity_registry)
        if problems:
            raise ValueError("Unit capacity validation failed:\n" + "\n".join(problems))

        buy_orders_extended = [b.__dict__.copy() for b in buy_orders]
        if allow_overholding_hook:
            for (product, window), vol in allow_overholding_hook.items():
                buy_orders_extended.append({
                    "orderID": f"OVERHOLD_{product}_{window[0]}_{window[1]}",
                    "auctionProduct": product,
                    "deliveryStart": window[0],
                    "deliveryEnd": window[1],
                    "price": 0.0,
                    "quantity": vol,
                    "paradoxical": True
                })


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
            # Check if any fragment has a price limit above PRICE_MAX - if so, force rejection
            max_fragment_price = max(frag.price for frag in s.fragments)
            order_infeasible = max_fragment_price > PRICE_MAX
            
            if s.order_type == "parent":
                x_s[s.key] = pulp.LpVariable(f"x_s_{s.key}", lowBound=0, upBound=1, cat="Binary")
                if order_infeasible:
                    prob += x_s[s.key] == 0, f"price_infeasible_{s.key}"
            else:
                declared_min = s.acceptance
                declared_min = max(0.0, min(1.0, declared_min))
                x_var = pulp.LpVariable(f"x_s_{s.key}", lowBound=0.0, upBound=1, cat="Continuous")
                x_s[s.key] = x_var
                
                if order_infeasible:
                    prob += x_var == 0, f"price_infeasible_{s.key}"
                else:
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

        time_windows = set()
        for s in sell_orders:
            time_windows.add(s.window)
            for b in buy_orders_extended:
                time_windows.add((b.get("deliveryStart", ""), b.get("deliveryEnd", "")))
        time_windows = list(time_windows)

        # Product balance constraints across the buy orders and sell orders for a given product in a given time window
        for time_window in time_windows:
            for product in products:
                buy_terms = []
                for b in buy_orders_extended:
                    bid = b.get("orderID", b.get("id", ""))
                    if b.get("auctionProduct", b.get("product", "")) == product and (b.get("deliveryStart", ""), b.get("deliveryEnd", "")) == time_window:
                        buy_terms.append(b.get("quantity", b.get("volume", 0)) * x_b[bid])

                sell_terms = []
                for s in sell_orders:
                    if s.window == time_window:
                        for order in s.fragments:
                            if order.auctionProduct == product:
                                sell_terms.append(order.quantity * x_s[s.key])
                if buy_terms or sell_terms:
                    prob += pulp.lpSum(buy_terms) == pulp.lpSum(sell_terms), f"product_balance_{product}_{time_window[0]}_{time_window[1]}"

        # Pricing feasibility constraints: prevent accepting conflicting sell/buy orders
        # A non-paradoxical buy order with price P_b sets a ceiling on the price for (product, window)
        # A sell order with price P_s sets a floor on the price for (product, window)
        # If P_s > P_b, both cannot be accepted together
        for b in buy_orders_extended:
            if b.get("paradoxical", False):
                continue  # Paradoxical buy orders don't constrain prices
            
            bid = b.get("orderID", b.get("id", ""))
            b_product = b.get("auctionProduct", b.get("product", ""))
            b_window = (b.get("deliveryStart", ""), b.get("deliveryEnd", ""))
            b_price = b.get("price", 0.0)
            
            for s in sell_orders:
                if s.window != b_window:
                    continue
                for frag in s.fragments:
                    if frag.auctionProduct == b_product and frag.price > b_price:
                        # Conflict: sell order needs price >= frag.price, but buy caps at b_price
                        # At most one of them can be accepted
                        prob += x_s[s.key] + x_b[bid] <= 1, f"pricing_conflict_{s.key}_{bid}_{b_product}"


        for fam_id, members in (substitutability_families_buy or {}).items():
            prob += pulp.lpSum([x_b[bid] for bid in members]) <= 1.0, f"buy_subs_family_{fam_id}"

        # Cross-window loop constraints: baskets in same loop family must have same acceptance
        global_loop_families = build_loop_families(baskets)
        if global_loop_families:
            for _, basket_ids in global_loop_families.items():
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
        return prob, x_b, x_s, y_parent, buy_orders_extended, global_loop_families

    def solve_with_pricing_loop(
        self,
        products,
        buy_orders,
        sell_orders,
        baskets,
        unit_capacity_registry=None,
        substitutability_families_buy=None,
        allow_overholding_hook=None,
        msg: int = 0
    ):

        # Build base MILP
        prob, x_b, x_s, y_parent, buy_orders_ext, loop_families = self.build_problem(
            products,
            buy_orders,
            sell_orders,
            baskets,
            unit_capacity_registry,
            substitutability_families_buy,
            allow_overholding_hook
        )

        self.backend.solve(prob)

        if pulp.LpStatus[prob.status] != "Optimal":
            return {"final": False, "reason": "Initial MILP infeasible"}

        x_b_val = {k: float(pulp.value(v) or 0.0) for k, v in x_b.items()}
        x_s_val = {k: float(pulp.value(v) or 0.0) for k, v in x_s.items()}
        y_val = {k: int(round(pulp.value(v))) for k, v in y_parent.items()}

        best_welfare = 0.0
        for b in buy_orders_ext:
            bid = b["orderID"]
            best_welfare += b["price"] * b["quantity"] * x_b_val.get(bid, 0.0)
        for s in sell_orders:
            for frag in s.fragments:
                best_welfare -= frag.price * frag.quantity * x_s_val.get(s.key, 0.0)

        for s in sell_orders:
            s.actual_acceptance = x_s_val.get(s.key, 0.0)

        prices, price_lp, price_status = self.pricing.solve(
            sell_orders,
            buy_orders=buy_orders,
            basket_to_loop=loop_families,
            buy_acceptance=x_b_val
        )

        if price_status == "Optimal":
            return {
                "final": True,
                "welfare": best_welfare,
                "x_b": x_b_val,
                "x_s": x_s_val,
                "y_parent": y_val,
                "prices_unrounded": prices,
                "price_problem": price_lp
            }

        return {
            "final": False,
            "reason": "No pricing-feasible solution found after welfare relaxation"
        }