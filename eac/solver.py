from typing import Any, Optional
import pulp

"""
A simple solver backend using PuLP.
This class provides an interface to solve linear programming problems using the PuLP library.
"""

class PulpSolverBackend:
    def __init__(self, msg: int = 0):
        self.msg = msg


    def solve(self, prob: pulp.LpProblem) -> int:
        prob.solve(pulp.PULP_CBC_CMD(msg=self.msg))
        return prob.status