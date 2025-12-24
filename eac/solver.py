from typing import Any, Optional
import pulp

"""
A simple solver backend using PuLP.
This class provides an interface to solve linear programming problems using the PuLP library.
This is done using the CBC solver. Coin Branch and Cut Solver.
"""

class PulpSolverBackend:
    def __init__(self, msg: int = 0, time_limit: int = 300):
        self.msg = msg
        self.time_limit = time_limit


    def solve(self, prob: pulp.LpProblem) -> int:
        prob.solve(pulp.GUROBI_CMD(msg=self.msg, timeLimit=self.time_limit))
        return prob.status