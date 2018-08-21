import pickle as pkl
import numpy as np
import pandas as pd
import json
import copy
import os
import atexit

from respy.pre_processing.model_processing import write_init_file
from respy.python.shared.shared_auxiliary import distribute_parameters
from respy.python.shared.shared_auxiliary import dist_class_attributes
from respy.python.shared.shared_auxiliary import remove_scratch

from respy.python.shared.shared_constants import ROOT_DIR
from respy.python.shared.shared_constants import OPT_EST_FORT
from respy.python.shared.shared_constants import OPT_EST_PYTH
from respy.pre_processing.model_processing import read_init_file, convert_init_dict_to_attr_dict, \
    convert_attr_dict_to_init_dict
from respy.pre_processing.model_checking import check_model_attributes, \
    check_model_solution
from respy.custom_exceptions import UserError
from respy.python.interface import respy_interface
from respy.fortran.interface import resfort_interface
from respy.python.record.record_estimation import record_estimation_sample
from respy.python.shared.shared_auxiliary import get_est_info
from respy.pre_processing.data_processing import process_dataset
from respy.python.shared.shared_auxiliary import replace_missing_values
from respy.python.simulate.simulate_auxiliary import check_dataset_sim
from respy.python.shared.shared_constants import DATA_FORMATS_SIM
from respy.python.shared.shared_constants import DATA_LABELS_SIM
from respy.python.simulate.simulate_auxiliary import write_info
from respy.python.simulate.simulate_auxiliary import write_out
from respy.python.shared.shared_auxiliary import add_solution


class RespyCls(object):
    """Class that defines a model in respy.  """

    def __init__(self, fname):
        self._set_hardcoded_attributes()
        ini = read_init_file(fname)
        self.attr = convert_init_dict_to_attr_dict(ini)
        self._update_derived_attributes()
        self._initialize_solution_attributes()
        self.attr['is_locked'] = False
        self.attr['is_solved'] = False
        self.lock()

    def _set_hardcoded_attributes(self):
        """Set attributes that can't be changed by the model specification."""
        self.derived_attributes = ['is_myopic', 'num_paras']
        self.solution_attributes = [
            'periods_rewards_systematic', 'states_number_period',
            'mapping_state_idx', 'periods_emax', 'states_all']

    def _initialize_solution_attributes(self):
        """Initialize solution attributes to None."""
        for attribute in self.solution_attributes:
            self.attr[attribute] = None

    def update_optim_paras(self, x_econ):
        """Update model parameters."""
        x_econ = copy.deepcopy(x_econ)

        self.reset()

        new_paras_dict = distribute_parameters(
            paras_vec=x_econ, is_debug=True, paras_type='econ',)
        self.attr['optim_paras'].update(new_paras_dict)

    def lock(self):
        """Lock class instance."""
        assert (not self.attr['is_locked']), \
            'Only unlocked instances of clsRespy can be locked.'

        self._update_derived_attributes()
        self._check_model_attributes()
        self._check_model_solution()
        self.attr['is_locked'] = True

    def unlock(self):
        """Unlock class instance."""
        assert self.attr['is_locked'], \
            'Only locked instances of clsRespy can be unlocked.'

        self.attr['is_locked'] = False

    def get_attr(self, key):
        """Get attributes."""
        assert self.attr['is_locked']
        self._check_key(key)

        if key in self.solution_attributes:
            assert self.get_attr('is_solved'), 'invalid request'

        return self.attr[key]

    def set_attr(self, key, value):
        """Set attributes."""
        assert (not self.attr['is_locked'])
        self._check_key(key)

        invalid_attr = self.derived_attributes + ['optim_paras', 'init_dict']
        if key in invalid_attr:
            raise AssertionError(
                '{} must not be modified by users!'.format(key))

        if key in self.solution_attributes:
            assert not self.attr['is_solved'], \
                'Solution attributes can only be set if model is not solved.'

        self.attr[key] = value
        self._update_derived_attributes()

    def store(self, file_name):
        """Store class instance."""
        assert self.attr['is_locked']
        assert isinstance(file_name, str)
        pkl.dump(self, open(file_name, 'wb'))

    def write_out(self, fname='model.respy.ini'):
        """Write out the implied initialization file of the class instance."""
        init_dict = convert_attr_dict_to_init_dict(self.attr)
        write_init_file(init_dict, fname)

    def reset(self):
        """Remove solution attributes from class instance."""
        for label in self.solution_attributes:
            self.attr[label] = None
        self.attr['is_solved'] = False

    def check_equal_solution(self, other):
        """Compare two class instances for equality of solution attributes."""
        assert (isinstance(other, RespyCls))

        for key_ in self.solution_attributes:
            try:
                np.testing.assert_almost_equal(
                    self.attr[key_], other.attr[key_])
            except AssertionError:
                return False

        return True

    def _update_derived_attributes(self):
        """Update derived attributes."""
        num_types = self.attr['num_types']
        self.attr['is_myopic'] = (self.attr['optim_paras']['delta'] == 0.00)[0]
        self.attr['num_paras'] = 53 + (num_types - 1) * 6

    def _check_model_attributes(self):
        """Check integrity of class instance.

        This testing is done the first time the class is locked and if
        the package is running in debug mode.

        """
        with open(ROOT_DIR + '/.config', 'r') as infile:
            config_dict = json.load(infile)

        check_model_attributes(self.attr, config_dict)

    def _check_model_solution(self):
        """Check the integrity of the results."""
        check_model_solution(self.attr)

    def _check_key(self, key):
        """Check that key is present."""
        assert (key in self.attr.keys()), \
            'Invalid key requested: {}'.format(key)

    def check_estimation(self):
        """Check model attributes that are only relevant for estimation tasks."""
        # Check that class instance is locked.
        assert self.get_attr('is_locked')

        # Check that no other estimations are currently running in this directory.
        assert not os.path.exists('.estimation.respy.scratch')

        # Distribute class attributes
        optimizer_options, optimizer_used, optim_paras, version, maxfun, \
            num_paras, file_est = dist_class_attributes(
                self, 'optimizer_options', 'optimizer_used', 'optim_paras',
                'version', 'maxfun', 'num_paras', 'file_est')

        # Ensure that at least one parameter is free.
        if sum(optim_paras['paras_fixed']) == num_paras:
            raise UserError('Estimation requires at least one free parameter')

        # Make sure the estimation dataset exists
        if not os.path.exists(file_est):
            raise UserError('Estimation dataset does not exist')

        if maxfun > 0:
            assert optimizer_used in optimizer_options.keys()

            # Make sure the requested optimizer is valid
            if version == 'PYTHON':
                assert optimizer_used in OPT_EST_PYTH
            elif version == 'FORTRAN':
                assert optimizer_used in OPT_EST_FORT
            else:
                raise AssertionError

        return self

    def fit(self):
        """Estimate the model."""
        # Cleanup
        for fname in ['est.respy.log', 'est.respy.info']:
            if os.path.exists(fname):
                os.unlink(fname)

        if self.get_attr('is_solved'):
            self.reset()

        self.check_estimation()

        # This locks the estimation directory for additional estimation requests.
        atexit.register(remove_scratch, '.estimation.respy.scratch')
        open('.estimation.respy.scratch', 'w').close()

        # Read in estimation dataset. It only reads in the number of agents
        # requested for the estimation (or all available, depending on which is
        # less). It allows to read in only a subset of the initial conditions.
        data_frame = process_dataset(self)
        record_estimation_sample(data_frame)
        data_array = data_frame.as_matrix()

        # Distribute class attributes
        version = self.get_attr('version')

        # Select appropriate interface
        if version in ['PYTHON']:
            respy_interface(self, 'estimate', data_array)
        elif version in ['FORTRAN']:
            resfort_interface(self, 'estimate', data_array)
        else:
            raise NotImplementedError

        rslt = get_est_info()
        x, val = rslt['paras_step'], rslt['value_step']

        for fname in ['.estimation.respy.scratch', '.stop.respy.scratch']:
            remove_scratch(fname)

        # Finishing
        return x, val

    def simulate(self):
        """Simulate dataset of synthetic agents following the model."""
        # Distribute class attributes
        is_debug, version, is_store, file_sim = dist_class_attributes(
            self, 'is_debug', 'version', 'is_store', 'file_sim')

        # Cleanup
        for ext in ['sim', 'sol', 'dat', 'info']:
            fname = file_sim + '.respy.' + ext
            if os.path.exists(fname):
                os.unlink(fname)

        # Select appropriate interface
        if version in ['PYTHON']:
            solution, data_array = respy_interface(self, 'simulate')
        elif version in ['FORTRAN']:
            solution, data_array = resfort_interface(self, 'simulate')
        else:
            raise NotImplementedError

        # Attach solution to class instance
        self = add_solution(self, *solution)

        self.unlock()
        self.set_attr('is_solved', True)
        self.lock()

        # Store object to file
        if is_store:
            self.store('solution.respy.pkl')

        # Create pandas data frame with missing values.
        data_frame = pd.DataFrame(data=replace_missing_values(data_array),
                                  columns=DATA_LABELS_SIM)
        data_frame = data_frame.astype(DATA_FORMATS_SIM)
        data_frame.set_index(['Identifier', 'Period'], drop=False, inplace=True)

        # Checks
        if is_debug:
            check_dataset_sim(data_frame, self)

        write_out(self, data_frame)
        write_info(self, data_frame)

        return self, data_frame
