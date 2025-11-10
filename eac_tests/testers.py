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
from eac import run_market, SellOrder, BuyOrder, Basket

# Trivial single product, single parent sell vs single buy — check basic balance, price, rounding.
def test_eac_single_buy_order_single_sell_order():
    products = ["POWER"]
    
    buy_orders = [
        BuyOrder(
            id="BUY_1",
            product="POWER",
            price=100.0,
            volume=50,
            family=None,
            paradoxical=False,
            min_acceptance_ratio=0.0
        )
    ]
    
    sell_orders = [
        SellOrder(
            id="SELL_1",
            basket="BASKET_1",
            type="parent",
            qty={"POWER": 50},
            price=60.0,
            min_acceptance_ratio=0.0
        )
    ]
    
    baskets = {
        "BASKET_1": Basket(
            id="BASKET_1",
            unit="UNIT_1",
            concomitant=[],
            looped_to=None
        )
    }

    unit_capacity_registry = {
        "UNIT_1": 100
    }
    
    result = run_market(products, buy_orders, sell_orders, baskets, unit_capacity_registry=unit_capacity_registry, msg=0)

    assert(result["x_b"] == {"BUY_1": 1.0})
    assert(result["x_s"] == {"SELL_1": 1.0})
    assert(result["prices_rounded"] == {"POWER": 60.0})
    assert(result["final"] == True)
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.volume * result["x_b"][b.id] for b in buy_orders)
    welfare_sell = sum(s.price * sum(s.qty.values()) * result["x_s"][s.id] 
                      for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 2000.0)


# Multiple buys (different prices) vs single parent sell — ensures selection by price and fractional x_b behaviour.
def test_eac_multiple_buys_single_sell():
    products = ["POWER"]
    
    buy_orders = [
        BuyOrder(
            id="BUY_HIGH",
            product="POWER",
            price=100.0,
            volume=30,
            family=None,
            paradoxical=False,
            min_acceptance_ratio=0.0
        ),
        BuyOrder(
            id="BUY_LOW",
            product="POWER",
            price=80.0,
            volume=30,
            family=None,
            paradoxical=False,
            min_acceptance_ratio=0.0
        )
    ]
    
    sell_orders = [
        SellOrder(
            id="SELL_1",
            basket="BASKET_1",
            type="parent",
            qty={"POWER": 50},
            price=60.0,
            min_acceptance_ratio=0.0
        )
    ]
    
    baskets = {
        "BASKET_1": Basket(
            id="BASKET_1",
            unit="UNIT_1",
            concomitant=[],
            looped_to=None
        )
    }

    unit_capacity_registry = {
        "UNIT_1": 100
    }
    
    result = run_market(products, buy_orders, sell_orders, baskets, unit_capacity_registry=unit_capacity_registry, msg=0)

    assert(result.get("final")), "Market did not clear successfully"
    assert(result["x_b"]["BUY_HIGH"] == 1.0), "High price buy order not fully accepted"
    assert(result["x_b"]["BUY_LOW"] == 0.66666667), "Low price buy order acceptance incorrect"
    assert(result["x_s"]["SELL_1"] == 1.0), "Sell order not fully accepted"
    assert(result["prices_rounded"] == {"POWER": 60.0}), "Clearing price incorrect"
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.volume * result["x_b"][b.id] for b in buy_orders)
    welfare_sell = sum(s.price * sum(s.qty.values()) * result["x_s"][s.id] for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 1600.000008)

# Parent + child (child requires parent) where parent is accepted — child accepted only if parent accepted.
def test_eac_parent_child_acceptance():
    products = ["POWER"]
    
    buy_orders = [
        BuyOrder(
            id="BUY_1",
            product="POWER",
            price= 100.0,
            volume= 60,
            family=None,
            paradoxical=False,
            min_acceptance_ratio=0.0
        )
    ]
    
    sell_orders = [
        SellOrder(
            id="SELL_PARENT",
            basket="BASKET_1",
            type="parent",
            qty={"POWER": 50},
            price=60.0,
            min_acceptance_ratio=0.0
        ),
        SellOrder(
            id="SELL_CHILD",
            basket="BASKET_1",
            type="child",
            qty={"POWER": 20},
            price=55.0,
            min_acceptance_ratio=0.0
        )
    ]
    
    baskets = {
        "BASKET_1": Basket(
            id="BASKET_1",
            unit="UNIT_1",
            concomitant=[],
            looped_to=None
        )
    }

    unit_capacity_registry = {
        "UNIT_1": 100
    }
    
    result = run_market(products, buy_orders, sell_orders, baskets, unit_capacity_registry=unit_capacity_registry, msg=0)

    assert(result.get("final"))
    assert(result["x_s"]["SELL_PARENT"] == 1.0)
    assert(result["x_s"]["SELL_CHILD"] == 0.5)
    assert(result["prices_rounded"] == {"POWER": 60.0})
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.volume * result["x_b"][b.id] for b in buy_orders)
    welfare_sell = sum(s.price * sum(s.qty.values()) * result["x_s"][s.id] 
                      for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 2450.0)


# Substitutable children (many substitutable_child in same basket) — solver must accept ≤1 of that family.
def test_eac_substitutable_children():
    products = ["POWER"]
    
    buy_orders = [
        BuyOrder(
            id="BUY_1",
            product="POWER",
            price=100.0,
            volume=50,
            family=None,
            paradoxical=False,
            min_acceptance_ratio=0.0
        )
    ]
    
    sell_orders = [
        SellOrder(
            id="SELL_CHILD_1",
            basket="BASKET_1",
            type="substitutable_child",
            qty={"POWER": 30},
            price=60.0,
            min_acceptance_ratio=0.0
        ),
        SellOrder(
            id="SELL_CHILD_2",
            basket="BASKET_1",
            type="substitutable_child",
            qty={"POWER": 30},
            price=65.0,
            min_acceptance_ratio=0.0
        )
    ]
    
    baskets = {
        "BASKET_1": Basket(
            id="BASKET_1",
            unit="UNIT_1",
            concomitant=[],
            looped_to=None
        )
    }

    unit_capacity_registry = {
        "UNIT_1": 100
    }
    
    result = run_market(products, buy_orders, sell_orders, baskets, unit_capacity_registry=unit_capacity_registry, msg=0)

    assert(result.get("final"))
    accepted_children = sum(1 for s in sell_orders if result["x_s"][s.id] > 0)
    assert(accepted_children <= 1)
    assert(result["prices_rounded"] == {"POWER": 60.0})
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.volume * result["x_b"][b.id] for b in buy_orders)
    welfare_sell = sum(s.price * sum(s.qty.values()) * result["x_s"][s.id] 
                      for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 1200.0)

# Concomitant baskets (mutually exclusive pairs) — check concomitant constraint.
def test_eac_concomitant_baskets():
    products = ["POWER"]
    
    buy_orders = [
        BuyOrder(
            id="BUY_1",
            product="POWER",
            price=100.0,
            volume=100,
            family=None,
            paradoxical=False,
            min_acceptance_ratio=0.0
        )
    ]
    
    sell_orders = [
        SellOrder(
            id="SELL_1",
            basket="BASKET_1",
            type="parent",
            qty={"POWER": 50},
            price=60.0,
            min_acceptance_ratio=0.0
        ),
        SellOrder(
            id="SELL_2",
            basket="BASKET_2",
            type="parent",
            qty={"POWER": 50},
            price=65.0,
            min_acceptance_ratio=0.0
        )
    ]
    
    baskets = {
        "BASKET_1": Basket(
            id="BASKET_1",
            unit="UNIT_1",
            concomitant=["BASKET_2"],
            looped_to=None
        ),
        "BASKET_2": Basket(
            id="BASKET_2",
            unit="UNIT_1",
            concomitant=["BASKET_1"],
            looped_to=None
        )
    }

    unit_capacity_registry = {
        "UNIT_1": 100
    }
    
    result = run_market(products, buy_orders, sell_orders, baskets, unit_capacity_registry=unit_capacity_registry, msg=0)

    assert(result.get("final"))
    accepted_baskets = [s.basket for s in sell_orders if result["x_s"][s.id] > 0]
    assert(len(accepted_baskets) == 1)
    assert(result["prices_rounded"] == {"POWER": 60.0})
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.volume * result["x_b"][b.id]
                        for b in buy_orders)
    welfare_sell = sum(s.price * sum(s.qty.values()) * result["x_s"][s.id]
                        for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 2000.0)

# Looped baskets (loop family) — check y_parent equality across loop and pricing loop handling.
def test_eac_looped_baskets():
    products = ["POWER"]
    
    buy_orders = [
        BuyOrder(
            id="BUY_1",
            product="POWER",
            price=100.0,
            volume=100,
            family=None,
            paradoxical=False,
            min_acceptance_ratio=0.0
        )
    ]
    
    sell_orders = [
        SellOrder(
            id="SELL_1",
            basket="BASKET_1",
            type="parent",
            qty={"POWER": 50},
            price=60.0,
            min_acceptance_ratio=0.0
        ),
        SellOrder(
            id="SELL_2",
            basket="BASKET_2",
            type="parent",
            qty={"POWER": 50},
            price=65.0,
            min_acceptance_ratio=0.0
        )
    ]
    
    baskets = {
        "BASKET_1": Basket(
            id="BASKET_1",
            unit="UNIT_1",
            concomitant=[],
            looped_to="BASKET_2"
        ),
        "BASKET_2": Basket(
            id="BASKET_2",
            unit="UNIT_1",
            concomitant=[],
            looped_to="BASKET_1"
        )
    }

    unit_capacity_registry = {
        "UNIT_1": 100
    }
    
    result = run_market(products, buy_orders, sell_orders, baskets, unit_capacity_registry=unit_capacity_registry, msg=0)

    assert(result.get("final"))
    assert(result["x_s"]["SELL_1"] == result["x_s"]["SELL_2"])
    assert(result["prices_rounded"] == {"POWER": 65.0})
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.volume * result["x_b"][b.id]
                        for b in buy_orders)
    welfare_sell = sum(s.price * sum(s.qty.values()) * result["x_s"][s.id]
                        for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 3750.0)


# Paradoxical buy rejection (non-paradoxical buy that would lose surplus) — ensure pricing loop rejects configurations that create negative surplus for non-paradoxical buys.
def test_eac_paradoxical_buy_rejection():
    products = ["POWER"]
    
    buy_orders = [
        BuyOrder(
            id="BUY_1",
            product="POWER",
            price=80.0,
            volume=50,
            family=None,
            paradoxical=False,
            min_acceptance_ratio=0.0
        ),
        BuyOrder(
            id="BUY_2",
            product="POWER",
            price=60.0,
            volume=50,
            family=None,
            paradoxical=True,
            min_acceptance_ratio=0.0
        )
    ]
    
    sell_orders = [
        SellOrder(
            id="SELL_1",
            basket="BASKET_1",
            type="parent",
            qty={"POWER": 100},
            price=65.0,
            min_acceptance_ratio=0.0
        )
    ]
    
    baskets = {
        "BASKET_1": Basket(
            id="BASKET_1",
            unit="UNIT_1",
            concomitant=[],
            looped_to=None
        )
    }

    unit_capacity_registry = {
        "UNIT_1": 200
    }
    
    result = run_market(products, buy_orders, sell_orders, baskets, unit_capacity_registry=unit_capacity_registry, msg=0)

    assert(result["final"] == True)
    assert(result["x_b"]["BUY_1"] == 1.0)
    assert(result["x_b"]["BUY_2"] == 1.0)
    assert(result["x_s"]["SELL_1"] == 1.0)
    assert(result["prices_rounded"] == {"POWER":  65.0})
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.volume * result["x_b"][b.id] for b in buy_orders)
    welfare_sell = sum(s.price * sum(s.qty.values()) * result["x_s"][s.id] 
                      for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 500.0)

# Overholding (allow_overholding_hook) — add OVERHOLD buy(s) to absorb supply when you want forced balance.

def test_eac_overholding():
    products = ["POWER"]
    
    buy_orders = [
        BuyOrder(
            id="BUY_1",
            product="POWER",
            price=100.0,
            volume=50,
            family=None,
            paradoxical=False,
            min_acceptance_ratio=0.0
        )
    ]
    
    sell_orders = [
        SellOrder(
            id="SELL_1",
            basket="BASKET_1",
            type="parent",
            qty={"POWER": 100},
            price=40.0,
            min_acceptance_ratio=1.0
        )
    ]
    
    baskets = {
        "BASKET_1": Basket(
            id="BASKET_1",
            unit="UNIT_1",
            concomitant=[],
            looped_to=None
        )
    }

    unit_capacity_registry = {
        "UNIT_1": 200
    }
    
    overhold_buy = {"POWER": 100}
        
    result = run_market(
        products, buy_orders, sell_orders, baskets,
        unit_capacity_registry=unit_capacity_registry,
        overholding= overhold_buy,
        msg=0
    )

    assert(result.get("final"))
    assert(result["x_b"]["BUY_1"] == 1.0)
    assert(result["x_s"]["SELL_1"] == 1.0)
    assert(result["prices_rounded"] == {"POWER": 40.0})
    # Verify welfare calculation
    welfare_buy = sum(b.price * b.volume * result["x_b"][b.id] for b in buy_orders)
    welfare_sell = sum(s.price * sum(s.qty.values()) * result["x_s"][s.id] 
                      for s in sell_orders)
    total_welfare = welfare_buy - welfare_sell
    assert(total_welfare == 1000.0)



