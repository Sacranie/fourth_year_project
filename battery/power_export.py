from typing import Dict, List, Tuple
import numpy as np
from .battery import VolkanBattery
from eac.models import MultiProductOrder
import urllib.request
import urllib.parse
import json

class PowerExport:
    def __init__(self, power_export_type: str):
        self.power_export_type = power_export_type
        self.deadband = 0.015
        self.url = "https://data.elexon.co.uk/bmrs/api/v1/system/frequency"

    def dynamic_contaiment_power_export(self, frequency: float):
        if self.power_export_type == "DCH" and frequency < 0:
            return 0.0
        if self.power_export_type == "DCL" and frequency > 0:
            return 0.0
        if abs(frequency) <= self.deadband:
            return 0.0
        if frequency >= -0.2 and frequency <= 0.2:
            return 10/37 * abs(frequency) - 3/740
        elif (frequency < -0.2 and frequency >= -0.5) or (frequency > 0.2 and frequency <= 0.5):
            return 19/6 * abs(frequency) - 7/12
        else:
            return 1

    def dynamic_moderation_power_export(self, frequency: float):
        if self.power_export_type == "DMH" and frequency < 0:
            return 0.0
        if self.power_export_type == "DML" and frequency > 0:
            return 0.0
        if abs(frequency) <= self.deadband:
            return 0.0
        if frequency >= -0.1 and frequency <= 0.1:
            return 10/17 * abs(frequency) - 3/340
        elif (frequency < -0.1 and frequency >= -0.2) or (frequency > 0.1 and frequency <= 0.2):
            return 9.5 * abs(frequency) - 0.9
        else:
            return 1
        
    def dynamic_regulation_power_export(self, frequency: float):
        if self.power_export_type == "DRH" and frequency < 0:
            return 0.0
        if self.power_export_type == "DRL" and frequency > 0:
            return 0.0
        if abs(frequency) <= self.deadband:
            return 0.0
        if frequency >= -0.2 and frequency <= 0.2:
            return 200/72 * abs(frequency) - 3/37
        else:
            return 1
    
    def get_power_export_function(self):
        if self.power_export_type == "DCH" or self.power_export_type == "DCL":
            return self.dynamic_contaiment_power_export
        elif self.power_export_type == "DML" or self.power_export_type == "DMH":
            return self.dynamic_moderation_power_export
        elif self.power_export_type == "DRL" or self.power_export_type == "DRH":
            return self.dynamic_regulation_power_export
        else:
            print(self.power_export_type)
            raise ValueError("Invalid power export type")

    def degradation_model(self, battery: VolkanBattery, multi_product_orders: List[MultiProductOrder], meu: int) -> float:
        
        power_profile = self.build_power_profile_from_orders(multi_product_orders)
        
        if len(power_profile) == 0:
            return 0.0
        
        # Store initial state before simulation (preserving all battery state variables)
        initial_soh = battery.soh if battery.soh is not None else battery.settings['SOH0']
        initial_energy = battery.energy if battery.energy is not None else battery.settings['E0']
        initial_temp = battery.temp if battery.temp is not None else battery.settings['Tk0']
        
        # Pass all current state variables to simulate() so they don't reset
        battery.simulate(power_profile, energy=initial_energy, temp=initial_temp, soh=initial_soh)
        
        # Calculate SOH loss
        delta_soh = initial_soh - battery.soh_trajectory[-1]
        
        # Convert SOH loss to cost
        degradation_cost = delta_soh * meu 
        
        return degradation_cost


    def build_power_profile_from_orders(self, multi_product_orders: List[MultiProductOrder]) -> np.ndarray:

        power_by_timestamp = {}  # timestamp -> power (kW)
        maximum_ratio = 0.0
        max_power = 0.0
        
        for mpo in multi_product_orders:
            # Use SOLVED acceptance from x_s_computed
            acceptance = 1.0  # Assume full acceptance in price taker mode
                
            for fragment in mpo.fragments:
                product = fragment.auctionProduct.upper()
                start_time = fragment.deliveryStart
                end_time = fragment.deliveryEnd
                quantity_mw = fragment.quantity * acceptance  # MW
                
                # Fetch frequency data for this delivery window
                frequencies = self.frequency_data(start_time, end_time)
                
                # Get power export function for this product type
                power_export = PowerExport(product)
                power_export_function = power_export.get_power_export_function()
                
                for timestamp, freq in frequencies:
                    # Calculate export ratio based on frequency deviation
                    export_ratio = power_export_function(freq - 50.0)
                    maximum_ratio = max(maximum_ratio, export_ratio)
                    
                    # Power in kW (convert from MW)
                    # DCL/DML/DRL = Low frequency -> discharge (negative power)
                    # DCH/DMH/DRH = High frequency -> charge (positive power)
                    power_kw = quantity_mw * 1000 * export_ratio  # Convert MW to kW
                    max_power = max(max_power, power_kw)
                    
                    # Determine sign based on product type and frequency
                    if product in {'DCL', 'DML', 'DRL'}:
                        power_kw = -power_kw
                    
                    # Accumulate power at this timestamp
                    if timestamp in power_by_timestamp:
                        power_by_timestamp[timestamp] += power_kw
                    else:
                        power_by_timestamp[timestamp] = power_kw
        
        # Convert to sorted array
        if not power_by_timestamp:
            return np.array([])
        
        sorted_timestamps = sorted(power_by_timestamp.keys())
        power_profile = np.array([power_by_timestamp[ts] for ts in sorted_timestamps])
        
        return power_profile
        

    def frequency_data(self, start_time: str, end_time: str) -> List[Tuple[str, float]]:
        """
        Fetch frequency data from ELEXON API for a given time range.
        
        Args:
            start_time: ISO 8601 formatted start time (e.g., '2025-03-31T22:00:00')
            end_time: ISO 8601 formatted end time (e.g., '2025-04-07T22:00:00')
        
        Returns:
            List of (timestamp, frequency) tuples
        """
        if not start_time.endswith('Z'):
            start_time = start_time + 'Z'
        if not end_time.endswith('Z'):
            end_time = end_time + 'Z'
        
        params = urllib.parse.urlencode({
            "from": start_time,
            "to": end_time,
            "format": "json"
        })
        url = f"{self.url}?{params}"
        
        try:
            with urllib.request.urlopen(url) as response:
                data = json.loads(response.read().decode())
                frequencies = []
                for entry in data.get('data', []):
                    timestamp = entry.get('measurementTime', '')
                    freq = float(entry.get('frequency', 50.0))
                    frequencies.append((timestamp, freq))
                return frequencies
        except Exception as e:
            print(f"Error fetching frequency data: {e}")
            return []

