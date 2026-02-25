import matplotlib.pyplot as plt
import numpy as np
import pickle 

# NESO API endpoints
SELL_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=13b511df-d6ec-4143-afb1-0ecc6fd19810"
BUY_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=1cf68f59-8eb8-4f1d-bccf-11b5a47b24e5"

ACCEPTANCE_TOLERANCE = 0.01  # Compare within 1%


if __name__ == "__main__":

    with open("price_diffs_cache.pkl", "rb") as f:
        data = pickle.load(f)

    all_products = data['all_products']
    price_diffs = data['price_diffs']
    
    alpha_vals = [1, 1.5, 2, 2.5, 3.0, 3.5, 4.0]
    beta_vals = [1, 1.2, 1.4, 1.6, 2]
    
    # =========================================================================
    # METRIC 1: % True Price Taker (|diff| < threshold) - INVERTED view
    # Lower values = more price maker influence
    # =========================================================================
    TAKER_THRESHOLD = 0.30  # £0.10 tolerance for "no impact"
    
    for product in all_products:
        heatmap_data = np.zeros((len(alpha_vals), len(beta_vals)))
        
        for i, a in enumerate(alpha_vals):
            for j, b in enumerate(beta_vals):
                if a == 1 and b == 1:
                    heatmap_data[i, j] = 100  # Baseline = 100% price taker
                    continue
                key = (product, a, b)
                if key in price_diffs and price_diffs[key]:
                    # % of times with negligible price impact (true price taker)
                    taker_count = sum(1 for diff in price_diffs[key] if abs(diff) < TAKER_THRESHOLD)
                    heatmap_data[i, j] = (taker_count / len(price_diffs[key])) * 100
                else:
                    heatmap_data[i, j] = np.nan
        
        plt.figure(figsize=(10, 8))
        # Use reversed colormap: blue (high/taker) to red (low/maker)
        im = plt.imshow(heatmap_data, cmap='RdYlBu', aspect='auto', origin='lower',
                        vmin=0, vmax=100)
        plt.colorbar(im, label='Price Taker Frequency (%)')
        plt.xticks(range(len(beta_vals)), beta_vals)
        plt.yticks(range(len(alpha_vals)), alpha_vals)
        plt.xlabel('Beta (Price Multiplier)')
        plt.ylabel('Alpha (Quantity Multiplier)')
        plt.title(f'Price Taker Frequency for {product}\n(|MCP Change| < £{TAKER_THRESHOLD:.2f} = No Impact)')
        
        for i in range(len(alpha_vals)):
            for j in range(len(beta_vals)):
                if not np.isnan(heatmap_data[i, j]):
                    plt.text(j, i, f'{heatmap_data[i, j]:.0f}%',
                            ha='center', va='center', color='black', fontsize=9, fontweight='bold')
        
        plt.tight_layout()
        safe_name = product.replace('/', '_').replace('\\', '_').replace(' ', '_')
        plt.savefig(f"price_taker_{safe_name}.png", dpi=150)
        plt.close()
        print(f"Saved price taker heatmap for: {product}")

    # =========================================================================
    # METRIC 2: Median |Price Diff| - Shows typical magnitude of impact
    # =========================================================================
    for product in all_products:
        heatmap_data = np.zeros((len(alpha_vals), len(beta_vals)))
        
        for i, a in enumerate(alpha_vals):
            for j, b in enumerate(beta_vals):
                if a == 1 and b == 1:
                    heatmap_data[i, j] = 0
                    continue
                key = (product, a, b)
                if key in price_diffs and price_diffs[key]:
                    heatmap_data[i, j] = np.median([abs(d) for d in price_diffs[key]])
                else:
                    heatmap_data[i, j] = np.nan
        
        plt.figure(figsize=(10, 8))
        vmax = np.nanmax(heatmap_data) if not np.all(np.isnan(heatmap_data)) else 1
        im = plt.imshow(heatmap_data, cmap='YlOrRd', aspect='auto', origin='lower',
                        vmin=0, vmax=max(0.5, vmax))
        plt.colorbar(im, label='Median |MCP Change| (£)')
        plt.xticks(range(len(beta_vals)), beta_vals)
        plt.yticks(range(len(alpha_vals)), alpha_vals)
        plt.xlabel('Beta (Price Multiplier)')
        plt.ylabel('Alpha (Quantity Multiplier)')
        plt.title(f'Median Price Impact for {product}\n(Higher = More Price Maker)')
        
        for i in range(len(alpha_vals)):
            for j in range(len(beta_vals)):
                if not np.isnan(heatmap_data[i, j]):
                    plt.text(j, i, f'£{heatmap_data[i, j]:.2f}',
                            ha='center', va='center', color='black', fontsize=9)
        
        plt.tight_layout()
        safe_name = product.replace('/', '_').replace('\\', '_').replace(' ', '_')
        plt.savefig(f"median_impact_{safe_name}.png", dpi=150)
        plt.close()
        print(f"Saved median impact heatmap for: {product}")

    # =========================================================================
    # METRIC 3: 90th Percentile |Price Diff| - Extreme/worst-case impacts
    # =========================================================================
    for product in all_products:
        heatmap_data = np.zeros((len(alpha_vals), len(beta_vals)))
        
        for i, a in enumerate(alpha_vals):
            for j, b in enumerate(beta_vals):
                if a == 1 and b == 1:
                    heatmap_data[i, j] = 0
                    continue
                key = (product, a, b)
                if key in price_diffs and price_diffs[key]:
                    heatmap_data[i, j] = np.percentile([abs(d) for d in price_diffs[key]], 90)
                else:
                    heatmap_data[i, j] = np.nan
        
        plt.figure(figsize=(10, 8))
        vmax = np.nanmax(heatmap_data) if not np.all(np.isnan(heatmap_data)) else 1
        im = plt.imshow(heatmap_data, cmap='YlOrRd', aspect='auto', origin='lower',
                        vmin=0, vmax=max(1.5, vmax))
        plt.colorbar(im, label='90th Percentile |MCP Change| (£)')
        plt.xticks(range(len(beta_vals)), beta_vals)
        plt.yticks(range(len(alpha_vals)), alpha_vals)
        plt.xlabel('Beta (Price Multiplier)')
        plt.ylabel('Alpha (Quantity Multiplier)')
        plt.title(f'90th Percentile Price Impact for {product}\n(Worst-case scenario)')
        
        for i in range(len(alpha_vals)):
            for j in range(len(beta_vals)):
                if not np.isnan(heatmap_data[i, j]):
                    plt.text(j, i, f'£{heatmap_data[i, j]:.2f}',
                            ha='center', va='center', color='black', fontsize=9)
        
        plt.tight_layout()
        safe_name = product.replace('/', '_').replace('\\', '_').replace(' ', '_')
        plt.savefig(f"p90_impact_{safe_name}.png", dpi=150)
        plt.close()
        print(f"Saved 90th percentile heatmap for: {product}")

    # =========================================================================
    # COMBINED SUMMARY: 1D plot of alpha effect (averaging over beta)
    # =========================================================================
    plt.figure(figsize=(12, 6))
    
    for product in all_products:
        alpha_means = []
        for a in alpha_vals[1:]:  # Skip baseline
            vals = []
            for b in beta_vals:
                key = (product, a, b)
                if key in price_diffs and price_diffs[key]:
                    taker_pct = 100 * sum(1 for d in price_diffs[key] if abs(d) < TAKER_THRESHOLD) / len(price_diffs[key])
                    vals.append(taker_pct)
            if vals:
                alpha_means.append(np.mean(vals))
            else:
                alpha_means.append(np.nan)
        
        if any(not np.isnan(v) for v in alpha_means):
            plt.plot(alpha_vals[1:], alpha_means, 'o-', label=product, alpha=0.8, markersize=8)
    
    plt.axhline(y=50, color='gray', linestyle='--', alpha=0.5, label='50% threshold')
    plt.xlabel('Alpha (Quantity Multiplier)', fontsize=12)
    plt.ylabel('Price Taker Frequency (%)', fontsize=12)
    plt.title(f'Effect of Quantity Scaling on Price Taker Status\n(Averaged across all Beta values, threshold=£{TAKER_THRESHOLD:.2f})')
    plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig("alpha_effect_summary.png", dpi=150)
    plt.close()
    print("Saved alpha effect summary")

    # =========================================================================
    # COMBINED SUMMARY: 1D plot of beta effect (averaging over alpha)
    # =========================================================================
    plt.figure(figsize=(12, 6))
    
    for product in all_products:
        beta_means = []
        for b in beta_vals[1:]:  # Skip baseline
            vals = []
            for a in alpha_vals:
                key = (product, a, b)
                if key in price_diffs and price_diffs[key]:
                    taker_pct = 100 * sum(1 for d in price_diffs[key] if abs(d) < TAKER_THRESHOLD) / len(price_diffs[key])
                    vals.append(taker_pct)
            if vals:
                beta_means.append(np.mean(vals))
            else:
                beta_means.append(np.nan)
        
        if any(not np.isnan(v) for v in beta_means):
            plt.plot(beta_vals[1:], beta_means, 's-', label=product, alpha=0.8, markersize=8)
    
    plt.axhline(y=50, color='gray', linestyle='--', alpha=0.5, label='50% threshold')
    plt.xlabel('Beta (Price Multiplier)', fontsize=12)
    plt.ylabel('Price Taker Frequency (%)', fontsize=12)
    plt.title(f'Effect of Price Scaling on Price Taker Status\n(Averaged across all Alpha values, threshold=£{TAKER_THRESHOLD:.2f})')
    plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig("beta_effect_summary.png", dpi=150)
    plt.close()
    print("Saved beta effect summary")

    print("\n=== All plots generated ===")

