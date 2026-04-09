from scripts.degradation_cause import AUCTION_IDS
import matplotlib.pyplot as plt


if __name__ == "__main__":
    meus = [8e6, 9e6,10e6,20e6,30e6,40e6,50e6,70e6]
    cumRev = [998274, 980954, 962792, 742807,483006, 266989, 120449, 44958]
    soh = [0.9529, 0.9549, 0.9568, 0.9717, 0.9819, 0.9880, 0.9912, 0.9922]
    total_revenue = []
    for i in range(len(meus)):
        rev_in_a_year = (365 / len(AUCTION_IDS))  * cumRev[i]
        soh_degrade_in_a_year = (1 - soh[i]) * (365 / len(AUCTION_IDS))
        total_life = 0.2 / (soh_degrade_in_a_year)
        total_rev = min(11, total_life) * rev_in_a_year
        total_revenue.append(total_rev)
    
    plt.figure(figsize=(10, 6))
    plt.plot(meus, total_revenue, marker='o', linewidth=2, markersize=8, color="#1f6cb4")

    # Mark the optimal point
    optimal_idx = total_revenue.index(max(total_revenue))
    plt.plot(meus[optimal_idx], total_revenue[optimal_idx], marker='*', markersize=20, color='red', zorder=5)

    plt.xscale('log')
    plt.xlabel('Degradation Shadow Price $\\mu$', fontsize=13)
    plt.ylabel('Estimated Total Lifetime Revenue (£)', fontsize=13)
    plt.title('Estimated Total Lifetime Revenue vs $\\mu$', fontsize=14)
    plt.xticks(meus, [f'{m:.0e}' for m in meus], rotation=45)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()
