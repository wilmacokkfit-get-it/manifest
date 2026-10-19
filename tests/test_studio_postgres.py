"""Apply Studio's migration to a disposable, local-only PostgreSQL cluster."""
import json
from pathlib import Path
import unittest

import test_storage_policy_audit_postgres as postgres_fixture


class StudioMigrationPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = type("DisposableStudioCluster", (postgres_fixture.StoragePolicyAuditPostgresTests,), {})
        cls.cluster.setUpClass()
        cls.addClassCleanup(cls.cluster.doClassCleanups)
        cls.sql("create role anon; create role authenticated; create role service_role bypassrls; "
                "create table public.ventures(id text primary key, name text); "
                "insert into public.ventures values ('preserve', 'existing venture');")
        cls.migration = (Path(__file__).parents[1] / "supabase/app-studio.sql").read_text()
        cls.sql(cls.migration)

    @classmethod
    def sql(cls, sql):
        return cls.cluster.run_tool("psql", "-X", "-h", str(cls.cluster.socket), "-U", "audit_owner",
                                    "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-At", input=sql)

    def test_idempotent_and_no_existing_data_changes(self):
        self.sql(self.migration)
        self.assertEqual(self.sql("select name from public.ventures where id = 'preserve';").strip(), "existing venture")

    def test_rls_enabled_and_client_roles_cannot_read_or_write(self):
        self.assertEqual(self.sql("select relrowsecurity from pg_class where oid = 'public.app_projects'::regclass;").strip(), "t")
        for role in ("anon", "authenticated"):
            for privilege in ("select", "insert", "update", "delete"):
                self.assertEqual(self.sql(f"select has_table_privilege('{role}', 'public.app_projects', '{privilege}');").strip(), "f")
        for privilege in ("select", "insert", "update"):
            self.assertEqual(self.sql(f"select has_table_privilege('service_role', 'public.app_projects', '{privilege}');").strip(), "t")
        self.assertEqual(self.sql("select has_table_privilege('service_role', 'public.app_projects', 'delete');").strip(), "f")

    def test_revision_guard_and_five_version_limit_execute_in_postgres(self):
        payload = json.dumps({"versions": [{"id": i} for i in range(5)], "cursor": 4})
        self.sql(f"insert into public.app_projects(id, owner_token, idea, payload) values "
                 f"('00000000-0000-0000-0000-000000000001', 'test-owner', 'A disposable test idea', '{payload}'::jsonb);")
        first = self.sql("update public.app_projects set revision=1 where revision=0 returning revision;")
        second = self.sql("update public.app_projects set revision=2 where revision=0 returning revision;")
        self.assertIn("1", first)
        self.assertNotIn("2", second)
        too_many = json.dumps({"versions": [{"id": i} for i in range(6)]})
        with self.assertRaisesRegex(RuntimeError, "app_projects_history_limit"):
            self.sql(f"update public.app_projects set payload = '{too_many}'::jsonb;")
