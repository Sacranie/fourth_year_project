from eac.models import SellOrder, Basket
from eac.Validators import validate_unit_capacity, build_loop_families 


# Test to validate capacity 
def test_validate_capacity_ok():
    sells = [
        SellOrder(auctionID=1, registeredAuctionParticipant="P1", auctionUnit="U1", basketID=1, 
                  service="S1", deliveryStart="2025-01-01", deliveryEnd="2025-01-31", 
                  orderID="S1", orderType="parent", auctionProduct="PROD1", quantity=30, price=10.0),
        SellOrder(auctionID=1, registeredAuctionParticipant="P1", auctionUnit="U1", basketID=1, 
                  service="S1", deliveryStart="2025-01-01", deliveryEnd="2025-01-31", 
                  orderID="S2", orderType="child", auctionProduct="PROD1", quantity=20, price=5.0),
        SellOrder(auctionID=1, registeredAuctionParticipant="P1", auctionUnit="U1", basketID=1, 
                  service="S1", deliveryStart="2025-01-01", deliveryEnd="2025-01-31", 
                  orderID="S3", orderType="substitutable_child", auctionProduct="PROD1", quantity=10, price=3.0),
    ]
    registry = {"U1": 100.0}
    problems = validate_unit_capacity(sells, registry)
    assert problems == []


def test_validate_capacity_violation_parent_child_substitutable():
    """Test that orders exceeding capacity are flagged."""
    sells = [
        SellOrder(auctionID=1, registeredAuctionParticipant="P1", auctionUnit="U1", basketID=1, 
                  service="S1", deliveryStart="2025-01-01", deliveryEnd="2025-01-31", 
                  orderID="S1", orderType="parent", auctionProduct="PROD1", quantity=46, price=10.0),
        SellOrder(auctionID=1, registeredAuctionParticipant="P1", auctionUnit="U1", basketID=1, 
                  service="S1", deliveryStart="2025-01-01", deliveryEnd="2025-01-31", 
                  orderID="S2", orderType="child", auctionProduct="PROD1", quantity=30, price=5.0),
        SellOrder(auctionID=1, registeredAuctionParticipant="P1", auctionUnit="U1", basketID=1, 
                  service="S1", deliveryStart="2025-01-01", deliveryEnd="2025-01-31", 
                  orderID="S3", orderType="substitutable_child", auctionProduct="PROD1", quantity=25, price=3.0),
    ]
    registry = {"U1": 100.0}
    problems = validate_unit_capacity(sells, registry)
    assert any("violates capacity" in p for p in problems)


def test_validate_missing_capacity():
    """Test that missing capacity registration is flagged."""
    sells = [
        SellOrder(auctionID=1, registeredAuctionParticipant="P1", auctionUnit="U1", basketID=1, 
                  service="S1", deliveryStart="2025-01-01", deliveryEnd="2025-01-31", 
                  orderID="S1", orderType="parent", auctionProduct="PROD1", quantity=10, price=10.0),
    ]
    registry = {}  # missing capacity for U1
    problems = validate_unit_capacity(sells, registry)
    assert any("Unit capacity not registered for unit U1" in p for p in problems)


def test_substitutable_children_counted_only_max():
    """Test that only the maximum substitutable child is counted."""
    sells = [
        SellOrder(auctionID=1, registeredAuctionParticipant="P1", auctionUnit="U1", basketID=1, 
                  service="S1", deliveryStart="2025-01-01", deliveryEnd="2025-01-31", 
                  orderID="S1", orderType="parent", auctionProduct="PROD1", quantity=40, price=10.0),
        SellOrder(auctionID=1, registeredAuctionParticipant="P1", auctionUnit="U1", basketID=1, 
                  service="S1", deliveryStart="2025-01-01", deliveryEnd="2025-01-31", 
                  orderID="S2", orderType="child", auctionProduct="PROD1", quantity=20, price=5.0),
        SellOrder(auctionID=1, registeredAuctionParticipant="P1", auctionUnit="U1", basketID=1, 
                  service="S1", deliveryStart="2025-01-01", deliveryEnd="2025-01-31", 
                  orderID="S3", orderType="substitutable_child", auctionProduct="PROD1", quantity=25, price=3.0),
        SellOrder(auctionID=1, registeredAuctionParticipant="P1", auctionUnit="U1", basketID=1, 
                  service="S1", deliveryStart="2025-01-01", deliveryEnd="2025-01-31", 
                  orderID="S4", orderType="substitutable_child", auctionProduct="PROD1", quantity=30, price=4.0),
    ]
    registry = {"U1": 90.0}
    problems = validate_unit_capacity(sells, registry)
    assert problems == []


def test_zero_and_negative_capacity_behaviour():
    """Test that zero and negative capacity flags violations."""
    sells = [SellOrder(auctionID=1, registeredAuctionParticipant="P1", auctionUnit="U1", basketID=1, 
                       service="S1", deliveryStart="2025-01-01", deliveryEnd="2025-01-31", 
                       orderID="S1", orderType="parent", auctionProduct="PROD1", quantity=1, price=10.0)]
    problems = validate_unit_capacity(sells, {"U1": 0.0})
    assert any("violates capacity" in p for p in problems)

    problems = validate_unit_capacity(sells, {"U1": -10.0})
    assert any("violates capacity" in p for p in problems)


def test_no_sell_orders():
    """Test that no problems are reported when there are no sell orders."""
    sells = []
    problems = validate_unit_capacity(sells, {"U1": 10.0})
    assert problems == []


def test_multiple_time_windows_same_unit():
    """Test that different time windows for the same unit are validated separately."""
    sells = [
        # Window 1: 2025-01-01 to 2025-01-31
        SellOrder(auctionID=1, registeredAuctionParticipant="P1", auctionUnit="U1", basketID=1, 
                  service="S1", deliveryStart="2025-01-01", deliveryEnd="2025-01-31", 
                  orderID="S1", orderType="parent", auctionProduct="PROD1", quantity=50, price=10.0),
        # Window 2: 2025-02-01 to 2025-02-28
        SellOrder(auctionID=1, registeredAuctionParticipant="P1", auctionUnit="U1", basketID=2, 
                  service="S1", deliveryStart="2025-02-01", deliveryEnd="2025-02-28", 
                  orderID="S2", orderType="parent", auctionProduct="PROD1", quantity=60, price=10.0),
    ]
    registry = {"U1": 100.0}
    problems = validate_unit_capacity(sells, registry)
    assert problems == []


def test_different_units_independent():
    """Test that different units are validated independently."""
    sells = [
        SellOrder(auctionID=1, registeredAuctionParticipant="P1", auctionUnit="U1", basketID=1, 
                  service="S1", deliveryStart="2025-01-01", deliveryEnd="2025-01-31", 
                  orderID="S1", orderType="parent", auctionProduct="PROD1", quantity=80, price=10.0),
        SellOrder(auctionID=1, registeredAuctionParticipant="P1", auctionUnit="U2", basketID=2, 
                  service="S1", deliveryStart="2025-01-01", deliveryEnd="2025-01-31", 
                  orderID="S2", orderType="parent", auctionProduct="PROD1", quantity=150, price=10.0),
    ]
    registry = {"U1": 100.0, "U2": 100.0}
    problems = validate_unit_capacity(sells, registry)
    assert any("violates capacity" in p for p in problems)
    assert any("U2" in p for p in problems)

# Logic to test building loop families
def test_chained_and_multi_node_loop_families():
    """Test that baskets with the same looped_to value are grouped together."""
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
    assert families[("460", 1)] == ["A"]
    assert families[("460", 2)] == ["B"]


def test_no_looped_baskets():
    """Test that baskets with no looped_to value are not included."""
    baskets = [
        Basket(id="A", auctionID=1, unit="U1", concomitant=[], looped_to=None),
        Basket(id="B", auctionID=1, unit="U2", concomitant=[], looped_to=None),
    ]
    families = build_loop_families(baskets)
    # No families should be created
    assert len(families) == 0