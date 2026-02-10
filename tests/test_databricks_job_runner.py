"""Unit tests for the Databricks executor job runner."""

import json
import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# Add the executor src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "databricks", "executor", "src"))

from job_runner import run, _resolve_host, _resolve_token, _resolve_max_results


class TestResolvers(unittest.TestCase):
    def test_resolve_host_from_payload(self):
        self.assertEqual(
            _resolve_host({"host": "https://adb-1234.azuredatabricks.net"}),
            "https://adb-1234.azuredatabricks.net",
        )

    def test_resolve_host_adds_https(self):
        self.assertEqual(
            _resolve_host({"host": "adb-1234.azuredatabricks.net"}),
            "https://adb-1234.azuredatabricks.net",
        )

    def test_resolve_host_from_env(self):
        with patch.dict(os.environ, {"DATABRICKS_HOST": "https://db.example.com"}):
            self.assertEqual(_resolve_host({}), "https://db.example.com")

    def test_resolve_host_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DATABRICKS_HOST", None)
            os.environ.pop("DATABRICKS_WORKSPACE_URL", None)
            self.assertEqual(_resolve_host({}), "")

    def test_resolve_token_from_payload(self):
        self.assertEqual(_resolve_token({"token": "dapi123"}), "dapi123")

    def test_resolve_max_results_default(self):
        self.assertEqual(_resolve_max_results({}, default=50), 50)

    def test_resolve_max_results_from_payload(self):
        self.assertEqual(_resolve_max_results({"limit": "25"}), 25)


class TestRunJob(unittest.TestCase):
    def test_missing_action(self):
        result = run({})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "missing_action")

    def test_destructive_action_blocked(self):
        result = run({"action": "databricks.workspace.delete_cluster"})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "destructive_action_not_supported")

    def test_drop_action_blocked(self):
        result = run({"action": "databricks.unity_catalog.drop_schema"})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "destructive_action_not_supported")

    def test_unsupported_action(self):
        with patch.dict(os.environ, {
            "DATABRICKS_HOST": "https://db.example.com",
            "DATABRICKS_TOKEN": "dapi_test",
        }):
            result = run({"action": "databricks.unknown.action"})
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "unsupported_action")

    def test_missing_host(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DATABRICKS_HOST", None)
            os.environ.pop("DATABRICKS_WORKSPACE_URL", None)
            os.environ.pop("DATABRICKS_TOKEN", None)
            result = run({"action": "databricks.workspace.list_clusters"})
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "host_missing")

    @patch("job_runner._databricks_get")
    def test_list_clusters_success(self, mock_get):
        mock_get.return_value = {
            "clusters": [
                {"cluster_id": "c1", "cluster_name": "test", "state": "RUNNING"},
            ]
        }
        with patch.dict(os.environ, {
            "DATABRICKS_HOST": "https://db.example.com",
            "DATABRICKS_TOKEN": "dapi_test",
        }):
            result = run({"action": "databricks.workspace.list_clusters"})
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["result"]["count"], 1)

    @patch("job_runner._databricks_get")
    def test_list_jobs_success(self, mock_get):
        mock_get.return_value = {
            "jobs": [
                {"job_id": 1, "settings": {"name": "etl-daily"}},
                {"job_id": 2, "settings": {"name": "ml-train"}},
            ],
            "has_more": False,
        }
        with patch.dict(os.environ, {
            "DATABRICKS_HOST": "https://db.example.com",
            "DATABRICKS_TOKEN": "dapi_test",
        }):
            result = run({"action": "databricks.workspace.list_jobs"})
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["result"]["count"], 2)

    @patch("job_runner._databricks_get")
    def test_list_notebooks_success(self, mock_get):
        mock_get.return_value = {
            "objects": [
                {"path": "/Users/me/notebook1", "object_type": "NOTEBOOK"},
            ]
        }
        with patch.dict(os.environ, {
            "DATABRICKS_HOST": "https://db.example.com",
            "DATABRICKS_TOKEN": "dapi_test",
        }):
            result = run({
                "action": "databricks.workspace.list_notebooks",
                "payload": {"path": "/Users/me"},
            })
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["result"]["count"], 1)

    @patch("job_runner._databricks_get")
    def test_list_sql_warehouses_success(self, mock_get):
        mock_get.return_value = {
            "warehouses": [
                {"id": "w1", "name": "starter", "state": "RUNNING"},
            ]
        }
        with patch.dict(os.environ, {
            "DATABRICKS_HOST": "https://db.example.com",
            "DATABRICKS_TOKEN": "dapi_test",
        }):
            result = run({"action": "databricks.sql.list_warehouses"})
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["result"]["count"], 1)

    @patch("job_runner._databricks_get")
    def test_list_catalogs_success(self, mock_get):
        mock_get.return_value = {
            "catalogs": [
                {"name": "main", "owner": "admin"},
            ]
        }
        with patch.dict(os.environ, {
            "DATABRICKS_HOST": "https://db.example.com",
            "DATABRICKS_TOKEN": "dapi_test",
        }):
            result = run({"action": "databricks.unity_catalog.list_catalogs"})
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["result"]["count"], 1)

    @patch("job_runner._databricks_get")
    def test_list_schemas_requires_catalog(self, mock_get):
        with patch.dict(os.environ, {
            "DATABRICKS_HOST": "https://db.example.com",
            "DATABRICKS_TOKEN": "dapi_test",
        }):
            result = run({"action": "databricks.unity_catalog.list_schemas"})
            self.assertEqual(result["status"], "failed")
            self.assertIn("catalog_name_required", str(result.get("reason", "")))


if __name__ == "__main__":
    unittest.main()
