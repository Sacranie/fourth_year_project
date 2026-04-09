import numpy as np
import matplotlib.pyplot as plt

from battery.battery import VolkanBattery
from battery.price_maker_optimiser import PriceMakerOptimiser
from degradation_meu.Jack_meu import JackMeu
from scripts.degradation_cause import AUCTION_IDS


def compare_methods(auction_ids, data_location='data/', constant_meu=2e7):
    # --- Initialise both methods ---
    battery_const = VolkanBattery()
    battery_const.populate_with_volkan_parameters(data_location=data_location)

    battery_jack = VolkanBattery()
    battery_jack.populate_with_volkan_parameters(data_location=data_location)

    WARMUP = 30
    T_eval = len(auction_ids) - WARMUP          # auctions used for evaluation
    LIFETIME_AUCTIONS = 10 * 365                # 10-year lifetime in days

    # target_degradation = rho = B/T_total (allowable degradation per auction over full lifetime)
    # step_size = sqrt(log(T_eval) / T_eval) as per paper
    jack = JackMeu(meu=2e7, window=WARMUP, step_size=(np.log(T_eval) / T_eval) ** 0.5, target_degradation=0.2 / LIFETIME_AUCTIONS, dual_variable=0.0)

    const_revenues, const_sohs, const_alphas = [], [1.0], []
    jack_revenues, jack_sohs, jack_alphas, jack_meus = [], [1.0], [], []

    warmup_ids = auction_ids[:WARMUP]
    eval_ids = auction_ids[WARMUP:]

    # --- Warm-up: fill JackMeu's rolling window only, then reset both batteries to SOH=1.0 ---
    print(f"Warming up over {WARMUP} auctions...")
    warmup_soh_tracker = 1.0
    for auction_id in warmup_ids:
        optimiser = PriceMakerOptimiser(auction_id)
        optimiser.load_data_without_clearing_market()

        current_meu = jack.meu
        result = optimiser.solve(0.0, 3.2, meu=current_meu, battery=battery_jack)
        degradation = warmup_soh_tracker - result['SOH']
        warmup_soh_tracker = result['SOH']
        jack.computation(result['revenue'], degradation)

    # Reset both batteries to SOH=1.0 — warm-up was purely for filling JackMeu's window
    battery_const.initialize_state(soh=1.0)
    battery_jack.initialize_state(soh=1.0)
    print("Warm-up complete. Both batteries reset to SOH=1.0")

    # --- Evaluation ---
    for auction_id in eval_ids:
        print(f"Processing auction: {auction_id}")

        # Load data once per auction — both methods reuse the cache
        optimiser = PriceMakerOptimiser(auction_id)
        optimiser.load_data_without_clearing_market()

        result = optimiser.solve(0.0, 3.2, meu=constant_meu, battery=battery_const)
        const_revenues.append(result['revenue'])
        const_sohs.append(result['SOH'])
        const_alphas.append(result['optimal_alpha'])
        print(f"The sum of the revenues for the constant MEU method is: {sum(const_revenues):.2f} and the SOH is: {result['SOH']:.4f}")

        current_meu = jack.meu
        jack_meus.append(current_meu)
        result = optimiser.solve(0.0, 3.2, meu=current_meu, battery=battery_jack)
        degradation = jack_sohs[-1] - result['SOH']
        jack.computation(result['revenue'], degradation)
        jack_revenues.append(result['revenue'])
        jack_sohs.append(result['SOH'])
        jack_alphas.append(result['optimal_alpha'])
        print(f"The sum of the revenues for the JackMeu method is: {sum(jack_revenues):.2f} and the SOH is: {result['SOH']:.4f}")
    return [
        {
            'method': f'Constant MEU={constant_meu:.0e}',
            'revenues': const_revenues,
            'sohs': const_sohs,
            'alphas': const_alphas,
            'cumulative_revenue': sum(const_revenues),
            'final_soh': const_sohs[-1],
            'auctions_completed': len(const_revenues),
        },
        {
            'method': 'JackMeu (Adaptive)',
            'revenues': jack_revenues,
            'sohs': jack_sohs,
            'alphas': jack_alphas,
            'meus': jack_meus,
            'cumulative_revenue': sum(jack_revenues),
            'final_soh': jack_sohs[-1],
            'auctions_completed': len(jack_revenues),
        },
    ]


def plot_comparison(results, save_path=None):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # 1. Cumulative revenue over time
    ax1 = axes[0, 0]
    for result in results:
        ax1.plot(np.cumsum(result['revenues']), label=result['method'])
    ax1.set_xlabel('Auction')
    ax1.set_ylabel('Cumulative Revenue (£)')
    ax1.set_title('Cumulative Revenue Over Time')
    ax1.legend()
    ax1.grid(True)

    # 2. SOH trajectory
    ax2 = axes[0, 1]
    for result in results:
        ax2.plot(result['sohs'], label=result['method'])
    ax2.axhline(y=0.8, color='r', linestyle='--', label='EOL Threshold')
    ax2.set_xlabel('Auction')
    ax2.set_ylabel('State of Health')
    ax2.set_title('Battery SOH Over Time')
    ax2.legend()
    ax2.grid(True)

    # 3. MEU values (adaptive method only)
    ax3 = axes[1, 0]
    for result in results:
        if 'meus' in result and result['meus']:
            ax3.plot(result['meus'], label=result['method'])
    ax3.set_xlabel('Auction')
    ax3.set_ylabel('MEU Value')
    ax3.set_title('MEU Values Over Time (Adaptive)')
    ax3.legend()
    ax3.grid(True)

    # 4. Summary bar chart
    ax4 = axes[1, 1]
    methods = [r['method'] for r in results]
    revenues = [r['cumulative_revenue'] for r in results]
    final_sohs = [r['final_soh'] for r in results]

    x = np.arange(len(methods))
    width = 0.35

    ax4.bar(x - width / 2, revenues, width, label='Cumulative Revenue (£)')
    ax4_twin = ax4.twinx()
    ax4_twin.bar(x + width / 2, final_sohs, width, color='orange', label='Final SOH')

    ax4.set_ylabel('Cumulative Revenue (£)')
    ax4_twin.set_ylabel('Final SOH')
    ax4.set_xticks(x)
    ax4.set_xticklabels(methods, rotation=15, ha='right')
    ax4.set_title('Summary Comparison')

    lines1, labels1 = ax4.get_legend_handles_labels()
    lines2, labels2 = ax4_twin.get_legend_handles_labels()
    ax4.legend(lines1 + lines2, labels1 + labels2, loc='upper left')

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"Figure saved to: {save_path}")

    plt.show()


def print_summary(results):
    print("\n" + "=" * 80)
    print("COMPARISON SUMMARY")
    print("=" * 80)
    print(f"{'Method':<30} {'Revenue (£)':<15} {'Final SOH':<12} {'Auctions':<10}")
    print("-" * 80)

    for result in results:
        print(f"{result['method']:<30} {result['cumulative_revenue']:<15.2f} {result['final_soh']:<12.4f} {result['auctions_completed']:<10}")

    print("=" * 80)

    best = max(results, key=lambda x: x['cumulative_revenue'])
    print(f"\nBest method: {best['method']} with £{best['cumulative_revenue']:.2f} revenue")


if __name__ == "__main__":
    print(f"Evaluating on {len(AUCTION_IDS)} auctions")

    results = compare_methods(AUCTION_IDS)

    print_summary(results)
    plot_comparison(results)
