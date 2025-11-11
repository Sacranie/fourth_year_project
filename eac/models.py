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
    id: str
    basket: str
    qty: Mapping[str, float]
    price: float
    type: str # 'parent' | 'child' | 'substitutable_child'
    min_acceptance_ratio: float = 0.0


    @staticmethod
    def from_dict(d: Dict) -> "SellOrder":
        return SellOrder(
            id=d["id"],
            basket=d["basket"],
            qty=d.get("qty", {}),
            price=float(d.get("price", 0.0)),
            type=d.get("type", "parent"),
            min_acceptance_ratio=float(d.get("min_acceptance_ratio", 0.0)),
        )


@dataclass(frozen=True)
class BuyOrder:
    id: str
    product: str
    price: float
    volume: float
    family: Optional[str] = None
    paradoxical: bool = True
    min_acceptance_ratio: float = 0.0


    @staticmethod
    def from_dict(d: Dict) -> "BuyOrder":
        return BuyOrder(
            id=d["id"],
            product=d["product"],
            price=float(d.get("price", 0.0)),
            volume=float(d.get("volume", 0.0)),
            family=d.get("family"),
            paradoxical=bool(d.get("paradoxical", True)),
            min_acceptance_ratio=float(d.get("min_acceptance_ratio", 0.0)),
        )


@dataclass
class Basket:
    id: str
    unit: str
    concomitant: List[str]
    looped_to: Optional[str]


    @staticmethod
    def from_dict(bid: str, d: Dict) -> "Basket":
        return Basket(id=bid, unit=d.get("unit"), concomitant=list(d.get("concomitant", [])), looped_to=d.get("looped_to"))