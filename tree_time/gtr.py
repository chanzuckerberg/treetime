import numpy as np
from scipy import optimize as sciopt
import config as ttconf
from seq_utils import alphabets
class GTR(object):
    """
    Defines General-Time-Reversible model of character evolution.
    """
    def __init__(self, alphabet_type):
        """
        Initialize empty evolutionary model.
        Args:
         - alphabet (numpy.array): alphabet of the sequence.
        """
        if not alphabet_type in alphabets:
            raise AttributeError("Unknown alphabet type specified")

        self.alphabet_type = alphabet_type
        alphabet = alphabets[alphabet_type]
        self.alphabet = alphabet

        # general rate matrix
        self.W = np.zeros((alphabet.shape[0], alphabet.shape[0]))
        # stationary states of the characters
        self.Pi = np.zeros((alphabet.shape[0], alphabet.shape[0]))
        # mutation rate, scaling factor
        self.mu = 1.0
        # eigendecomposition of the GTR matrix
        # Pi.dot(W) = v.dot(eigenmat).dot(v_inv)
        self.v = np.zeros((alphabet.shape[0], alphabet.shape[0]))
        self.v_inv = np.zeros((alphabet.shape[0], alphabet.shape[0]))
        self.eigenmat = np.zeros(alphabet.shape[0])

        # distance matrix (needed for topology optimization and for NJ)
        self.dm = None

    @classmethod
    def standard(cls, model='Jukes-Cantor', **kwargs):
        if 'alphabet' in kwargs and alphabet in alphabets.keys():
            alphabet = kwargs['alphabet']
        else:
            print ("No alphabet specified. Using default nucleotide.")
            alphabet = 'nuc'
        if 'mu' in kwargs:
            mu = kwargs['mu']
        else:
            mu = 1.0

        if model=='Jukes-Cantor':

            gtr = cls('nuc')
            gtr.mu = mu
            a = gtr.alphabet.shape[0]

            # flow matrix
            gtr.W = np.ones((a,a))
            np.fill_diagonal(gtr.W, - ((gtr.W).sum(0) - 1))

            # equilibrium concentrations matrix
            gtr.Pi = np.zeros(gtr.W.shape)
            np.fill_diagonal(gtr.Pi, 1.0/a)

            gtr._check_fix_Q() # make sure the main diagonal is correct
            gtr._eig() # eigendecompose the rate matrix
            return gtr

        elif model=='random':
            gtr = cls(alphabet)
            a = gtr.alphabet.shape[0]

            gtr.mu = mu

            Pi = 1.0*np.random.randint(0,100,size=(a))
            Pi /= Pi.sum()
            gtr.Pi = np.diagflat(Pi)

            W = 1.0*np.random.randint(0,100,size=(a,a)) # with gaps
            gtr.W = W+W.T

            gtr._check_fix_Q()
            gtr._eig()
            return gtr
        else:
            raise NotImplementedError("The specified evolutionary model is unsupported!")

    def _check_fix_Q(self):
        """
        Check the main diagonal of Q and fix it in case it does not corresond the definition of Q.
        """
        Q = self.Pi.dot(self.W)
        if (Q.sum(0) < 1e-10).sum() < self.alphabet.shape[0]: # at least one rate is wrong
            # fix Q
            self.Pi /= self.Pi.sum() # correct the Pi manually
            # fix W
            np.fill_diagonal(self.W, 0)
            Wdiag = -((self.W.T*np.diagonal(self.Pi)).T).sum(0)/ \
                    np.diagonal(self.Pi)
            np.fill_diagonal(self.W, Wdiag)
            Q1 = self.Pi.dot(self.W)
            if (Q1.sum(0) < 1e-10).sum() <  self.alphabet.shape[0]: # fix failed
                raise ArithmeticError("Cannot fix the diagonal of the GTR rate matrix.")
        return

    def _eig(self):
        """
        Perform eigendecompositon of the rate matrix
        """
        # eigendecomposition of the rate matrix
        eigvals, eigvecs = np.linalg.eig(self.Pi.dot(self.W))
        self.v = eigvecs
        self.v_inv = np.linalg.inv(self.v)
        self.eigenmat = eigvals
        return

    def prob_t(self, profile_p, profile_ch, t, rotated=False, return_log=False):
        """
        Compute the probability of the two profiles to be separated by the time t.
        Args:
         - profile_p(np.array): parent profile of shape (L, a), where L - length of the sequence, a - alpphabet size.

         - profile_ch(np.array): child profile of shape (L, a), where L - length of the sequence, a - alpphabet size.

         - t (double): time (branch len), separating the profiles.

         - rotated (bool, default False): if True, assume that the supplied profiles are already rotated.

         - return_log(bool, default False): whether return log-probability.

        Returns:
         - prob(np.array): resulting probability.
        """

        if t < 0:
            if return_log:
                return -BIG_NUMBER
            else:
                return 0.0

        L = profile_p.shape[0]
        if L != profile_ch.shape[0]:
            raise ValueError("Sequence lengths do not match!")
        eLambdaT = self._exp_lt(t)
        if not rotated: # we need to rotate first
            p1 = profile_p.dot(self.v) # (L x a).dot(a x a) = (L x a) - prof in eigenspace
            p2 = (self.v_inv.dot(profile_ch.T)).T # (L x a).dot(a x a) = (L x a) - prof in eigenspace
        else:
            p1 = profile_p
            p2 = profile_ch
            #prob = (profile_p*eLambdaT*profile_ch).sum(1) # sum over the alphabet

        prob = (p1*eLambdaT*p2).sum(1) # sum_i (p1_i * exp(l_i*t) * p_2_i) result = vector lenght L
        prob[prob<0] = 0.0 # avoid rounding instability

        if return_log:
            prob = (np.log(prob + ttconf.TINY_NUMBER)).sum() # sum all sites
        else:
            prob = prob.prod() # prod of all sites
        return prob

    def optimal_t(self, profile_p, profile_ch, rotated=False, return_log=False):
        """
        Find the optimal distance between the two profiles
        """

        def _neg_prob(t, parent, child):
            """
            Probability to observe child given the the parent state, transition
            matrix and the time of evolution (branch length).

            Args:
             - t(double): branch length (time between sequences)
             - parent (numpy.array): parent sequence
             - child(numpy.array): child sequence
             - tm (GTR): model of evolution

            Returns:
             - prob(double): negative probability of the two given sequences
               to be separated by the time t.
            """
            return -1*self.prob_t (parent, child, t, rotated=False, return_log=True)

        opt = sciopt.minimize_scalar(_neg_prob,
                bounds=[0,ttconf.MAX_BRANCH_LENGTH],
                method='Bounded',
                args=(profile_p, profile_ch))

        new_len = opt["x"]

        if new_len > .9 * ttconf.MAX_BRANCH_LENGTH or opt["success"] != True:
            if verbose > 0:
                print ("Cannot optimize branch length, minimization failed.")
            import ipdb; ipdb.set_trace()
            return -1.0
        else:
            return  new_len

    def propagate_profile(self, profile, t, rotated=False, return_log=False):
        """
        Compute the probability of the sequence state (profile) at time (t+t0),
        given the sequence state (profile) at time t0.
        Args:
         - profile(numpy.array): sequence profile. Shape = (L, a), where L - sequence length, a - alphabet size.

         - t(doble): time to propagate

         - rotated(bool default False): whether the supplied profile is in the GTR matrix eigenspace

         - return log (bool, default False): whether to return log-probability

        Returns:
         - res(np.array): profile of the sequence after time t. Shape = (L, a), where L - sequence length, a - alphabet size.
        """
        eLambdaT = self._exp_lt(t) # vector lenght = a

        if not rotated:
            # rotate
            p = self.v_inv.dot(profile.T).T
        else:
            p = profile

        res = (self.v.dot((eLambdaT * p).T)).T

        if not return_log:
            return res
        else:
            return np.log(res)

    def _exp_lt(self, t):
        """
        Returns:
         - exp_lt(numpy.array): array of values exp(lambda(i) * t), where (i) - alphabet index (the eigenvalue number).
        """
        return np.exp(self.mu * t * self.eigenmat)

    def create_dm(self, profiles):
        self.dm = np.zeros(self.Pi.shape)

        T = np.array([[self.optimal_t(p1,p2) for p1 in profiles] for p2 in profiles])


    def dm_dist(self, p1,p2):
        """
        Distance between two prfiles based on the pre-computed
        distance matrix
        """
        pass

if __name__ == "__main__":
     pass