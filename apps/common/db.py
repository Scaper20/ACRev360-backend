"""
Row-level security helpers used from migrations. See V2_ARCHITECTURE.md §3: every
tenant-scoped table gets an RLS policy written in the same migration that creates it,
not bolted on after.

FORCE ROW LEVEL SECURITY matters here: Postgres exempts a table's owner from RLS by
default, and the owner is exactly who Django's migrations connect as. Without FORCE,
the policy would silently do nothing in this setup.
"""


def enable_tenant_rls_sql(table_name: str, policy_name: str | None = None) -> list[str]:
    policy_name = policy_name or f"{table_name}_tenant_isolation"
    return [
        f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY;',
        f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY;',
        f'''
        CREATE POLICY "{policy_name}" ON "{table_name}"
        USING (council_id = NULLIF(current_setting('app.council_id', true), '')::integer)
        WITH CHECK (council_id = NULLIF(current_setting('app.council_id', true), '')::integer);
        ''',
    ]


def disable_tenant_rls_sql(table_name: str, policy_name: str | None = None) -> list[str]:
    policy_name = policy_name or f"{table_name}_tenant_isolation"
    return [
        f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}";',
        f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY;',
        f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY;',
    ]
