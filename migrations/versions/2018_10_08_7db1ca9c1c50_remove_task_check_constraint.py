"""Remove task check constraint.

Revision ID: 7db1ca9c1c50
Revises: 19eabf5fe31f
Create Date: 2018-10-08 20:13:03.455246

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7db1ca9c1c50'
down_revision = '19eabf5fe31f'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint('task_check', 'task')


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_check_constraint(
        'task_check', 'task',
        ' (user_id IS NOT NULL AND sticker_set_name IS NULL) OR (user_id IS NULL AND sticker_set_name IS NOT NULL)',
    )
