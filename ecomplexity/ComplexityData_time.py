# Complexity calculations
import numpy as np
import pandas as pd
import warnings
import sys
from functools import wraps
import time
import datetime

# # Get user input
# cols_input = {'time':'year','loc':'origin','prod':'hs92','val':'export_val'}
# val_errors_flag_input = 'coerce' # Options: 'coerce','raise','ignore'
# rca_mcp_threshold_input = 1

class ComplexityData(object):
    """Calculate complexity and other related results

    Args:
        data: pandas dataframe containing production / trade data.
            Including variables indicating time, location, product and value
        cols_input: dict of column names for time, location, product and value.
            Example: {'time':'year', 'loc':'origin', 'prod':'hs92', 'val':'export_val'}
        presence_test: str for test used for presence of industry in location.
            One of "rca" (default), "rpop", "both", or "manual".
            Determines which values are used for M_cp calculations.
            If "manual", M_cp is taken as given from the "value" column in data
        val_errors_flag: {'coerce','ignore','raise'}. Passed to pd.to_numeric
            *default* coerce.
        rca_mcp_threshold: numeric indicating RCA threshold beyond which mcp is 1.
            *default* 1.
        rca_mcp_threshold: numeric indicating RPOP threshold beyond which mcp is 1.
            *default* 1. Only used if presence_test is not "rca".
        pop: pandas df, with time, location and corresponding population, in that order.
            Not required if presence_test is "rca" (default).

    Attributes:
        diversity: k_c,0
        ubiquity: k_p,0
        rca: Balassa's RCA
        rpop: (available if presence_test!="rca") RPOP
        mcp: MCP used for complexity calculations
        eci: Economic complexity index
        pci: Product complexity index
    """

    def __init__(self, data, cols_input, presence_test="rca", val_errors_flag='coerce',
                 rca_mcp_threshold=1, rpop_mcp_threshold=1, pop=None):
        self.data = data.copy()
        self.rename_cols(cols_input)
        self.clean_data(val_errors_flag)

        self.output_list = []

        for t in self.data.index.unique("time"):
            print(t)
            self.create_full_df_time(t)

            if presence_test != "manual":
                self.calculate_rca_time()
                self.calculate_mcp_time(rca_mcp_threshold, rpop_mcp_threshold,
                                        presence_test, pop, t)
            else:
                self.calculate_manual_mcp_time()

            self.diversity_t = np.nansum(self.mcp_t, axis=1)
            self.ubiquity_t = np.nansum(self.mcp_t, axis=0)

            Mcc, Mpp = self.calculate_Mcc_Mpp_time()
            # print(Mcc.shape, Mpp.shape)
            kp = self.calculate_Kvec_time(Mpp)
            kc = self.calculate_Kvec_time(Mcc)
            # self.calculate_kp_kc()
            # print(kc.shape, self.diversity_t.shape)
            s1 = self.sign_time(self.diversity_t, kc)
            s2 = self.sign_time(kp, self.ubiquity_t)
            self.eci_t = self.normalize_time(s1 * kc)
            self.pci_t = self.normalize_time(s1 * kp)
            self.reshape_output_to_data_time(t)

        self.output = pd.concat(self.output_list)
        self.conform_to_original_data(cols_input, data)

    def rename_cols(self, cols_input):
        # Rename cols
        cols_map_inv = {v: k for k, v in cols_input.items()}
        self.data = self.data.rename(columns=cols_map_inv)
        self.data = self.data[['time', 'loc', 'prod', 'val']]

    def clean_data(self, val_errors_flag_input):
        # Make sure values are numeric
        self.data.val = pd.to_numeric(
            self.data.val, errors=val_errors_flag_input)
        self.data.set_index(['time', 'loc', 'prod'], inplace=True)
        if self.data.val.isnull().values.any():
            warnings.warn('NaN value(s) present, coercing to zero(es)')
            self.data.val.fillna(0, inplace=True)
        dups = self.data.index.duplicated()
        if dups.sum() > 0:
            warnings.warn(
                'Duplicate values exist, keeping the first occurrence')
            self.data = self.data[~self.data.index.duplicated()]

    def create_full_df_time(self, t):
        # Create pandas dataframe with all possible combinations of values
        # but remove rows with diversity or ubiquity zero
        self.data_t = self.data.loc[t].copy()
        diversity_check = self.data_t.reset_index().groupby(
            ['loc'])['val'].sum().reset_index()
        ubiquity_check = self.data_t.reset_index().groupby(
            ['prod'])['val'].sum().reset_index()
        diversity_check = diversity_check[diversity_check.val != 0]
        ubiquity_check = ubiquity_check[ubiquity_check.val != 0]
        self.data_t = self.data_t.reset_index()
        self.data_t = self.data_t.merge(
            diversity_check[['loc']], on='loc', how='right')
        self.data_t = self.data_t.merge(
            ubiquity_check[['prod']], on='prod', how='right')
        self.data_t.set_index(['loc','prod'], inplace=True)
        data_index = pd.MultiIndex.from_product(
            self.data_t.index.levels, names=self.data_t.index.names)
        self.data_t = self.data_t.reindex(data_index, fill_value=0)

    def calculate_rca_time(self):
        # Convert data into numpy array
        loc_n_vals = len(self.data_t.index.levels[0])
        prod_n_vals = len(self.data_t.index.levels[1])
        data_np = self.data_t.values.reshape((loc_n_vals, prod_n_vals))

        # Calculate RCA, disable dividebyzero errors
        with np.errstate(divide='ignore', invalid='ignore'):
            num = (data_np / np.nansum(data_np, axis=1)[:, np.newaxis])
            loc_total = np.nansum(data_np, axis=0)[np.newaxis, :]
            world_total = np.nansum(loc_total, axis=1)[:, np.newaxis]
            den = loc_total / world_total
            self.rca_t = num / den

    def calculate_rpop_time(self, pop, t):
        # After constructing df with all combinations, convert data into ndarray
        loc_n_vals = len(self.data_t.index.levels[0])
        prod_n_vals = len(self.data_t.index.levels[1])
        data_np = self.data_t.values.reshape(
            (loc_n_vals, prod_n_vals))

        pop_t = pop.copy()
        pop_t.columns = ['time', 'loc', 'pop']
        pop_t = pop_t[pop_t.time == t]
        pop_t = pop_t.drop(columns="time")
        pop_t = pop_t.reset_index(drop=True).set_index('loc')
        pop_index = self.data_t.index.unique('loc')
        pop_t = pop_t.reindex(pop_index)
        pop_t = pop_t.values
        # print(pop_t.shape, data_np.shape)

        num = data_np / pop_t
        # print("Num done. Num shape {}".format(num.shape))
        loc_total = np.nansum(data_np, axis=0)[np.newaxis, :]
        world_pop_total = np.nansum(pop_t)

        den = loc_total / world_pop_total
        # print("Den done. Den shape {}".format(den.shape))
        rpop = num / den
        self.rpop_t = rpop

    def calculate_mcp_time(self, rca_mcp_threshold_input, rpop_mcp_threshold_input,
                           presence_test, pop, t):
        def convert_to_binary(x, threshold):
            x = np.nan_to_num(x)
            x = np.where(x >= threshold, 1, 0)
            return(x)

        if presence_test == "rca":
            self.mcp_t = convert_to_binary(self.rca_t, rca_mcp_threshold_input)

        elif presence_test == "rpop":
            self.calculate_rpop_time(pop, t)
            self.mcp_t = convert_to_binary(
                self.rca_t, rpop_mcp_threshold_input)

        elif presence_test == "both":
            self.calculate_rpop_time(pop, t)
            self.mcp_t = convert_to_binary(
                self.rca_t, rca_mcp_threshold_input) + convert_to_binary(self.rca_t, rpop_mcp_threshold_input)

    def calculate_manual_mcp_time(self):
        # Test to see if indeed MCP
        if np.any(~np.isin(self.data_t.values, [0, 1])):
            error_val = self.data_t.values[~np.isin(
                self.data_t.values, [0, 1])].flat[0]
            raise ValueError(
                "Manually supplied MCP column contains values other than 0 or 1 - Val: {}".format(error_val))

        # Convert data into numpy array
        loc_n_vals = len(self.data_t.index.levels[0])
        prod_n_vals = len(self.data_t.index.levels[1])
        data_np = self.data_t.values.reshape(
            (loc_n_vals, prod_n_vals))

        self.mcp_t = data_np

    def calculate_Mcc_Mpp_time(self):

        mcp1 = (self.mcp_t / self.diversity_t[:, np.newaxis])
        mcp2 = (self.mcp_t / self.ubiquity_t[np.newaxis, :])
        # These matrix multiplication lines are very slow
        Mcc = mcp1 @ mcp2.T
        Mpp = mcp2.T @ mcp1
        return(Mcc, Mpp)

    def calculate_Mcc_Mpp_time_2(self):
        # Get number of countries and products
        ncx, npx = self.mcp_t.shape

        # Calculate diversity and ubiquity matrices
        kc0 = self.mcp_t @ np.full((npx, npx), 1)
        kp0 = np.full((ncx, ncx), 1) @ self.mcp_t

        # Calculate the tilde Ms
        mpp = ((self.mcp_t / kc0) / kp0).T @ self.mcp_t
        mcc = self.mcp_t.T @ ((self.mcp_t / kp0) / kc0)

        return(mcc, mpp)

    def calculate_kp_kc(self):
        # Get number of countries and products
        ncx, npx = self.mcp_t.shape

        # Calculate diversity and ubiquity matrices
        kc0 = self.mcp_t @ np.full((npx, npx), 1)
        kp0 = np.full((ncx, ncx), 1) @ self.mcp_t

        # Calculate the tilde Ms
        mpp = ((self.mcp_t / kc0) / kp0).T @ self.mcp_t
        kp = self.calculate_Kvec_time(mpp)
        kc = (self.mcp_t/kc0) @ kp
        kc = kc[:,np.newaxis]

        kc01d = self.mcp_t @ np.full((npx,1),1)
        s = self.sign_time(kc01d, kc)
        kc = s * kc
        kp = s * kp
        self.eci_t = kc @ np.full((1,npx), 1)
        print(self.eci_t.shape)
        self.pci_t = np.full((ncx, 1), 1) @ kp.T

    def reshape_output_to_data_time(self, t):

        diversity = self.diversity_t[:, np.newaxis].repeat(
            self.mcp_t.shape[1], axis=1).ravel()
        ubiquity = self.ubiquity_t[np.newaxis, :].repeat(
            self.mcp_t.shape[0], axis=0).ravel()
        eci = self.eci_t[:, np.newaxis].repeat(
            self.mcp_t.shape[1], axis=1).ravel()
        pci = self.pci_t[np.newaxis, :].repeat(
            self.mcp_t.shape[0], axis=0).ravel()

        # dict_op = {'diversity': diversity,
        #                                  'ubiquity': ubiquity,
        #                                  'rca': self.rca_t.ravel(),
        #                                  'rpop': self.rpop_t.ravel(),
        #                                  'mcp': self.mcp_t.ravel(),
        #                                  'eci': eci,
        #                                  'pci': pci}
        # print({k:len(v) for k,v in dict_op.items()})

        if hasattr(self, 'rpop_t'):
            output = pd.DataFrame.from_dict({'diversity': diversity,
                                             'ubiquity': ubiquity,
                                             'rca': self.rca_t.ravel(),
                                             'rpop': self.rpop_t.ravel(),
                                             'mcp': self.mcp_t.ravel(),
                                             'eci': eci,
                                             'pci': pci}).reset_index(drop=True)

        elif hasattr(self, 'rca_t'):
            output = pd.DataFrame.from_dict({'diversity': diversity,
                                             'ubiquity': ubiquity,
                                             'rca': self.rca_t.ravel(),
                                             'mcp': self.mcp_t.ravel(),
                                             'eci': eci,
                                             'pci': pci}).reset_index(drop=True)

        else:
            output = pd.DataFrame.from_dict({'diversity': diversity,
                                             'ubiquity': ubiquity,
                                             'mcp': self.mcp_t.ravel(),
                                             'eci': eci,
                                             'pci': pci}).reset_index(drop=True)

        self.data_t['time'] = t
        self.output_t = pd.concat([self.data_t.reset_index(), output], axis=1)
        self.output_list.append(self.output_t)

    def conform_to_original_data(self, cols_input, data):
        # Reset column names and add dropped columns back
        self.output = self.output.rename(columns=cols_input)
        self.output = self.output.merge(
            data, how="outer", on=list(cols_input.values()))

    @staticmethod
    def calculate_Kvec_time(m_tilde):
        eigvals, eigvecs = np.linalg.eig(m_tilde)
        eigvecs = np.real(eigvecs)
        # Get eigenvector corresponding to second largest eigenvalue
        eig_index = eigvals.argsort()[-2]
        Kvec_time = eigvecs[:, eig_index]
        return(Kvec_time)

    @staticmethod
    def sign_time(k, kx_0):
        # return(np.sign(np.corrcoef(kc0, eci_vec)[0][1]))
        return(2 * int(np.corrcoef(k, kx_0)[0, 1] > 0) - 1)

    @staticmethod
    def normalize_time(v):
        return((v - v.mean()) / v.std())


def ecomplexity(data, cols_input, presence_test="rca", val_errors_flag='coerce',
                rca_mcp_threshold=1, rpop_mcp_threshold=1, pop=None):
    """Wrapper for complexity calculations through the ComplexityData class

    Args:
        data: pandas dataframe containing production / trade data.
            Including variables indicating time, location, product and value
        cols_input: dict of column names for time, location, product and value.
            Example: {'time':'year', 'loc':'origin', 'prod':'hs92', 'val':'export_val'}
        presence_test: str for test used for presence of industry in location.
            One of "rca" (default), "rpop", "both", or "manual".
            Determines which values are used for M_cp calculations.
            If "manual", M_cp is taken as given from the "value" column in data
        val_errors_flag: {'coerce','ignore','raise'}. Passed to pd.to_numeric
            *default* coerce.
        rca_mcp_threshold: numeric indicating RCA threshold beyond which mcp is 1.
            *default* 1.
        rca_mcp_threshold: numeric indicating RPOP threshold beyond which mcp is 1.
            *default* 1. Only used if presence_test is not "rca".
        pop: pandas df, with time, location and corresponding population, in that order.
            Not required if presence_test is "rca" (default).

    Returns:
        Pandas dataframe containing the data with the following additional columns:
            - diversity: k_c,0
            - ubiquity: k_p,0
            - rca: Balassa's RCA
            - rpop: (available if presence_test!="rca") RPOP
            - mcp: MCP used for complexity calculations
            - eci: Economic complexity index
            - pci: Product complexity index

    """
    cdata = ComplexityData(data, cols_input, presence_test,
                           val_errors_flag, rca_mcp_threshold, rpop_mcp_threshold, pop)
    return(cdata.output)