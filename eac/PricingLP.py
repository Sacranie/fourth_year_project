from typing import List, Dict, Tuple, Optional, Iterable, Set
from collections import defaultdict
import pulp
from eac.solver import PulpSolverBackend
from eac.models import SellOrder, MultiProductOrder, BuyOrder
from eac.rounding import round_price_up_to_cent


ACCEPTANCE_EPS = 1e-9
COEFF_TOL = 1e-12
PRICE_MIN = -10.0
PRICE_MAX = 35.0 


def _collect_active_pairs(
    multi_orders: Iterable[MultiProductOrder],
) -> Set[Tuple[str, Tuple[str, str]]]:

    active_pairs = set()
    for order in multi_orders:
        if order.is_accepted:
            for fragment in order.fragments:
                window = (fragment.deliveryStart, fragment.deliveryEnd)
                active_pairs.add((fragment.auctionProduct, window))
    return active_pairs


def _accumulate_order_terms(
    order: MultiProductOrder,
    price_variables: Dict[Tuple[str, Tuple[str, str]], pulp.LpVariable],
) -> Tuple[List, float]:

    terms = []
    constant = 0.0

    if not order.is_accepted:
        return terms, constant

    for fragment in order.fragments:
        window = (fragment.deliveryStart, fragment.deliveryEnd)
        coeff = fragment.quantity * order.actual_acceptance
        if abs(coeff) < COEFF_TOL:
            continue
        computed_MCP = price_variables.get((fragment.auctionProduct, window))
        if computed_MCP is None:
            raise KeyError(
                f"Missing price variable for active pair {(fragment.auctionProduct, window)} "
                f"referenced by order {order.canonical_order_id}"
            )
        terms.append(computed_MCP * coeff)
        constant -= fragment.price * coeff

    return terms, constant


def _accumulate_orders_terms(
    orders: Iterable[MultiProductOrder],
    price_variables: Dict[Tuple[str, Tuple[str, str]], pulp.LpVariable],
) -> Tuple[List, float]:
    """Aggregate surplus terms across a collection of orders (basket or loop family)."""
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
    Global Pricing LP — solves all time windows simultaneously.

    Constraints implemented (per spec Section 7.1 and clearing rules 7-8):
      1. CHILD orders (individual): surplus >= 0
      2. SUBSTITUTABLE CHILD orders (individual): surplus >= 0
         Note: spec rule 7 applies to both child and substitutable child.
      3. NON-LOOPED BASKETS: total basket surplus >= 0
         (covers the parent's contribution via the basket aggregate)
      4. LOOP FAMILIES: total surplus across ALL baskets in the family >= 0
      5. PQR / NQR products: MCP >= £0.00  (product-specific floor)

    Objective (spec Section 7.2):
      Minimise procurement cost = sum( MCP * accepted_qty ) over all accepted sell orders.
      This resolves price indeterminacy while respecting all surplus constraints above.
    """

    def __init__(
        self,
        backend: Optional[PulpSolverBackend] = None,
        price_min: float = PRICE_MIN,
        price_max: float = PRICE_MAX,
    ):
        self.backend = backend or PulpSolverBackend()
        self.price_min = price_min
        self.price_max = price_max

    def solve(
        self,
        multi_orders: List[MultiProductOrder],
        buy_orders: List[BuyOrder],          # kept for API compatibility; not used in pricing
        basket_to_loop: Dict[int, List[int]] = None,
        buy_acceptance: Dict[str, float] = None,  # kept for API compatibility; not used
    ) -> Tuple[Dict[Tuple[str, Tuple], float], pulp.LpProblem, str]:

        basket_to_loop = basket_to_loop or defaultdict(list)

        active_product_windows = _collect_active_pairs(multi_orders)

        price_prob = pulp.LpProblem("EAC_Global_Pricing", pulp.LpMinimize)
        p_vars: Dict[Tuple[str, Tuple[str, str]], pulp.LpVariable] = {}

        for product, window in sorted(active_product_windows):
            var_name = (
                f"price_{product}_{window[0]}_{window[1]}"
                .replace(":", "_")
                .replace("-", "_")
            )
            p_vars[(product, window)] = pulp.LpVariable(
                var_name, lowBound=self.price_min, upBound=self.price_max, cat="Continuous"
            )

        # Objective: minimise procurement cost (spec Section 7.2)
        # Procurement cost = sum over accepted sell orders of (MCP * qty * acceptance)
        procurement_terms = []
        for order in multi_orders:
            if order.is_accepted:
                for fragment in order.fragments:
                    window = (fragment.deliveryStart, fragment.deliveryEnd)
                    coeff = fragment.quantity * order.actual_acceptance
                    if abs(coeff) < COEFF_TOL:
                        continue
                    var_mcp = p_vars.get((fragment.auctionProduct, window))
                    if var_mcp is not None:
                        procurement_terms.append(var_mcp * coeff)

        price_prob += (
            pulp.lpSum(procurement_terms) if procurement_terms else 0.0,
            "MinimizeProcurementCost",
        )

        baskets_in_loops: Set[int] = set(
            b_id for basket_ids in basket_to_loop.values() for b_id in basket_ids
        )

        orders_by_basket: Dict[int, List[MultiProductOrder]] = defaultdict(list)
        for order in multi_orders:
            orders_by_basket[order.basket_id].append(order)

        # Constraint 1 & 2: Individual surplus >= 0 for child and
        # substitutable_child orders (spec clearing rule 7)
        for order in multi_orders:
            if order.order_type in ("child", "substitutable_child") and order.is_accepted:
                order_terms, order_constant = _accumulate_order_terms(order, p_vars)
                if not order_terms and abs(order_constant) > COEFF_TOL:
                    raise RuntimeError(
                        f"Cannot enforce surplus for {order.order_type} order "
                        f"{order.canonical_order_id}; missing price variables"
                    )
                if order_terms or abs(order_constant) > COEFF_TOL:
                    constraint_expr = pulp.lpSum(order_terms) + order_constant
                    price_prob += (
                        constraint_expr >= 0.0,
                        f"individual_surplus_{order.canonical_order_id}",
                    )

        # Constraint 3: Non-looped basket total surplus >= 0
        #  (spec clearing rule 8, non-looped case)
        for basket_id, orders in orders_by_basket.items():
            if basket_id not in baskets_in_loops:
                basket_terms, basket_constant = _accumulate_orders_terms(orders, p_vars)
                if not basket_terms and abs(basket_constant) > COEFF_TOL:
                    raise RuntimeError(
                        f"Cannot enforce basket surplus for basket {basket_id}; "
                        f"missing price variables"
                    )
                if basket_terms or abs(basket_constant) > COEFF_TOL:
                    basket_expr = pulp.lpSum(basket_terms) + basket_constant
                    price_prob += basket_expr >= 0.0, f"basket_surplus_{basket_id}"

        # Constraint 4: Loop family total surplus >= 0
        # (spec clearing rule 8, looped case) 
        for loop_id, basket_ids in basket_to_loop.items():
            loop_orders: List[MultiProductOrder] = []
            for basket_id in basket_ids:
                loop_orders.extend(orders_by_basket.get(basket_id, []))
            loop_terms, loop_constant = _accumulate_orders_terms(loop_orders, p_vars)
            if not loop_terms and abs(loop_constant) > COEFF_TOL:
                raise RuntimeError(
                    f"Cannot enforce loop family surplus for loop {loop_id}; "
                    f"missing price variables"
                )
            if loop_terms or abs(loop_constant) > COEFF_TOL:
                loop_expr = pulp.lpSum(loop_terms) + loop_constant
                price_prob += loop_expr >= 0.0, f"loop_family_surplus_{loop_id}"

        # Constraint 5: PQR / NQR floor at £0.00
        for product, window in active_product_windows:
            if product in ("PQR", "NQR"):
                var_mcp = p_vars.get((product, window))
                if var_mcp is not None:
                    price_prob += (
                        var_mcp >= 0,
                        f"min_price_zero_{product}_{window[0]}_{window[1]}",
                    )

        status = self.backend.solve(price_prob)
        status_str = pulp.LpStatus[status]

        prices_val: Dict[Tuple[str, Tuple[str, str]], float] = {}
        for key, var_mcp in p_vars.items():
            raw = pulp.value(var_mcp)
            prices_val[key] = round_price_up_to_cent(raw if raw is not None else 0.0)

        return prices_val, price_prob, status_str