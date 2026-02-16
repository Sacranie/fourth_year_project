from typing import Dict, List, Tuple, Optional, Set
from collections import defaultdict
import json
import urllib.request
import urllib.parse
import numpy as np
import matplotlib.pyplot as plt
import csv
import os


# ELEXON API endpoints
ELEXON_BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1/system/frequency"

DATA_FILE = "neso_buy_data.csv"

def fetch_and_save_data(resource_id: str, output_file: str, batch_size: int = 10000, 
                         target_auction_ids: int = 240) -> Tuple[int, Set[str]]:
    """
    Fetch data from NESO API in batches and save to CSV file.
    Stops when target number of unique auction IDs is reached.
    
    Args:
        resource_id: The NESO datastore resource ID
        output_file: Path to save the CSV file
        batch_size: Number of records to fetch per request
        target_auction_ids: Stop fetching when this many unique auction IDs found
        
    Returns:
        Tuple of (total records fetched, set of unique auction IDs)
    """
    import requests
    from urllib import parse
    
    unique_auction_ids: Set[str] = set()
    total_records = 0
    offset = 0
    fieldnames = None
    
    # Check if file already exists and get existing auction IDs
    if os.path.exists(output_file):
        print(f"Found existing data file: {output_file}")
        with open(output_file, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                total_records += 1
                auction_id = row.get("auctionID")
                if auction_id:
                    unique_auction_ids.add(auction_id)
        print(f"Loaded {total_records} existing records with {len(unique_auction_ids)} unique auction IDs")
        
        if len(unique_auction_ids) >= target_auction_ids:
            print(f"Already have {len(unique_auction_ids)} auction IDs, no need to fetch more")
            return total_records, unique_auction_ids
        
        # Continue from where we left off
        offset = total_records
        print(f"Resuming fetch from offset {offset}...")
    
    # Open file in append mode
    file_exists = os.path.exists(output_file) and total_records > 0
    
    with open(output_file, 'a' if file_exists else 'w', newline='', encoding='utf-8') as f:
        writer = None
        
        while len(unique_auction_ids) < target_auction_ids:
            sql_query = f'''SELECT * FROM "{resource_id}" ORDER BY "_id" ASC LIMIT {batch_size} OFFSET {offset}'''
            params = {'sql': sql_query}
            
            try:
                response = requests.get(
                    'https://api.neso.energy/api/3/action/datastore_search_sql',
                    params=parse.urlencode(params),
                    timeout=60
                )
                response.raise_for_status()
                
                data = response.json()["result"]
                records = data.get("records", [])
                
                if not records:
                    print(f"\nNo more records to fetch at offset {offset}")
                    return total_records, unique_auction_ids
                
                # Initialize CSV writer with fieldnames from first batch
                if writer is None:
                    if not file_exists:
                        fieldnames = list(records[0].keys())
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                    else:
                        # Read existing fieldnames
                        with open(output_file, 'r', newline='', encoding='utf-8') as rf:
                            reader = csv.DictReader(rf)
                            fieldnames = reader.fieldnames
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                
                # Write records to CSV and track auction IDs
                for record in records:
                    writer.writerow(record)
                    auction_id = record.get("auctionID")
                    if auction_id:
                        unique_auction_ids.add(auction_id)
                
                f.flush()  # Ensure data is written to disk
                
                total_records += len(records)
                print(f"Fetched {len(records)} records (total: {total_records}, unique auctions: {len(unique_auction_ids)})")
                
                if len(records) < batch_size:
                    print(f"\nReached end of data")
                    return total_records, unique_auction_ids
                
                offset += batch_size
                
            except requests.exceptions.RequestException as e:
                print(f"Error at offset {offset}: {e}")
                print(f"Saving progress and stopping.")
                return total_records, unique_auction_ids
    
    return total_records, unique_auction_ids


def load_data_from_csv(filepath: str) -> List[dict]:
    """Load previously saved data from CSV file."""
    if not os.path.exists(filepath):
        print(f"No data file found at {filepath}")
        return []
    
    records = []
    with open(filepath, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(row)
    
    print(f"Loaded {len(records)} records from {filepath}")
    return records


if __name__ == "__main__":
    resource_id = "1cf68f59-8eb8-4f1d-bccf-11b5a47b24e5"
    
    # Fetch and save data (will resume if file exists)
    total_records, unique_auction_ids = fetch_and_save_data(
        resource_id, 
        DATA_FILE, 
        batch_size=10000,  # Smaller batch to avoid memory issues
        target_auction_ids=240
    )
    
    print(f"\n{'='*50}")
    print(f"Total records saved: {total_records}")
    print(f"Total unique auction IDs: {len(unique_auction_ids)}")
    print(f"Unique auction IDs: {sorted(unique_auction_ids)}")
    print(f"Data saved to: {DATA_FILE}")
    print(f"\nTo load data later, use: load_data_from_csv('{DATA_FILE}')")