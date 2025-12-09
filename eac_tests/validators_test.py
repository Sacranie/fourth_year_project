from eac.models import SellOrder, Basket
from eac.Validators import validate_unit_capacity, build_loop_families 


# Test to validate capacity 
def test_validate_capacity_ok():
    baskets = [Basket(id="A", auctionID=1, unit="U1", concomitant=[], looped_to=None)]
    sells = [
        SellOrder(id="S1", basket="A", qty={"P1": 30}, price=10.0, type="parent", auctionID=1),
        SellOrder(id="S2", basket="A", qty={"P1": 20}, price=5.0, type="child", auctionID=1),
        SellOrder(id="S3", basket="A", qty={"P1": 10}, price=3.0, type="substitutable_child", auctionID=1),
    ]
    registry = {"U1": 100.0}
    problems = validate_unit_capacity(sells, baskets, registry)
    assert problems == []


def test_validate_capacity_violation_parent_child_substitutable():
    baskets = [Basket(id="A", auctionID=1, unit="U1", concomitant=[], looped_to=None)]
    sells = [
        SellOrder(id="S1", basket="A", qty={"P1": 46}, price=10.0, type="parent", auctionID=1),
        SellOrder(id="S2", basket="A", qty={"P1": 30}, price=5.0, type="child", auctionID=1),
        SellOrder(id="S3", basket="A", qty={"P1": 25}, price=3.0, type="substitutable_child", auctionID=1),
    ]
    registry = {"U1": 100.0}
    problems = validate_unit_capacity(sells, baskets, registry)
    assert any("violates capacity" in p for p in problems)


def test_validate_undefined_basket_and_missing_capacity():
    baskets = [Basket(id="A", auctionID=1, unit="U1", concomitant=[], looped_to=None)]
    sells = [
        SellOrder(id="S1", basket="A", qty={"P1": 10}, price=10.0, type="parent", auctionID=1),
        SellOrder(id="S2", basket="B", qty={"P1": 5}, price=5.0, type="child", auctionID=1),  # B undefined
    ]
    registry = {}  # missing capacity for U1
    problems = validate_unit_capacity(sells, baskets, registry)
    assert any("Undefined basket B" in p for p in problems)
    assert any("Unit capacity not registered for unit U1" in p for p in problems)


def test_substitutable_children_counted_only_max():
    baskets = [Basket(id="A", auctionID=1, unit="U1", concomitant=[], looped_to=None)]
    sells = [
        SellOrder(id="S1", basket="A", qty={"P1": 40}, price=10.0, type="parent", auctionID=1),
        SellOrder(id="S2", basket="A", qty={"P1": 20}, price=5.0, type="child", auctionID=1),
        SellOrder(id="S3", basket="A", qty={"P1": 25}, price=3.0, type="substitutable_child", auctionID=1),
        SellOrder(id="S4", basket="A", qty={"P1": 30}, price=4.0, type="substitutable_child", auctionID=1),
    ]
    registry = {"U1": 90.0}
    problems = validate_unit_capacity(sells, baskets, registry)
    assert problems == []


def test_zero_and_negative_capacity_behaviour():
    # Zero capacity should flag violation if any positive quantity present.
    baskets = [Basket(id="A", auctionID=1, unit="U1", concomitant=[], looped_to=None)]
    sells = [SellOrder(id="S1", basket="A", qty={"P1": 1}, price=10.0, type="parent", auctionID=1)]
    problems = validate_unit_capacity(sells, baskets, {"U1": 0.0})
    assert any("violates capacity" in p for p in problems)

    # Negative capacity (invalid) should also produce violation
    problems = validate_unit_capacity(sells, baskets, {"U1": -10.0})
    assert any("violates capacity" in p for p in problems)

# If there are no sell orders for a basket, nothing should be reported (no sells_by_basket entries).
def test_no_sell_orders():
    baskets = [Basket(id="A", auctionID=1, unit="U1", concomitant=[], looped_to=None)]
    sells = []
    problems = validate_unit_capacity(sells, baskets, {"U1": 10.0})
    assert problems == []

# Logic to test building loop families
def test_chained_and_multi_node_loop_families():
    baskets = [
        Basket(id="A", auctionID=1, unit="U1", concomitant=[], looped_to="460"),
        Basket(id="B", auctionID=1, unit="U1", concomitant=[], looped_to="460"),
        Basket(id="C", auctionID=1, unit="U1", concomitant=[], looped_to="460"),
        Basket(id="D", auctionID=1, unit="U1", concomitant=[], looped_to="460"),
    ]
    families = build_loop_families(baskets)
    # All baskets A, B, C, D looped_to "460", should be in one family
    assert ("460", 1) in families
    assert set(families[("460", 1)]) == {"A", "B", "C", "D"}


def test_looped_families_with_different_auction_IDs():
    """
    Baskets with the same looped_to value but different auctionIDs 
    should be in different families.
    """
    baskets = [
        Basket(id="A", auctionID=1, unit="U1", concomitant=[], looped_to="460"),
        Basket(id="B", auctionID=2, unit="U2", concomitant=[], looped_to="460"),
    ]
    families = build_loop_families(baskets)
    assert ("460", 1) in families
    assert ("460", 2) in families