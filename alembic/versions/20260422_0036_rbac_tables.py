"""RBAC metadata-driven tables: role_definitions + module_permissions.

Roles and permissions defined in DB, not code. New role = SQL INSERT.
Numeric level system (7-tier, gaps of 20).

Revision ID: 0036
Revises: 0035
"""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS role_definitions (
            id SERIAL PRIMARY KEY,
            role_name VARCHAR(32) NOT NULL UNIQUE,
            level INTEGER NOT NULL,
            scope VARCHAR(32) NOT NULL DEFAULT 'workspace',
            description TEXT,
            is_system BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS module_permissions (
            id SERIAL PRIMARY KEY,
            module VARCHAR(64) NOT NULL,
            permission VARCHAR(64) NOT NULL,
            min_role_level INTEGER NOT NULL,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(module, permission)
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_role_def_level ON role_definitions (level)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_module_perm_module ON module_permissions (module)")

    # Seed 7 roles
    op.execute("""
        INSERT INTO role_definitions (role_name, level, scope, description, is_system) VALUES
        ('super_admin', 100, 'platform', 'Platform superadmin — full access', true),
        ('tenant',       80, 'workspace', 'Workspace owner — manage all bots + members', false),
        ('admin',        60, 'workspace', 'Admin — view audit, manage bots', false),
        ('operator',     40, 'workspace', 'Operator — manage bots, upload docs', false),
        ('user',         20, 'workspace', 'User — chat with bots', false),
        ('viewer',       10, 'workspace', 'Viewer — read only', false),
        ('guest',         0, 'public', 'Guest — public endpoints only', false)
        ON CONFLICT (role_name) DO NOTHING
    """)

    # Seed permissions
    op.execute("""
        INSERT INTO module_permissions (module, permission, min_role_level, description) VALUES
        ('bot', 'create', 60, 'Create new bot'),
        ('bot', 'update', 40, 'Update bot config'),
        ('bot', 'delete', 60, 'Delete bot'),
        ('bot', 'bypass_token_limit', 80, 'Toggle token limit bypass'),
        ('bot', 'bypass_rate_limit', 80, 'Toggle rate limit bypass'),
        ('document', 'upload', 20, 'Upload document to bot'),
        ('document', 'delete', 60, 'Delete document from bot'),
        ('document', 'sync', 40, 'Sync documents from external source'),
        ('chat', 'query', 10, 'Send chat message'),
        ('chat', 'view_history', 20, 'View conversation history'),
        ('chat', 'clear_history', 40, 'Clear chat history'),
        ('ai', 'configure', 60, 'Configure AI models/bindings'),
        ('ai', 'view_models', 20, 'View available AI models'),
        ('admin', 'view_audit', 60, 'View audit logs and metrics'),
        ('admin', 'manage_members', 60, 'Manage workspace members'),
        ('admin', 'manage_tenants', 100, 'Manage tenants (platform only)'),
        ('system', 'view_metrics', 60, 'View Prometheus metrics'),
        ('system', 'manage_config', 80, 'Modify system_config keys')
        ON CONFLICT (module, permission) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS module_permissions")
    op.execute("DROP TABLE IF EXISTS role_definitions")
