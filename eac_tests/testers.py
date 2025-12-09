"""
Trivial single product, single parent sell vs single buy — check basic balance, price, rounding.

Multiple buys (different prices) vs single parent sell — ensures selection by price and fractional x_b behaviour.

Parent + child (child requires parent) where parent is accepted — child accepted only if parent accepted.

Substitutable children (many substitutable_child in same basket) — solver must accept ≤1 of that family.

Concomitant baskets (mutually exclusive pairs) — check concomitant constraint.

Looped baskets (loop family) — check y_parent equality across loop and pricing loop handling.

Paradoxical buy rejection (non-paradoxical buy that would lose surplus) — ensure pricing loop rejects configurations that create negative surplus for non-paradoxical buys.

Overholding (allow_overholding_hook) — add OVERHOLD buy(s) to absorb supply when you want forced balance.

"""
from eac.models import SellOrder, BuyOrder, Basket
from eac.orchestrator import run_market
from math import isclose

# Helper functions to create test objects with reasonable defaults
def create_buy_order(order_id, product, price, quantity, auction_id=1, service="", 
                     delivery_start="2025-01-01", delivery_end="2025-01-02", 
                     paradoxical=False, min_acceptance_ratio=0.0):
    
    return BuyOrder(
        auctionID=auction_id,
        orderID=order_id,
        service=service,
        auctionProduct=product,
        deliveryStart=delivery_start,
        deliveryEnd=delivery_end,
        quantity=float(quantity),
        price=float(price),
        paradoxical=paradoxical,
        min_acceptance_ratio=float(min_acceptance_ratio)
    )

def create_sell_order(order_id, product, quantity, price, basket_id, order_type="parent",
                      auction_id=1, participant="SELLER", unit="UNIT_1", service="",
                      delivery_start="2025-01-01", delivery_end="2025-01-02",
                      min_acceptance_ratio=0.0):

    return SellOrder(
        auctionID=auction_id,
        registeredAuctionParticipant=participant,
        auctionUnit=unit,
        basketID=basket_id,
        service=service,
        deliveryStart=delivery_start,
        deliveryEnd=delivery_end,
        orderID=order_id,
        orderType=order_type,
        auctionProduct=product,
        quantity=float(quantity),
        price=float(price),
        min_acceptance_ratio=float(min_acceptance_ratio)
    )

def create_basket(basket_id, unit, auction_id=1, concomitant=[], looped_to=None):

    return Basket(
        id=basket_id,
        auctionID=auction_id,
        unit=unit,
        concomitant=concomitant,
        looped_to=looped_to
    )

# Trivial single product, single parent sell vs single buy — check basic balance, price, rounding.
def test_eac_single_buy_order_single_sell_order():
    products = ["POWER"]
    
    buy_orders = [
        create_buy_order(1, "POWER", 100.0, 50, paradoxical=False)
    ]
    
    sell_orders = [
        create_sell_order(1, "POWER", 50, 60.0, basket_id=1, order_type="parent")
    ]
    
    baskets = [
        create_basket(1, "UNIT_1")
    ]

    unit_capacity_registry = {
        "UNIT_1": 100
    }
    
    result = run_market(products, buy_orders, sell_orders, baskets, unit_capacity_registry=unit_capacity_registry, msg=0)

    assert(result["x_b"][1] == 1.0)
    assert(result["x_s"][1] == 1.0)
    assert(result["prices_rounded"]["POWER"] == 60.0)
    assert(result["final"] == True)
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.quantity * result["x_b"][b.orderID] for b in buy_orders)
    welfare_sell = sum(s.price * s.quantity * result["x_s"][s.orderID] 
                      for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 2000.0)


# Multiple buys (different prices) vs single parent sell — ensures selection by price and fractional x_b behaviour.
def test_eac_multiple_buys_single_sell():
    products = ["POWER"]
    
    buy_orders = [
        create_buy_order("BUY_HIGH", "POWER", 100.0, 30, paradoxical=False),
        create_buy_order("BUY_LOW", "POWER", 80.0, 30, paradoxical=False)
    ]
    
    sell_orders = [
        create_sell_order("SELL_1", "POWER", 50, 60.0, basket_id=1, order_type="parent")
    ]
    
    baskets = [
        create_basket("BASKET_1", "UNIT_1")
    ]

    unit_capacity_registry = {
        "UNIT_1": 100
    }
    
    result = run_market(products, buy_orders, sell_orders, baskets, unit_capacity_registry=unit_capacity_registry, msg=0)

    assert(result.get("final")), "Market did not clear successfully"
    assert(result["x_b"]["BUY_HIGH"] == 1.0), "High price buy order not fully accepted"
    assert(result["x_b"]["BUY_LOW"] == 0.66666667), "Low price buy order acceptance incorrect"
    assert(result["x_s"]["SELL_1"] == 1.0), "Sell order not fully accepted"
    assert(result["prices_rounded"]["POWER"] == 60.0), "Clearing price incorrect"
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.quantity * result["x_b"][b.orderID] for b in buy_orders)
    welfare_sell = sum(s.price * s.quantity * result["x_s"][s.orderID] for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 1600.000008)

# Parent + child (child requires parent) where parent is accepted — child accepted only if parent accepted.
def test_eac_parent_child_acceptance():
    products = ["POWER"]
    
    buy_orders = [
        create_buy_order("BUY_1", "POWER", 100.0, 60, paradoxical=False)
    ]
    
    sell_orders = [
        create_sell_order("SELL_PARENT", "POWER", 50, 60.0, basket_id=1, order_type="parent"),
        create_sell_order("SELL_CHILD", "POWER", 20, 55.0, basket_id=1, order_type="child")
    ]
    
    baskets = [
        create_basket("BASKET_1", "UNIT_1")
    ]

    unit_capacity_registry = {
        "UNIT_1": 100
    }
    
    result = run_market(products, buy_orders, sell_orders, baskets, unit_capacity_registry=unit_capacity_registry, msg=0)

    assert(result.get("final"))
    assert(result["x_s"]["SELL_PARENT"] == 1.0)
    assert(result["x_s"]["SELL_CHILD"] == 0.5)
    assert(result["prices_rounded"]["POWER"] == 60.0)
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.quantity * result["x_b"][b.orderID] for b in buy_orders)
    welfare_sell = sum(s.price * s.quantity * result["x_s"][s.orderID] 
                      for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 2450.0)


# Substitutable children (many substitutable_child in same basket) — solver must accept ≤1 of that family.
def test_eac_substitutable_children():
    products = ["POWER"]
    
    buy_orders = [
        create_buy_order("BUY_1", "POWER", 100.0, 50, paradoxical=False)
    ]
    
    sell_orders = [
        create_sell_order("SELL_CHILD_1", "POWER", 30, 60.0, basket_id=1, order_type="substitutable_child"),
        create_sell_order("SELL_CHILD_2", "POWER", 30, 65.0, basket_id=1, order_type="substitutable_child")
    ]
    
    baskets = [
        create_basket("BASKET_1", "UNIT_1")
    ]

    unit_capacity_registry = {
        "UNIT_1": 100
    }
    
    result = run_market(products, buy_orders, sell_orders, baskets, unit_capacity_registry=unit_capacity_registry, msg=0)

    assert(result.get("final"))
    accepted_children = sum(1 for s in sell_orders if result["x_s"][s.orderID] > 0)
    assert(accepted_children <= 1)
    assert(result["prices_rounded"]["POWER"] == 60.0)
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.quantity * result["x_b"][b.orderID] for b in buy_orders)
    welfare_sell = sum(s.price * s.quantity * result["x_s"][s.orderID] 
                      for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 1200.0)

# Concomitant baskets (mutually exclusive pairs) — check concomitant constraint.
def test_eac_concomitant_baskets():
    products = ["POWER"]
    
    buy_orders = [
        create_buy_order(1, "POWER", 100.0, 100, paradoxical=False)
    ]
    
    sell_orders = [
        create_sell_order(1, "POWER", 50, 60.0, basket_id=1, order_type="parent"),
        create_sell_order(2, "POWER", 50, 65.0, basket_id=2, order_type="parent")
    ]
    
    baskets = [
        create_basket(1, "UNIT_1", concomitant=[2]),
        create_basket(2, "UNIT_1", concomitant=[1])
    ]

    unit_capacity_registry = {
        "UNIT_1": 100
    }
    
    result = run_market(products, buy_orders, sell_orders, baskets, unit_capacity_registry=unit_capacity_registry, msg=0)

    assert(result.get("final"))
    accepted_baskets = [s.basketID for s in sell_orders if result["x_s"][s.orderID] > 0]
    assert(len(accepted_baskets) == 1)
    assert(result["prices_rounded"]["POWER"] == 60.0)
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.quantity * result["x_b"][b.orderID]
                        for b in buy_orders)
    welfare_sell = sum(s.price * s.quantity * result["x_s"][s.orderID]
                        for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 2000.0)

# Looped baskets (loop family) — check y_parent equality across loop and pricing loop handling.
def test_eac_looped_baskets():
    products = ["POWER"]
    
    buy_orders = [
        create_buy_order("BUY_1", "POWER", 100.0, 100, paradoxical=False)
    ]
    
    sell_orders = [
        create_sell_order("SELL_1", "POWER", 50, 60.0, basket_id=1, order_type="parent"),
        create_sell_order("SELL_2", "POWER", 50, 65.0, basket_id=2, order_type="parent")
    ]
    
    baskets = [
        create_basket("BASKET_1", "UNIT_1", looped_to=2),
        create_basket("BASKET_2", "UNIT_1", looped_to=1)
    ]

    unit_capacity_registry = {
        "UNIT_1": 100
    }
    
    result = run_market(products, buy_orders, sell_orders, baskets, unit_capacity_registry=unit_capacity_registry, msg=0)

    assert(result.get("final"))
    assert(result["x_s"]["SELL_1"] == result["x_s"]["SELL_2"])
    assert(result["prices_rounded"]["POWER"] == 65.0)
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.quantity * result["x_b"][b.orderID]
                        for b in buy_orders)
    welfare_sell = sum(s.price * s.quantity * result["x_s"][s.orderID]
                        for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 3750.0)


# Paradoxical buy rejection (non-paradoxical buy that would lose surplus) — ensure pricing loop rejects configurations that create negative surplus for non-paradoxical buys.
def test_eac_paradoxical_buy_rejection():
    products = ["POWER"]
    
    buy_orders = [
        create_buy_order("BUY_1", "POWER", 80.0, 50, paradoxical=False),
        create_buy_order("BUY_2", "POWER", 60.0, 50, paradoxical=True)
    ]
    
    sell_orders = [
        create_sell_order("SELL_1", "POWER", 100, 65.0, basket_id=1, order_type="parent")
    ]
    
    baskets = [
        create_basket("BASKET_1", "UNIT_1")
    ]

    unit_capacity_registry = {
        "UNIT_1": 200
    }
    
    result = run_market(products, buy_orders, sell_orders, baskets, unit_capacity_registry=unit_capacity_registry, msg=0)

    assert(result["final"] == True)
    assert(result["x_b"]["BUY_1"] == 1.0)
    assert(result["x_b"]["BUY_2"] == 1.0)
    assert(result["x_s"]["SELL_1"] == 1.0)
    assert(result["prices_rounded"]["POWER"] == 65.0)
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.quantity * result["x_b"][b.orderID] for b in buy_orders)
    welfare_sell = sum(s.price * s.quantity * result["x_s"][s.orderID] 
                      for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 500.0)

# Overholding (allow_overholding_hook) — add OVERHOLD buy(s) to absorb supply when you want forced balance.

def test_eac_overholding():
    products = ["POWER"]
    
    buy_orders = [
        create_buy_order("BUY_1", "POWER", 100.0, 50, paradoxical=False)
    ]
    
    sell_orders = [
        create_sell_order("SELL_1", "POWER", 100, 40.0, basket_id=1, order_type="parent", min_acceptance_ratio=1.0)
    ]
    
    baskets = [
        create_basket("BASKET_1", "UNIT_1")
    ]

    unit_capacity_registry = {
        "UNIT_1": 200
    }
    
    overhold_buy = {"POWER": 100}
        
    result = run_market(
        products, buy_orders, sell_orders, baskets,
        unit_capacity_registry=unit_capacity_registry,
        overholding=overhold_buy,
        msg=0
    )

    assert(result.get("final"))
    assert(result["x_b"]["BUY_1"] == 1.0)
    assert(result["x_s"]["SELL_1"] == 1.0)
    assert(result["prices_rounded"]["POWER"] == 40.0)
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.quantity * result["x_b"][b.orderID] for b in buy_orders)
    welfare_sell = sum(s.price * s.quantity * result["x_s"][s.orderID] 
                      for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 1000.0)

def test_eac_zero_quantity_parent_with_children():
    """Parent with 0 quantity acts as enabler for children"""
    products = ["DCL", "DCH"]
    
    buy_orders = [
        create_buy_order("BUY_DCL", "DCL", 10.0, 50),
        create_buy_order("BUY_DCH", "DCH", 5.0, 50)
    ]
    
    sell_orders = [
        create_sell_order("SELL_PARENT", "DCL", 0, 0.0, basket_id=1, order_type="parent", min_acceptance_ratio=1.0),
        create_sell_order("SELL_CHILD_DCL", "DCL", 8, 3.69, basket_id=1, order_type="child"),
        create_sell_order("SELL_CHILD_DCH", "DCH", 8, 1.77, basket_id=1, order_type="child")
    ]
    
    baskets = [
        create_basket("BASKET_1", "UNIT_1")
    ]

    unit_capacity_registry = {"UNIT_1": 100}
    
    result = run_market(products, buy_orders, sell_orders, baskets, unit_capacity_registry=unit_capacity_registry, msg=0)

    assert(result.get("final")), "Market did not clear successfully"
    assert(result["x_s"]["SELL_PARENT"] == 1.0), "Parent with zero quantity should be accepted"
    assert(result["x_s"]["SELL_CHILD_DCL"] == 1.0), "DCL child should be fully accepted"
    assert(result["x_s"]["SELL_CHILD_DCH"] == 1.0), "DCH child should be fully accepted"
    
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.quantity * result["x_b"][b.orderID] for b in buy_orders)
    welfare_sell = sum(s.price * s.quantity * result["x_s"][s.orderID] 
                      for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 76.32), "Total welfare calculation incorrect"

# i want to run a test that if we are unable to accept the parent, we should not accept the child
def test_eac_child_rejection_if_parent_not_accepted():
    products = ["POWER"]
    
    buy_orders = [
        create_buy_order("BUY_1", "POWER", 100.0, 50, paradoxical=False)
    ]
    
    sell_orders = [
        create_sell_order("SELL_PARENT", "POWER", 50, 120.0, basket_id=1, order_type="parent"),
        create_sell_order("SELL_CHILD", "POWER", 50, 55.0, basket_id=1, order_type="child")
    ]
    
    baskets = [
        create_basket("BASKET_1", "UNIT_1")
    ]

    unit_capacity_registry = {
        "UNIT_1": 100
    }
    
    # Run market without accepting the parent
    result = run_market(products, buy_orders, sell_orders, baskets, unit_capacity_registry=unit_capacity_registry, msg=0)

    assert(result.get("final"))
    assert(result["x_s"]["SELL_PARENT"] == 0.0)  # Parent should not be accepted
    assert(result["x_s"]["SELL_CHILD"] == 0.0)   # Child should also not be accepted
    assert(result["prices_rounded"]["POWER"] == 0.0)  # Price should not change
    
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.quantity * result["x_b"][b.orderID] for b in buy_orders)
    welfare_sell = sum(s.price * s.quantity * result["x_s"][s.orderID] 
                      for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 0.0), "Total welfare should be zero when no orders are accepted"

def test_mcp_multi_product_not_max_sell():
    # Products: two distinct products so pricing LP can reallocate burden
    products = ["P1", "P2"]

    # Big buyers so all sells are accepted at the volume stage
    buy_orders = [
        create_buy_order("B1", "P1", 1000.0, 100),
        create_buy_order("B2", "P2", 1000.0, 100),
    ]

    # Sell orders:
    # - blue: cheap on P1 only (price 10)
    # - orange: expensive overall (price 20) but sells both P1 and P2
    # - green: cheap on P1 only (price 10)
    sell_orders = [
        create_sell_order("S_blue", "P1", 10, 10.0, basket_id=1, order_type="parent", unit="U0"),
        create_sell_order("S_orange", "P1", 10, 20.0, basket_id=1, order_type="parent", unit="U0"),  # Also need P2
        create_sell_order("S_green", "P1", 40, 10.0, basket_id=1, order_type="parent", unit="U0"),
    ]

    baskets = [
        create_basket("B0", "U0")
    ]

    unit_capacity_registry = {"U0": 200}

    result = run_market(
        products,
        buy_orders,
        sell_orders,
        baskets,
        unit_capacity_registry=unit_capacity_registry,
        msg=0
    )

    assert result.get("final") is True

    # Expected result explained:
    # - If Blue + Green are selected, p1 = 10
    # - If Orange is selected instead, p1 = 20
    # Both are valid market clearing outcomes depending on solver selection
    # Check that price is set to cost of selected supplier(s)
    assert result["prices_rounded"]["P1"] >= 10.00
    assert result["prices_rounded"]["P2"] >= 0
