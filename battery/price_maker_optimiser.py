import pulp 
from .loading_data import LoadingData
from .battery import VolkanBattery
from .power_export import PowerExport
from eac.models import MultiProductOrder
from eac.Volume import VolumeMILP
from eac.Validators import validate_unit_capacity, build_loop_families
from eac.solver import PulpSolverBackend

class PriceMakerOptimiser:

    def __init__(self, auction_id):
        self.auction_unit = "GSET-02"  # The auction unit we want to vary
        self.auction_id = auction_id
        self.solver_backend = PulpSolverBackend()  # Initialize Gurobi solver backend
        self.alpha = None  # Store alpha variable for access after solving
        

    def load_data_to_clear_market(self):
        loading_data = LoadingData(self.auction_id, auction_unit=self.auction_unit)
        sell_records = loading_data.load_sell_orders_for_auction()
        buy_records = loading_data.load_buy_orders_for_auction()
        volume_milp = VolumeMILP(backend=self.solver_backend)

        # Process orders
        multi_orders, sell_orders, original_mcp = loading_data.process_sell_orders(sell_records)
        buy_orders = loading_data.process_buy_orders(buy_records)

        # Build baskets and extract loop families (pass raw sell_records to populate concomitant/loop info)
        baskets = loading_data.build_baskets_from_orders(sell_orders, sell_records)
                
        # Extract unique products
        products = set(o.auctionProduct for o in sell_orders) | set(o.auctionProduct for o in buy_orders)

        # Extract unique units for capacity registry
        units = set(order.auctionUnit for order in sell_orders)
        unit_capacity_registry = {unit: 1e9 for unit in units}  # Set very high capacity for each unit

        data = volume_milp.solve_with_pricing_loop(
            products=list(products),
            buy_orders=buy_orders,
            sell_orders=multi_orders,
            baskets=baskets,
            unit_capacity_registry=unit_capacity_registry
        )

        return data, multi_orders

    def load_data_without_clearing_market(self):
        loading_data = LoadingData(self.auction_id, auction_unit=self.auction_unit)
        sell_records = loading_data.load_sell_orders_for_auction()
        buy_records = loading_data.load_buy_orders_for_auction()

        # Process orders
        multi_orders, sell_orders, original_mcp = loading_data.process_sell_orders(sell_records)
        buy_orders = loading_data.process_buy_orders(buy_records)

        return original_mcp, multi_orders


    def compute_profit_coefficients(self, meu):
        """
        Compute the profit coefficients for each order.
        Returns coefficients that can be used to build the linear objective:
        profit = alpha * (revenue_coeff - degradation_coeff)
        
        Both revenue and degradation scale with alpha since alpha represents
        the fraction of capacity being offered.
        
        Args:
            meu: Degradation cost parameter
            
        Returns:
            Tuple of (total_revenue_coefficient, total_degradation_coefficient, data, multi_orders)
        """
        # Initialize VolkanBattery with degradation model parameters
        battery = VolkanBattery()
        battery.populate_with_volkan_parameters(data_location='data/')
        # data, multi_orders = self.load_data() There is no point clearing the market again because we are in price taker region
        original_mcp, multi_orders = self.load_data_without_clearing_market()
        
        total_revenue_coeff = 0.0
        total_degradation_coeff = 0.0
        
        # Calculate coefficients for the multi_sell_order
        for multi_order in multi_orders:
            for order in multi_order.fragments:
                if order.auctionUnit == self.auction_unit:
                    # Revenue coefficient: quantity * price * acceptance_ratio
                    price = original_mcp.get((order.auctionProduct, (multi_order.window)), 0.0)
                    acceptance = 1.0  # Assume full acceptance in price taker mode
                    revenue_coeff = order.quantity * price * acceptance
                    total_revenue_coeff += revenue_coeff
                    
                    # Degradation coefficient (scales with alpha like revenue)
                    power_export = PowerExport(order.auctionProduct)
                    degradation_cost = power_export.degradation_model(battery, [multi_order], meu)
                    total_degradation_coeff += degradation_cost
        
        return total_revenue_coeff, total_degradation_coeff, original_mcp, multi_orders


    def build_problem(self, lower_alpha, upper_alpha, meu):
        """
        Build the profit maximization problem.
        
        Objective: maximize alpha * (revenue_coeff - degradation_coeff)
        Subject to: lower_alpha <= alpha <= upper_alpha
        
        Both revenue and degradation scale with alpha:
        - alpha = 0: no bidding, no revenue, no degradation, profit = 0
        - alpha = 1: bid full capacity, full revenue, full degradation
        """
        prob = pulp.LpProblem("PriceMaker", pulp.LpMaximize)

        # Define decision variable
        self.alpha = pulp.LpVariable('alpha', lowBound=lower_alpha, upBound=upper_alpha, cat='Continuous')

        # Get profit coefficients
        revenue_coeff, degradation_coeff, _, _ = self.compute_profit_coefficients(meu)
        
        # Net profit coefficient (revenue - degradation per unit of alpha)
        net_profit_coeff = revenue_coeff - degradation_coeff
        
        print(f"Revenue coefficient: {revenue_coeff}")
        print(f"Degradation coefficient: {degradation_coeff}")
        print(f"Net profit coefficient: {net_profit_coeff}")

        # Objective function: profit = alpha * net_profit_coeff
        # If net_profit_coeff > 0: maximize alpha (bid more)
        # If net_profit_coeff < 0: minimize alpha (bid nothing)
        prob += self.alpha * net_profit_coeff, "Objective"

        return prob

    def solve(self, lower_alpha, upper_alpha, meu):
        """Build and solve the profit maximization problem using Gurobi."""
        prob = self.build_problem(lower_alpha, upper_alpha, meu)
        
        # Use the Gurobi solver backend
        status = self.solver_backend.solve(prob)
        
        return {
            "status": pulp.LpStatus[status],
            "objective_value": pulp.value(prob.objective),
            "optimal_alpha": self.alpha.varValue if self.alpha else None,
            "problem": prob
        }
