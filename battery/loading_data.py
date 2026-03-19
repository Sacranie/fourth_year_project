from typing import Dict, List, Tuple, Optional
from collections import defaultdict
import json
import csv
import os

from eac.models import SellOrder, BuyOrder, Basket
from eac.multi_product_orders import group_multi_product_orders

# CSV file paths
SELL_CSV = "neso_auction_data.csv"
BUY_CSV = "neso_buy_data.csv"

class LoadingData:

    def __init__(self, auction_id: int, auction_unit: str, sell_csv: str = SELL_CSV, buy_csv: str = BUY_CSV):
        self.auction_id = auction_id
        self.auction_unit = auction_unit
        self.sell_csv = sell_csv
        self.buy_csv = buy_csv

    def load_sell_orders_for_auction(self) -> List[Dict]:
        """Load all sell orders for a specific Auction ID from CSV file."""
        if not os.path.exists(self.sell_csv):
            print(f"Error: Sell orders CSV file not found: {self.sell_csv}")
            return []
        
        records = []
        with open(self.sell_csv, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Filter by auction ID
                if str(row.get("auctionID", "")) == str(self.auction_id):
                    records.append(row)
        
        print(f"Loaded {len(records)} sell records for auction {self.auction_id}")
        return records

    def load_buy_orders_for_auction(self) -> List[Dict]:
        """Load all buy orders for a specific Auction ID from CSV file."""
        if not os.path.exists(self.buy_csv):
            print(f"Error: Buy orders CSV file not found: {self.buy_csv}")
            return []
        
        records = []
        with open(self.buy_csv, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Filter by auction ID
                if str(row.get("auctionID", "")) == str(self.auction_id):
                    records.append(row)
        
        print(f"Loaded {len(records)} buy records for auction {self.auction_id}")
        return records


    def process_sell_orders(self, sell_records: List[Dict]) -> Tuple[List, List[SellOrder], Dict]:
        """
        Build logical SellOrder objects from raw API rows.
        
        Returns:
            (multi_orders, all_sell_orders, original_mcp)
        """
        all_sell_orders = []

        original_mcp = defaultdict(float)

        for row in sell_records:
            status = str(row.get("status", "")).strip().upper()
            order_id = int(row.get("orderID", 0))

            sell_order = SellOrder(
                auctionID=int(row.get("auctionID", 0) or 0),
                registeredAuctionParticipant=str(row.get("registeredAuctionParticipant", "") or ""),
                auctionUnit=str(row.get("auctionUnit", "") or ""),
                basketID=int(row.get("basketID", 0) or 0),
                service=str(row.get("service", "") or ""),
                deliveryStart=str(row.get("deliveryStart", "") or ""),
                deliveryEnd=str(row.get("deliveryEnd", "") or ""),
                orderID=int(order_id),
                orderType=str(row.get("orderType", "parent")).lower(),
                auctionProduct=str(row.get("auctionProduct", "") or ""),
                quantity=float(row.get("quantity", 0.0) or 0.0),
                price=float(row.get("priceLimit", row.get("price", 0.0) or 0.0)),
                orderEntryTime=str(row.get("orderEntryTime", "") or ""),
                product_id=str(row.get("productID", "") or ""),
                status=status,
                min_acceptance_ratio=row.get("acceptanceRatio", 0.0) or 0.0,
            )
            original_mcp[(sell_order.auctionProduct, (sell_order.deliveryStart, sell_order.deliveryEnd))] = float(row.get("clearingPrice", 0.0) or 0.0)
        
            all_sell_orders.append(sell_order)

        multi_orders = group_multi_product_orders(all_sell_orders)

        for orders in multi_orders:
            if not orders.is_accepted:
                orders.acceptance = 1.0
                
        return multi_orders, all_sell_orders, original_mcp


    def process_buy_orders(self, buy_records: List[Dict]) -> List[BuyOrder]:
        """
        Build BuyOrder objects from raw API records.
        
        Args:
            buy_records: Raw records from the CSV file
        
        Returns:
            List of BuyOrder objects
        """
        all_buy_orders = []

        for row in buy_records:
            status = str(row.get("status", "")).strip().upper()
            order_id = row.get("orderID", 0)

            if status == "REJECTED":
                min_acceptance = 1.0 # force reject
            else:
                min_acceptance = row.get("acceptanceRatio", 0.0)

            raw = row.get("paradoxicallyAcceptanceAllowed", "false")
            paradoxical = (str(raw).lower() == "true")

            buy_order = BuyOrder(
                auctionID=self.auction_id,
                orderID=order_id,
                service=str(row.get("service", "") or ""),
                auctionProduct=str(row.get("auctionProduct", "") or ""),
                deliveryStart=str(row.get("deliveryStart", "") or ""),
                deliveryEnd=str(row.get("deliveryEnd", "") or ""),
                quantity=float(row.get("quantity", 0.0) or 0.0),
                price=float(row.get("price", 0.0) or 0.0),
                paradoxical=paradoxical,
                min_acceptance_ratio=min_acceptance,
            )

            all_buy_orders.append(buy_order)

        print(f"\nBuy Orders: {len(all_buy_orders)} loaded")
        return all_buy_orders

    def build_baskets_from_orders(self, sell_orders: List[SellOrder], raw_records: List[Dict]) -> List[Basket]:

        baskets = {}
        for s in sell_orders:
            bid = int(s.basketID)
            if bid not in baskets:
                baskets[bid] = Basket(id=bid, auctionID=int(s.auctionID), unit=s.auctionUnit, looped_to=None, concomitant=[])

        concomitance = defaultdict(set)

        # populate looped_to and concomitant fields
        for row in raw_records:
            b_id = row.get("basketID")

            if b_id not in baskets:
                continue

            # loopedBasketID may be absent/empty
            looped = row.get("loopedBasketID")
            if looped not in (None, "", "None"):
                baskets[b_id].looped_to = int(looped)

            delivery_start = row.get("deliveryStart")
            delivery_end = row.get("deliveryEnd")
            unit = row.get("auctionUnit")

            concomitance[(unit, delivery_start, delivery_end)].add(int(b_id))

        for (unit, delivery_start, delivery_end), basket_ids in concomitance.items():
            for basket_id in basket_ids:
                baskets[basket_id].concomitant = list(basket_ids - {basket_id})

        print(f"\nBaskets built: {len(baskets)} (concomitant/loop info where present)")
        return list(baskets.values())
