import unittest
import logging
from src.agents.agent_utils import sort_models_by_size_performance_id, DEFAULT_PERFORMANCE_SCORE, DEFAULT_NUM_PARAMETERS

# Disable most logging output for tests unless specifically testing logging
logging.disable(logging.CRITICAL)

class TestSortModelsBySizePerformanceId(unittest.TestCase):

    def test_sort_by_different_sizes(self):
        models = [
            {"id": "model_small", "provider": "p1", "num_parameters": 1_000_000_000},
            {"id": "model_large", "provider": "p1", "num_parameters": 7_000_000_000},
            {"id": "model_medium", "provider": "p1", "num_parameters": 3_000_000_000},
        ]
        # Performance metrics are not provided, so default score will be used for all.
        # Sorting should be by size descending.
        sorted_models = sort_models_by_size_performance_id(models, None)
        self.assertEqual([m["id"] for m in sorted_models], ["model_large", "model_medium", "model_small"])

    def test_sort_same_size_different_performance(self):
        models = [
            {"id": "model_A_low_perf", "provider": "p1", "num_parameters": 7_000_000_000},
            {"id": "model_B_high_perf", "provider": "p1", "num_parameters": 7_000_000_000},
        ]
        performance = {
            "p1": {
                "model_A_low_perf": {"score": 0.7},
                "model_B_high_perf": {"score": 0.9},
            }
        }
        # Size is same, performance B > A
        sorted_models = sort_models_by_size_performance_id(models, performance)
        self.assertEqual([m["id"] for m in sorted_models], ["model_B_high_perf", "model_A_low_perf"])

    def test_sort_same_size_same_performance_different_ids(self):
        models = [
            {"id": "model_Z", "provider": "p1", "num_parameters": 7_000_000_000},
            {"id": "model_A", "provider": "p1", "num_parameters": 7_000_000_000},
            {"id": "model_M", "provider": "p1", "num_parameters": 7_000_000_000},
        ]
        performance = {
            "p1": { # All have same effective score
                "model_Z": {"score": 0.8},
                "model_A": {"score": 0.8},
                "model_M": {"score": 0.8},
            }
        }
        # Size same, performance same, sort by ID ascending
        sorted_models = sort_models_by_size_performance_id(models, performance)
        self.assertEqual([m["id"] for m in sorted_models], ["model_A", "model_M", "model_Z"])

    def test_sort_with_num_parameters_none(self):
        models = [
            {"id": "model_sized", "provider": "p1", "num_parameters": 7_000_000_000},
            {"id": "model_none_params_A", "provider": "p1", "num_parameters": None},
            {"id": "model_zero_params", "provider": "p1", "num_parameters": 0},
            {"id": "model_none_params_Z", "provider": "p1"}, # num_parameters key missing
        ]
        # Models with None or 0 parameters should be treated as smallest and sorted after sized models.
        # Then by ID for those with effectively 0 params.
        sorted_models = sort_models_by_size_performance_id(models, None)
        # Expected: model_sized (7B), then the rest by ID as they all have DEFAULT_NUM_PARAMETERS (0)
        # model_none_params_A, model_none_params_Z, model_zero_params
        self.assertEqual(sorted_models[0]["id"], "model_sized")
        
        # Check the order of the 'smallest' models (those with None/0 parameters)
        # Their num_parameters_sortable should be DEFAULT_NUM_PARAMETERS (0)
        # Their performance_score should be DEFAULT_PERFORMANCE_SCORE (0.0)
        # So they should be sorted by ID alphabetically.
        smallest_models_ids = [m["id"] for m in sorted_models[1:]]
        self.assertCountEqual(smallest_models_ids, ["model_none_params_A", "model_none_params_Z", "model_zero_params"])
        self.assertEqual(sorted(smallest_models_ids), smallest_models_ids, "Smallest models not sorted by ID")


    def test_sort_mixed_scenario(self):
        models = [
            {"id": "m1_large_high", "provider": "p1", "num_parameters": 13_000_000_000}, # Perf A
            {"id": "m2_large_low",  "provider": "p1", "num_parameters": 13_000_000_000}, # Perf B
            {"id": "m3_medium_high","provider": "p1", "num_parameters": 7_000_000_000}, # Perf A
            {"id": "m4_medium_low", "provider": "p1", "num_parameters": 7_000_000_000}, # Perf B
            {"id": "m5_medium_z_id","provider": "p1", "num_parameters": 7_000_000_000}, # Perf B, ID Z
            {"id": "m6_small",      "provider": "p1", "num_parameters": 3_000_000_000}, # Perf A
            {"id": "m7_no_params",  "provider": "p1"},                                  # Perf A
        ]
        performance = {
            "p1": {
                "m1_large_high": {"score": 0.9}, "m2_large_low":  {"score": 0.7},
                "m3_medium_high":{"score": 0.9}, "m4_medium_low": {"score": 0.7},
                "m5_medium_z_id":{"score": 0.7}, # Same as m4_medium_low
                "m6_small":      {"score": 0.9},
                "m7_no_params":  {"score": 0.9}, 
            }
        }
        sorted_models = sort_models_by_size_performance_id(models, performance)
        expected_order = [
            "m1_large_high",  # 13B, 0.9
            "m2_large_low",   # 13B, 0.7
            "m3_medium_high", # 7B, 0.9
            "m4_medium_low",  # 7B, 0.7 (ID tie-break with m5)
            "m5_medium_z_id", # 7B, 0.7
            "m6_small",       # 3B, 0.9
            "m7_no_params",   # 0B (default), 0.9
        ]
        self.assertEqual([m["id"] for m in sorted_models], expected_order)

    def test_sort_models_missing_performance_data(self):
        models = [
            {"id": "model_A_has_perf", "provider": "p1", "num_parameters": 7_000_000_000},
            {"id": "model_B_no_perf", "provider": "p1", "num_parameters": 7_000_000_000}, # Same size
            {"id": "model_C_larger_no_perf", "provider": "p1", "num_parameters": 10_000_000_000},
        ]
        performance = {
            "p1": {
                "model_A_has_perf": {"score": 0.9},
                # model_B_no_perf is missing, will get DEFAULT_PERFORMANCE_SCORE
            }
            # model_C_larger_no_perf also missing, will get DEFAULT_PERFORMANCE_SCORE
        }
        sorted_models = sort_models_by_size_performance_id(models, performance)
        # Expected:
        # 1. model_C_larger_no_perf (10B, score 0.0)
        # 2. model_A_has_perf (7B, score 0.9)
        # 3. model_B_no_perf (7B, score 0.0)
        self.assertEqual([m["id"] for m in sorted_models], ["model_C_larger_no_perf", "model_A_has_perf", "model_B_no_perf"])

    def test_sort_models_all_params_none_or_zero(self):
        models = [
            {"id": "model_B_none", "provider": "p1", "num_parameters": None},
            {"id": "model_A_zero", "provider": "p1", "num_parameters": 0},
            {"id": "model_C_missing", "provider": "p1"},
        ]
        performance = { # Scores to differentiate them after size (all effectively 0)
            "p1": {
                "model_B_none": {"score": 0.8},
                "model_A_zero": {"score": 0.9}, # Highest score
                "model_C_missing": {"score": 0.7},
            }
        }
        # All have num_parameters_sortable = DEFAULT_NUM_PARAMETERS (0)
        # So, sort by performance (desc), then ID (asc)
        sorted_models = sort_models_by_size_performance_id(models, performance)
        # Expected: A (0B, 0.9), B (0B, 0.8), C (0B, 0.7)
        self.assertEqual([m["id"] for m in sorted_models], ["model_A_zero", "model_B_none", "model_C_missing"])


if __name__ == '__main__':
    unittest.main()
