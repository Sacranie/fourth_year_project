from dataclasses import dataclass
from typing import Mapping, Optional, List, Dict

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
    orderID: str
    orderType: str  # 'parent' | 'child' | 'substitutable_child'
    auctionProduct: str
    qty: float
    price: float
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
            qty=float(d.get("qty", 0.0)),
            price=float(d.get("price", 0.0)),
            min_acceptance_ratio=float(d.get("min_acceptance_ratio", 0.0)),
        )


@dataclass(frozen=True)
class BuyOrder:
    auctionID: int
    orderID: str
    service: str
    auctionProduct: str
    deliveryStart: str
    deliveryEnd: str
    quanity: float
    price: float
    paradoxical: bool = True
    min_acceptance_ratio: float = 0.0

    @staticmethod
    def from_dict(d: Dict) -> "BuyOrder":
        return BuyOrder(
            auctionID=int(d.get("auctionID", 0)),
            orderID=d.get("orderID", ""),
            service=d.get("service", ""),
            auctionProduct=d.get("auctionProduct", ""),
            deliveryStart=d.get("deliveryStart", ""),
            deliveryEnd=d.get("deliveryEnd", ""),
            quanity=float(d.get("quanity", 0.0)),
            price=float(d.get("price", 0.0)),
            paradoxical=bool(d.get("paradoxical", True)),
            min_acceptance_ratio=float(d.get("min_acceptance_ratio", 0.0)),
        )


@dataclass
class Basket:
    id: str
    auctionID: int
    unit: str
    concomitant: List[str]
    looped_to: Optional[str]


    @staticmethod
    def from_dict(d: Dict) -> "Basket":
        return Basket(
            id=d.get("id"), 
            auctionID=int(d.get("auctionID", 0)),
            unit=d.get("unit"), 
            concomitant=list(d.get("concomitant", [])), 
            looped_to=d.get("looped_to")
        )