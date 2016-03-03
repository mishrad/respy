""" This module contains some auxiliary functions for the PYTHON
implementations of the core functions.
"""

# standard library
import numpy as np

# project library
from robupy.auxiliary import check_model_parameters
from robupy.constants import HUGE_FLOAT

def _check_optimization_parameters(x):
    """ Check optimization parameters.
    """
    # Perform checks
    assert (isinstance(x, np.ndarray))
    assert (x.dtype == np.float)
    assert (x.shape == (26,))
    assert (np.all(np.isfinite(x)))

    # Finishing
    return True



def opt_get_optim_parameters(coeffs_a, coeffs_b, coeffs_edu, coeffs_home,
                             shocks, eps_cholesky, is_debug):
    """ Get parameters.
    """
    if is_debug:
        args = coeffs_a, coeffs_b, coeffs_edu, coeffs_home, shocks, eps_cholesky
        assert (check_model_parameters(*args))

    # Initialize container
    x = np.tile(np.nan, 26)

    # Occupation A
    x[0:6] = coeffs_a

    # Occupation B
    x[6:12] = coeffs_b

    # Education
    x[12:15] = coeffs_edu

    # Home
    x[15:16] = coeffs_home

    # Shocks
    x[16:20] = eps_cholesky[0:4, 0]
    x[20:23] = eps_cholesky[1:4, 1]
    x[23:25] = eps_cholesky[2:4, 2]
    x[25:26] = eps_cholesky[3:4, 3]

    # Checks
    if is_debug:
        _check_optimization_parameters(x)

    # Finishing
    return x


def opt_get_model_parameters(x, is_debug):
    """ Update parameter values. Note that it is crucial to transform the
    subsets of the numpy array to lists. Otherwise, the code does produce
    random output.
    """
    # Antibugging
    if is_debug:
        _check_optimization_parameters(x)

    # Occupation A
    coeffs_a = x[0:6]

    # Occupation B
    coeffs_b = x[6:12]

    # Education
    coeffs_edu = x[12:15]

    # Home
    coeffs_home = x[15:16]

    # Cholesky
    eps_cholesky = np.tile(0.0, (4, 4))

    eps_cholesky[0:4, 0] = x[16:20]
    eps_cholesky[1:4, 1] = x[20:23]
    eps_cholesky[2:4, 2] = x[23:25]
    eps_cholesky[3:4, 3] = x[25]

    # Shocks
    shocks = np.matmul(eps_cholesky, eps_cholesky.T)

    if is_debug:
        args = coeffs_a, coeffs_b, coeffs_edu, coeffs_home, shocks, eps_cholesky
        assert (check_model_parameters(*args))

    # Finishing
    return coeffs_a, coeffs_b, coeffs_edu, coeffs_home, shocks, eps_cholesky


def simulate_emax(num_periods, num_draws, period, k, eps_relevant_emax,
        payoffs_systematic, edu_max, edu_start, periods_emax, states_all,
        mapping_state_idx, delta):
    """ Simulate expected future value.
    """
    # Initialize containers
    emax_simulated, payoffs_ex_post, future_payoffs = 0.0, 0.0, 0.0

    # Calculate maximum value
    for i in range(num_draws):

        # Select disturbances for this draw
        disturbances = eps_relevant_emax[i, :]

        # Get total value of admissible states
        total_payoffs, payoffs_ex_post, future_payoffs = get_total_value(period,
            num_periods, delta, payoffs_systematic, disturbances, edu_max,
            edu_start, mapping_state_idx, periods_emax, k, states_all)

        # Determine optimal choice
        maximum = max(total_payoffs)

        # Recording expected future value
        emax_simulated += maximum

    # Scaling
    emax_simulated = emax_simulated / num_draws

    # Finishing
    return emax_simulated, payoffs_ex_post, future_payoffs


def get_total_value(period, num_periods, delta, payoffs_systematic,
                    disturbances, edu_max, edu_start, mapping_state_idx,
                    periods_emax, k, states_all):
    """ Get total value of all possible states.
    """
    # Auxiliary objects
    is_myopic = (delta == 0.00)

    # Initialize containers
    payoffs_ex_post = np.tile(np.nan, 4)

    # Calculate ex post payoffs
    for j in [0, 1]:
        payoffs_ex_post[j] = payoffs_systematic[j] * disturbances[j]

    for j in [2, 3]:
        payoffs_ex_post[j] = payoffs_systematic[j] + disturbances[j]

    # Get future values
    if period != (num_periods - 1):
        future_payoffs = _get_future_payoffs(edu_max, edu_start,
            mapping_state_idx, period, periods_emax, k, states_all)
    else:
        future_payoffs = np.tile(0.0, 4)

    # Calculate total utilities
    total_payoffs = payoffs_ex_post + delta * future_payoffs

    # Special treatment in case of myopic agents
    if is_myopic:
        total_payoffs = _stabilize_myopic(total_payoffs, future_payoffs)

    # Finishing
    return total_payoffs, payoffs_ex_post, future_payoffs


''' Private functions
'''


def _get_future_payoffs(edu_max, edu_start, mapping_state_idx, period,
        periods_emax, k, states_all):
    """ Get future payoffs for additional choices.
    """
    # Distribute state space
    exp_a, exp_b, edu, edu_lagged = states_all[period, k, :]

    # Future utilities
    future_payoffs = np.tile(np.nan, 4)

    # Working in occupation A
    future_idx = mapping_state_idx[period + 1, exp_a + 1, exp_b, edu, 0]
    future_payoffs[0] = periods_emax[period + 1, future_idx]

    # Working in occupation B
    future_idx = mapping_state_idx[period + 1, exp_a, exp_b + 1, edu, 0]
    future_payoffs[1] = periods_emax[period + 1, future_idx]

    # Increasing schooling. Note that adding an additional year
    # of schooling is only possible for those that have strictly
    # less than the maximum level of additional education allowed.
    if edu < edu_max - edu_start:
        future_idx = mapping_state_idx[period + 1, exp_a, exp_b, edu + 1, 1]
        future_payoffs[2] = periods_emax[period + 1, future_idx]
    else:
        future_payoffs[2] = -HUGE_FLOAT

    # Staying at home
    future_idx = mapping_state_idx[period + 1, exp_a, exp_b, edu, 0]
    future_payoffs[3] = periods_emax[period + 1, future_idx]

    # Finishing
    return future_payoffs


def _stabilize_myopic(total_payoffs, future_payoffs):
    """ Ensuring that schooling does not increase beyond the maximum allowed
    level. This is necessary as in the special case where delta is equal to
    zero, (-np.inf * 0.00) evaluates to NAN. This is returned as the maximum
    value when calling np.argmax.
    """
    # Determine NAN
    is_huge = (future_payoffs[2] == -HUGE_FLOAT)

    # Replace with negative infinity
    if is_huge:
        total_payoffs[2] = -HUGE_FLOAT

    # Finishing
    return total_payoffs
