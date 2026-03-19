from typing import Dict, List, Tuple
from datetime import datetime, timedelta
import numpy as np
from .battery import VolkanBattery
from eac.models import MultiProductOrder
import urllib.request
import urllib.parse
import json


class PowerExport:
    # Class-level cache for frequency data (shared across all instances)
    _frequency_cache: Dict[Tuple[str, str], List[Tuple[str, float]]] = {}
    
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
