import unittest
import os
import json
from pathlib import Path
from agent.orchestration.registry import WorkerRegistry

class TestWorkerRegistry(unittest.TestCase):
    def setUp(self):
        # Use a temporary DB for testing
        self.db_path = Path("test_lancedb")
        if self.db_path.exists():
            import shutil
            shutil.rmtree(self.db_path)
        self.registry = WorkerRegistry(db_path=self.db_path)

    def tearDown(self):
        if self.db_path.exists():
            import shutil
            shutil.rmtree(self.db_path)

    def test_registration_and_ranking(self):
        # Register two workers
        self.registry.register_worker("w1", "Worker 1", ["coding", "testing"])
        self.registry.register_worker("w2", "Worker 2", ["coding", "design"])
        
        workers = self.registry.list_workers()
        self.assertEqual(len(workers), 2)
        
        # Update metrics: w1 is better than w2
        self.registry.update_metrics("w1", success=True, latency=1.0)
        self.registry.update_metrics("w2", success=False, latency=5.0)
        
        # Get best worker for 'coding'
        best = self.registry.get_best_worker("coding")
        self.assertEqual(best["worker_id"], "w1")
        self.assertGreater(best["reliability"], 0.5)

    def test_specialty_filter(self):
        self.registry.register_worker("w1", "Worker 1", ["testing"])
        self.registry.register_worker("w2", "Worker 2", ["design"])
        
        best = self.registry.get_best_worker("design")
        self.assertEqual(best["worker_id"], "w2")

if __name__ == "__main__":
    unittest.main()
