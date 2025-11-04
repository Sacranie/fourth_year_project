from eac.models import SellOrder, Basket
from eac.Validators import validate_unit_capacity, build_loop_families 


# Test to validate capacity 
def test_validate_capacity_ok():
    baskets = {"A": Basket(id="A", unit="U1", concomitant=[], looped_to=None)}
    sells = [
        SellOrder(id="S1", basket="A", qty={"P1": 30}, price=10.0, type="parent"),
        SellOrder(id="S2", basket="A", qty={"P1": 20}, price=5.0, type="child"),
        SellOrder(id="S3", basket="A", qty={"P1": 10}, price=3.0, type="substitutable_child"),
    ]
    registry = {"U1": 100.0}
    problems = validate_unit_capacity(sells, baskets, registry)
    assert problems == []


def test_validate_capacity_violation_parent_child_substitutable():
    baskets = {"A": Basket(id="A", unit="U1", concomitant=[], looped_to=None)}
    sells = [
        SellOrder(id="S1", basket="A", qty={"P1": 46}, price=10.0, type="parent"),
        SellOrder(id="S2", basket="A", qty={"P1": 30}, price=5.0, type="child"),
        SellOrder(id="S3", basket="A", qty={"P1": 25}, price=3.0, type="substitutable_child"),
    ]
    registry = {"U1": 100.0}
    problems = validate_unit_capacity(sells, baskets, registry)
    assert any("violates capacity" in p for p in problems)


def test_validate_undefined_basket_and_missing_capacity():
    baskets = {"A": Basket(id="A", unit="U1", concomitant=[], looped_to=None)}
    sells = [
        SellOrder(id="S1", basket="A", qty={"P1": 10}, price=10.0, type="parent"),
        SellOrder(id="S2", basket="B", qty={"P1": 5}, price=5.0, type="child"),  # B undefined
    ]
    registry = {}  # missing capacity for U1
    problems = validate_unit_capacity(sells, baskets, registry)
    assert any("Undefined basket B" in p for p in problems)
    assert any("Unit capacity not registered for unit U1" in p for p in problems)


def test_substitutable_children_counted_only_max():
    baskets = {"A": Basket(id="A", unit="U1", concomitant=[], looped_to=None)}
    sells = [
        SellOrder(id="S1", basket="A", qty={"P1": 40}, price=10.0, type="parent"),
        SellOrder(id="S2", basket="A", qty={"P1": 20}, price=5.0, type="child"),
        SellOrder(id="S3", basket="A", qty={"P1": 25}, price=3.0, type="substitutable_child"),
        SellOrder(id="S4", basket="A", qty={"P1": 30}, price=4.0, type="substitutable_child"),
    ]
    registry = {"U1": 90.0}
    problems = validate_unit_capacity(sells, baskets, registry)
    assert problems == []


def test_zero_and_negative_capacity_behaviour():
    # Zero capacity should flag violation if any positive quantity present.
    baskets = {"A": Basket(id="A", unit="U1", concomitant=[], looped_to=None)}
    sells = [SellOrder(id="S1", basket="A", qty={"P1": 1}, price=10.0, type="parent")]
    problems = validate_unit_capacity(sells, baskets, {"U1": 0.0})
    assert any("violates capacity" in p for p in problems)

    # Negative capacity (invalid) should also produce violation
    problems = validate_unit_capacity(sells, baskets, {"U1": -10.0})
    assert any("violates capacity" in p for p in problems)

# If there are no sell orders for a basket, nothing should be reported (no sells_by_basket entries).
def test_no_sell_orders():
    baskets = {"A": Basket(id="A", unit="U1", concomitant=[], looped_to=None)}
    sells = []
    problems = validate_unit_capacity(sells, baskets, {"U1": 10.0})
    assert problems == []

# Logic to test building loop families
def test_chained_and_multi_node_loop_families():
    baskets = {
        "A": Basket(id="A", unit="U1", concomitant=[], looped_to="B"),
        "B": Basket(id="B", unit="U2", concomitant=[], looped_to="A"),
        "C": Basket(id="C", unit="U3", concomitant=[], looped_to="D"),
        "D": Basket(id="D", unit="U4", concomitant=[], looped_to="C"),
        "E": Basket(id="E", unit="U5", concomitant=[], looped_to=None),
        "F": Basket(id="F", unit="U6", concomitant=[], looped_to="G"),
        "G": Basket(id="G", unit="U7", concomitant=[], looped_to="H"),
        "H": Basket(id="H", unit="U8", concomitant=[], looped_to="F"),
    }
    families = build_loop_families(baskets)
    fam_sets = [set(f) for f in families]
    assert {"A", "B"} in fam_sets
    assert {"C", "D"} in fam_sets
    assert {"F", "G", "H"} in fam_sets
    # ensure there are no size-1 families
    assert all(len(f) > 1 for f in fam_sets)