from battery.price_maker_optimiser import PriceMakerOptimiser
from battery.battery import VolkanBattery

# Constants
AUCTION_IDS = 1112

if __name__ == "__main__":
    meu = [6e7]  # Example MEU values to test
    cumulative_profit = 0.0


    # Define bounds for alpha (the fraction of the order to accept)
    lower_alpha = 0.0
    upper_alpha = 3.2
    
    for m in meu:
        battery = VolkanBattery()
        battery.populate_with_volkan_parameters(data_location='data/')
        soh = 1.0
        optimiser = PriceMakerOptimiser(AUCTION_IDS)
        result = optimiser.solve(lower_alpha, upper_alpha, meu=m, battery=battery)
        print(f"The meu value is: {m}")
        print(f"Solver status: {result['status']}")
        print(f"Optimal alpha: {result['optimal_alpha']}")
        print(f"Objective value (profit): {result['objective_value']}")
        print(f"SOH is: {result['SOH']}")

    fig, ax = battery.plot_simulation_global_trajectories(
        energy_trajectory=result['global_energy_trajectory'],
        power_trajectory=result['global_power_trajectory'],
        temp_trajectory=result['global_temp_trajectory'],
        soh_trajectory=result['global_soh_trajectory']
    )
            
    # Print the results
    print(f"The meu value is: {meu}")
    print(f"Solver status: {result['status']}")
    print(f"Optimal alpha: {result['optimal_alpha']}")
    print(f"Objective value (profit): {result['objective_value']}")
    print(f"SOH is: {result['SOH']}")
    cumulative_profit += result['objective_value']
    soh = result['SOH']

