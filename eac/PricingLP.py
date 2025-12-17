from typing import List, Dict, Tuple, Optional, Iterable, Set
from collections import defaultdict
from decimal import Decimal, getcontext
import logging
import pulp
from eac.solver import PulpSolverBackend
from eac.models import SellOrder, MultiProductOrder
from eac.rounding import round_price_up_to_cent
from eac.multi_product_orders import group_multi_product_orders

"""
Implements the pricing linear program for the EAC market clearing process.
"""

ACCEPTANCE_EPS = 1e-9
COEFF_TOL = 1e-12
ROUNDING_TOL_DECIMAL = Decimal("0.000001")
PRICE_MIN = -10.0
PRICE_MAX = 50.0


def _collect_active_pairs(multi_orders: Iterable[MultiProductOrder]) -> Set[Tuple[str, Tuple[str, str]]]:
    active_pairs = set()
    for order in multi_orders:
        if order.is_accepted:
            for fragment in order.fragments:
                window = (fragment.deliveryStart, fragment.deliveryEnd)
                active_pairs.add((fragment.auctionProduct, window))
    return active_pairs


def _accumulate_order_terms(order: MultiProductOrder,
                            price_variables: Dict[Tuple[str, Tuple[str, str]], pulp.LpVariable]) -> Tuple[List, float]:
    terms = []
    constant = 0.0

    if not order.is_accepted:
        return terms, constant

    for fragment in order.fragments:
        window = (fragment.deliveryStart, fragment.deliveryEnd)
        coeff = fragment.quantity * order.acceptance
        if abs(coeff) > COEFF_TOL:
            computed_MCP_pence = price_variables.get((fragment.auctionProduct, window))
            if computed_MCP_pence is None:
                raise KeyError(f"Missing price variable for active pair {(fragment.auctionProduct, window)}")
            terms.append((computed_MCP_pence * coeff) / 100.0)
            constant -= order.price_limit * coeff

    return terms, constant


def _accumulate_orders_terms(orders: Iterable[MultiProductOrder],
                             price_variables: Dict[Tuple[str, Tuple[str, str]], pulp.LpVariable]) -> Tuple[List, float]:
    agg_terms = []
    agg_constant = 0.0

    for order in orders:
        if order.is_accepted:
            order_terms, order_constant = _accumulate_order_terms(order, price_variables)
            agg_terms.extend(order_terms)
            agg_constant += order_constant

    return agg_terms, agg_constant


class GlobalPricingLP:
    """
    Global Pricing LP that solves all time windows simultaneously.
    
    Constraints:
    1. CHILD orders: Individual/multi-product surplus >= 0
    2. PARENT orders: NO individual constraint
    3. NON-LOOPED BASKETS: Total basket surplus >= 0 
    4. LOOP FAMILIES: Total surplus across ALL baskets in family >= 0
    
    Objective: Minimize procurement cost subject to all surplus constraints
    """
    
    def __init__(self, backend: Optional[PulpSolverBackend] = None,
                 price_min: float = PRICE_MIN, price_max: float = PRICE_MAX):
        self.backend = backend or PulpSolverBackend()
        self.price_min = price_min
        self.price_max = price_max

    def solve(self, 
              all_sell_orders: List,
              x_s_val: Dict[int, float],
              basket_to_loop: Dict[int, int] = None,
             ) -> Tuple[Dict[Tuple[str, Tuple], float], pulp.LpProblem, str]:
        basket_to_loop = basket_to_loop or defaultdict(list)

        multi_orders = group_multi_product_orders(all_sell_orders, x_s_val)
        active_product_windows = _collect_active_pairs(multi_orders)

        # Use global price bounds for all active product-window pairs (per NESO spec)
        price_prob = pulp.LpProblem("EAC_Global_Pricing", pulp.LpMinimize)
        p_vars: Dict[Tuple[str, Tuple[str, str]], pulp.LpVariable] = {}
        cents_min = int(round(self.price_min * 100))
        cents_max = int(round(self.price_max * 100))
        for product, window in sorted(active_product_windows):
            var_name = f"price_{product}_{window[0]}_{window[1]}".replace(":", "_").replace("-", "_")
            p_vars[(product, window)] = pulp.LpVariable(
                var_name, lowBound=cents_min, upBound=cents_max, cat="Integer"
            )

        baskets_in_loops = set(b_id for _, basket_ids in basket_to_loop.items() for b_id in basket_ids)

        orders_by_basket: Dict[int, List[MultiProductOrder]] = defaultdict(list)
        for order in multi_orders:
            orders_by_basket[order.basket_id].append(order)

        # Compute procurement cost terms (the objective)
        procurement_terms = []
        for order in multi_orders:
            if order.is_accepted:
                for fragment in order.fragments:
                    window = (fragment.deliveryStart, fragment.deliveryEnd)
                    coeff = fragment.quantity * order.acceptance
                    if abs(coeff) > COEFF_TOL:
                        var_pence = p_vars.get((fragment.auctionProduct, window))
                        if var_pence is not None:
                            procurement_terms.append((var_pence * coeff) / 100.0)
        
        # Set objective: Minimize procurement cost
        if procurement_terms:
            price_prob += pulp.lpSum(procurement_terms), "MinimizeProcurementCost"
        else:
            price_prob += 0.0, "MinimizeProcurementCost"

        # CONSTRAINT 1: Child order surplus >= 0
        for order in multi_orders:
            if order.order_type == 'child' and order.is_accepted:
                order_terms, order_constant = _accumulate_order_terms(order, p_vars)
                if order_terms or abs(order_constant) > COEFF_TOL:
                    if not order_terms and abs(order_constant) > COEFF_TOL:
                        raise RuntimeError(
                            f"Cannot enforce surplus for child order {order.canonical_order_id}; missing price variables"
                        )
                    constraint_expr = pulp.lpSum(order_terms) + order_constant
                    price_prob += constraint_expr >= 0.0, f"child_multiproduct_{order.canonical_order_id}"

        # CONSTRAINT 2: Non-looped basket surplus >= 0
        for basket_id, orders in orders_by_basket.items():
            if basket_id not in baskets_in_loops:
                basket_terms, basket_constant = _accumulate_orders_terms(orders, p_vars)
                if basket_terms or abs(basket_constant) > COEFF_TOL:
                    if not basket_terms and abs(basket_constant) > COEFF_TOL:
                        raise RuntimeError(
                            f"Cannot enforce basket surplus for basket {basket_id}; missing price variables"
                        )
                    basket_expr = pulp.lpSum(basket_terms) + basket_constant
                    price_prob += basket_expr >= 0.0, f"basket_profit_{basket_id}"

        # CONSTRAINT 3: Loop family surplus >= 0
        for loop_id, basket_ids in basket_to_loop.items():
            loop_orders = []
            for basket_id in basket_ids:
                loop_orders.extend(orders_by_basket.get(basket_id, []))
            loop_terms, loop_constant = _accumulate_orders_terms(loop_orders, p_vars)
            if loop_terms or abs(loop_constant) > COEFF_TOL:
                if not loop_terms and abs(loop_constant) > COEFF_TOL:
                    raise RuntimeError(
                        f"Cannot enforce loop family surplus for loop {loop_id}; missing price variables"
                    )
                loop_expr = pulp.lpSum(loop_terms) + loop_constant
                price_prob += loop_expr >= 0.0, f"loop_family_{loop_id}"

        status = self.backend.solve(price_prob)
        status_str = pulp.LpStatus[status]

        prices_val: Dict[Tuple[str, Tuple[str, str]], float] = {}
        for key, var_pence in p_vars.items():
            raw_value = float(pulp.value(var_pence) if pulp.value(var_pence) is not None else 0.0)
            prices_val[key] = float(Decimal(raw_value) / Decimal(100))

        return prices_val, price_prob, status_str
