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

"""create_firewall_address_group

Revision ID: 9305a54df5d9
Revises: f24e0d5e5bff
Create Date: 2018-02-06 15:51:06.247764

"""

# revision identifiers, used by Alembic.
revision = '9305a54df5d9'
down_revision = 'f24e0d5e5bff'

from alembic import op
import sqlalchemy as sa


def upgrade():

    op.create_table(
        'address_groups',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('name', sa.String(length=255)),
        sa.Column('description', sa.String(length=1024)),
        sa.Column('project_id', sa.String(length=255), index=True))


    op.create_table(
        'address_associations',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('address_group_id', sa.String(length=36),
                  sa.ForeignKey('address_groups.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('ip_version', sa.Integer),
        sa.Column('timeout', sa.Integer),
        sa.Column('ip_address', sa.String(length=64)))
