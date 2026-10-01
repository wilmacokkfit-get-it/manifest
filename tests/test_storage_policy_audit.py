"""Offline safety checks for the metadata-only operator audit generator."""

import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts.render_storage_policy_audit import audit_sql, main


class StoragePolicyAuditTests(unittest.TestCase):
    def test_default_and_custom_bucket(self):
        for bucket in ("renders", "custom-private-renders", "画面"):
            sql = audit_sql(bucket)
            self.assertIn(f"SELECT E'{bucket}'::text AS bucket_id", sql)
            self.assertNotIn("__RENDERS_BUCKET_LITERAL__", sql)
            self.assertIn("BEGIN TRANSACTION READ ONLY;", sql)
            self.assertTrue(sql.rstrip().endswith("ROLLBACK;"))

    def test_literal_escaping(self):
        bucket = "render\\'; DROP POLICY shared ON storage.objects; --"
        sql = audit_sql(bucket)
        self.assertIn(
            "SELECT E'render\\\\''; DROP POLICY shared ON storage.objects; --'::text",
            sql,
        )

    def test_bad_configuration_fails_explicitly(self):
        for bucket in ("", "renders\x00", "renders\n", "renders\r", "renders\x7f"):
            with self.subTest(bucket=repr(bucket)), self.assertRaises(ValueError):
                audit_sql(bucket)

    def test_only_catalogs_and_bucket_metadata_are_read(self):
        sql = audit_sql("renders")
        self.assertNotRegex(sql.lower(), r"\b(?:from|join)\s+storage\.objects\b")
        self.assertIn("FROM storage.buckets b WHERE b.id = config.bucket_id", sql)
        self.assertNotRegex(sql.lower(), r"\b(?:insert|update|delete|alter|drop|create|grant|revoke)\s")

    def test_policy_inventory_does_not_hide_bucket_specific_or_all_policies(self):
        sql = audit_sql("renders")
        self.assertIn("p.polcmd IN ('r', '*')", sql)
        self.assertIn("pg_catalog.pg_get_expr(p.polqual, p.polrelid)", sql)
        self.assertIn("p.polpermissive", sql)
        self.assertNotIn("LIKE", sql.upper())
        self.assertIn("granted.oid = 0", sql)
        self.assertIn("pg_catalog.pg_has_role(b.oid, granted.oid, 'USAGE')", sql)
        self.assertIn("'anon'::text", sql)
        self.assertIn("'authenticated'::text", sql)
        self.assertIn("pg_catalog.has_any_column_privilege", sql)
        self.assertIn("pg_catalog.aclexplode", sql)
        self.assertIn("'bypasses_rls'", sql)
        self.assertIn("'rls_enabled'", sql)

    def test_cli_uses_exact_configured_bucket_without_secrets_or_network(self):
        root = Path(__file__).resolve().parents[1]
        # A minimal environment without credentials still produces the SQL.
        result = subprocess.run(
            [sys.executable, str(root / "scripts/render_storage_policy_audit.py")],
            cwd="/tmp", env={"SUPABASE_RENDERS_BUCKET": "configured-bucket"},
            capture_output=True, text=True, check=True,
        )
        self.assertIn("SELECT E'configured-bucket'::text", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_unset_defaults_but_empty_is_not_silently_defaulted(self):
        with patch.dict(os.environ, {}, clear=True), patch("builtins.print") as output:
            main()
        self.assertIn("SELECT E'renders'::text", output.call_args.args[0])
        with patch.dict(os.environ, {"SUPABASE_RENDERS_BUCKET": ""}, clear=True):
            with self.assertRaises(SystemExit):
                main()