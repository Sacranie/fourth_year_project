from battery.price_maker_optimiser import PriceMakerOptimiser
from battery.battery import VolkanBattery
import matplotlib.pyplot as plt


if __name__ == "__main__":
    meus = 6e6  # 9600 kWh × £150/kWh / 0.2 EOL × scaling
    profits = []
    revenue = []
    cost = []
    alphas = [0, 0.5, 1.0, 1.5,  2.0, 2.5, 3.0, 3.2]
    auction_id = 1112
    
    iteration = 0
    for alpha in alphas:
        optimiser = PriceMakerOptimiser(auction_id)
        battery = VolkanBattery()
        battery.populate_with_volkan_parameters(data_location='data/')
        revenue_val, cost_val, profit, _ = optimiser.compute_profit_at_alpha(alpha, meu=meus, battery=battery)
        print(f"Iteration {iteration}: Alpha={alpha}, Revenue={revenue_val}, Cost={cost_val}, Profit={profit}")
        revenue.append(revenue_val)
        cost.append(cost_val)
        profits.append(profit)
    
    # Plot the profit, revenue, and cost curves 
    plt.figure(figsize=(10, 6))
    plt.plot(alphas, profits, marker='o', label='Profit')
    plt.plot(alphas, revenue, marker='o', label='Revenue')
    plt.plot(alphas, cost, marker='o', label='Degradation Cost')
    plt.xlabel('Alpha (Fraction of Order Accepted)')
    plt.ylabel('Value (£)')
    plt.title(f'Profit, Revenue, and Degradation Cost vs Alpha (MEU={meus})')
    plt.grid(True)
    plt.legend()
    plt.show()


