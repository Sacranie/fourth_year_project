from dataclasses import dataclass
from typing import Mapping, Optional, List, Dict, Tuple

"""
These are the different model entities used in EAC.
We have SellOrder, BuyOrder, Basket.
SellOrder can be of type 'parent', 'child', or 'substitutable_child'.
BuyOrder represents a buy order for a specific product.
Basket represents a basket of Sell orders with possible concomitant relationships.
"""

@dataclass(frozen=True)
class SellOrder:
    auctionID: int
    registeredAuctionParticipant: str
    auctionUnit: str
    basketID: int
    service: str
    deliveryStart: str
    deliveryEnd: str
    orderID: int
    orderType: str  # 'parent' | 'child' | 'substitutable_child'
    auctionProduct: str
    quantity: float
    price: float
    orderEntryTime: str = ""
    product_id: str = ""
    status: str = ""
    min_acceptance_ratio: float = 0.0


    @staticmethod
    def from_dict(d: Dict) -> "SellOrder":
        return SellOrder(
            auctionID=int(d.get("auctionID", 0)),
            registeredAuctionParticipant=d.get("registeredAuctionParticipant", ""),
            auctionUnit=d.get("auctionUnit", ""),
            basketID=int(d.get("basketID", 0)),
            service=d.get("service", ""),
            deliveryStart=d.get("deliveryStart", ""),
            deliveryEnd=d.get("deliveryEnd", ""),
            orderID=d.get("orderID", ""),
            orderType=d.get("orderType", ""),
            auctionProduct=d.get("auctionProduct", ""),
            quantity=float(d.get("quantity", 0.0)),
            price=float(d.get("price", 0.0)),
            orderEntryTime=d.get("orderEntryTime", ""),
            product_id=str(d.get("productID", "")),
            status=str(d.get("status", "")),
            min_acceptance_ratio=float(d.get("min_acceptance_ratio", 0.0)),
        )


@dataclass(frozen=True)
class BuyOrder:
    auctionID: int
    orderID: int
    service: str
    auctionProduct: str
    deliveryStart: str
    deliveryEnd: str
    quantity: float
    price: float
    paradoxical: bool = True
    min_acceptance_ratio: float = 0.0

    @staticmethod
    def from_dict(d: Dict) -> "BuyOrder":
        return BuyOrder(
            auctionID=int(d.get("auctionID", 0)),
            orderID=int(d.get("orderID", 0)),
            service=d.get("service", ""),
            auctionProduct=d.get("auctionProduct", ""),
            deliveryStart=d.get("deliveryStart", ""),
            deliveryEnd=d.get("deliveryEnd", ""),
            quantity=float(d.get("quantity", 0.0)),
            price=float(d.get("price", 0.0)),
            paradoxical=bool(d.get("paradoxical", True)),
            min_acceptance_ratio=float(d.get("min_acceptance_ratio", 0.0)),
        )


@dataclass
class Basket:
    id: int
    auctionID: int
    unit: str
    concomitant: List[int]
    looped_to: Optional[int]


    @staticmethod
    def from_dict(d: Dict) -> "Basket":
        return Basket(
            id=int(d.get("id", 0)), 
            auctionID=int(d.get("auctionID", 0)),
            unit=d.get("unit"), 
            concomitant=list(d.get("concomitant", [])), 
            looped_to=d.get("looped_to")
        )


@dataclass(frozen=True)
class MultiProductOrder:
    key: Tuple
    fragments: List[SellOrder]
    acceptance: float
    price_limit: float
    basket_id: int
    order_type: str
    window: Tuple[str, str]
    canonical_order_id: int