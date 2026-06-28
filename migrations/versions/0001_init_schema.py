"""init schema

Revision ID: 0001_init_schema
Revises:
Create Date: 2026-06-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_init_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("alias", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("password", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_phone"), "users", ["phone"], unique=True)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("module", sa.String(length=128), nullable=True),
        sa.Column("summary", sa.String(length=255), nullable=True),
        sa.Column("method", sa.String(length=16), nullable=True),
        sa.Column("path", sa.String(length=500), nullable=True),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("response_time", sa.Integer(), nullable=True),
        sa.Column("request_args", sa.Text(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_logs_id"), "audit_logs", ["id"], unique=False)
    op.create_index(op.f("ix_audit_logs_user_id"), "audit_logs", ["user_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_username"), "audit_logs", ["username"], unique=False)
    op.create_index(op.f("ix_audit_logs_method"), "audit_logs", ["method"], unique=False)
    op.create_index(op.f("ix_audit_logs_path"), "audit_logs", ["path"], unique=False)
    op.create_index(op.f("ix_audit_logs_status"), "audit_logs", ["status"], unique=False)

    op.create_table(
        "file_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=64), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("upload_user_id", sa.Integer(), nullable=True),
        sa.Column("file_path", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_file_mappings_id"), "file_mappings", ["id"], unique=False)
    op.create_index(op.f("ix_file_mappings_file_id"), "file_mappings", ["file_id"], unique=True)
    op.create_index(op.f("ix_file_mappings_upload_user_id"), "file_mappings", ["upload_user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_file_mappings_upload_user_id"), table_name="file_mappings")
    op.drop_index(op.f("ix_file_mappings_file_id"), table_name="file_mappings")
    op.drop_index(op.f("ix_file_mappings_id"), table_name="file_mappings")
    op.drop_table("file_mappings")
    op.drop_index(op.f("ix_audit_logs_status"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_path"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_method"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_username"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_user_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_id"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(op.f("ix_users_phone"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_table("users")
