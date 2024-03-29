# import random
import numpy as np
import random
from dolfin import *
from tqdm import tqdm
from copy import copy
from scipy.special import gamma
import sys, os

from source.TimeStepper import FracTS_RA, FracTS_L1


###########################################

# Form compiler options
parameters["form_compiler"]["optimize"]     = True
parameters["form_compiler"]["cpp_optimize"] = True

###########################################

# Class representing the intial conditions
class InitialConditions(UserExpression):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # random.seed(2 + MPI.rank(MPI.comm_world))
        np.random.seed(0)
        self.nModes = 10
        self.corrlen = 0.2
        self.k0 = np.arange(self.nModes)
        self.k1 = np.arange(self.nModes)
        self.coefs  = np.random.normal(loc=0, scale=1/self.nModes**2, size=(self.nModes,self.nModes)) #* np.exp(-self.k0**2/(2*self.corrlen**2)) + self.k1**2))
        self.G0 = np.exp(-self.k0**2 * self.corrlen**2/2)
        self.G1 = np.exp(-self.k1**2 * self.corrlen**2/2)
    def eval(self, values, x):
        # ptb = 1/100
        # for i in range(len(x)):
        #     ptb *= cos(2 * pi * x[i])
        # values[0] =  1/50*(cos(2*pi*x[1])  + cos(2*pi*x[0])) #0.02*(0.5 - np.random.random())
        # values[0] = np.random.normal(loc=0.4, scale=0.01)
        # values[0] = 0.4 + 0.2*(0.5 - np.random.random())
        # values[0] = np.random.random()
        # values[0] =  0.4+1/50*(cos(1/4*pi*x[1])  + cos(4*pi*x[0]))
        values[0] =  0.4 + 1/50 * cos(2*pi*x[0]) * cos(2*pi*x[1]) # * cos(2*pi*x[2])
        # values[0] = np.random.normal(loc=0.5, scale=0.001)
        # values[0] = (np.cos(pi*self.k0*x[0])*self.G0) @ self.coefs @ (np.cos(pi*self.k1*x[1])*self.G1)
        values[1] = 0.0
    def value_shape(self):
        return (2,)

class IC_TwoBubbles(UserExpression):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.c = [ (0.5-0.2, 0.5), (0.5+0.2, 0.5) ]
        # self.c = [ (0.5-0.2, 0.5-0.2), (0.5+0.2, 0.5-0.2), (0.5+0.2, 0.5+0.2), (0.5-0.2, 0.5+0.2) ]
        self.r = 0.15
        self.eps = kwargs.get('eps', 0.03)
    def eval(self, values, x):
        v = len(self.c)-1
        for c in self.c:
            r = sqrt( (x[0]-c[0])**2 + (x[1]-c[1])**2 )
            v += np.tanh((self.r-r)/(sqrt(2)*self.eps))
        values[0] = v
        values[1] = 0
    def value_shape(self):
        return (2,)

class IC_FourBubbles(UserExpression):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        d = 0.2
        self.c = [ (0.5-d, 0.5-d), (0.5+d, 0.5-d), (0.5+d, 0.5+d), (0.5-d, 0.5+d) ]
        self.r = 0.15
        self.eps = kwargs.get('eps', 0.03)
    def eval(self, values, x):
        v = len(self.c)-1
        for c in self.c:
            r = sqrt( (x[0]-c[0])**2 + (x[1]-c[1])**2 )
            v += np.tanh((self.r-r)/(sqrt(2)*self.eps))
        values[0] = v
        values[1] = 0
    def value_shape(self):
        return (2,)


###########################################


class CahnHilliard(NonlinearProblem):

    def __init__(self, **kwargs):
        NonlinearProblem.__init__(self)

        self.verbose = kwargs.get('verbose', False)
        set_log_active(self.verbose)
        # set_log_level(40)

        ### Spacial dimension
        self.ndim = kwargs.get('ndim', 2)
        
        ### Time-stepper
        self.TS = self.set_TimeStepper(**kwargs)
        b1, b2  = self.TS.beta1, self.TS.beta2

        ### Exports
        ExportFolder  = kwargs.get('ExportFolder', None)
        self.Exporter = Exporter(self, Folder=ExportFolder)

        ###--------------------------------------------------------

        ### Parameters
        zeros = kwargs.get("zeros", [-1,1])
        eps   = kwargs.get('eps', 1.e-1)    # surface parameter
        lmbda = Constant(eps**2)

        ### Chemical potential derivative
        if zeros==[0,1]:
            Phi  = lambda x: 0.25 * x**2 * (1-x)**2
            phi1 = kwargs.get('phi1', lambda x: 0.25 * (3 * x)               )  ### convex part
            phi2 = kwargs.get('phi2', lambda x: 0.25 * (4*x*x*x - 6*x*x - x) )  ### concave part
        elif zeros==[-1,1]:
            factor = 1
            Phi  = lambda x: factor*0.25 * (1-x**2)**2
            Phi1 = lambda x: factor*0.25 * (4*x**2 + 1)
            Phi2 = lambda x: factor*0.25 * (x**4 - 6*x**2)
            phi1 = kwargs.get('phi1', lambda x: factor*0.25 * (8 * x)          )  ### convex part
            phi2 = kwargs.get('phi2', lambda x: factor*0.25 * (4*x**3 - 12*x) )  ### concave part
        else:
            raise Exception('Dont know these zeros.')

        ### Mobility
        M = kwargs.get('mobility', 1)
        M = Constant(M)
        kappa = kwargs.get('kappa', 0)
        m     = kwargs.get('mean',  0)
        kappa, m = Constant(kappa), Constant(m)  

        ### Mesh
        self.Nx   = kwargs.get('Nx', 2**5)    # number of grid points in space (one direction)
        self.ndim = kwargs.get('ndim', 2)
        if self.ndim == 2:
            self.mesh = UnitSquareMesh.create(self.Nx, self.Nx, CellType.Type.quadrilateral)
        elif self.ndim == 3:
            self.mesh = UnitCubeMesh.create(self.Nx, self.Nx, self.Nx, CellType.Type.hexahedron)
        else:
            raise Exception('The dimesion is not implemented!')


        ### Function space
        fg_PerBC = kwargs.get('PerBC', False)
        if fg_PerBC:
            ### Periodic BCs
            BC = PeriodicBoundary(ndim=self.ndim)
            P1 = FiniteElement("Lagrange", self.mesh.ufl_cell(), 1)
            self.V1 = FunctionSpace(self.mesh, P1, constrained_domain=BC)
            self.V2 = FunctionSpace(self.mesh, P1, constrained_domain=BC)
            W = FunctionSpace(self.mesh, MixedElement([P1, P1]), constrained_domain=BC)
            self.W = W
        else:
            ### Homogeneous Neumann BCs
            P1 = FiniteElement("Lagrange", self.mesh.ufl_cell(), 1)
            W = FunctionSpace(self.mesh, MixedElement([P1, P1]))
            self.W = W
            self.V1 = W.sub(0).collapse()
            self.V2 = W.sub(1).collapse()
        self.v2d = vertex_to_dof_map(self.V1)
        self.dofmap1 = self.W.sub(0).dofmap()
        self.dofmap2 = self.W.sub(1).dofmap()


        # Define trial and test functions
        du    = TrialFunction(W)
        q, v  = TestFunctions(W)

        # Define functions
        u   = Function(W)  # current solution
        u0  = Function(W)  # solution from previous converged step
        self.CurrSolFull, self.PrevSolFull = u, u0

        # Split mixed functions
        dc, dmu = split(du)
        c,  mu  = split(u)
        c0, mu0 = split(u0)
        self.CurrSol, self.PrevSol = c, c0

        # Create intial conditions and interpolate
        IC = kwargs.get('IC')
        if isinstance(IC, np.ndarray):
            u.vector()[self.dofmap1.dofs()]  = IC
            u0.vector()[self.dofmap1.dofs()] = IC
        else:            
            u_init = IC(degree=1)
            u.interpolate(u_init)
            u0.interpolate(u_init)
        self.c_init = u.split(True)[0].vector()[self.v2d]

        ###-----------------------------------
        ### Weak statement of the equations
        ###-----------------------------------

        self.fg_LinearSolve = kwargs.get('LinSolve', False)

        if self.fg_LinearSolve:
            ###----------------------------
            ### Linear case assemlage
            ###----------------------------

            self.set_LinSolver(**kwargs)

            # self.CurrSolFull = Function(W)  # current solution
            # self.PrevSolFull = Function(W)  # solution from previous converged step
            # c,  mu  = split(self.CurrSolFull)
            # c0, mu0 = split(self.PrevSolFull)
            
            u1, u2  = TrialFunction(W)
            v1, v2  = TestFunctions(W)


            K = u1*v1*dx + u2*v2*dx + (b1+b2)*dot(M*grad(u2), grad(v1))*dx - factor*2*u1*v2*dx - lmbda * dot(grad(u1), grad(v2))*dx + (b1+b2)*M*kappa*u1*v1*dx
            self.StiffnessMatrix = assemble(K)
            # self.LinSolver.set_operator(self.StiffnessMatrix)
            # self.LinSolverMu.set_operator(self.MassMatrixMu)

            # self._RHS = Constant(1/eps) * phi2(c0)*v2*dx
            self._RHS = phi2(c0)*v2*dx + (b1+b2)*M*kappa*m*v1*dx


            ### Init history term amd modes (in numpy vector terms)

            u1, v1 = TrialFunction(self.V1), TestFunction(self.V1)
            u2, v2 = TrialFunction(self.V2), TestFunction(self.V2)

            self.Modes      = np.zeros([len(self.dofmap1.dofs()), self.TS.nModes+1])
            self.Modes[:,0] = assemble(c*v1*dx).get_local()    
            self.History    = self.Modes @ self.TS.gamma_k
            self.Modes1     = np.zeros([len(self.dofmap1.dofs()), self.TS.nModes+1])
            self.Modes1[:,0]= self.Modes[:,0]   

            self.DiffusionMatrix  = assemble(dot(M*grad(u1),grad(v1))*dx)
            self.MassMatrix2      = assemble(u2*v2*dx)
            if self.TS.Scheme == 'mIE':
                self._Mmu  = (phi1(c) + phi2(c0))*v2*dx + lmbda * dot(grad(c), grad(v2))*dx
            elif self.TS.Scheme == 'mCN':
                self._Mmu  = (phi1(c) + phi2(c))*v2*dx + lmbda * dot(grad(c), grad(v2))*dx
            else:
                raise Exception('Unknown scheme!')
            self.mu    = Function(self.V2)
            self.mu0   = Function(self.V2)
            self.mu.vector()[:]  = 0
            self.mu0.vector()[:] = 0
            self.mu0.vector().set_local(self.mu.vector()[:])
            Mmu = assemble(self._Mmu)
            self.LinSolver2.solve(self.MassMatrix2, self.mu.vector(), Mmu)
            self._CurrFlux = dot(M*grad(mu ), grad(v1))*dx
            self._PrevFlux = dot(M*grad(mu0), grad(v1))*dx
            F = dot(M*grad(u2), grad(v1))*dx
            self.FluxMatrix = assemble(F)
            self._FluxNorm  = dot(M*grad(self.mu), grad(self.mu))*dx

            ### Source          
            self.SourceMatrix = assemble(M*kappa*u1*v1*dx)
            self.SourceTerm   = assemble(M*kappa*m*v1*dx)


        else:
            ###----------------------------
            ### General case assemlage
            ###----------------------------

            ### Solver
            self.set_Solver(**kwargs)

            ### Potential term
            P = phi1(c)*v*dx + phi2(c0)*v*dx  + lmbda * dot(grad(c), grad(v))*dx
            # P = phi1(c)*v*dx + phi2(c)*v*dx  + lmbda * dot(grad(c), grad(v))*dx

            self._CurrFlux = dot(M*grad(mu ), grad(q))*dx
            self._PrevFlux = dot(M*grad(mu0), grad(q))*dx

            b1, b2 = self.TS.beta1, self.TS.beta2
            Eq1 = c*q*dx + (b1+b2)*self._CurrFlux #+ b2*self._PrevFlux ### c0 goes to History term
            Eq2 = mu*v*dx - P
            self._Residual = Eq1 + Eq2  ### without history term


            ### Source
            S = kwargs.get('S', 0)
            if S:
                SourceTerm = Constant(-S)*(c-zeros[0])*(c-zeros[1])*q*dx
                self._Residual += SourceTerm

            # Compute directional derivative about u in the direction of du (Jacobian)
            self.Jacobian = derivative(self._Residual, u, du)

            ### Init history term amd modes (in numpy vector terms)
            self.Modes      = np.zeros([u.vector().get_local().size, self.TS.nModes+1])
            self.Modes[:,0] = assemble(c*q*dx).get_local()        
            self.History    = self.Modes @ self.TS.gamma_k

            mu, v = TrialFunction(self.V2), TestFunction(self.V2)
            self._ModalRHS  = (phi1(c) + phi2(c))*v*dx + lmbda * dot(grad(c), grad(v))*dx
            self.MassMatrixMu = assemble(mu*v*dx)
            # self.LinSolverMu.set_operator(self.MassMatrixMu)
            self.mu = Function(self.V2)

            u1, u2  = TrialFunction(W)
            v1, v2  = TestFunctions(W)

            self.DiffusionMatrix = assemble(dot(M*grad(u1),grad(v1))*dx + dot(M*grad(u2),grad(v2))*dx)
            self._FluxNorm = dot(M*grad(mu), grad(mu))*dx

        ###----------------------------------------------------------
        
        ### Other quantities
        self._mass     = c*dx
        # self._Energy1  = (Phi1(c)+Phi2(c))*dx + 0.5*lmbda*dot(grad(c), grad(c))*dx
        self._Energy   = Phi(c)*dx + 0.5*lmbda*dot(grad(c), grad(c))*dx





        ###-----------------------------------------------------------

    def F(self, b, x):
        assemble(self._Residual, tensor=b)
        b[:] = b - self.History

    def J(self, A, x):
        assemble(self.Jacobian, tensor=A)

    def update_history(self):
        if self.scheme == 'L1':
            alpha = self.alpha        
            j  = len(self.TS.b)
            bj = ((j+1)**(1-alpha) - j**(1-alpha)) / gamma(2-alpha)
            self.TS.b = np.append(self.TS.b, bj)
            coefs = np.append(-np.diff(self.TS.b)/self.TS.b[0], self.TS.b[-1]/self.TS.b[0])[::-1]

            HistSolNew      = self.Model.multMass(self.CurrSol).copy(True) 
            self.HistSol    = np.hstack([ self.HistSol,  HistSolNew.reshape([-1,1])])
            self.History[:] = self.HistSol @ coefs

            self.PrevSol[:] = self.CurrSol[:]
            self.CurrF[:]   = self.Model.update_NonLinearPart(self.CurrSol, time=self.CurrTime)
            self.PrevF[:]   = self.CurrF[:]
        else: ### RA scheme
            if self.fg_LinearSolve:
                c = self.CurrSolFull.split(True)[0].vector()
                c0= self.PrevSolFull.split(True)[0].vector()
                self.mu0.vector().set_local(self.mu.vector()[:])
                Mmu = assemble(self._Mmu)
                self.LinSolver2.solve(self.MassMatrix2, self.mu.vector(), Mmu)
                Flux1 = ( self.FluxMatrix*self.mu.vector()  + self.SourceMatrix*c  - self.SourceTerm).get_local()
                Flux2 = ( self.FluxMatrix*self.mu0.vector() + self.SourceMatrix*c0 - self.SourceTerm).get_local()
                # self.mu0.vector().set_local(self.mu.vector().get_local())
                # self.mu.vector().set_local(self.CurrSolFull.split(True)[1].vector()[:])
                # Flux1 = assemble(self._CurrFlux).get_local()
                # Flux2 = assemble(self._PrevFlux).get_local()
                g_k, b1_k, b2_k = self.TS.gamma_k, self.TS.beta1_k, self.TS.beta2_k
                self.Modes[:]   = g_k*self.Modes - (b1_k*Flux1[:,None] + b2_k*Flux2[:,None])
                # self.Modes1[:]  = g_k*self.Modes1- (b1_k*FFlux1[:,None] + b2_k*FFlux2[:,None])
                self.History[:] = self.Modes @ g_k
            else:
                # u = self.CurrSolFull
                # ModalRHS = assemble(self._ModalRHS)
                # self.LinSolverMu.solve(self.MassMatrixMu, self.mu.vector(), ModalRHS)
                # u.vector()[self.dofmap2.dofs()] = self.mu.vector()[:]
                F1 = assemble(self._CurrFlux).get_local()
                F0 = assemble(self._PrevFlux).get_local()
                g_k, b1_k, b2_k = self.TS.gamma_k, self.TS.beta1_k, self.TS.beta2_k
                self.Modes[:]   = g_k*self.Modes - (b1_k*F1[:,None] + b2_k*F0[:,None])
                self.History[:] = self.Modes @ g_k

    def __call__(self):
        u, u0 = self.CurrSolFull, self.PrevSolFull

        ### yield initial solution
        self.TS.restart()
        yield u.split(True)[0].vector()[:]

        # Step in time
        if self.fg_LinearSolve:
            while self.TS.inc():    
                u0.vector().set_local(u.vector().get_local())
                RHS = assemble(self._RHS) 
                RHS[self.dofmap1.dofs()] += self.History
                self.LinSolver.solve(self.StiffnessMatrix, u.vector(), RHS)
                self.update_history()
                yield u.split(True)[0].vector()[:]
        else:
            while self.TS.inc():    
                u0.vector()[:] = u.vector()[:]
                self.Solver.solve(self, u.vector())
                self.update_history()
                yield u.split(True)[0].vector()[:]
    
    ### Solution iterator (time-integration)
    def SolutionIterator(self):
        return tqdm(enumerate(self()), total=self.TS.nTimeSteps)


    #----------------------------------------
    # Fractional Time Stepper
    #----------------------------------------
    def set_TimeStepper(self, **kwargs):
        self.scheme = kwargs.get('scheme', 'mCN')
        if self.scheme == 'L1':
            self.TS = FracTS_L1(**kwargs)
        else:
            self.TS = FracTS_RA(**kwargs)          
        self.dt = self.TS.dt
        self.T  = self.TS.TimeInterval
        return self.TS

    #----------------------------------------
    # NL Solver
    #----------------------------------------
    def set_Solver(self, **kwargs):
        # Nonlinear solver
        self.Solver = NewtonSolver()
        self.Solver.parameters["linear_solver"] = "lu"
        if self.Solver.parameters["linear_solver"] != "lu":
            self.Solver.parameters["preconditioner"] = "ilu"
        self.Solver.parameters["convergence_criterion"] = "incremental" #"residual" #"incremental"
        # self.Solver.parameters["maximum_iterations"] = 1000
        self.Solver.parameters["relative_tolerance"] = 1e-6
        # self.Solver.parameters["absolute_tolerance"] = 1.e-8
        # self.Solver.parameters["error_on_nonconvergence"] = True
        self.Solver.parameters["report"] = self.verbose

        ### Linear solver
        LinSolver = PETScLUSolver("mumps")
        # LinSolver = PETScKrylovSolver("gmres", "ilu")
        # # LinSolver = PETScKrylovSolver("cg", "amg")
        # LinSolver.parameters["maximum_iterations"] = 1000
        # LinSolver.parameters["relative_tolerance"] = 1.e-10
        # LinSolver.parameters["absolute_tolerance"] = 1.e-8
        # LinSolver.parameters["error_on_nonconvergence"] = True
        # LinSolver.parameters["nonzero_initial_guess"] = True
        # LinSolver.parameters["monitor_convergence"] = False
        self.LinSolver  = LinSolver

        self.LinSolverMu = PETScLUSolver("mumps") #PETScKrylovSolver("cg", "icc")
        # self.LinSolverMu.parameters["maximum_iterations"] = 1000
        # self.LinSolverMu.parameters["relative_tolerance"] = 1.e-10
        # self.LinSolverMu.parameters["absolute_tolerance"] = 1.e-8
        # self.LinSolverMu.parameters["error_on_nonconvergence"] = True
        # self.LinSolverMu.parameters["nonzero_initial_guess"] = True
        # self.LinSolverMu.parameters["monitor_convergence"] = False


    #----------------------------------------
    # Linear Solver
    #----------------------------------------
    def set_LinSolver(self, **kwargs):

        ### Linear solver
        self.LinSolver = PETScLUSolver("mumps")
        # self.LinSolver = PETScKrylovSolver("gmres", "ilu")
        # # # LinSolver = PETScKrylovSolver("cg", "amg")
        # # LinSolver.parameters["maximum_iterations"] = 1000
        # self.LinSolver.parameters["relative_tolerance"] = 1.e-12
        # self.LinSolver.parameters["absolute_tolerance"] = 1.e-12
        # # LinSolver.parameters["error_on_nonconvergence"] = True
        # self.LinSolver.parameters["nonzero_initial_guess"] = True
        # # LinSolver.parameters["monitor_convergence"] = False

        # self.LinSolverM2 = PETScLUSolver("mumps") #PETScKrylovSolver("cg", "icc")
        # self.LinSolverMu.parameters["maximum_iterations"] = 1000
        # self.LinSolverMu.parameters["relative_tolerance"] = 1.e-10
        # self.LinSolverMu.parameters["absolute_tolerance"] = 1.e-8
        # self.LinSolverMu.parameters["error_on_nonconvergence"] = True
        # self.LinSolverMu.parameters["nonzero_initial_guess"] = True
        # self.LinSolverMu.parameters["monitor_convergence"] = False


        self.LinSolver2 = PETScLUSolver("mumps")
        # self.LinSolver2 = PETScKrylovSolver("cg", "icc")
        # self.LinSolver2.parameters["relative_tolerance"] = 1.e-12
        # self.LinSolver2.parameters["absolute_tolerance"] = 1.e-12
        # self.LinSolver2.parameters["nonzero_initial_guess"] = True



        self.LinSolverD = PETScKrylovSolver("cg", "icc")



    #----------------------------------------------------------------------
    # Prperties and postprocessing
    #----------------------------------------------------------------------

    @property
    def Energy(self):
        return assemble(self._Energy)

    @property
    def HistoryEnergy(self):
        if self.fg_LinearSolve:
            d, c, c_inf = self.TS.RA.d, self.TS.RA.c, self.TS.RA.c_inf
            g_k, b1_k, b2_k = self.TS.gamma_k, self.TS.beta1_k, self.TS.beta2_k
            w = d/c #(1-g_k)/(b1_k) #d/c
            f   = Function(self.V1)
            v   = f.vector()
            Muk = Vector(v)
            mu  = self.CurrSolFull.split(True)[1].vector()
            HistoryEnergy = 0
            for k in range(self.TS.nModes):
                Muk.set_local(self.Modes[:,k+1])
                self.LinSolverD.solve(self.FluxMatrix, v, Muk)
                # HistoryEnergy += d[k]/c[k] * 0.5*assemble(dot(grad(f),grad(f))*dx)
                # HistoryEnergy += d[k]/c[k] * 0.5* v.inner(self.FluxMatrix*v)
                HistoryEnergy += w[k] * 0.5* v.inner(Muk)
            # HistoryEnergy += 0.5*c_inf*assemble(self._FluxNorm)
            HistoryEnergy += 0.5*c_inf* mu.inner(self.FluxMatrix*mu)
            return HistoryEnergy
        else:
            d, c, c_inf = self.TS.RA.d, self.TS.RA.c, self.TS.RA.c_inf
            D   = self.DiffusionMatrix
            f   = Function(self.W)
            v   = f.vector()
            Muk = Vector(v)
            mu = self.CurrSolFull.sub(1)
            FluxNorm = assemble(dot(grad(mu),grad(mu))*dx)
            HistoryEnergy = 0
            for k in range(self.TS.nModes):
                Muk.set_local(self.Modes[:,k+1])
                # Muk[:] = self.Modes[:,k+1]
                solve(D, v, Muk)
                HistoryEnergy += d[k]/c[k] * 0.5*assemble(dot(grad(f.sub(0)),grad(f.sub(0)))*dx)
            # HistoryEnergy += 0.5*c_inf*assemble(self._FluxNorm)
            HistoryEnergy += 0.5*c_inf*FluxNorm
            return HistoryEnergy

    @property
    def EnergyModified(self):
        return self.Energy + self.HistoryEnergy

    @property
    def EnergyGradNorm(self):
        EGN = assemble(self._FluxNorm)
        return EGN

    @property
    def mass(self):
        return assemble(self._mass)

    def norm(self, v):
        f = Function(self.W)
        f.vector()[self.dofmap1.dofs()] = v[:]
        sqr_norm_f = assemble(f.sub(0)*f.sub(0)*dx)
        norm_f = np.sqrt(sqr_norm_f)
        return norm_f


#===========================================================
#   Helpers
#===========================================================

class Exporter:

    def __init__(self, ProblemObj, **kwargs):
        self.ProblemObj = ProblemObj
        self.Folder     = kwargs.get('Folder', None)
        if self.Folder:
            self.vtk_file = File(self.Folder + 'Solution.pvd', "compressed")

    def test_export_every(self, export_every):
        if export_every is None:
            return True
        else:
            t = self.ProblemObj.TS.CurrTime
            h = self.ProblemObj.TS.dt
            tau  = export_every
            test = ( t%tau <= h/2 or tau-t%tau < h/2)
            return test

    def export_step_vtk(self, **kwargs):
        export_every = kwargs.get('export_every', None)
        if self.test_export_every(export_every):
            t = self.ProblemObj.TS.CurrTime
            u = self.ProblemObj.CurrSolFull
            self.vtk_file << (u.split()[0], t)

    def export_step_npy(self, **kwargs):
        export_every = kwargs.get('export_every', None)
        field        = kwargs.get('field', self.ProblemObj.CurrSolFull.split()[0].vector()[:])
        name         = kwargs.get('name', 'field')
        if self.test_export_every(export_every):
            i = self.ProblemObj.TS.TimeStep
            np.save(self.Folder + name + '_i={0:d}'.format(i), field)

    def export_npy(self, data, **kwargs):
        name = kwargs.get('name', 'data')
        np.save(self.Folder + name, data)

    def import_npy(self, **kwargs):
        name = kwargs.get('name', 'data')
        return np.load(self.Folder + name + '.npy')

    def clear(self):
        os.system('rm ' + self.Folder + '*' )





#######################################################################################################
#	Periodic boundary conditions
#######################################################################################################

class PeriodicBoundary(SubDomain):

	def __init__(self, ndim):
		SubDomain.__init__(self)
		self.ndim = ndim

	def inside(self, x, on_boundary):
		return bool( 	any([ near(x[j], 0) for j in range(self.ndim) ]) and 
					not any([ near(x[j], 1) for j in range(self.ndim) ]) and 
						on_boundary
					)

	def map(self, x, y):
		for j in range(self.ndim):
			if near(x[j], 1):
				y[j] = x[j] - 1
			else:
				y[j] = x[j]






###########################################

