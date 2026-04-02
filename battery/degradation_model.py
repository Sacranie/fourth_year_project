import urllib.request
import urllib.parse
import numpy as np
import json
from typing import List, Tuple
from .battery import VolkanBattery

from .power_export import PowerExport
from eac.models import MultiProductOrder


class degradation_model:
    _frequency_cache = {}
    _frequency_url = "https://data.elexon.co.uk/bmrs/api/v1/system/frequency"

    def __init__(self):
        pass


    def degradation_model(self, battery: VolkanBattery, multi_product_orders: List[MultiProductOrder], meu: int) -> float:
        
        power_profile = self.build_power_profile_from_orders(multi_product_orders)
        
        if len(power_profile) == 0:
            return 0.0, battery.soh if battery.soh is not None else battery.settings['SOH0']
        
        # Store initial state before simulation (preserving all battery state variables)
        initial_soh = battery.soh if battery.soh is not None else battery.settings['SOH0']
        initial_energy = battery.energy if battery.energy is not None else battery.settings['E0']
        initial_temp = battery.temp if battery.temp is not None else battery.settings['Tk0']
        
        battery.simulate(power_profile, energy=initial_energy, temp=initial_temp, soh=initial_soh)
        
        # Calculate SOH loss
        delta_soh = initial_soh - battery.soh_trajectory[-1]
        
        # Convert SOH loss to cost
        degradation_cost = delta_soh * meu 
        
        return degradation_cost, battery.soh_trajectory[-1]

    def degradation_model_with_alpha(self, battery: VolkanBattery, multi_product_orders: List[MultiProductOrder], meu: float, alpha: float) -> Tuple[float, float]:

        power_profile = self.build_power_profile_from_orders(multi_product_orders)
        
        if len(power_profile) == 0:
            soh = battery.soh if battery.soh is not None else battery.settings['SOH0']
            return 0.0, soh, [], [], [], []

        # Scale the power profile by alpha
        scaled_power_profile = power_profile * alpha
        
        # Store initial state before simulation
        initial_energy = battery.energy if battery.energy is not None else battery.settings['E0']
        initial_temp = battery.temp if battery.temp is not None else battery.settings['Tk0']
        degradation_cost = 0.0

        global_energy_trajectory = []
        global_power_trajectory = []
        global_temp_trajectory = []
        global_soh_trajectory = []

        for i in range(0, len(scaled_power_profile), 20):
            scaled_power_profile_by_hour = scaled_power_profile[i:i+20]

            # Simulate with scaled power profile
            initial_soh = battery.soh if battery.soh is not None else battery.settings['SOH0']
            battery.simulate(scaled_power_profile_by_hour, energy=initial_energy, temp=initial_temp, soh=initial_soh)

            # Calculate SOH loss
            delta_soh = initial_soh - battery.soh_trajectory[-1]

            global_energy_trajectory.extend(battery.energy_trajectory)
            global_power_trajectory.extend(scaled_power_profile_by_hour)
            global_temp_trajectory.extend(battery.temp_trajectory)
            global_soh_trajectory.extend(battery.soh_trajectory)
        
            # Convert SOH loss to cost
            degradation_cost += delta_soh * meu 
            
        return degradation_cost, battery.soh_trajectory[-1], global_energy_trajectory, global_power_trajectory, global_temp_trajectory, global_soh_trajectory


    def build_power_profile_from_orders(self, multi_product_orders: List[MultiProductOrder]) -> np.ndarray:

        power_by_timestamp = {}  # timestamp -> power (kW)

        
        for mpo in multi_product_orders:
            for fragment in mpo.fragments:
                product = fragment.auctionProduct.upper()

                start_time = fragment.deliveryStart
                end_time = fragment.deliveryEnd
                quantity_mw = fragment.quantity  # MW

                # Fetch frequency data for this delivery window
                frequencies = self.frequency_data(start_time, end_time)
                
                # Get power export function for this product type
                power_export = PowerExport(product)
                power_export_function = power_export.get_power_export_function()
                
                for timestamp, freq in frequencies:
                    # Calculate export ratio based on frequency deviation
                    export_ratio = power_export_function(freq - 50.0)
                    
                    # Power in kW (convert from MW)
                    # DCL/DML/DRL = Low frequency -> discharge (negative power)
                    # DCH/DMH/DRH = High frequency -> charge (positive power)
                    power_kw = quantity_mw * 1000 * export_ratio  # Convert MW to kW

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
        Results are cached to avoid repeated API calls during optimization.
        
        Args:
            start_time: ISO 8601 formatted start time (e.g., '2025-03-31T22:00:00')
            end_time: ISO 8601 formatted end time (e.g., '2025-04-07T22:00:00')
        
        Returns:
            List of (timestamp, frequency) tuples
        """
        # Normalize timestamps for cache key
        cache_start = start_time if start_time.endswith('Z') else start_time + 'Z'
        cache_end = end_time if end_time.endswith('Z') else end_time + 'Z'
        cache_key = (cache_start, cache_end)
        
        # Check cache first
        if cache_key in self._frequency_cache:
            return self._frequency_cache[cache_key]
        
        params = urllib.parse.urlencode({
            "from": cache_start,
            "to": cache_end,
            "format": "json"
        })
        url = f"{self._frequency_url}?{params}"
        
        try:
            with urllib.request.urlopen(url) as response:
                data = json.loads(response.read().decode())
                frequencies = []
                for entry in data.get('data', []):
                    timestamp = entry.get('measurementTime', '')
                    freq = float(entry.get('frequency', 50.0))
                    frequencies.append((timestamp, freq))
                
                # Store in cache
                self._frequency_cache[cache_key] = frequencies
                return frequencies
        except Exception as e:
            print(f"Error fetching frequency data: {e}")
            return []
