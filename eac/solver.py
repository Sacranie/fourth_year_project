import pulp

class PulpSolverBackend:
    def __init__(self, time_limit=300, msg=0):
        self.time_limit = time_limit
        self.msg = msg

    def solve(self, prob):
        solver = pulp.GUROBI(
            msg=self.msg,
            timeLimit=self.time_limit
        )
        return prob.solve(solver)
