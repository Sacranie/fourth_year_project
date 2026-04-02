from collections import deque

class JackMeu:
    def __init__(self, meu, window, step_size, target_degradation, dual_variable):
        self.meu = meu
        self.window = window
        self.step_size = step_size
        self.revenue_history = deque()
        self.degradation_history = deque()
        self.target_degradation = target_degradation
        self.dual_variable = dual_variable
        self.meu_max = 6e7  # Maximum MEU value for normalization
        self.meu_min = 0  # Minimum MEU value for normalization
    
    def computation(self, current_revenue, current_degradation):
        self.revenue_history.append(current_revenue)
        self.degradation_history.append(current_degradation)
        
        if len(self.revenue_history) > self.window:
            self.revenue_history.popleft()
            self.degradation_history.popleft()
        
        avg_revenue = sum(self.revenue_history) / len(self.revenue_history)
        avg_degradation = sum(self.degradation_history) / len(self.degradation_history)

        avg_meu = avg_revenue / (avg_degradation + 1e-6)  # Avoid division by zero
        
        subgradient = self.target_degradation - current_degradation

        lower_bound_thi = -1 * avg_meu
        upper_bound_thi = self.meu_max + lower_bound_thi

        self.thi = self.update_dual_varaible(lower_bound_thi, upper_bound_thi, subgradient)
        self.meu = self.thi + avg_meu

    
    def update_dual_varaible(self, lower_bound, upper_bound, subgradient):
        # I need to update the thi but it needs to be the minimum within the bounds such that it is the minimum of the following equation:
        # thi = argmin step_size * subgradient * x + 0.5 * (x- self.dual_variable) ** 2
        # subject to lower_bound <= x <= upper_bound
        # This is a quadratic optimization problem with a linear term, and the solution can be found by taking the derivative and setting it to zero, then applying the bounds.
        optimal_x = self.dual_variable - self.step_size * subgradient
        optimal_x = max(lower_bound, min(upper_bound, optimal_x))
        self.dual_variable = optimal_x
        return self.dual_variable
