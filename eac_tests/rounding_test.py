from eac.rounding import round_price_up_to_cent, rounding_and_residual_distribution

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
    
    x_s_val = {"S1": 0.78, "S2": 0.75}
    sell_orders = [
        # Substitutable child: 10 * 0.78 = 7.8 → floor → 7
        {"id": "S1", "type": "substitutable_child", "qty": {"P1": 10}},
        # Parent across P1 and P2: total=30, accept 30*0.75=22.5 → round → 23
        # Then distribute 23 proportionally: P1 gets 20*(23/30)=15.33→ 15, P2 gets 10*(23/30)=7.66→7
        # But 15+7=22, so need to distribute 1 more based on fractional remainder
        # That one more is given to P1 as it has the larger fractional part (0.33 vs 0.66)
        {"id": "S2", "type": "parent", "qty": {"P1": 20, "P2": 10}}
    ]
    
    # Buy orders
    x_b_val = {"B1": 0.75, "B2": 0.65, "B3": 0.80}
    buy_orders = [
        # P1: 10*0.75 = 7.5 → 8
        {"id": "B1", "product": "P1", "volume": 10, "price": 55},
        # P2: 10*0.65 = 6.5 → 7 
        {"id": "B2", "product": "P2", "volume": 10, "price": 65},
        # P1: 10*0.80 = 8.0 → 8
        {"id": "B3", "product": "P1", "volume": 10, "price": 52}
    ]
    
    prices, sells, buys = rounding_and_residual_distribution(
        products, mcp_prices_val_unrounded, x_s_val, sell_orders, x_b_val, buy_orders
    )
    
    # Verify price rounding UP
    assert prices["P1"] == 50.24
    assert prices["P2"] == 60.57
    
    # Verify sell rounding
    assert sells["S1"] == 7  
    assert sells["S2"] == 23 
    
    # Total sells per product (after internal distribution of S2's 23)
    # P1: 7 (from S1) + 15 (from S2) = 22
    # P2: 7 (from S2)
    
    # Buy rounding before residual: B1=8, B3=8 (P1 total ~16), B2=7 (P2)
    # Residuals will be adjusted

    assert buys["B1"]  == 11  # P1 buys adjusted
    assert buys["B2"] == 8  # P2 buys adjusted
    assert buys["B3"] == 11  # P1 buys adjusted
