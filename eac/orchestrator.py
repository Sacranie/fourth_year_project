from .PricingLP import PricingLP
from .Volume import VolumeMILP
from .solver import PulpSolverBackend
from .rounding import rounding_and_residual_distribution

def run_market(products, buy_orders, sell_orders, baskets,
               unit_capacity_registry=None, overholding=None, msg=0):
    backend = PulpSolverBackend(msg=msg)
    pricing = PricingLP(backend)
    volume = VolumeMILP(pricing, backend)
    res = volume.solve_with_pricing_loop(
        products, buy_orders, sell_orders, baskets,
        unit_capacity_registry=unit_capacity_registry,
        allow_overholding_hook=overholding, msg=msg
    )
    if res.get("final"):
        prices_unrounded = res["prices_unrounded"]
        prices_rounded, sell_round, buy_round = rounding_and_residual_distribution(
            products, prices_unrounded, res["x_s"],
            sell_orders, res["x_b"], buy_orders
        )
        res["prices_rounded"] = prices_rounded
        res["sell_round"] = sell_round
        res["buy_round"] = buy_round
    return res
