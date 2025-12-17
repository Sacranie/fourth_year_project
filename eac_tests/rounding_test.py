from eac.rounding import round_price_up_to_cent, rounding_and_residual_distribution
from eac import SellOrder, BuyOrder, Basket

# Test rounding of both positive and negative prices 
def test_round_price_up_to_cent():
    assert round_price_up_to_cent(10.331) == 10.34
    assert round_price_up_to_cent(10.339) == 10.34
    assert round_price_up_to_cent(10.330) == 10.33
    assert round_price_up_to_cent(-10.330) == -10.33
    assert round_price_up_to_cent(-10.339) == -10.33
    assert round_price_up_to_cent(-10.340) == -10.34

def test_comprehensive_rounding_with_residual():

    products = ["P1", "P2"]
    
    mcp_prices_val_unrounded = {"P1": 50.234, "P2": 60.567}
    
    x_s_val = {1: 0.78, 2: 0.75}
    sell_orders = [
        # Substitutable child: 10 * 0.78 = 7.8 → floor → 7
        SellOrder(
            auctionID=1, registeredAuctionParticipant="Participant1", auctionUnit="Unit1",
            basketID=1, service="Service1", deliveryStart="2025-01-01", deliveryEnd="2025-01-31",
            orderID=1, orderType="substitutable_child", auctionProduct="P1", quantity=10, price=45.0
        ),
        # Parent across P1 and P2: total=30, accept 30*0.75=22.5 → round → 23
        # Then distribute 23 proportionally: P1 gets 20*(23/30)=15.33→ 15, P2 gets 10*(23/30)=7.66→7
        # But 15+7=22, so need to distribute 1 more based on fractional remainder
        # That one more is given to P2 as it has the larger fractional part (0.33 vs 0.66)
        SellOrder(
            auctionID=1, registeredAuctionParticipant="Participant2", auctionUnit="Unit2",
            basketID=2, service="Service2", deliveryStart="2025-01-01", deliveryEnd="2025-01-31",
            orderID=2, orderType="parent", auctionProduct="P1", quantity=20, price=55.0
        ),
        SellOrder(
            auctionID=1, registeredAuctionParticipant="Participant2", auctionUnit="Unit2",
            basketID=2, service="Service2", deliveryStart="2025-01-01", deliveryEnd="2025-01-31",
            orderID=2, orderType="parent", auctionProduct="P2", quantity=10, price=65.0
        )
    ]
    # Buy orders
    x_b_val = {1: 0.75, 2: 0.65, 3: 0.80}
    buy_orders = [
        # P1: 10*0.75 = 7.5 → 8
        BuyOrder(
            auctionID=1, orderID=1, service="Service1", auctionProduct="P1",
            deliveryStart="2025-01-01", deliveryEnd="2025-01-31", quantity=10, price=55.0
        ),
        # P2: 10*0.65 = 6.5 → 7 
        BuyOrder(
            auctionID=1, orderID=2, service="Service2", auctionProduct="P2",
            deliveryStart="2025-01-01", deliveryEnd="2025-01-31", quantity=10, price=65.0
        ),
        # P1: 10*0.80 = 8.0 → 8
        BuyOrder(
            auctionID=1, orderID=3, service="Service3", auctionProduct="P1",
            deliveryStart="2025-01-01", deliveryEnd="2025-01-31", quantity=10, price=52.0
        )
    ]
    
    prices, sells, buys = rounding_and_residual_distribution(
        products, mcp_prices_val_unrounded, x_s_val, sell_orders, x_b_val, buy_orders
    )
    
    # Verify price rounding UP
    assert prices["P1"] == 50.24
    assert prices["P2"] == 60.57
    
    # Verify sell rounding
    assert sells[1] == 7  
    assert sells[2] == 8 
    
    # Total sells per product (after internal distribution of S2's 23)
    # P1: 7 (from S1) + 15 (from S2) = 22
    # P2: 8 (from S2)
    
    # Buy rounding before residual: B1=8, B3=8 (P1 total ~16), B2=7 (P2)
    # Residuals will be adjusted

    assert buys[1]  == 7  # P1 buys adjusted
    assert buys[2] == 8  # P2 buys adjusted
    assert buys[3] == 8  # P1 buys adjusted
