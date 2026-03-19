import copy
from scipy.optimize import minimize_scalar
from .loading_data import LoadingData
from .battery import VolkanBattery
from .degradation_model import degradation_model


class PriceMakerOptimiser:

    def __init__(self, auction_id):
        self.auction_unit = "GSET-02"  # The auction unit we want to vary
        self.auction_id = auction_id
        self._cached_data = None  # Cache to avoid reloading during optimization iterations

    def load_data_without_clearing_market(self):
        """Load market data, caching to avoid repeated reads during optimization."""
        if self._cached_data is not None:
            return self._cached_data
            
        loading_data = LoadingData(self.auction_id, auction_unit=self.auction_unit)
        sell_records = loading_data.load_sell_orders_for_auction()
        buy_records = loading_data.load_buy_orders_for_auction()

        # Process orders
        multi_orders, sell_orders, original_mcp = loading_data.process_sell_orders(sell_records)
        buy_orders = loading_data.process_buy_orders(buy_records)

        self._cached_data = (original_mcp, multi_orders)
        return self._cached_data

    def compute_profit_at_alpha(self, alpha: float, meu: float, battery: VolkanBattery):

        original_mcp, multi_orders = self.load_data_without_clearing_market()
        
        # Deep copy battery to avoid mutating the original during profit evaluation
        battery_copy = copy.deepcopy(battery)
        
        total_revenue = 0.0
        total_degradation = 0.0
        final_soh = battery_copy.soh if battery_copy.soh is not None else battery_copy.settings['SOH0']

        # We need to iterate through the multi orders an we need to find the orders that belong to the auctionunit GSET-02 and then we need to only look at accepted ones and then we need to build the powerprofile from all of thes emulti orders within the auction id
        mo = []
        for multi_order in multi_orders:
            for order in multi_order.fragments:
                if order.auctionUnit == self.auction_unit and order.status == "ACCEPTED":
                    mo.append(multi_order)
                    break
        
        for multi_order in mo:
            for order in multi_order.fragments:
                if order.auctionUnit == self.auction_unit:
                    # Revenue: alpha * quantity * price
                    price = original_mcp.get((order.auctionProduct, multi_order.window), 0.0)
                    revenue = alpha * order.quantity * price * 4 # This is because each order is for 4 hours, so we multiply by 4 to get total revenue for the order
                    total_revenue += revenue
                    
                    
        degradation = degradation_model()
        degradation_cost, final_soh = degradation.degradation_model_with_alpha(
            battery_copy, mo, meu, alpha
        )
        total_degradation += degradation_cost
    
        profit = total_revenue - total_degradation
        return total_revenue, total_degradation, profit, final_soh

    def solve(self, lower_alpha: float, upper_alpha: float, meu: float, battery: VolkanBattery):
        
        def negative_profit(alpha):
            """Objective function: negative profit (since minimize_scalar minimizes)."""
            _, _, profit, _ = self.compute_profit_at_alpha(alpha, meu, battery)
            return -profit
        
        # Use bounded Brent's method for 1D optimization
        result = minimize_scalar(
            negative_profit, 
            bounds=(lower_alpha, upper_alpha), 
            method='bounded',
            options={'xatol': 1e-2}  # Tolerance for alpha
        )
        
        optimal_alpha = result.x
        
        # Compute final profit and SOH at optimal alpha, updating the actual battery
        final_profit, final_soh = self._apply_optimal_solution(optimal_alpha, meu, battery)
        
        return {
            "status": "Optimal" if result.success else "Failed",
            "objective_value": final_profit,
            "optimal_alpha": optimal_alpha,
            "SOH": final_soh,
            "iterations": result.nfev
        }
    
    def _apply_optimal_solution(self, alpha: float, meu: float, battery: VolkanBattery):
        """
        Apply the optimal solution to the actual battery (updating its state).
        
        Unlike compute_profit_at_alpha, this modifies the battery in place.
        """
        original_mcp, multi_orders = self.load_data_without_clearing_market()
        
        total_revenue = 0.0
        total_degradation = 0.0
        final_soh = battery.soh if battery.soh is not None else battery.settings['SOH0']

        # We need to iterate through the multi orders an we need to find the orders that belong to the auctionunit GSET-02 and then we need to only look at accepted ones and then we need to build the powerprofile from all of thes emulti orders within the auction id
        mo = []
        for multi_order in multi_orders:
            for order in multi_order.fragments:
                if order.auctionUnit == self.auction_unit and order.status == "ACCEPTED":
                    mo.append(multi_order)
                    break
        
        for multi_order in mo:
            for order in multi_order.fragments:
                if order.auctionUnit == self.auction_unit:
                    # Revenue: alpha * quantity * price
                    price = original_mcp.get((order.auctionProduct, multi_order.window), 0.0)
                    revenue = alpha * order.quantity * price * 4 # This is because each order is for 4 hours, so we multiply by 4 to get total revenue for the order
                    total_revenue += revenue
                    
                    
        degradation = degradation_model()
        degradation_cost, final_soh = degradation.degradation_model_with_alpha(
            battery, mo, meu, alpha
        )
        total_degradation += degradation_cost
    
        profit = total_revenue - total_degradation
        return total_revenue, total_degradation, profit, final_soh