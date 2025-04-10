# START OF FILE src/agents/performance_tracker.py
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import copy

# Define path for storing metrics (consider making this configurable later)
METRICS_FILE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "model_performance_metrics.json"

logger = logging.getLogger(__name__)

# Define the structure for storing metrics per model
class ModelMetrics(Dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setdefault("success_count", 0)
        self.setdefault("failure_count", 0)
        self.setdefault("total_duration_ms", 0.0) # Use float for potentially fractional ms
        self.setdefault("call_count", 0)
        # Optional future additions:
        # self.setdefault("total_prompt_tokens", 0)
        # self.setdefault("total_completion_tokens", 0)
        # self.setdefault("last_success_ts", 0)
        # self.setdefault("last_failure_ts", 0)
        # self.setdefault("cumulative_score", 0.0) # For more advanced ranking

class ModelPerformanceTracker:
    """
    Tracks performance metrics (success rate, latency) for different LLM models.
    Loads and saves metrics to a JSON file. Provides basic ranking capabilities.
    """

    def __init__(self, metrics_file: Path = METRICS_FILE_PATH):
        """
        Initializes the tracker and loads existing metrics.

        Args:
            metrics_file (Path): The path to the JSON file for storing metrics.
        """
        self.metrics_file: Path = metrics_file
        # Metrics structure: { "provider": { "model_id": ModelMetrics(...) } }
        self._metrics: Dict[str, Dict[str, ModelMetrics]] = {}
        self._lock = asyncio.Lock() # Lock for safe concurrent updates
        self._load_metrics_sync() # Load metrics synchronously on init

    def _ensure_data_dir(self):
        """Ensures the directory for the metrics file exists."""
        try:
            self.metrics_file.parent.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Ensured data directory exists: {self.metrics_file.parent}")
        except Exception as e:
            logger.error(f"Error creating data directory {self.metrics_file.parent}: {e}", exc_info=True)

    def _load_metrics_sync(self):
        """Synchronously loads metrics from the JSON file."""
        self._ensure_data_dir()
        if not self.metrics_file.exists():
            logger.info(f"Metrics file not found at {self.metrics_file}. Initializing empty metrics.")
            self._metrics = {}
            return

        logger.info(f"Loading performance metrics from: {self.metrics_file}")
        try:
            with open(self.metrics_file, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)
                # Validate and convert loaded data into ModelMetrics instances
                valid_metrics = {}
                if isinstance(loaded_data, dict):
                    for provider, models in loaded_data.items():
                        if isinstance(models, dict):
                            valid_metrics[provider] = {}
                            for model_id, metrics_dict in models.items():
                                if isinstance(metrics_dict, dict):
                                    # Create ModelMetrics, ensuring all keys exist
                                    valid_metrics[provider][model_id] = ModelMetrics(metrics_dict)
                                else:
                                     logger.warning(f"Invalid metrics data for model '{model_id}' under provider '{provider}'. Skipping.")
                        else:
                             logger.warning(f"Invalid models data for provider '{provider}'. Skipping provider.")
                    self._metrics = valid_metrics
                    logger.info(f"Successfully loaded metrics for {len(self._metrics)} providers.")
                else:
                    logger.error("Invalid format in metrics file (expected dictionary). Initializing empty metrics.")
                    self._metrics = {}
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from metrics file {self.metrics_file}: {e}. Initializing empty metrics.")
            self._metrics = {}
        except Exception as e:
            logger.error(f"Error loading metrics file {self.metrics_file}: {e}. Initializing empty metrics.", exc_info=True)
            self._metrics = {}

    async def save_metrics(self):
        """Asynchronously saves the current metrics to the JSON file."""
        async with self._lock:
            logger.info(f"Saving performance metrics to: {self.metrics_file}")
            try:
                # Ensure directory exists just before saving
                await asyncio.to_thread(self._ensure_data_dir)

                # Use temp file for atomic write
                temp_fd, temp_path_str = await asyncio.to_thread(
                    lambda: tempfile.mkstemp(suffix=".tmp", prefix=self.metrics_file.name + '_', dir=self.metrics_file.parent)
                )
                temp_file_path = Path(temp_path_str)

                # Convert metrics to plain dicts for JSON serialization
                metrics_to_save = copy.deepcopy(self._metrics)

                def write_json_sync():
                    with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                        json.dump(metrics_to_save, f, indent=2)

                await asyncio.to_thread(write_json_sync)
                logger.debug(f"Successfully wrote metrics to temporary file: {temp_file_path}")

                # Atomically replace the original file
                await asyncio.to_thread(os.replace, temp_file_path, self.metrics_file)
                logger.info(f"Successfully saved metrics to {self.metrics_file}")
                temp_file_path = None # Avoid deletion in finally block
            except Exception as e:
                logger.error(f"Error saving metrics file {self.metrics_file}: {e}", exc_info=True)
            finally:
                if temp_file_path and temp_file_path.exists():
                     try: await asyncio.to_thread(os.remove, temp_file_path); logger.debug(f"Removed temporary metrics file: {temp_file_path}")
                     except Exception as rm_err: logger.error(f"Error removing temporary metrics file {temp_file_path}: {rm_err}")


    async def record_call(self, provider: str, model_id: str, duration_ms: float, success: bool):
        """
        Records the outcome of a single LLM call, updating metrics.

        Args:
            provider (str): The provider name.
            model_id (str): The specific model ID used.
            duration_ms (float): The duration of the LLM call in milliseconds.
            success (bool): True if the call completed without provider/stream errors, False otherwise.
        """
        if not provider or not model_id:
             logger.warning("Attempted to record call with missing provider or model_id.")
             return

        async with self._lock:
            logger.debug(f"Recording call: Provider='{provider}', Model='{model_id}', Success={success}, Duration={duration_ms:.2f}ms")
            if provider not in self._metrics:
                self._metrics[provider] = {}
            if model_id not in self._metrics[provider]:
                self._metrics[provider][model_id] = ModelMetrics() # Initialize if new

            # Get the specific model's metrics object
            model_stats = self._metrics[provider][model_id]

            # Update counts
            model_stats["call_count"] += 1
            if success:
                model_stats["success_count"] += 1
                model_stats["total_duration_ms"] += duration_ms
                # model_stats["last_success_ts"] = int(time.time())
            else:
                model_stats["failure_count"] += 1
                # model_stats["last_failure_ts"] = int(time.time())

            logger.debug(f"Updated metrics for {provider}/{model_id}: {model_stats}")

            # --- Optional: Trigger periodic save ---
            # if model_stats["call_count"] % 10 == 0: # Save every 10 calls for this model, for example
            #     asyncio.create_task(self.save_metrics())

    def get_metrics(self, provider: Optional[str] = None, model_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieves metrics, optionally filtered by provider and/or model.
        Returns a deep copy.
        """
        # Return a copy to prevent external modification
        metrics_copy = copy.deepcopy(self._metrics)

        if provider and model_id:
            return metrics_copy.get(provider, {}).get(model_id, ModelMetrics()) # Return empty metrics if not found
        elif provider:
            return metrics_copy.get(provider, {})
        else:
            return metrics_copy

    def _calculate_score(self, stats: ModelMetrics, min_calls_threshold: int = 5) -> float:
        """
        Calculates a simple performance score for ranking.
        Prioritizes success rate, penalizes high latency.
        Returns a lower score if below the call threshold.
        Higher score is better.
        """
        call_count = stats["call_count"]
        if call_count == 0:
            return -1.0 # No data, lowest score

        success_count = stats["success_count"]
        success_rate = success_count / call_count if call_count > 0 else 0.0

        # Calculate average duration for successful calls only
        avg_duration_ms = stats["total_duration_ms"] / success_count if success_count > 0 else float('inf')

        # Simple scoring: High success rate is primary, low latency is secondary.
        # Normalize latency penalty - e.g., penalize more for > 10s avg duration.
        # Adjust scaling factors as needed.
        latency_penalty = 0.0
        if avg_duration_ms > 10000: # Penalize significantly over 10 seconds
             latency_penalty = min(0.3, (avg_duration_ms / 100000.0)) # Max penalty 0.3
        elif avg_duration_ms > 3000: # Smaller penalty over 3 seconds
             latency_penalty = min(0.1, (avg_duration_ms / 50000.0)) # Max penalty 0.1

        score = (success_rate * 0.8) + ((1.0 - latency_penalty) * 0.2)

        # Penalize models with very few calls to avoid ranking sparse data too high
        if call_count < min_calls_threshold:
            score *= (call_count / min_calls_threshold) # Scale down score linearly

        return round(score, 4)


    def get_ranked_models(self, provider: Optional[str] = None, min_calls: int = 3) -> List[Tuple[str, str, float, Dict]]:
        """
        Returns a list of models ranked by performance score (higher is better).
        Can be filtered by provider.

        Args:
            provider (Optional[str]): If specified, rank only models for this provider.
            min_calls (int): Minimum number of calls required for a model to be fully ranked.

        Returns:
            List[Tuple[str, str, float, Dict]]: List of (provider, model_id, score, metrics_dict) sorted by score descending.
        """
        ranked_list = []
        metrics_to_rank = self.get_metrics(provider=provider) # Get relevant part of metrics

        if provider and isinstance(metrics_to_rank, dict): # Ranking within a specific provider
             prov_name = provider
             for model_id, stats_dict in metrics_to_rank.items():
                 stats = ModelMetrics(stats_dict) # Ensure it's ModelMetrics obj
                 score = self._calculate_score(stats, min_calls_threshold=min_calls)
                 ranked_list.append((prov_name, model_id, score, stats))
        elif not provider and isinstance(metrics_to_rank, dict): # Ranking across all providers
            for prov_name, models_dict in metrics_to_rank.items():
                 if isinstance(models_dict, dict):
                    for model_id, stats_dict in models_dict.items():
                         stats = ModelMetrics(stats_dict)
                         score = self._calculate_score(stats, min_calls_threshold=min_calls)
                         ranked_list.append((prov_name, model_id, score, stats))

        # Sort by score descending (higher score is better)
        ranked_list.sort(key=lambda item: item[2], reverse=True)

        return ranked_list

# --- Helper for atomic writes (needed for save_metrics) ---
import os
import tempfile
