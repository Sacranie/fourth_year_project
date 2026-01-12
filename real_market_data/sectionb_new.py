from typing import Dict, List, Tuple, Set, Optional
from collections import defaultdict
from decimal import Decimal, getcontext
import json
import urllib.request
import urllib.parse
import pulp

from eac.models import SellOrder, BuyOrder, Basket
from eac.multi_product_orders import group_multi_product_orders
from eac.Volume import VolumeMILP
from eac.solver import PulpSolverBackend
from eac.Validators import validate_unit_capacity, build_loop_families

# NESO API endpoints
SELL_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=13b511df-d6ec-4143-afb1-0ecc6fd19810"
BUY_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=1cf68f59-8eb8-4f1d-bccf-11b5a47b24e5"

ACCEPTANCE_TOLERANCE = 0.01  # Compare within 1%


def load_orders_for_auction(base_url: str, auction_id: int, limit: int) -> List[Dict]:
    """Load all orders for a specific Auction ID using the NESO datastore_search API."""
    filters = {"auctionID": auction_id}
    filters_json = json.dumps(filters)
    url = f"{base_url}&limit={limit}&filters={urllib.parse.quote(filters_json)}"
    try:
        with urllib.request.urlopen(url) as response:
            data = json.load(response)
    except Exception as e:
        print(f"Error loading data: {e}")
        return []
    return data.get("result", {}).get("records", [])


def process_sell_orders(sell_records: List[Dict], auction_unit: str) -> Tuple[List, List[SellOrder], List[SellOrder]]:
    """
    Build logical SellOrder objects from raw API rows.
    Separates orders into fixed orders and changing orders (those matching auction_unit).
    
    Returns:
        (multi_orders, all_sell_orders, changing_sell_orders)
    """
    all_sell_orders = []
    changing_sell_orders = []
    rejected_count = 0
    executed_count = 0
    partial_count = 0

    for row in sell_records:
        status = str(row.get("status", "")).strip().upper()
        order_id = int(row.get("orderID", 0))

        if status == "REJECTED":
            rejected_count += 1
        else:
            if status == "EXECUTED":
                executed_count += 1
            else:
                partial_count += 1

        sell_order = SellOrder(
            auctionID=int(row.get("auctionID", 0) or 0),
            registeredAuctionParticipant=str(row.get("registeredAuctionParticipant", "") or ""),
            auctionUnit=str(row.get("auctionUnit", "") or ""),
            basketID=int(row.get("basketID", 0) or 0),
            service=str(row.get("service", "") or ""),
            deliveryStart=str(row.get("deliveryStart", "") or ""),
            deliveryEnd=str(row.get("deliveryEnd", "") or ""),
            orderID=int(order_id),
            orderType=str(row.get("orderType", "parent")).lower(),
            auctionProduct=str(row.get("auctionProduct", "") or ""),
            quantity=float(row.get("quantity", 0.0) or 0.0),
            price=float(row.get("priceLimit", row.get("price", 0.0) or 0.0)),
            orderEntryTime=str(row.get("orderEntryTime", "") or ""),
            product_id=str(row.get("productID", "") or ""),
            status=status,
            min_acceptance_ratio=row.get("minAcceptanceRatio", 0.0) or 0.0,
        )
        
        if auction_unit != sell_order.auctionUnit:
            all_sell_orders.append(sell_order)
        else:
            changing_sell_orders.append(sell_order)

    multi_orders = group_multi_product_orders(all_sell_orders)

    # We need to now iterate through the multi_orders and adjust the acceptance ratios
    # If they were rejected we should se to 1 and if they were executed or partially executed we should set to 1
    
    for orders in multi_orders:
        if not orders.is_accepted:
            orders.acceptance = 1.0
    


    return multi_orders, all_sell_orders, changing_sell_orders


def process_buy_orders(buy_records: List[Dict], auction_id: Optional[int] = None) -> List[BuyOrder]:
    """
    Build BuyOrder objects from raw API records.
    
    Args:
        buy_records: Raw records from the NESO API
        auction_id: Fallback auction ID if row lacks auctionID
    
    Returns:
        List of BuyOrder objects
    """
    all_buy_orders = []

    for row in buy_records:
        status = str(row.get("status", "")).strip().upper()
        order_id = row.get("orderID", 0)

        if status == "REJECTED":
            min_acceptance = 1.0 # force reject
        else:
            min_acceptance = row.get("acceptanceRatio", 0.0)

        raw = row.get("paradoxicallyAcceptanceAllowed", "false")
        paradoxical = (str(raw).lower() == "true")

        buy_order = BuyOrder(
            auctionID=auction_id,
            orderID=order_id,
            service=str(row.get("service", "") or ""),
            auctionProduct=str(row.get("auctionProduct", "") or ""),
            deliveryStart=str(row.get("deliveryStart", "") or ""),
            deliveryEnd=str(row.get("deliveryEnd", "") or ""),
            quantity=float(row.get("quantity", 0.0) or 0.0),
            price=float(row.get("price", 0.0) or 0.0),
            paradoxical=paradoxical,
            min_acceptance_ratio=min_acceptance,
        )

        all_buy_orders.append(buy_order)

    print(f"\nBuy Orders: {len(all_buy_orders)} loaded")
    return all_buy_orders

def build_baskets_from_orders(sell_orders: List[SellOrder], raw_records: List[Dict]) -> List[Basket]:

    baskets = {}
    for s in sell_orders:
        bid = int(s.basketID)
        if bid not in baskets:
            baskets[bid] = Basket(id=bid, auctionID=int(s.auctionID), unit=s.auctionUnit, looped_to=None, concomitant=[])

    concomitance = defaultdict(set)

    # populate looped_to and concomitant fields
    for row in raw_records:
        b_id = row.get("basketID")

        if b_id not in baskets:
            continue

        # loopedBasketID may be absent/empty
        looped = row.get("loopedBasketID")
        if looped not in (None, "", "None"):
            baskets[b_id].looped_to = int(looped)

        delivery_start = row.get("deliveryStart")
        delivery_end = row.get("deliveryEnd")
        unit = row.get("auctionUnit")

        concomitance[(unit, delivery_start, delivery_end)].add(int(b_id))

    for (unit, delivery_start, delivery_end), basket_ids in concomitance.items():
        for basket_id in basket_ids:
            baskets[basket_id].concomitant = list(basket_ids - {basket_id})

    print(f"\nBaskets built: {len(baskets)} (concomitant/loop info where present)")
    return list(baskets.values())


if __name__ == "__main__":
    auction_ids = [1112, 1114, 1116, 1118, 1120, 1122, 1124, 1126, 1128, 1130, 1132, 1134, 1136, 1138, 1140, 1142, 1144, 1146, 1148, 1150, 1152, 1154, 1156, 1158, 1189, 1222, 1224, 1226, 1228, 1230, 1232, 1234, 1236, 1255, 1257, 1259, 1261, 1263, 1288, 1290, 1292, 1294, 1296, 1298, 1300]
    welfare = []
    AUCTION_UNIT = "GSET-02"  # The auction unit we want to vary
    cumulative_revenue = [0.0]
    for auction_index, auction_id in enumerate(auction_ids):
        print(f"\n\n=== Processing Auction ID: {auction_id} ===")
        AUCTION_ID = auction_id
        TEST_LIMIT = 1000000

        sell_records = load_orders_for_auction(SELL_URL, auction_id=AUCTION_ID, limit=TEST_LIMIT)
        buy_records = load_orders_for_auction(BUY_URL, auction_id=AUCTION_ID, limit=TEST_LIMIT)

        # Process orders
        multi_orders, sell_orders, changing_sell_orders = process_sell_orders(sell_records, auction_unit=AUCTION_UNIT)
        buy_orders = process_buy_orders(buy_records, auction_id=AUCTION_ID)
        
        # Build baskets and extract loop families (pass raw sell_records to populate concomitant/loop info)
        baskets = build_baskets_from_orders(sell_orders, sell_records)
        
        # Extract unique products
        products = set(o.auctionProduct for o in sell_orders) | set(o.auctionProduct for o in buy_orders)

        # Extract unique units for capacity registry
        units = set(order.auctionUnit for order in sell_orders)
        unit_capacity_registry = {unit: 1e9 for unit in units}  # Set very high capacity for each unit

        # add unit capacity for the auction unit we are testing
        unit_capacity_registry[AUCTION_UNIT] = 1e9  # Set very high capacity for the test unit

        alpha = [1, 1.25, 1.5, 2, 3, 4]
        beta  = [1, 1.1, 1.2, 1.4, 1.6, 2]

        # Store original values to avoid compounding modifications
        original_values = []
        for order in changing_sell_orders:
            order.quantity = float(order.quantity)
            order.price = float(order.price)
            original_values.append((order.quantity, order.price))

        best_revenue = 0
        best_alpha, best_beta = 1, 1
        results = []  # Store all (alpha, beta, revenue) tuples
        
        backend = PulpSolverBackend(msg=0, time_limit=600)
        volume_milp = VolumeMILP(backend=backend)

        # Iterate through alpha/beta combinations to find optimal pricing
        for a in alpha:
            for b in beta:
                print(f"\nTesting parameters: alpha={a}, beta={b}")


                # Reset to original values then apply multipliers
                for i, order in enumerate(changing_sell_orders):
                    order.quantity = original_values[i][0] * a
                    order.price = original_values[i][1] * b
                multi_changing_sell_order = group_multi_product_orders(changing_sell_orders)
                
                # Apply the same acceptance treatment as fixed orders
                for orders in multi_changing_sell_order:
                    if not orders.is_accepted:
                        orders.acceptance = 1.0
                
                multi_orders.extend(multi_changing_sell_order)

                changing_baskets = build_baskets_from_orders(changing_sell_orders, sell_records)

                baskets.extend(changing_baskets)
        
                # Build and solve
                data = volume_milp.solve_with_pricing_loop(
                    products=list(products),
                    buy_orders=buy_orders,
                    sell_orders=multi_orders,
                    baskets=baskets,
                    unit_capacity_registry=unit_capacity_registry
                )

        
                if not data["final"]:
                    print(f"Auction {auction_id} failed to find valid clearing: {data.get('reason', 'Unknown')}")
                    # Still need to remove the orders we added before continuing
                    multi_orders = multi_orders[:-len(multi_changing_sell_order)]
                    baskets = baskets[:-len(changing_baskets)]
                    continue  # Skip to next alpha/beta combination
                
                # We would now need to go through that specific unit and find out the total revenue which is calcuated by 
                # summing up the accepted quantity multiplied by the market clearing price for each order in the auction unit we have chosen
                revenue = 0

                # Calculate profit for the new orders
                x_s_computed = data["x_s"]
                prices_unrounded = data["prices_unrounded"]

                if multi_changing_sell_order:
                    sample_key = multi_changing_sell_order[0].key
                    sample_window = multi_changing_sell_order[0].window

                for multi in multi_changing_sell_order:
                    acceptance = x_s_computed.get(multi.key, 0.0)
                    if acceptance > 0:
                        for frag in multi.fragments:
                            # Get MCP for this product in this window
                            # prices_unrounded keys are (product, window) tuples
                            mcp = prices_unrounded.get((frag.auctionProduct, multi.window), 0.0)
                            # Revenue = MCP * accepted_quantity
                            revenue += acceptance * frag.quantity * mcp
                print(f"  Revenue for changing orders at alpha={a}, beta={b}: {revenue}")
                results.append((a, b, revenue))
                if revenue > best_revenue:
                    best_revenue = revenue
                    best_alpha, best_beta = a, b
                
                multi_orders = multi_orders[:-len(multi_changing_sell_order)]

                baskets = baskets[:-len(changing_baskets)]

        if auction_index == 0:
            # I want to plot the alpha and betas and revenues for the first auction only
            import matplotlib.pyplot as plt
            alphas = [r[0] for r in results]
            betas  = [r[1] for r in results]
            revenues = [r[2] for r in results]
            fig = plt.figure(figsize=(10, 7))
            ax = fig.add_subplot(111, projection='3d')
            ax.scatter(alphas, betas, revenues, c=revenues, cmap='viridis', depthshade=True)
            ax.set_xlabel('Alpha')
            ax.set_ylabel('Beta')
            ax.set_zlabel('Revenue')
            ax.set_title('Revenue vs Alpha and Beta for First Auction')
            plt.show()
        
        print(f"Maximum Revenue for Auction {auction_id} at unit {AUCTION_UNIT}: {best_revenue}")
        print(f"  Best parameters: alpha={best_alpha}, beta={best_beta}")
        welfare.append({"auction_id": auction_id, "max_revenue": best_revenue, "best_alpha": best_alpha, "best_beta": best_beta, "all_results": results})
        cumulative_revenue.append(best_revenue + cumulative_revenue[-1])
    
    # We want to plot a cumulative revenue graph here
    print(f"\n\nCumulative Revenue across all auctions at unit {AUCTION_UNIT}: {cumulative_revenue}")
    import matplotlib.pyplot as plt
    plt.plot(range(len(cumulative_revenue)), cumulative_revenue, marker='o')
    plt.title(f"Cumulative Revenue across Auctions at Unit {AUCTION_UNIT}")
    plt.xlabel("Number of Auctions Processed")
    plt.ylabel("Cumulative Revenue")
    plt.grid(True)
    plt.savefig(f"cumulative_revenue_{AUCTION_UNIT}.png")
    plt.show()

    import numpy as np
    from matplotlib.gridspec import GridSpec
    best_alpha = np.array([w["best_alpha"] for w in welfare])
    best_beta  = np.array([w["best_beta"]  for w in welfare])
    fig = plt.figure(figsize=(8, 8))
    gs = GridSpec(4, 4, figure=fig)

    ax_joint = fig.add_subplot(gs[1:4, 0:3])
    ax_xhist = fig.add_subplot(gs[0, 0:3], sharex=ax_joint)
    ax_yhist = fig.add_subplot(gs[1:4, 3], sharey=ax_joint)

    # --- Joint scatter ---
    ax_joint.scatter(best_alpha, best_beta, alpha=0.6)
    ax_joint.set_xlabel(r"Best $\alpha$")
    ax_joint.set_ylabel(r"Best $\beta$")
    ax_joint.grid(True)

    alpha_med = np.median(best_alpha)
    beta_med  = np.median(best_beta)

    ax_joint.scatter(alpha_med, beta_med, color="red", marker="x", s=100, label="Median")
    ax_joint.legend()


    # --- Marginal histograms ---
    ax_xhist.hist(best_alpha, bins=6, edgecolor="black")
    ax_yhist.hist(best_beta, bins=6, orientation="horizontal", edgecolor="black")

    # Remove tick labels on marginal plots
    plt.setp(ax_xhist.get_xticklabels(), visible=False)
    plt.setp(ax_yhist.get_yticklabels(), visible=False)

    ax_xhist.set_ylabel("Count")
    ax_yhist.set_xlabel("Count")

    plt.tight_layout()
    plt.show()

    # Print summary of all auctions
    print("\n\n=== SUMMARY ===")
    for w in welfare:
        print(f"Auction {w['auction_id']}: Max Revenue={w['max_revenue']:.2f}, alpha={w['best_alpha']}, beta={w['best_beta']}")


