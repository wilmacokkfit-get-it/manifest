-- TEMPLATE ONLY: generate executable SQL with
-- python scripts/render_storage_policy_audit.py
-- Run the generated SQL in the matching project's privileged SQL editor.
-- Reads bucket metadata and PostgreSQL catalogs, never storage.objects rows.
-- Do not add probes that evaluate policy functions against existing objects.
BEGIN TRANSACTION READ ONLY;

WITH
config AS (
    SELECT __RENDERS_BUCKET_LITERAL__::text AS bucket_id
),
objects_table AS (
    SELECT c.oid, c.relowner, c.relrowsecurity, c.relforcerowsecurity, c.relacl
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'storage' AND c.relname = 'objects'
),
browser_roles AS (
    SELECT requested.name, r.oid, r.rolsuper, r.rolbypassrls
    FROM (VALUES ('anon'::text), ('authenticated'::text)) requested(name)
    LEFT JOIN pg_catalog.pg_roles r ON r.rolname = requested.name
),
read_policies AS (
    SELECT p.*
    FROM pg_catalog.pg_policy p
    JOIN objects_table t ON t.oid = p.polrelid
    WHERE p.polcmd IN ('r', '*') -- SELECT and ALL both authorize reads
)
SELECT pg_catalog.jsonb_build_object(
    'audit_time', CURRENT_TIMESTAMP,
    'database', pg_catalog.current_database(),
    'configured_bucket', config.bucket_id,
    'bucket', (
        SELECT pg_catalog.jsonb_build_object('id', b.id, 'public', b.public)
        FROM storage.buckets b WHERE b.id = config.bucket_id
    ),
    'objects_table', (
        SELECT pg_catalog.jsonb_build_object(
            'rls_enabled', t.relrowsecurity,
            'rls_forced', t.relforcerowsecurity,
            'owner', pg_catalog.pg_get_userbyid(t.relowner)
        ) FROM objects_table t
    ),
    'browser_roles', (
        SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
            'role', b.name,
            'exists', b.oid IS NOT NULL,
            'superuser', b.rolsuper,
            'bypasses_rls', b.rolbypassrls,
            'owns_objects', b.oid = t.relowner,
            'schema_usage', pg_catalog.has_schema_privilege(b.oid, 'storage', 'USAGE'),
            'table_select', pg_catalog.has_table_privilege(b.oid, t.oid, 'SELECT'),
            'any_column_select', pg_catalog.has_any_column_privilege(b.oid, t.oid, 'SELECT')
        ) ORDER BY b.name)
        FROM browser_roles b CROSS JOIN objects_table t
    ),
    'table_select_grants', (
        SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
            'grantee', CASE WHEN a.grantee = 0 THEN 'public'
                ELSE pg_catalog.pg_get_userbyid(a.grantee) END,
            'grantor', pg_catalog.pg_get_userbyid(a.grantor),
            'grantable', a.is_grantable
        ) ORDER BY a.grantee), '[]'::jsonb)
        FROM objects_table t
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(t.relacl, pg_catalog.acldefault('r', t.relowner))
        ) a
        WHERE a.privilege_type = 'SELECT'
    ),
    'select_and_all_policies', (
        SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
            'name', p.polname,
            'command', CASE WHEN p.polcmd = 'r' THEN 'SELECT' ELSE 'ALL' END,
            'mode', CASE WHEN p.polpermissive THEN 'PERMISSIVE' ELSE 'RESTRICTIVE' END,
            'granted_roles', (
                SELECT pg_catalog.jsonb_agg(
                    CASE WHEN granted.oid = 0 THEN 'public'
                    ELSE pg_catalog.pg_get_userbyid(granted.oid) END
                    ORDER BY granted.oid
                ) FROM unnest(p.polroles) AS granted(oid)
            ),
            'applies_to_browser_roles', (
                SELECT COALESCE(pg_catalog.jsonb_agg(b.name ORDER BY b.name), '[]'::jsonb)
                FROM browser_roles b
                WHERE b.oid IS NOT NULL AND EXISTS (
                    SELECT 1 FROM unnest(p.polroles) AS granted(oid)
                    WHERE CASE WHEN granted.oid = 0 THEN true
                        ELSE pg_catalog.pg_has_role(b.oid, granted.oid, 'USAGE') END
                )
            ),
            'using_expression', pg_catalog.pg_get_expr(p.polqual, p.polrelid),
            'with_check_expression', pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid)
        ) ORDER BY p.polname), '[]'::jsonb)
        FROM read_policies p
    ),
    'verdict', 'OPERATOR REVIEW REQUIRED: expressions are listed, not evaluated'
) AS storage_privacy_audit
FROM config;

ROLLBACK;