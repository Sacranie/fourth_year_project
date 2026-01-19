class PowerExport:
    def __init__(self, power_export_type: str):
        self.power_export_type = power_export_type

    def dynamic_contaiment_power_export(self, frequency: float):
        if frequency >= -0.2 and frequency <= 0.2:
            return -25 * frequency / 100
        elif frequency < -0.2 and frequency >= -0.5:
            return -95/0.3 * frequency - 175/3
        elif frequency > 0.2 and frequency <= 0.5:
            return -95/0.3 * frequency + 175/3
        elif frequency <= -0.5:
            return 1
        else:
            return 1

    def dynamic_moderation_power_export(frequency: float):
        if frequency >= -0.1 and frequency <= 0.1:
            return -10 * frequency / 100
        elif frequency < -0.1 and frequency >= -0.3:
            return -40/0.2 * frequency - 3
        elif frequency > 0.1 and frequency <= 0.3:
            return -40/0.2 * frequency + 3
        elif frequency <= -0.3:
            return 1
        else:
            return 1
        
    def dynamic_regulation_power_export(frequency: float):
        if frequency >= -0.05 and frequency <= 0.05:
            return -5 * frequency / 100
        elif frequency < -0.05 and frequency >= -0.15:
            return -15/0.1 * frequency - 1
        elif frequency > 0.05 and frequency <= 0.15:
            return -15/0.1 * frequency + 1
        elif frequency <= -0.15:
            return 1
        else:
            return 1
    
    def get_power_export_function(self):
        if self.power_export_type == "dynamic_contaiment":
            return self.dynamic_contaiment_power_export
        elif self.power_export_type == "dynamic_moderation":
            return self.dynamic_moderation_power_export
        elif self.power_export_type == "dynamic_regulation":
            return self.dynamic_regulation_power_export
        else:
            raise ValueError("Invalid power export type")
        

