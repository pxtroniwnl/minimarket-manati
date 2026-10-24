"""pedidos desde el panel

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24 19:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('orders') as batch_op:
        batch_op.add_column(sa.Column('source', sa.String(length=10), server_default='whatsapp', nullable=False))
    # Los clientes que llegan por la tienda pueden no tener WhatsApp.
    with op.batch_alter_table('customers') as batch_op:
        batch_op.alter_column('phone', existing_type=sa.String(length=20), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM messages WHERE customer_id IN (SELECT id FROM customers WHERE phone IS NULL)")
    op.execute("DELETE FROM orders WHERE customer_id IN (SELECT id FROM customers WHERE phone IS NULL)")
    op.execute("DELETE FROM customers WHERE phone IS NULL")
    with op.batch_alter_table('customers') as batch_op:
        batch_op.alter_column('phone', existing_type=sa.String(length=20), nullable=False)
    with op.batch_alter_table('orders') as batch_op:
        batch_op.drop_column('source')
