"""Unit tests for the Snowflake executor job runner."""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# Add the executor src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "snowflake", "executor", "src"))

from job_runner import run, _resolve_account, _resolve_database, _resolve_schema


class TestResolvers(unittest.TestCase):
    def test_resolve_account_from_payload(self):
        self.assertEqual(_resolve_account({"account": "org-acct"}), "org-acct")

    def test_resolve_account_from_env(self):
        with patch.dict(os.environ, {"SNOWFLAKE_ACCOUNT": "env-acct"}):
            self.assertEqual(_resolve_account({}), "env-acct")

    def test_resolve_database_from_payload(self):
        self.assertEqual(_resolve_database({"database": "MYDB"}), "MYDB")

    def test_resolve_database_empty(self):
        self.assertEqual(_resolve_database({}), "")

    def test_resolve_schema_from_payload(self):
        self.assertEqual(_resolve_schema({"schema": "PUBLIC"}), "PUBLIC")


class TestRunJob(unittest.TestCase):
    def test_missing_action(self):
        result = run({})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "missing_action")

    def test_destructive_action_blocked(self):
        result = run({"action": "snowflake.database.drop_table"})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "destructive_action_not_supported")

    def test_delete_action_blocked(self):
        result = run({"action": "snowflake.account.delete_warehouse"})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "destructive_action_not_supported")

    def test_unsupported_action(self):
        with patch.dict(os.environ, {
            "SNOWFLAKE_ACCOUNT": "test-acct",
            "SNOWFLAKE_USER": "user",
            "SNOWFLAKE_PASSWORD": "pass",
        }):
            with patch("job_runner._get_connection") as mock_conn:
                mock_conn.return_value = MagicMock()
                result = run({"action": "snowflake.unknown.action"})
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["reason"], "unsupported_action")

    @patch("job_runner._get_connection")
    def test_list_databases_success(self, mock_conn):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"NAME": "DB1", "OWNER": "SYSADMIN", "ORIGIN": "", "CREATED_ON": "2024-01-01", "RETENTION_TIME": "1"},
            {"NAME": "DB2", "OWNER": "SYSADMIN", "ORIGIN": "", "CREATED_ON": "2024-02-01", "RETENTION_TIME": "1"},
        ]
        mock_connection = MagicMock()
        mock_connection.cursor.return_value = mock_cursor
        mock_conn.return_value = mock_connection

        with patch.dict(os.environ, {
            "SNOWFLAKE_ACCOUNT": "test-acct",
            "SNOWFLAKE_USER": "user",
            "SNOWFLAKE_PASSWORD": "pass",
        }):
            result = run({"action": "snowflake.account.list_databases"})
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["result"]["count"], 2)

    @patch("job_runner._get_connection")
    def test_list_warehouses_success(self, mock_conn):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"NAME": "COMPUTE_WH", "STATE": "STARTED", "TYPE": "STANDARD", "SIZE": "X-Small",
             "OWNER": "SYSADMIN", "AUTO_SUSPEND": "600", "AUTO_RESUME": "true", "CREATED_ON": "2024-01-01"},
        ]
        mock_connection = MagicMock()
        mock_connection.cursor.return_value = mock_cursor
        mock_conn.return_value = mock_connection

        with patch.dict(os.environ, {
            "SNOWFLAKE_ACCOUNT": "test-acct",
            "SNOWFLAKE_USER": "user",
            "SNOWFLAKE_PASSWORD": "pass",
        }):
            result = run({"action": "snowflake.account.list_warehouses"})
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["result"]["count"], 1)

    @patch("job_runner._get_connection")
    def test_list_schemas_requires_database(self, mock_conn):
        mock_connection = MagicMock()
        mock_conn.return_value = mock_connection

        with patch.dict(os.environ, {
            "SNOWFLAKE_ACCOUNT": "test-acct",
            "SNOWFLAKE_USER": "user",
            "SNOWFLAKE_PASSWORD": "pass",
        }):
            result = run({"action": "snowflake.database.list_schemas"})
            self.assertEqual(result["status"], "failed")
            self.assertIn("database_required", str(result.get("reason", "")))

    @patch("job_runner._get_connection")
    def test_list_tables_requires_schema(self, mock_conn):
        mock_connection = MagicMock()
        mock_conn.return_value = mock_connection

        with patch.dict(os.environ, {
            "SNOWFLAKE_ACCOUNT": "test-acct",
            "SNOWFLAKE_USER": "user",
            "SNOWFLAKE_PASSWORD": "pass",
        }):
            result = run({
                "action": "snowflake.schema.list_tables",
                "payload": {"database": "MYDB"},
            })
            self.assertEqual(result["status"], "failed")
            self.assertIn("schema_required", str(result.get("reason", "")))

    @patch("job_runner._get_connection")
    def test_list_roles_success(self, mock_conn):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"NAME": "SYSADMIN", "OWNER": "", "COMMENT": "System admin",
             "CREATED_ON": "2024-01-01", "ASSIGNED_TO_USERS": "2",
             "GRANTED_TO_ROLES": "1", "GRANTED_ROLES": "3"},
        ]
        mock_connection = MagicMock()
        mock_connection.cursor.return_value = mock_cursor
        mock_conn.return_value = mock_connection

        with patch.dict(os.environ, {
            "SNOWFLAKE_ACCOUNT": "test-acct",
            "SNOWFLAKE_USER": "user",
            "SNOWFLAKE_PASSWORD": "pass",
        }):
            result = run({"action": "snowflake.account.list_roles"})
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["result"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
