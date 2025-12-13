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

getcontext().prec = 28

logger = logging.getLogger(__name__)

ACCEPTANCE_EPS = 1e-9
COEFF_TOL = 1e-12
ROUNDING_TOL_DECIMAL = Decimal("0.000001")
PRICE_MIN = -50.0
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
            var = price_variables.get((fragment.auctionProduct, window))
            if var is None:
                raise KeyError(f"Missing price variable for active pair {(fragment.auctionProduct, window)}")
            terms.append(var * coeff)
            constant -= order.price_limit * coeff

    return terms, constant


def _accumulate_orders_terms(orders: Iterable[MultiProductOrder],
                             price_variables: Dict[Tuple[str, Tuple[str, str]], pulp.LpVariable]) -> Tuple[List, float]:
    agg_terms: List = []
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
              products: List[str],
              all_sell_orders: List,
              x_s_val: Dict[int, float],
              all_baskets: List,
              basket_to_loop: Dict[int, int] = None,
             ) -> Tuple[Dict[Tuple[str, Tuple], float], pulp.LpProblem, str]:
        basket_to_loop = basket_to_loop or {}

        # Normalize basket_to_loop keys
        normalized_loop_map: Dict[int, Optional[int]] = {}
        for basket_id, loop_group_id in basket_to_loop.items():
            try:
                normalized_basket = int(basket_id)
            except (TypeError, ValueError):
                normalized_basket = basket_id

            if loop_group_id is None:
                normalized_loop_map[normalized_basket] = None
            else:
                try:
                    normalized_loop_map[normalized_basket] = int(loop_group_id)
                except (TypeError, ValueError):
                    normalized_loop_map[normalized_basket] = loop_group_id

        basket_to_loop = normalized_loop_map

        multi_orders = group_multi_product_orders(all_sell_orders, x_s_val)
        active_product_windows = _collect_active_pairs(multi_orders)

        # Use global price bounds for all active product-window pairs (per NESO spec)
        price_prob = pulp.LpProblem("EAC_Global_Pricing", pulp.LpMinimize)
        p_vars: Dict[Tuple[str, Tuple[str, str]], pulp.LpVariable] = {}
        for product, window in sorted(active_product_windows):
            var_name = f"price_{product}_{window[0]}_{window[1]}".replace(":", "_").replace("-", "_")
            lower, upper = (self.price_min, self.price_max)
            p_vars[(product, window)] = pulp.LpVariable(
                var_name, lowBound=lower, upBound=upper, cat="Continuous"
            )

        loop_families: Dict[Optional[int], Set[int]] = defaultdict(set)
        for basket_id, loop_group_id in basket_to_loop.items():
            if loop_group_id is not None:
                loop_families[loop_group_id].add(basket_id)

        baskets_in_loops = set(b_id for basket_ids in loop_families.values() for b_id in basket_ids)

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
                        var = p_vars.get((fragment.auctionProduct, window))
                        if var is not None:
                            procurement_terms.append(var * coeff)
        
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
        for loop_id, basket_ids in loop_families.items():
            loop_orders: List[MultiProductOrder] = []
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

        # Solve the LP
        logger.info("Solving pricing LP: minimize procurement cost subject to surplus constraints...")
        status = self.backend.solve(price_prob)
        status_str = pulp.LpStatus[status]
        
        if status != pulp.LpStatusOptimal:
            logger.warning(f"Pricing LP failed with status: {status_str}")
        else:
            obj_value = pulp.value(price_prob.objective)
            logger.info(f"Pricing LP solved: Procurement cost = {obj_value:.6f}")

        # Extract and round prices
        prices_val: Dict[Tuple[str, Tuple[str, str]], float] = {}
        for key, var in p_vars.items():
            raw_value = float(pulp.value(var) if pulp.value(var) is not None else 0.0)
            prices_val[key] = float(Decimal(str(round_price_up_to_cent(raw_value))))

        # Verify surpluses with rounded prices
        self._verify_surpluses(multi_orders, prices_val, basket_to_loop)

        return prices_val, price_prob, status_str

    def _verify_surpluses(self,
                          multi_orders: List[MultiProductOrder],
                          prices_val: Dict[Tuple[str, Tuple[str, str]], float],
                          basket_to_loop: Dict[int, Optional[int]]) -> None:
        tol = ROUNDING_TOL_DECIMAL
        basket_surplus: Dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
        family_surplus: Dict[int, Decimal] = defaultdict(lambda: Decimal("0"))

        for order in multi_orders:
            if order.is_accepted:
                acceptance_dec = Decimal(str(order.acceptance))
                price_limit_dec = Decimal(str(order.price_limit))
                order_surplus = Decimal("0")

                for fragment in order.fragments:
                    window = (fragment.deliveryStart, fragment.deliveryEnd)
                    price_key = (fragment.auctionProduct, window)
                    if price_key not in prices_val:
                        raise RuntimeError(f"Rounded price missing for active pair {price_key}")

                    price_dec = Decimal(str(prices_val[price_key]))
                    quantity_dec = Decimal(str(fragment.quantity))
                    order_surplus += quantity_dec * acceptance_dec * (price_dec - price_limit_dec)

                if order.order_type == 'child' and order_surplus < -tol:
                    raise RuntimeError(
                        f"Rounded prices violate surplus for child order {order.canonical_order_id}: {order_surplus}"
                    )

                basket_surplus[order.basket_id] += order_surplus

                loop_id = basket_to_loop.get(order.basket_id)
                if loop_id is not None:
                    family_surplus[loop_id] += order_surplus

        for basket_id, surplus in basket_surplus.items():
            if basket_to_loop.get(basket_id) is None and surplus < -tol:
                raise RuntimeError(
                    f"Rounded prices violate surplus for basket {basket_id}: {surplus}"
                )

        for loop_id, surplus in family_surplus.items():
            if surplus < -tol:
                raise RuntimeError(
                    f"Rounded prices violate surplus for loop family {loop_id}: {surplus}"
                )