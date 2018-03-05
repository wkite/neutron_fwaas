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

"""modify_firewall_rule_table

Revision ID: 0490a95683aa
Revises: c575480592dd
Create Date: 2018-03-01 16:20:11.990630

"""

# revision identifiers, used by Alembic.
revision = '0490a95683aa'
down_revision = 'c575480592dd'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column(
        'firewall_rules_v2',
        sa.Column('source_address_group_id', sa.String(length=36),
                  sa.ForeignKey('address_groups.id', ondelete='CASCADE'),
                  nullable=True))
    op.add_column(
        'firewall_rules_v2',
        sa.Column('destination_address_group_id', sa.String(length=36),
                  sa.ForeignKey('address_groups.id', ondelete='CASCADE'),
                  nullable=True))
    op.add_column(
        'firewall_rules_v2',
        sa.Column('source_service_group_id', sa.String(length=36),
                  sa.ForeignKey('service_groups.id', ondelete='CASCADE'),
                  nullable=True))
    op.add_column(
        'firewall_rules_v2',
        sa.Column('destination_service_group_id', sa.String(length=36),
                  sa.ForeignKey('service_groups.id', ondelete='CASCADE'),
                  nullable=True))

