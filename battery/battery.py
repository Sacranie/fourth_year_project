import numpy as np
import matplotlib.pyplot as plt

class VolkanBattery:
    """
    Some notes on the Volkan battery model:
    - energy (E) is in kWh
    - temperature (T) is in K
    - state of health (SOH) is a fraction (0-1)
    - power (P) is in kW
    - time is in hours, so dt = 0.25/60 means 15 seconds
    """

    def __init__(self):

        self.settings = None
        
        # battery state variables
        self.energy = None
        self.temp = None
        self.soh = None

        # state trajectories
        self.energy_trajectory = None
        self.temp_trajectory = None
        self.soh_trajectory = None
        self.power_trajectory = None

    def populate_with_volkan_parameters(self, data_location='../data/volkan_model'):
        
        KELVIN = 273.15

        cyc_ag_ch = np.genfromtxt(data_location+'/cyc_ageing_ch.csv', delimiter=",")
        cal_ag = np.genfromtxt(data_location+'/cal_ageing_discounted.csv', delimiter=",")

        settings = {'AC_eta_ch': 0.97, # charge eff of power electronics
                    'AC_eta_dc': 0.97, # discharge eff of power electronics
                    'bat_eta_ch': 0.98, # charge eff of battery
                    'bat_eta_dc': 0.98, # discharge eff of battery
                    'dataName': 'idc_positive_dummy.csv',
                    'studyName': 'opportunity_hypothesis_2023_09_09',
                    'horizon': 24, # horizon [h] = 1 day
                    'control-horizon' : 1,
                    'duration': 24*365*10, # 10 years
                    'C-rate': [1.0, 1.0], # C-rate for charge and discharge
                    'lambda_cal': 1.0,
                    'lambda_cyc': 1.0,
                    'dt' : 15/3600, # 15 s timestep in hours
                    'EOL': 0.8,
                    'price_kWhcap' : 250,
                    'Enom' : 8000,  # kWh
                    'SOCmin': 0.05, 
                    'SOCmax': 0.95,
                    # very initial values: 
                    'Tamb' : 18 + KELVIN, # Ambient temperature
                    'Tk0'  : KELVIN + 18,
                    'E0'   : 0.0, 
                    'SOH0' : 1.0,
                    'FEC0' : 0.0,
                    # PWA
                    'Qloss_cyc_Ab_ch' : cyc_ag_ch, # A_ch * rate_ch , b_ch [dSOH/hr/rate]
                    'Qloss_cyc_dc' : 3.18e-7,   # per total FEC
                    'Qloss_cal' : cal_ag, # Calendar ageing PWA
                    # Temperature model: k*((Tk-Tamb)*alpha + Qcell)
                    'k_Tcell': 0.014, # (1/(param.m_cell*param.c_p_cell))
                    'alpha_Tcell': 0.0192, # param.cell.alpha.fan_100*param.A_cell
                    # normally below numbers should be quadratic but considering now linear
                    'Qcell_ch': 0.575, # Qcell / C-rate -> func.dQcell( 3,0.5,KELVIN+25)
                    'Qcell_dc': 0.410, # Qcell / C-rate -> func.dQcell(-3,0.5,KELVIN+25)
                    }        
        
        self.settings = settings


    def simulate(self, power_trajectory, energy=None, temp=None, soh=None):

        self.power_trajectory = power_trajectory
        self.initialize_state(energy, temp, soh)
        self.reset_state_trajectory()

        for t, power in enumerate(power_trajectory):
            self.state_transition(power, t)
            self.update_state_trajectory() 
            if self.soh <= 0:
                print(f"Battery has reached end of life at time {t * self.settings['dt']} hours.")
                break

    def plot_simulation(self):

        if self.energy_trajectory is None:
            raise ValueError("No simulation data available. Please run the simulate method first.")
        
        time = np.arange(0, len(self.energy_trajectory) * self.settings["dt"], self.settings["dt"])
        
        num_plots = 5

        fig, ax = plt.subplots(num_plots, 1, figsize=(10, 8), sharex=True)
        plot_num = 0
        ax[plot_num].plot(time, self.power_trajectory[0:len(self.energy_trajectory)], label='Power (kW)')
        ax[plot_num].set_ylabel('Power (kW)')
        ax[plot_num].legend()
        ax[plot_num].grid()

        plot_num += 1
        ax[plot_num].plot(time, self.energy_trajectory, label='Energy (kWh)', color='orange')
        ax[plot_num].set_ylabel('Energy (kWh)')
        ax[plot_num].legend()
        ax[plot_num].grid()

        plot_num += 1
        ax[plot_num].plot(time, np.array(self.energy_trajectory) / (np.array(self.soh_trajectory) * self.settings['Enom']), label='State of Charge (SOC)', color='blue')
        ax[plot_num].set_ylabel('State of Charge (SOC)')
        ax[plot_num].legend()
        ax[plot_num].grid()

        plot_num += 1
        ax[plot_num].plot(time, self.kelvin_to_celsius(self.temp_trajectory), label='Temperature (C)', color='green')
        ax[plot_num].set_ylabel('Temperature (C)')
        ax[plot_num].legend()
        ax[plot_num].grid()

        plot_num += 1
        ax[plot_num].plot(time, self.soh_trajectory, label='State of Health (SOH)', color='red')
        ax[plot_num].set_ylabel('State of Health (SOH)')
        ax[plot_num].set_xlabel('Time (h)')
        ax[plot_num].legend()
        ax[plot_num].grid()
        # ax[3].set_xticklabels(np.arange(0, len(self.power_trajectory) * self.settings["dt"], self.settings["dt"]))
        plt.tight_layout()
        plt.show()

        return fig, ax
    
    def plot_state_of_health(self, ax=None, label=None, color='red'):
        if self.soh_trajectory is None:
            raise ValueError("No simulation data available. Please run the simulate method first.")
        
        time = np.arange(0, len(self.power_trajectory) * self.settings["dt"], self.settings["dt"])

        if ax is None:
            fig, ax = plt.subplots(figsize=(10, 5))
        
        if label is None:
            label = 'State of Health (SOH)'

        ax.plot(time, self.soh_trajectory, label='State of Health (SOH)', color=color)
        ax.set_ylabel('State of Health (SOH)')
        ax.set_xlabel('Time (h)')
        ax.legend()
        ax.grid()
        plt.tight_layout()

        return fig, ax

    def kelvin_to_celsius(self, kelvin):

        if isinstance(kelvin, list):
            return [k - 273.15 for k in kelvin]
        else: 
            return kelvin - 273.15

    def reset_state_trajectory(self):
        self.energy_trajectory = []
        self.temp_trajectory = []
        self.soh_trajectory = []

    def update_state_trajectory(self):
        self.energy_trajectory.append(self.energy)
        self.temp_trajectory.append(self.temp)
        self.soh_trajectory.append(self.soh)

    def initialize_state(self, energy=None, temp=None, soh=None):

        if energy is None:
            self.energy = self.settings['E0']
        else:
            self.energy = energy

        if temp is None:
            self.temp = self.settings['Tk0']
        else:
            self.temp = temp

        if soh is None:
            self.soh = self.settings['SOH0']
        else:
            self.soh = soh

    def state_transition(self, power, t=None):

        # Nb. positive power => charge, negative power => discharge
        # Nb. SOC is energy / (Enom * SOH0)
        new_energy = self.energy_transition(power)
        new_temp = self.temperature_transition(power)
        avg_soc = (self.energy + new_energy) / (2 * self.settings['Enom'] * self.settings['SOH0'])
        avg_temp = (self.temp + new_temp) / 2
        q_loss = self.q_loss_cyc(power) + self.q_loss_cal(avg_soc, avg_temp)

        # print(f"Time: {t}")
        # print(f"\tAvg temp: {avg_temp}, Avg SOC: {avg_soc}")
        # print(f"\tQ_loss_cyc: {self.q_loss_cyc(power)}, Q_loss_cal: {self.q_loss_cal(avg_soc, avg_temp)}")
        # print(f"\tQ_loss_cyc_plus: {self.q_loss_cyc_plus(power)}, Q_loss_cyc_minus: {self.q_loss_cyc_minus(power)}")

        self.energy = new_energy
        self.temp = new_temp
        self.soh = self.soh - q_loss


    def energy_transition(self, power):

        # Todo: add a warning if SOC limits would be violated?
        # Could return a modified power variable that respects the limits 
        new_energy_nom = self.energy + power * self.settings['dt']

        if new_energy_nom >= self.soh * self.settings['Enom']:
            return self.soh * self.settings['Enom']
        elif new_energy_nom <= 0:
            return 0
        else:
            return new_energy_nom        
    
    def temperature_transition(self, power):

        power_plus, power_minus = self.power_to_plus_minus(power)

        # Todo: can we use actual capacity instead of Enom?
        Qcell = (self.settings['Qcell_ch'] * power_plus + self.settings['Qcell_dc'] * power_minus) / self.settings['Enom']

        temp_change = self.settings['k_Tcell'] * ((self.settings['Tamb'] - self.temp) * self.settings['alpha_Tcell'] + Qcell)

        new_temp = self.temp + temp_change * self.settings['dt'] * 3600

        return new_temp

    def q_loss_cyc_plus(self, power):

        power_plus, power_minus = self.power_to_plus_minus(power)

        return np.max([self.settings['Qloss_cyc_Ab_ch'][i,0] * (power_plus / self.settings['Enom']) + self.settings['Qloss_cyc_Ab_ch'][i,1] 
                       for i in range(len(self.settings['Qloss_cyc_Ab_ch']))]) * self.settings['dt'] 
    
    def q_loss_cyc_minus(self, power):

        dFEC = 0.5 * np.abs(power) * self.settings['dt'] / self.settings['Enom']
        return self.settings['Qloss_cyc_dc'] * dFEC  
    
    def q_loss_cyc(self, power):

        return self.q_loss_cyc_plus(power) + self.q_loss_cyc_minus(power)

    def q_loss_cal(self, avg_soc, avg_temp):

        return np.max([self.settings['Qloss_cal'][i,0] + self.settings['Qloss_cal'][i,1] * avg_soc + self.settings['Qloss_cal'][i,2] * avg_temp 
                       for i in range(len(self.settings['Qloss_cal']))]) * self.settings['dt']


    def visualize_q_loss_cyc_plus(self):

        power_range = np.arange(0, 100)
        losses = []
        for power in power_range:
            losses.append(self.q_loss_cyc_plus(power))
        plt.plot(power_range, losses)
        plt.xlabel('Power (kW)')
        plt.ylabel('Q_loss_cyc_plus')
        plt.title('Q_loss_cyc_plus vs Power')
        plt.grid()
        plt.show()



    def power_to_plus_minus(self, power):
        # Nb. positive power => charge, negative power => discharge
        if power >= 0:
            return power, 0
        else:
            return 0, -power
        
    