"""Execute the metadata audit against a new local-only PostgreSQL cluster.

No Supabase client, credentials, connection URL, or existing database is used.
Missing PostgreSQL binaries are an error, not a silently skipped regression.
"""

import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.render_storage_policy_audit import audit_sql


class StoragePolicyAuditPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = {}
        for name in ("initdb", "pg_ctl", "psql"):
            binary = shutil.which(name)
            if not binary:
                raise RuntimeError(
                    f"{name} is required: install PostgreSQL server/client tools "
                    "and put them on PATH before running this test suite."
                )
            cls.tools[name] = binary

        # No TemporaryDirectory finalizer: it must never delete a live cluster
        # after a failed shutdown, including when this class is garbage-collected.
        cls.root = Path(tempfile.mkdtemp(prefix="audit-pg-", dir="/tmp"))
        cls.data = cls.root / "data"
        cls.socket = cls.root / "socket"
        cls.addClassCleanup(cls.cleanup_cluster)
        cls.socket.mkdir(mode=0o700)
        # An allowlist, not a copy of os.environ: no PG*, Supabase, secrets,
        # service files, or user startup configuration can reach subprocesses.
        cls.env = {"LC_ALL": "C", "HOME": str(cls.root)}
        cls.run_tool("initdb", "-D", str(cls.data), "-U", "audit_owner",
                     "--no-locale", "--encoding=UTF8",
                     "--auth-local=trust", "--auth-host=reject")
        options = shlex.join([
            "-c", "listen_addresses=",
            "-c", f"unix_socket_directories={cls.socket}",
            "-c", "unix_socket_permissions=0700",
            "-c", "port=5432",
        ])
        cls.run_tool("pg_ctl", "-D", str(cls.data), "-l", str(cls.root / "server.log"),
                     "-o", options, "-w", "-t", "20", "start")

    @classmethod
    def run_tool(cls, name, *args, input=None):
        try:
            result = subprocess.run(
                [cls.tools[name], *args], input=input, env=cls.env,
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            # TimeoutExpired can contain bytes even with text=True.
            output = [
                value.decode(errors="replace") if isinstance(value, bytes)
                else value or ""
                for value in (exc.stdout, exc.stderr)
            ]
            raise RuntimeError(
                f"Local {name} timed out after {exc.timeout}s:\n"
                + "\n".join(output) + cls.server_diagnostics()
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"Local {name} could not execute: {exc}"
                + cls.server_diagnostics()
            ) from exc
        if result.returncode:
            raise RuntimeError(
                f"Local {name} failed ({result.returncode}):\n"
                f"{result.stdout}\n{result.stderr}"
                + cls.server_diagnostics()
            )
        return result.stdout

    @classmethod
    def server_diagnostics(cls):
        log = cls.root / "server.log"
        try:
            return f"\nLocal server log ({log}):\n{log.read_text(errors='replace')}"
        except OSError:
            return f"\nLocal server log unavailable: {log}"

    @classmethod
    def cleanup_cluster(cls):
        try:
            cls.stop_cluster()
            if (cls.data / "postmaster.pid").exists():
                raise RuntimeError("postmaster.pid still exists after shutdown")
        except Exception as exc:
            raise RuntimeError(
                f"Local cluster shutdown could not be confirmed; preserved "
                f"{cls.root} for recovery.\n{exc}" + cls.server_diagnostics()
            ) from exc
        shutil.rmtree(cls.root)

    @classmethod
    def stop_cluster(cls):
        if (cls.data / "postmaster.pid").exists():
            cls.run_tool("pg_ctl", "-D", str(cls.data), "-m", "immediate",
                         "-w", "-t", "20", "stop")

    @classmethod
    def sql(cls, statement):
        return cls.run_tool(
            "psql", "-X", "--no-password", "-h", str(cls.socket),
            "-p", "5432", "-U", "audit_owner", "-d", "postgres",
            "-v", "ON_ERROR_STOP=1", "-q", "-A", "-t", input=statement,
        )

    def setUp(self):
        self.sql("""
            DROP SCHEMA IF EXISTS storage CASCADE;
            DROP ROLE IF EXISTS anon, authenticated, custom_reader, non_browser;
            CREATE ROLE anon NOLOGIN;
            CREATE ROLE authenticated NOLOGIN INHERIT;
            CREATE ROLE custom_reader NOLOGIN;
            CREATE ROLE non_browser NOLOGIN;
            CREATE SCHEMA storage;
            CREATE TABLE storage.buckets (id text PRIMARY KEY, public boolean);
            CREATE TABLE storage.objects (id integer, bucket_id text, name text);
            ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;
            GRANT USAGE ON SCHEMA storage TO anon, authenticated;
            INSERT INTO storage.buckets VALUES ('renders', false), ('other', true);
        """)

    def report(self, bucket="renders", standard_strings="on"):
        output = self.sql(
            f"SET standard_conforming_strings = {standard_strings};\n"
            + audit_sql(bucket)
        )
        report = json.loads(output)
        self.assertEqual(report["configured_bucket"], bucket)
        self.assertEqual(
            report["verdict"],
            "OPERATOR REVIEW REQUIRED: expressions are listed, not evaluated",
        )
        return report

    @staticmethod
    def policies(report):
        return {p["name"]: p for p in report["select_and_all_policies"]}

    @staticmethod
    def roles(report):
        return {r["role"]: r for r in report["browser_roles"]}

    def test_bucket_scoped_select_and_unrelated_bucket_are_both_inventoried(self):
        self.sql("""
            CREATE POLICY render_read ON storage.objects FOR SELECT TO anon
                USING (bucket_id = 'renders');
            CREATE POLICY other_read ON storage.objects FOR SELECT TO authenticated
                USING (bucket_id = 'other');
            CREATE POLICY write_only ON storage.objects FOR INSERT TO anon
                WITH CHECK (true);
        """)
        report = self.report()
        self.assertEqual(report["bucket"], {"id": "renders", "public": False})
        self.assertTrue(report["objects_table"]["rls_enabled"])
        self.assertFalse(report["objects_table"]["rls_forced"])
        policies = self.policies(report)
        self.assertEqual(set(policies), {"render_read", "other_read"})
        for name, role, bucket in (
            ("render_read", "anon", "renders"),
            ("other_read", "authenticated", "other"),
        ):
            policy = policies[name]
            self.assertEqual(policy["command"], "SELECT")
            self.assertEqual(policy["mode"], "PERMISSIVE")
            self.assertEqual(policy["granted_roles"], [role])
            self.assertEqual(policy["applies_to_browser_roles"], [role])
            self.assertIn(f"'{bucket}'", policy["using_expression"])
            self.assertIsNone(policy["with_check_expression"])

    def test_public_all_policy_reports_both_expressions_and_browser_roles(self):
        self.sql("""
            CREATE POLICY public_all ON storage.objects FOR ALL TO PUBLIC
                USING (true) WITH CHECK (bucket_id = 'renders');
            GRANT SELECT ON storage.objects TO PUBLIC;
        """)
        report = self.report()
        policy = self.policies(report)["public_all"]
        self.assertEqual(policy["command"], "ALL")
        self.assertEqual(policy["granted_roles"], ["public"])
        self.assertEqual(policy["applies_to_browser_roles"], ["anon", "authenticated"])
        self.assertEqual(policy["using_expression"], "true")
        self.assertIn("'renders'", policy["with_check_expression"])
        self.assertIn("public", {g["grantee"] for g in report["table_select_grants"]})
        for role in self.roles(report).values():
            self.assertTrue(role["table_select"])

    def test_inherited_custom_role_policy_and_table_grant(self):
        self.sql("""
            GRANT custom_reader TO authenticated;
            GRANT SELECT ON storage.objects TO custom_reader;
            CREATE POLICY inherited_read ON storage.objects FOR SELECT
                TO custom_reader USING (bucket_id = 'renders');
            CREATE POLICY unrelated_read ON storage.objects FOR SELECT
                TO non_browser USING (true);
        """)
        report = self.report()
        policies = self.policies(report)
        self.assertEqual(policies["inherited_read"]["granted_roles"], ["custom_reader"])
        self.assertEqual(
            policies["inherited_read"]["applies_to_browser_roles"], ["authenticated"]
        )
        self.assertEqual(policies["unrelated_read"]["applies_to_browser_roles"], [])
        roles = self.roles(report)
        self.assertTrue(roles["authenticated"]["table_select"])
        self.assertTrue(roles["authenticated"]["any_column_select"])
        self.assertFalse(roles["anon"]["table_select"])
        self.assertIn(
            "custom_reader", {g["grantee"] for g in report["table_select_grants"]}
        )

    def test_restrictive_policy_is_listed_not_treated_as_a_safe_verdict(self):
        self.sql("""
            CREATE POLICY broad_read ON storage.objects FOR SELECT TO PUBLIC
                USING (true);
            CREATE POLICY blocked_read ON storage.objects AS RESTRICTIVE
                FOR SELECT TO PUBLIC USING (false);
        """)
        policies = self.policies(self.report())
        self.assertEqual(set(policies), {"broad_read", "blocked_read"})
        self.assertEqual(policies["broad_read"]["mode"], "PERMISSIVE")
        self.assertEqual(policies["blocked_read"]["mode"], "RESTRICTIVE")
        self.assertEqual(policies["blocked_read"]["using_expression"], "false")
        self.assertEqual(
            policies["blocked_read"]["applies_to_browser_roles"], ["anon", "authenticated"]
        )

    def test_table_and_column_only_grants_are_distinguished(self):
        self.sql("""
            GRANT SELECT ON storage.objects TO authenticated WITH GRANT OPTION;
            GRANT SELECT (name) ON storage.objects TO anon;
        """)
        report = self.report()
        roles = self.roles(report)
        self.assertFalse(roles["anon"]["table_select"])
        self.assertTrue(roles["anon"]["any_column_select"])
        self.assertTrue(roles["authenticated"]["table_select"])
        self.assertTrue(roles["authenticated"]["any_column_select"])
        grants = {g["grantee"]: g for g in report["table_select_grants"]}
        self.assertNotIn("anon", grants)
        self.assertTrue(grants["authenticated"]["grantable"])
        self.assertEqual(grants["authenticated"]["grantor"], "audit_owner")
        self.assertEqual(report["select_and_all_policies"], [])

    def test_absent_roles_and_bucket_report_missing_metadata_without_errors(self):
        self.sql("""
            REVOKE USAGE ON SCHEMA storage FROM anon, authenticated;
            DROP ROLE anon, authenticated;
            CREATE POLICY public_read ON storage.objects FOR SELECT TO PUBLIC
                USING (true);
        """)
        report = self.report("missing-bucket")
        self.assertIsNone(report["bucket"])
        roles = self.roles(report)
        self.assertEqual(set(roles), {"anon", "authenticated"})
        for role in roles.values():
            self.assertFalse(role["exists"])
            for field in (
                "superuser", "bypasses_rls", "owns_objects", "schema_usage",
                "table_select", "any_column_select",
            ):
                self.assertIsNone(role[field], field)
        self.assertEqual(
            self.policies(report)["public_read"]["applies_to_browser_roles"], []
        )

    def test_empty_acl_defaults_and_missing_bucket_do_not_imply_safety(self):
        report = self.report("missing")
        self.assertIsNone(report["bucket"])
        self.assertEqual(report["select_and_all_policies"], [])
        self.assertEqual(
            report["table_select_grants"],
            [{"grantee": "audit_owner", "grantor": "audit_owner", "grantable": False}],
        )
        for role in self.roles(report).values():
            self.assertTrue(role["exists"])
            self.assertTrue(role["schema_usage"])
            self.assertFalse(role["table_select"])
            self.assertFalse(role["any_column_select"])

    def test_quoted_backslash_unicode_and_injection_like_bucket_literals(self):
        buckets = (
            "render's", r"render\backslash", "画面",
            r"render\'; DROP TABLE storage.objects; --",
        )
        # Dollar quoting fixtures independently of the generator under test.
        for bucket in buckets:
            self.sql(
                "INSERT INTO storage.buckets VALUES "
                f"($fixture${bucket}$fixture$, false);"
            )
        for bucket in buckets:
            for setting in ("on", "off"):
                with self.subTest(bucket=bucket, standard_strings=setting):
                    report = self.report(bucket, standard_strings=setting)
                    self.assertEqual(report["bucket"], {"id": bucket, "public": False})
                    self.assertTrue(report["objects_table"]["rls_enabled"])
        self.assertEqual(self.sql("SELECT count(*) FROM storage.buckets;").strip(), "6")

    def test_policy_functions_and_object_rows_are_never_evaluated(self):
        self.sql("""
            CREATE FUNCTION storage.must_not_run(text) RETURNS boolean
            LANGUAGE plpgsql VOLATILE AS $$
            BEGIN
                RAISE EXCEPTION 'Audit evaluated a policy or read object content';
            END $$;
            CREATE POLICY dangerous_helper ON storage.objects FOR SELECT TO PUBLIC
                USING (storage.must_not_run(name));
            INSERT INTO storage.objects VALUES (1, 'renders', 'disposable-only');
        """)
        report = self.report()
        self.assertIn(
            "storage.must_not_run(name)",
            self.policies(report)["dangerous_helper"]["using_expression"],
        )
        self.assertNotIn("disposable-only", json.dumps(report))

    def test_rls_disabled_forced_and_bypass_flags_remain_review_required(self):
        self.sql("""
            ALTER TABLE storage.objects DISABLE ROW LEVEL SECURITY;
            ALTER ROLE anon BYPASSRLS;
        """)
        report = self.report()
        self.assertFalse(report["objects_table"]["rls_enabled"])
        self.assertTrue(self.roles(report)["anon"]["bypasses_rls"])
        self.assertFalse(self.roles(report)["authenticated"]["bypasses_rls"])
        self.sql("""
            ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;
            ALTER TABLE storage.objects FORCE ROW LEVEL SECURITY;
        """)
        self.assertTrue(self.report()["objects_table"]["rls_forced"])


class StoragePolicyAuditHarnessFailureTests(unittest.TestCase):
    """Exercise unittest's real class-cleanup lifecycle, with no processes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="audit-harness-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "cluster"
        self.root.mkdir()

    def run_harness(self, *, init_failure=None, start_failure=None,
                    stop_failure=None, startup_pid=True, lingering_pid=False,
                    body_failure=False):
        def probe(test):
            if body_failure:
                raise AssertionError("permission assertion failed")

        harness = type("DisposableHarnessProbe", (StoragePolicyAuditPostgresTests,), {
            "setUp": lambda test: None,
            "runTest": probe,
        })
        calls = []

        def execute(command, **kwargs):
            calls.append(command)
            self.assertEqual(kwargs["env"], {
                "LC_ALL": "C", "HOME": str(self.root),
            })
            self.assertEqual(kwargs["timeout"], 30)
            self.assertTrue(kwargs["capture_output"])
            self.assertTrue(kwargs["text"])
            tool = Path(command[0]).name
            if tool == "initdb":
                harness.data.mkdir()
                failure = init_failure
            else:
                self.assertEqual(tool, "pg_ctl")
                if command[-1] == "start":
                    (self.root / "server.log").write_text("server diagnostic\n")
                    if startup_pid:
                        (harness.data / "postmaster.pid").write_text("fake pid\n")
                    failure = start_failure
                else:
                    self.assertEqual(command[-1], "stop")
                    self.assertIn("immediate", command)
                    failure = stop_failure
                    if failure is None and not lingering_pid:
                        (harness.data / "postmaster.pid").unlink()
            if isinstance(failure, BaseException):
                raise failure
            return subprocess.CompletedProcess(
                command, 1 if failure else 0,
                stdout=f"{failure or tool} stdout",
                stderr=f"{failure or tool} stderr",
            )

        # Even on a machine without PostgreSQL, all subprocesses stay mocked.
        with patch.object(shutil, "which", side_effect=lambda name: f"/offline/{name}"), \
                patch.object(tempfile, "mkdtemp", return_value=str(self.root)), \
                patch.object(subprocess, "run", side_effect=execute):
            result = unittest.TestResult()
            unittest.TestSuite([harness("runTest")]).run(result)
        diagnostics = "\n".join(text for _, text in result.errors + result.failures)
        return result, calls, diagnostics

    @staticmethod
    def timeout():
        return subprocess.TimeoutExpired(
            ["mocked-tool"], 30, output=b"partial stdout", stderr=b"partial stderr",
        )

    def assert_stop_attempted(self, calls):
        stops = [call for call in calls if call[-1] == "stop"]
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0][2], str(self.root / "data"))

    def test_initdb_failures_remove_partial_directory_without_starting_server(self):
        for failure in ("init failure", self.timeout(), OSError("cannot execute")):
            with self.subTest(failure=failure):
                if not self.root.exists():
                    self.root.mkdir()
                result, calls, diagnostics = self.run_harness(init_failure=failure)
                self.assertEqual(len(result.errors), 1)
                self.assertEqual(len(calls), 1)
                self.assertEqual(Path(calls[0][0]).name, "initdb")
                self.assertFalse(self.root.exists())
                self.assertIn("Local initdb", diagnostics)

    def test_partial_startup_failures_always_attempt_shutdown(self):
        for failure in ("start failure", self.timeout(), OSError("startup error")):
            with self.subTest(failure=failure):
                if not self.root.exists():
                    self.root.mkdir()
                result, calls, diagnostics = self.run_harness(start_failure=failure)
                self.assertEqual(len(result.errors), 1)
                self.assert_stop_attempted(calls)
                self.assertFalse(self.root.exists())
                self.assertIn("server diagnostic", diagnostics)
                if isinstance(failure, subprocess.TimeoutExpired):
                    self.assertIn("partial stdout", diagnostics)
                    self.assertIn("partial stderr", diagnostics)
                    self.assertIn("timed out after 30s", diagnostics)
                elif isinstance(failure, str):
                    self.assertIn("start failure stdout", diagnostics)
                    self.assertIn("start failure stderr", diagnostics)
                else:
                    self.assertIn("startup error", diagnostics)

    def test_start_failure_without_pid_removes_directory(self):
        result, calls, diagnostics = self.run_harness(
            start_failure="no server started", startup_pid=False,
        )
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.root.exists())
        self.assertIn("server diagnostic", diagnostics)

    def test_shutdown_failures_preserve_cluster_and_log(self):
        for failure in ("stop failure", self.timeout(), OSError("shutdown error")):
            with self.subTest(failure=failure):
                result, calls, diagnostics = self.run_harness(stop_failure=failure)
                self.assertEqual(len(result.errors), 1)
                self.assert_stop_attempted(calls)
                self.assertTrue((self.root / "data" / "postmaster.pid").exists())
                self.assertEqual(
                    (self.root / "server.log").read_text(), "server diagnostic\n",
                )
                self.assertIn(f"preserved {self.root}", diagnostics)
                self.assertIn("server diagnostic", diagnostics)
                if isinstance(failure, subprocess.TimeoutExpired):
                    self.assertIn("partial stdout", diagnostics)
                    self.assertIn("partial stderr", diagnostics)
                # Only this test's fake cluster may be removed manually.
                shutil.rmtree(self.root)
                self.root.mkdir()

    def test_start_and_stop_errors_both_survive_class_cleanup(self):
        result, calls, diagnostics = self.run_harness(
            start_failure="original startup error",
            stop_failure="secondary shutdown error",
        )
        self.assertEqual(len(result.errors), 2)
        self.assert_stop_attempted(calls)
        self.assertIn("original startup error stdout", diagnostics)
        self.assertIn("secondary shutdown error stderr", diagnostics)
        self.assertIn(f"preserved {self.root}", diagnostics)
        self.assertTrue((self.root / "data" / "postmaster.pid").exists())

    def test_successful_stop_with_lingering_pid_does_not_delete_cluster(self):
        result, calls, diagnostics = self.run_harness(lingering_pid=True)
        self.assertEqual(len(result.errors), 1)
        self.assert_stop_attempted(calls)
        self.assertTrue((self.root / "data" / "postmaster.pid").exists())
        self.assertIn("postmaster.pid still exists", diagnostics)
        self.assertIn(f"preserved {self.root}", diagnostics)

    def test_failed_permission_assertion_still_shuts_down_and_removes_cluster(self):
        result, calls, diagnostics = self.run_harness(body_failure=True)
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.errors, [])
        self.assert_stop_attempted(calls)
        self.assertFalse(self.root.exists())
        self.assertIn("permission assertion failed", diagnostics)

    def test_successful_run_shuts_down_before_removing_cluster(self):
        result, calls, diagnostics = self.run_harness()
        self.assertTrue(result.wasSuccessful(), diagnostics)
        self.assertEqual(result.testsRun, 1)
        self.assert_stop_attempted(calls)
        self.assertFalse(self.root.exists())