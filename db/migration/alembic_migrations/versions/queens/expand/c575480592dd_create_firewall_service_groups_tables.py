# Copyright 2018 <PUT YOUR NAME/COMPANY HERE>
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#

"""create_firewall_service_groups_tables

Revision ID: c575480592dd
Revises: 9305a54df5d9
Create Date: 2018-02-26 16:50:42.764412

"""

# revision identifiers, used by Alembic.
revision = 'c575480592dd'
down_revision = '9305a54df5d9'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        'service_groups',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('name', sa.String(length=255)),
        sa.Column('description', sa.String(length=1024)),
        sa.Column('project_id', sa.String(length=255), index=True),
        mysql_DEFAULT_CHARSET='utf8'
    )
    op.create_table(
        'service_associations',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('service_group_id', sa.String(length=36),
                  sa.ForeignKey('service_groups.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('port', sa.String(length=255)),
        sa.Column('timeout', sa.Integer),
        sa.Column('protocol', sa.String(length=8)),
        mysql_DEFAULT_CHARSET='utf8'
    )
