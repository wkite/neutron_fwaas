# Copyright (c) 2016
# All Rights Reserved.
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

import copy

import netaddr
from neutron.db import api as db_api
from neutron.db import common_db_mixin as base_db
from neutron_lib.api.definitions import constants as fw_const
from neutron_lib.api import validators
from neutron_lib import constants as nl_constants
from neutron_lib.db import constants as db_constants
from neutron_lib.db import model_base
from neutron_lib import exceptions
from neutron_lib.exceptions import firewall_v2 as f_exc
from oslo_config import cfg
from oslo_db import exception as db_exc
from oslo_log import log as logging
from oslo_utils import uuidutils
import sqlalchemy as sa
from sqlalchemy.ext.orderinglist import ordering_list
from sqlalchemy import or_
from sqlalchemy import orm
from sqlalchemy.orm import exc

from neutron_fwaas.common import fwaas_constants as const
from neutron_fwaas.extensions import firewall_v2 as fw_ext


LOG = logging.getLogger(__name__)


class FirewallDefaultParameterExists(exceptions.InUse):
    """Default Firewall Parameter conflict exception

    Occurs when user creates/updates any existing firewall resource with
    reserved parameter names.
    """
    message = _("Operation cannot be performed since '%(name)s' "
                "is a reserved name for %(resource_type)s.")


class FirewallDefaultObjectUpdateRestricted(FirewallDefaultParameterExists):
    message = _("Operation cannot be performed on default object "
                "'%(resource_id)s' of type %(resource_type)s.")


class HasName(object):
    name = sa.Column(sa.String(db_constants.NAME_FIELD_SIZE))


class HasDescription(object):
    description = sa.Column(
        sa.String(db_constants.LONG_DESCRIPTION_FIELD_SIZE))


class FirewallRuleV2(model_base.BASEV2, model_base.HasId, HasName,
                     HasDescription, model_base.HasProject):
    __tablename__ = "firewall_rules_v2"
    shared = sa.Column(sa.Boolean)
    protocol = sa.Column(sa.String(40))
    ip_version = sa.Column(sa.Integer)
    source_ip_address = sa.Column(sa.String(46))
    destination_ip_address = sa.Column(sa.String(46))
    source_port_range_min = sa.Column(sa.Integer)
    source_port_range_max = sa.Column(sa.Integer)
    destination_port_range_min = sa.Column(sa.Integer)
    destination_port_range_max = sa.Column(sa.Integer)
    action = sa.Column(sa.Enum('allow', 'deny', 'reject',
                               name='firewallrules_action'))
    enabled = sa.Column(sa.Boolean)


class FirewallGroup(model_base.BASEV2, model_base.HasId, HasName,
                    HasDescription, model_base.HasProject):
    __tablename__ = 'firewall_groups_v2'
    ports = orm.relationship(
        'FirewallGroupPortAssociation',
        backref=orm.backref('firewall_group_port_associations_v2',
                            cascade='all, delete'))
    name = sa.Column(sa.String(db_constants.NAME_FIELD_SIZE))
    description = sa.Column(
        sa.String(db_constants.LONG_DESCRIPTION_FIELD_SIZE))
    ingress_firewall_policy_id = sa.Column(
        sa.String(db_constants.UUID_FIELD_SIZE),
        sa.ForeignKey('firewall_policies_v2.id'))
    egress_firewall_policy_id = sa.Column(
        sa.String(db_constants.UUID_FIELD_SIZE),
        sa.ForeignKey('firewall_policies_v2.id'))
    admin_state_up = sa.Column(sa.Boolean)
    status = sa.Column(sa.String(db_constants.STATUS_FIELD_SIZE))
    shared = sa.Column(sa.Boolean)


class DefaultFirewallGroup(model_base.BASEV2, model_base.HasProjectPrimaryKey):
    __tablename__ = "default_firewall_groups"
    firewall_group_id = sa.Column(sa.String(db_constants.UUID_FIELD_SIZE),
                                  sa.ForeignKey('firewall_groups_v2.id',
                                                ondelete="CASCADE"),
                                  nullable=False)
    firewall_group = orm.relationship(
        FirewallGroup, lazy='joined',
        backref=orm.backref('default_firewall_group', cascade='all,delete'),
        primaryjoin="FirewallGroup.id==DefaultFirewallGroup.firewall_group_id",
    )


class FirewallGroupPortAssociation(model_base.BASEV2):
    __tablename__ = 'firewall_group_port_associations_v2'
    firewall_group_id = sa.Column(sa.String(db_constants.UUID_FIELD_SIZE),
                                  sa.ForeignKey('firewall_groups_v2.id',
                                                ondelete="CASCADE"),
                                  primary_key=True)
    port_id = sa.Column(sa.String(db_constants.UUID_FIELD_SIZE),
                        sa.ForeignKey('ports.id', ondelete="CASCADE"),
                        unique=True,
                        primary_key=True)


class FirewallPolicyRuleAssociation(model_base.BASEV2):

    """Tracks FW Policy and Rule(s) Association"""

    __tablename__ = 'firewall_policy_rule_associations_v2'

    firewall_policy_id = sa.Column(sa.String(db_constants.UUID_FIELD_SIZE),
                                   sa.ForeignKey('firewall_policies_v2.id',
                                                 ondelete="CASCADE"),
                                   primary_key=True)
    firewall_rule_id = sa.Column(sa.String(db_constants.UUID_FIELD_SIZE),
                                 sa.ForeignKey('firewall_rules_v2.id',
                                               ondelete="CASCADE"),
                                 primary_key=True)
    position = sa.Column(sa.Integer)


class FirewallPolicy(model_base.BASEV2, model_base.HasId, HasName,
                     HasDescription, model_base.HasProject):
    __tablename__ = 'firewall_policies_v2'
    name = sa.Column(sa.String(db_constants.NAME_FIELD_SIZE))
    description = sa.Column(
        sa.String(db_constants.LONG_DESCRIPTION_FIELD_SIZE))
    rule_count = sa.Column(sa.Integer)
    audited = sa.Column(sa.Boolean)
    rule_associations = orm.relationship(
        FirewallPolicyRuleAssociation,
        backref=orm.backref('firewall_policies_v2', cascade='all, delete'),
        order_by='FirewallPolicyRuleAssociation.position',
        collection_class=ordering_list('position', count_from=1))
    shared = sa.Column(sa.Boolean)


class Firewall_db_mixin_v2(fw_ext.Firewallv2PluginBase, base_db.CommonDbMixin):

    def _get_firewall_group(self, context, id):
        try:
            return self._get_by_id(context, FirewallGroup, id)
        except exc.NoResultFound:
            raise f_exc.FirewallGroupNotFound(firewall_id=id)

    def _get_firewall_policy(self, context, id):
        try:
            return self._get_by_id(context, FirewallPolicy, id)
        except exc.NoResultFound:
            raise f_exc.FirewallPolicyNotFound(firewall_policy_id=id)

    def _get_firewall_rule(self, context, id):
        try:
            return self._get_by_id(context, FirewallRuleV2, id)
        except exc.NoResultFound:
            raise f_exc.FirewallRuleNotFound(firewall_rule_id=id)

    def _validate_fwr_protocol_parameters(self, fwr, fwr_db=None):
        protocol = fwr.get('protocol', None)
        if fwr_db and not protocol:
            protocol = fwr_db.protocol
        if protocol not in (nl_constants.PROTO_NAME_TCP,
                            nl_constants.PROTO_NAME_UDP):
            if (fwr.get('source_port', None) or
                    fwr.get('destination_port', None)):
                raise f_exc.FirewallRuleInvalidICMPParameter(
                    param="Source, destination port")

    def _validate_fwr_src_dst_ip_version(self, fwr, fwr_db=None):
        src_version = dst_version = None
        if fwr.get('source_ip_address', None):
            src_version = netaddr.IPNetwork(fwr['source_ip_address']).version
        if fwr.get('destination_ip_address', None):
            dst_version = netaddr.IPNetwork(
                fwr['destination_ip_address']).version
        rule_ip_version = fwr.get('ip_version', None)
        if not rule_ip_version and fwr_db:
            rule_ip_version = fwr_db.ip_version
        if ((src_version and src_version != rule_ip_version) or
                (dst_version and dst_version != rule_ip_version)):
            raise f_exc.FirewallIpAddressConflict()

    def _validate_fwr_port_range(self, min_port, max_port):
        if int(min_port) > int(max_port):
            port_range = '%s:%s' % (min_port, max_port)
            raise f_exc.FirewallRuleInvalidPortValue(port=port_range)

    def _get_min_max_ports_from_range(self, port_range):
        if not port_range:
            return [None, None]
        min_port, sep, max_port = port_range.partition(":")
        if not max_port:
            max_port = min_port
        self._validate_fwr_port_range(min_port, max_port)
        return [int(min_port), int(max_port)]

    def _get_port_range_from_min_max_ports(self, min_port, max_port):
        if not min_port:
            return None
        if min_port == max_port:
            return str(min_port)
        self._validate_fwr_port_range(min_port, max_port)
        return '%s:%s' % (min_port, max_port)

    def _make_firewall_rule_dict(self, firewall_rule, fields=None,
                                 policies=None):
        src_port_range = self._get_port_range_from_min_max_ports(
            firewall_rule['source_port_range_min'],
            firewall_rule['source_port_range_max'])
        dst_port_range = self._get_port_range_from_min_max_ports(
            firewall_rule['destination_port_range_min'],
            firewall_rule['destination_port_range_max'])
        res = {'id': firewall_rule['id'],
               'tenant_id': firewall_rule['tenant_id'],
               'name': firewall_rule['name'],
               'description': firewall_rule['description'],
               'protocol': firewall_rule['protocol'],
               'firewall_policy_id': policies,
               'ip_version': firewall_rule['ip_version'],
               'source_ip_address': firewall_rule['source_ip_address'],
               'destination_ip_address':
               firewall_rule['destination_ip_address'],
               'source_port': src_port_range,
               'destination_port': dst_port_range,
               'action': firewall_rule['action'],
               'enabled': firewall_rule['enabled'],
               'shared': firewall_rule['shared']}
        return self._fields(res, fields)

    def _make_firewall_policy_dict(self, firewall_policy, fields=None):
        fw_rules = [
            rule_association.firewall_rule_id
            for rule_association in firewall_policy['rule_associations']]
        res = {'id': firewall_policy['id'],
               'tenant_id': firewall_policy['tenant_id'],
               'name': firewall_policy['name'],
               'description': firewall_policy['description'],
               'audited': firewall_policy['audited'],
               'firewall_rules': fw_rules,
               'shared': firewall_policy['shared']}
        return self._fields(res, fields)

    def _make_firewall_group_dict(self, firewall_group, fields=None):
        fwg_ports = [
            port_assoc.port_id for port_assoc in firewall_group['ports']
        ]
        res = {'id': firewall_group['id'],
               'tenant_id': firewall_group['tenant_id'],
               'name': firewall_group['name'],
               'description': firewall_group['description'],
               'ingress_firewall_policy_id':
                   firewall_group['ingress_firewall_policy_id'],
               'egress_firewall_policy_id':
                   firewall_group['egress_firewall_policy_id'],
               'admin_state_up': firewall_group['admin_state_up'],
               'ports': fwg_ports,
               'status': firewall_group['status'],
               'shared': firewall_group['shared']}
        return self._fields(res, fields)

    def _get_policy_ordered_rules(self, context, policy_id):
        query = (context.session.query(FirewallRuleV2)
                 .join(FirewallPolicyRuleAssociation)
                 .filter_by(firewall_policy_id=policy_id)
                 .order_by(FirewallPolicyRuleAssociation.position))
        return [self._make_firewall_rule_dict(rule) for rule in query]

    def _make_firewall_group_dict_with_rules(self, context, firewall_group_id):
        firewall_group = self.get_firewall_group(context, firewall_group_id)
        ingress_policy_id = firewall_group['ingress_firewall_policy_id']
        if ingress_policy_id:
            firewall_group['ingress_rule_list'] = (
                self._get_policy_ordered_rules(context, ingress_policy_id))
        else:
            firewall_group['ingress_rule_list'] = []

        egress_policy_id = firewall_group['egress_firewall_policy_id']
        if egress_policy_id:
            firewall_group['egress_rule_list'] = (
                self._get_policy_ordered_rules(context, egress_policy_id))
        else:
            firewall_group['egress_rule_list'] = []
        return firewall_group

    def _check_firewall_rule_conflict(self, fwr_db, fwp_db):
        if not fwr_db['shared']:
            if fwr_db['tenant_id'] != fwp_db['tenant_id']:
                raise f_exc.FirewallRuleConflict(
                    firewall_rule_id=fwr_db['id'],
                    project_id=fwr_db['tenant_id'])

    def _process_rule_for_policy(self, context, firewall_policy_id,
                                 firewall_rule_id, position, association_db):
        with context.session.begin(subtransactions=True):
            fwp_query = context.session.query(
                FirewallPolicy).with_lockmode('update')
            fwp_db = fwp_query.filter_by(id=firewall_policy_id).one()
            if position:
                # Note that although position numbering starts at 1,
                # internal ordering of the list starts at 0, so we compensate.
                fwp_db.rule_associations.insert(
                    position - 1,
                    FirewallPolicyRuleAssociation(
                        firewall_rule_id=firewall_rule_id))
            else:
                fwp_db.rule_associations.remove(association_db)
                context.session.delete(association_db)
            fwp_db.rule_associations.reorder()
            fwp_db.audited = False
        return self._make_firewall_policy_dict(fwp_db)

    def _get_policy_rule_association_query(self, context, firewall_policy_id,
                                           firewall_rule_id):
        fwpra_query = context.session.query(FirewallPolicyRuleAssociation)
        return fwpra_query.filter_by(firewall_policy_id=firewall_policy_id,
                                     firewall_rule_id=firewall_rule_id)

    def _ensure_rule_not_already_associated(self, context, firewall_policy_id,
                                            firewall_rule_id):
        """Checks that a rule is not already associated with a particular
        policy. If it is the function will throw an exception.
        """
        try:
            self._get_policy_rule_association_query(
                context, firewall_policy_id, firewall_rule_id).one()
            raise f_exc.FirewallRuleAlreadyAssociated(
                firewall_rule_id=firewall_rule_id,
                firewall_policy_id=firewall_policy_id)
        except exc.NoResultFound:
            return

    def _get_policy_rule_association(self, context, firewall_policy_id,
                                     firewall_rule_id):
        """Returns the association between a firewall rule and a firewall
        policy. Throws an exception if the assocaition does not exist.
        """
        try:
            return self._get_policy_rule_association_query(
                context, firewall_policy_id, firewall_rule_id).one()
        except exc.NoResultFound:
            raise f_exc.FirewallRuleNotAssociatedWithPolicy(
                firewall_rule_id=firewall_rule_id,
                firewall_policy_id=firewall_policy_id)

    def _create_default_firewall_rules(self, context, tenant_id):
        # NOTE(xgerman) Maybe generating the final set of rules from a
        # configuration file makes sense. Can be done some time later

        # 1. Drop any IPv4 packets for ingress traffic
        in_fwr_v4 = {'firewall_rule': {
            'description': 'default ingress rule for IPv4',
            'name': 'default ingress ipv4 (deny all)',
            'shared': False,
            'protocol': None,
            'tenant_id': tenant_id,
            'ip_version': nl_constants.IP_VERSION_4,
            'action': fw_const.FWAAS_DENY,
            'enabled': True,
            'source_port': None,
            'source_ip_address': None,
            'destination_port': None,
            'destination_ip_address': None,
        }}

        # 2. Drop any IPv6 packets for ingress traffic
        in_fwr_v6 = copy.deepcopy(in_fwr_v4)
        in_fwr_v6[
            'firewall_rule']['description'] = 'default ingress rule for IPv6'
        in_fwr_v6['firewall_rule']['name'] = 'default ingress ipv6 (deny all)'
        in_fwr_v6['firewall_rule']['ip_version'] = nl_constants.IP_VERSION_6

        # 3. Allow any IPv4 packets for egress traffic
        eg_fwr_v4 = copy.deepcopy(in_fwr_v4)
        eg_fwr_v4[
            'firewall_rule']['description'] = 'default egress rule for IPv4'
        eg_fwr_v4['firewall_rule']['action'] = fw_const.FWAAS_ALLOW
        eg_fwr_v4['firewall_rule']['name'] = 'default egress ipv4 (allow all)'

        # 4. Allow any IPv6 packets for egress traffic
        eg_fwr_v6 = copy.deepcopy(in_fwr_v6)
        eg_fwr_v6[
            'firewall_rule']['description'] = 'default egress rule for IPv6'
        eg_fwr_v6['firewall_rule']['name'] = 'default egress ipv6 (allow all)'
        eg_fwr_v6['firewall_rule']['action'] = fw_const.FWAAS_ALLOW

        return {
            'in_ipv4': self.create_firewall_rule(context, in_fwr_v4)['id'],
            'in_ipv6': self.create_firewall_rule(context, in_fwr_v6)['id'],
            'eg_ipv4': self.create_firewall_rule(context, eg_fwr_v4)['id'],
            'eg_ipv6': self.create_firewall_rule(context, eg_fwr_v6)['id'],
        }

    def create_firewall_rule(self, context, firewall_rule):
        LOG.debug("create_firewall_rule() called")
        fwr = firewall_rule['firewall_rule']
        self._validate_fwr_protocol_parameters(fwr)
        self._validate_fwr_src_dst_ip_version(fwr)
        if not fwr['protocol'] and (fwr['source_port'] or
           fwr['destination_port']):
            raise f_exc.FirewallRuleWithPortWithoutProtocolInvalid()
        src_port_min, src_port_max = self._get_min_max_ports_from_range(
            fwr['source_port'])
        dst_port_min, dst_port_max = self._get_min_max_ports_from_range(
            fwr['destination_port'])
        with context.session.begin(subtransactions=True):
            fwr_db = FirewallRuleV2(
                id=uuidutils.generate_uuid(),
                tenant_id=fwr['tenant_id'],
                name=fwr['name'],
                description=fwr['description'],
                protocol=fwr['protocol'],
                ip_version=fwr['ip_version'],
                source_ip_address=fwr['source_ip_address'],
                destination_ip_address=fwr['destination_ip_address'],
                source_port_range_min=src_port_min,
                source_port_range_max=src_port_max,
                destination_port_range_min=dst_port_min,
                destination_port_range_max=dst_port_max,
                action=fwr['action'],
                enabled=fwr['enabled'],
                shared=fwr['shared'])
            context.session.add(fwr_db)
        return self._make_firewall_rule_dict(fwr_db)

    def update_firewall_rule(self, context, id, firewall_rule):
        LOG.debug("update_firewall_rule() called")
        fwr = firewall_rule['firewall_rule']
        fwr_db = self._get_firewall_rule(context, id)
        self._validate_fwr_protocol_parameters(fwr, fwr_db=fwr_db)
        self._validate_fwr_src_dst_ip_version(fwr, fwr_db=fwr_db)
        if 'source_port' in fwr:
            src_port_min, src_port_max = self._get_min_max_ports_from_range(
                fwr['source_port'])
            fwr['source_port_range_min'] = src_port_min
            fwr['source_port_range_max'] = src_port_max
            del fwr['source_port']
        if 'destination_port' in fwr:
            dst_port_min, dst_port_max = self._get_min_max_ports_from_range(
                fwr['destination_port'])
            fwr['destination_port_range_min'] = dst_port_min
            fwr['destination_port_range_max'] = dst_port_max
            del fwr['destination_port']
        with context.session.begin(subtransactions=True):
            protocol = fwr.get('protocol', fwr_db['protocol'])
            if not protocol:
                sport = fwr.get('source_port_range_min',
                                fwr_db['source_port_range_min'])
                dport = fwr.get('destination_port_range_min',
                                fwr_db['destination_port_range_min'])
                if sport or dport:
                    raise f_exc.FirewallRuleWithPortWithoutProtocolInvalid()
            fwr_db.update(fwr)
            # if the rule on a policy, fix audited flag
            fwp_ids = self._get_policies_with_rule(context, id)
            for fwp_id in fwp_ids:
                fwp_db = self._get_firewall_policy(context, fwp_id)
                fwp_db['audited'] = False
        return self._make_firewall_rule_dict(fwr_db)

    def delete_firewall_rule(self, context, id):
        LOG.debug("delete_firewall_rule() called")
        with context.session.begin(subtransactions=True):
            fwr = self._get_firewall_rule(context, id)
            # make sure rule is not associated with any policy
            if self._get_policies_with_rule(context, id):
                raise f_exc.FirewallRuleInUse(firewall_rule_id=id)
            context.session.delete(fwr)

    def insert_rule(self, context, id, rule_info):
        LOG.debug("insert_rule() called")
        self._validate_insert_remove_rule_request(rule_info)
        firewall_rule_id = rule_info['firewall_rule_id']
        # ensure rule is not already assigned to the policy
        self._ensure_rule_not_already_associated(context, id, firewall_rule_id)
        insert_before = True
        ref_firewall_rule_id = None
        if 'insert_before' in rule_info:
            ref_firewall_rule_id = rule_info['insert_before']
        if not ref_firewall_rule_id and 'insert_after' in rule_info:
            # If insert_before is set, we will ignore insert_after.
            ref_firewall_rule_id = rule_info['insert_after']
            insert_before = False
        with context.session.begin(subtransactions=True):
            fwr_db = self._get_firewall_rule(context, firewall_rule_id)
            fwp_db = self._get_firewall_policy(context, id)
            self._check_firewall_rule_conflict(fwr_db, fwp_db)
            if ref_firewall_rule_id:
                # If reference_firewall_rule_id is set, the new rule
                # is inserted depending on the value of insert_before.
                # If insert_before is set, the new rule is inserted before
                # reference_firewall_rule_id, and if it is not set the new
                # rule is inserted after reference_firewall_rule_id.
                fwpra_db = self._get_policy_rule_association(
                    context, id, ref_firewall_rule_id)
                if insert_before:
                    position = fwpra_db.position
                else:
                    position = fwpra_db.position + 1
            else:
                # If reference_firewall_rule_id is not set, it is assumed
                # that the new rule needs to be inserted at the top.
                # insert_before field is ignored.
                # So default insertion is always at the top.
                # Also note that position numbering starts at 1.
                position = 1
            return self._process_rule_for_policy(context, id, firewall_rule_id,
                                                 position, None)

    def remove_rule(self, context, id, rule_info):
        LOG.debug("remove_rule() called")
        self._validate_insert_remove_rule_request(rule_info)
        firewall_rule_id = rule_info['firewall_rule_id']
        with context.session.begin(subtransactions=True):
            self._get_firewall_rule(context, firewall_rule_id)
            fwpra_db = self._get_policy_rule_association(context, id,
                                                         firewall_rule_id)
            return self._process_rule_for_policy(context, id, firewall_rule_id,
                                                 None, fwpra_db)

    def get_firewall_rule(self, context, id, fields=None):
        LOG.debug("get_firewall_rule() called")
        fwr = self._get_firewall_rule(context, id)
        policies = self._get_policies_with_rule(context, id) or None
        return self._make_firewall_rule_dict(fwr, fields, policies=policies)

    def get_firewall_rules(self, context, filters=None, fields=None):
        LOG.debug("get_firewall_rules() called")
        return self._get_collection(context, FirewallRuleV2,
                                    self._make_firewall_rule_dict,
                                    filters=filters, fields=fields)

    def _validate_insert_remove_rule_request(self, rule_info):
        """Validate rule_info dict

        Check that all mandatory fields are present, otherwise raise
        proper exception.
        """
        if not rule_info or 'firewall_rule_id' not in rule_info:
            raise f_exc.FirewallRuleInfoMissing()
        # Validator doesn't return anything if the check passes
        if validators.validate_uuid(rule_info['firewall_rule_id']):
            raise f_exc.FirewallRuleNotFound(
                firewall_rule_id=rule_info['firewall_rule_id'])

    def _get_rules_in_policy(self, context, fwpid):
        """Gets rules in a firewall policy"""
        with context.session.begin(subtransactions=True):
            fw_pol_rule_qry = context.session.query(
                FirewallPolicyRuleAssociation).filter_by(
                firewall_policy_id=fwpid)
            fwp_rules = [entry.firewall_rule_id for entry in fw_pol_rule_qry]
        return fwp_rules

    def _get_policies_with_rule(self, context, fwrid):
        """Gets rules in a firewall policy"""
        with context.session.begin(subtransactions=True):
            fw_pol_rule_qry = context.session.query(
                FirewallPolicyRuleAssociation).filter_by(
                firewall_rule_id=fwrid)
            fwps = [entry.firewall_policy_id for entry in fw_pol_rule_qry]
        return fwps

    def _set_rules_in_policy_rule_assoc(self, context, fwp_db, fwp):
        # Pull the rules and add it to policy - rule association table
        # Set the position (this can be used in the making the dict)
        # might be good to track the last position
        rule_id_list = fwp['firewall_rules']
        if not rule_id_list:
            return
        position = 0
        with context.session.begin(subtransactions=True):
            for rule_id in rule_id_list:
                fw_pol_rul_db = FirewallPolicyRuleAssociation(
                    firewall_policy_id=fwp_db['id'],
                    firewall_rule_id=rule_id,
                    position=position)
                context.session.add(fw_pol_rul_db)
                position += 1

    def _check_rules_for_policy_is_valid(self, context, fwp, fwp_db,
                                         rule_id_list, filters):
        rules_in_fwr_db = self._get_collection_query(context, FirewallRuleV2,
                                                 filters=filters)
        rules_dict = dict((fwr_db['id'], fwr_db) for fwr_db in rules_in_fwr_db)
        for fwrule_id in rule_id_list:
            if fwrule_id not in rules_dict:
                # Bail as soon as we find an invalid rule.
                raise f_exc.FirewallRuleNotFound(
                    firewall_rule_id=fwrule_id)
            if 'shared' in fwp:
                if fwp['shared'] and not rules_dict[fwrule_id]['shared']:
                    raise f_exc.FirewallRuleSharingConflict(
                        firewall_rule_id=fwrule_id,
                        firewall_policy_id=fwp_db['id'])
            elif fwp_db['shared'] and not rules_dict[fwrule_id]['shared']:
                raise f_exc.FirewallRuleSharingConflict(
                    firewall_rule_id=fwrule_id,
                    firewall_policy_id=fwp_db['id'])
            else:
                # the policy is not shared, the rule and policy should be in
                # the same project if the rule is not shared.
                if not rules_dict[fwrule_id]['shared']:
                    if (rules_dict[fwrule_id]['tenant_id'] != fwp_db[
                            'tenant_id']):
                        raise f_exc.FirewallRuleConflict(
                            firewall_rule_id=fwrule_id,
                            project_id=rules_dict[fwrule_id]['tenant_id'])

    def _check_if_rules_shared_for_policy_shared(self, context, fwp_db, fwp):
        if fwp['shared']:
            rules_in_db = fwp_db.rule_associations
            for entry in rules_in_db:
                fwr_db = self._get_firewall_rule(context,
                                                 entry.firewall_rule_id)
                if not fwp_db['shared']:
                    raise f_exc.FirewallPolicySharingConflict(
                        firewall_rule_id=fwr_db['id'],
                        firewall_policy_id=fwp_db['id'])

    def _get_fwgs_with_policy(self, context, fwp_id):
        with context.session.begin(subtransactions=True):
            fwg_ing_pol_qry = context.session.query(
                FirewallGroup).filter_by(
                ingress_firewall_policy_id=fwp_id)
            ing_fwg_ids = [entry.id for entry in fwg_ing_pol_qry]
            fwg_eg_pol_qry = context.session.query(
                FirewallGroup).filter_by(
                egress_firewall_policy_id=fwp_id)
            eg_fwg_ids = [entry.id for entry in fwg_eg_pol_qry]
        return ing_fwg_ids, eg_fwg_ids

    def _check_fwgs_associated_with_policy_in_same_project(self, context,
                                                           fwp_id,
                                                           fwp_tenant_id):
        with context.session.begin(subtransactions=True):
            fwg_with_fwp_id_db = context.session.query(FirewallGroup).filter(
                or_(FirewallGroup.ingress_firewall_policy_id == fwp_id,
                FirewallGroup.egress_firewall_policy_id == fwp_id))
        for entry in fwg_with_fwp_id_db:
            if entry.tenant_id != fwp_tenant_id:
                raise f_exc.FirewallPolicyInUse(
                            firewall_policy_id=fwp_id)

    def _delete_all_rules_from_policy(self, context, fwp_db):
        """Deletes all FirewallPolicyRuleAssociation objects

        fwp_db is an DB dict representing firewall policy.
        Returns a dictionary with updated rule_associations.
        """
        for rule_id in [rule_assoc.firewall_rule_id
                        for rule_assoc in fwp_db['rule_associations']]:
            fwpra_db = self._get_policy_rule_association(
                context, fwp_db['id'], rule_id)
            fwp_db.rule_associations.remove(fwpra_db)
            context.session.delete(fwpra_db)
        fwp_db.rule_associations = []
        return fwp_db

    def _set_rules_for_policy(self, context, firewall_policy_db, fwp):
        rule_id_list = fwp['firewall_rules']
        fwp_db = firewall_policy_db
        with context.session.begin(subtransactions=True):
            if not rule_id_list:
                self._delete_all_rules_from_policy(context, fwp_db)
                return
            # We will first check if the new list of rules is valid
            filters = {'firewall_rule_id': [r_id for r_id in rule_id_list]}
            # Run a validation on the Firewall Rules table
            self._check_rules_for_policy_is_valid(context, fwp, fwp_db,
                rule_id_list, filters)
            # new rules are valid, lets delete the old association
            self._delete_all_rules_from_policy(context, fwp_db)
            # and add in the new association
            self._set_rules_in_policy_rule_assoc(context, fwp_db, fwp)
            # we need care about the associations related with this policy
            # and its rules only.
            filters['firewall_policy_id'] = [fwp_db['id']]
            rules_in_fpol_rul_db = self._get_collection_query(
                context,
                FirewallPolicyRuleAssociation,
                filters=filters)
            rules_dict = dict((fpol_rul_db['firewall_rule_id'], fpol_rul_db)
                             for fpol_rul_db in rules_in_fpol_rul_db)
            fwp_db.rule_associations = []
            for fwrule_id in rule_id_list:
                fwp_db.rule_associations.append(rules_dict[fwrule_id])
            fwp_db.rule_associations.reorder()

    def _create_default_firewall_policy(self, context, tenant_id, policy_type,
                                        **kwargs):
        fwrs = kwargs.get('firewall_rules', [])
        description = kwargs.get('description', '')
        name = (const.DEFAULT_FWP_INGRESS
                if policy_type == 'ingress' else const.DEFAULT_FWP_EGRESS)
        firewall_policy = {'firewall_policy': {
            'name': name,
            'description': description,
            'audited': False,
            'shared': False,
            'firewall_rules': fwrs,
            'tenant_id': tenant_id,
        }}
        return self._do_create_firewall_policy(context, firewall_policy)

    def _do_create_firewall_policy(self, context, firewall_policy):
        fwp = firewall_policy['firewall_policy']
        with context.session.begin(subtransactions=True):
            fwp_db = FirewallPolicy(
                id=uuidutils.generate_uuid(),
                tenant_id=fwp['tenant_id'],
                name=fwp['name'],
                description=fwp['description'],
                audited=fwp['audited'],
                shared=fwp['shared'])
            context.session.add(fwp_db)
            self._set_rules_for_policy(context, fwp_db, fwp)
        return self._make_firewall_policy_dict(fwp_db)

    def create_firewall_policy(self, context, firewall_policy):
        LOG.debug("create_firewall_policy() called")
        self._ensure_not_default_resource(firewall_policy, 'firewall_policy')
        return self._do_create_firewall_policy(context, firewall_policy)

    def update_firewall_policy(self, context, id, firewall_policy):
        LOG.debug("update_firewall_policy() called")
        fwp = firewall_policy['firewall_policy']
        with context.session.begin(subtransactions=True):
            fwp_db = self._get_firewall_policy(context, id)
            self._ensure_not_default_resource(fwp_db, 'firewall_policy',
                                         action="update")
            if not fwp.get('shared', True):
                # an update is setting shared to False, make sure associated
                # firewall groups are in the same project.
                self._check_fwgs_associated_with_policy_in_same_project(
                    context, id, fwp_db['tenant_id'])
            if 'shared' in fwp and 'firewall_rules' not in fwp:
                self._check_if_rules_shared_for_policy_shared(
                    context, fwp_db, fwp)
            if 'firewall_rules' in fwp:
                self._set_rules_for_policy(context, fwp_db, fwp)
                del fwp['firewall_rules']
            if 'audited' not in fwp:
                fwp['audited'] = False
            fwp_db.update(fwp)
        return self._make_firewall_policy_dict(fwp_db)

    def delete_firewall_policy(self, context, id):
        LOG.debug("delete_firewall_policy() called")
        with context.session.begin(subtransactions=True):
            fwp_db = self._get_firewall_policy(context, id)
            # check if policy in use
            qry = context.session.query(FirewallGroup)
            if qry.filter_by(ingress_firewall_policy_id=id).first():
                raise f_exc.FirewallPolicyInUse(firewall_policy_id=id)
            elif qry.filter_by(egress_firewall_policy_id=id).first():
                raise f_exc.FirewallPolicyInUse(firewall_policy_id=id)
            else:
                fwp_db = self._delete_all_rules_from_policy(context, fwp_db)
                context.session.delete(fwp_db)

    def get_firewall_policy(self, context, id, fields=None):
        LOG.debug("get_firewall_policy() called")
        fwp = self._get_firewall_policy(context, id)
        return self._make_firewall_policy_dict(fwp, fields)

    def get_firewall_policies(self, context, filters=None, fields=None):
        LOG.debug("get_firewall_policies() called")
        return self._get_collection(context, FirewallPolicy,
                                    self._make_firewall_policy_dict,
                                    filters=filters, fields=fields)

    def _validate_tenant_for_fwg_policies(self, context, fwg, fwg_tenant_id):
        # On updates, all keys will not be present so fetch and validate.
        if 'ingress_firewall_policy_id' in fwg:
            fwp_id = fwg['ingress_firewall_policy_id']
            if fwp_id is not None:
                fwp = self._get_firewall_policy(context, fwp_id)
                if fwg_tenant_id != fwp['tenant_id'] and not fwp['shared']:
                    raise f_exc.FirewallPolicyConflict(
                        firewall_policy_id=fwp_id)

        if 'egress_firewall_policy_id' in fwg:
            fwp_id = fwg['egress_firewall_policy_id']
            if fwp_id is not None:
                fwp = self._get_firewall_policy(context, fwp_id)
                if fwg_tenant_id != fwp['tenant_id'] and not fwp['shared']:
                    raise f_exc.FirewallPolicyConflict(
                        firewall_policy_id=fwp_id)
        return

    def _set_ports_for_firewall_group(self, context, fwg_db, fwg):
        port_id_list = fwg['ports']
        if not port_id_list:
            return

        exc_ports = []
        for port_id in port_id_list:
            try:
                with context.session.begin(subtransactions=True):
                    fwg_port_db = FirewallGroupPortAssociation(
                        firewall_group_id=fwg_db['id'],
                        port_id=port_id)
                    context.session.add(fwg_port_db)
            except db_exc.DBDuplicateEntry:
                exc_ports.append(port_id)
        if exc_ports:
            raise f_exc.FirewallGroupPortInUse(port_ids=exc_ports)

    def _get_ports_in_firewall_group(self, context, firewall_group_id):
        """Get the Ports associated with the  firewall group."""
        with context.session.begin(subtransactions=True):
            fw_group_port_qry = context.session.query(
                FirewallGroupPortAssociation)
            fw_group_port_rows = fw_group_port_qry.filter_by(
                firewall_group_id=firewall_group_id)
            fw_ports = [entry.port_id for entry in fw_group_port_rows]
        return fw_ports

    def _delete_ports_in_firewall_group(self, context, firewall_group_id):
        """Delete the Ports associated with the  firewall group."""
        with context.session.begin(subtransactions=True):
            fw_group_port_qry = context.session.query(
                FirewallGroupPortAssociation)
            fw_group_port_qry.filter_by(
                firewall_group_id=firewall_group_id).delete()
        return

    def _validate_if_firewall_group_on_ports(
            self, context, port_ids, fwg_id=None):
        """Validate if ports are not associated with any firewall_group.
        If any of the ports in the list is already associated with
        a firewall_group, raise an exception else just return.
        """
        fwg_port_qry = context.session.query(
            FirewallGroupPortAssociation.port_id)
        fwg_ports = fwg_port_qry.filter(
            FirewallGroupPortAssociation.port_id.in_(port_ids),
            FirewallGroupPortAssociation.firewall_group_id != fwg_id).all()
        if fwg_ports:
            port_ids = [entry.port_id for entry in fwg_ports]
            raise f_exc.FirewallGroupPortInUse(port_ids=port_ids)

    def _get_default_fwg_id(self, context, tenant_id):
        """Returns an id of default firewall group for given tenant or None"""
        default_fwg = self._model_query(context, FirewallGroup).filter_by(
            project_id=tenant_id, name=const.DEFAULT_FWG).first()
        if default_fwg:
            return default_fwg.id

    def _ensure_default_firewall_group(self, context, tenant_id):
        """Create a default firewall group if one doesn't exist for a tenant

        Returns the default firewall group id for a given tenant.
        """
        exists = self._get_default_fwg_id(context, tenant_id)
        if exists:
            return exists

        try:
            # NOTE(cby): default fwg not created => we try to create it!
            with db_api.autonested_transaction(context.session):

                fwr_ids = self._create_default_firewall_rules(
                    context, tenant_id)
                ingress_fwp = {
                    'description': 'Ingress firewall policy',
                    'firewall_rules': [fwr_ids['in_ipv4'],
                                       fwr_ids['in_ipv6']],
                }
                egress_fwp = {
                    'description': 'Egress firewall policy',
                    'firewall_rules': [fwr_ids['eg_ipv4'],
                                       fwr_ids['eg_ipv6']],
                }
                ingress_fwp_db = self._create_default_firewall_policy(
                    context, tenant_id, 'ingress', **ingress_fwp)
                egress_fwp_db = self._create_default_firewall_policy(
                    context, tenant_id, 'egress', **egress_fwp)

                fwg = {
                    'firewall_group':
                        {'name': const.DEFAULT_FWG,
                         'tenant_id': tenant_id,
                         'ingress_firewall_policy_id': ingress_fwp_db['id'],
                         'egress_firewall_policy_id': egress_fwp_db['id'],
                         'ports': [],
                         'shared': False,
                         'admin_state_up': True,
                         'description': 'Default firewall group'}
                }
                fwg_db = self._create_firewall_group(
                    context, fwg, status=nl_constants.INACTIVE,
                    default_fwg=True)
                context.session.add(DefaultFirewallGroup(
                    firewall_group_id=fwg_db['id'],
                    project_id=tenant_id))
                return fwg_db['id']

        except db_exc.DBDuplicateEntry:
            # NOTE(cby): default fwg created concurrently
            LOG.debug("Default FWG was concurrently created")
            return self._get_default_fwg_id(context, tenant_id)

    def _create_firewall_group(self, context, firewall_group, status=None,
                               default_fwg=False):
        """Create a firewall group

        If default_fwg is True then a default firewall group is being created
        for a given tenant.
        """
        fwg = firewall_group['firewall_group']
        tenant_id = fwg['tenant_id']
        if not status:
            status = (nl_constants.CREATED if cfg.CONF.router_distributed
                      else nl_constants.PENDING_CREATE)
        if default_fwg:
            # A default firewall group is being created.
            default_fwg_id = self._get_default_fwg_id(context, tenant_id)
            if default_fwg_id is not None:
                # Default fwg for a given tenant exists, fetch it and return
                return self.get_firewall_group(default_fwg_id)
        else:
            # An ordinary firewall group is being created BUT let's make sure
            # that a default firewall group for given tenant exists
            self._ensure_default_firewall_group(context, tenant_id)

        self._validate_tenant_for_fwg_policies(context, fwg, tenant_id)
        with context.session.begin(subtransactions=True):
            fwg_db = FirewallGroup(
                id=uuidutils.generate_uuid(),
                tenant_id=tenant_id,
                name=fwg['name'],
                description=fwg['description'],
                status=status,
                ingress_firewall_policy_id=fwg['ingress_firewall_policy_id'],
                egress_firewall_policy_id=fwg['egress_firewall_policy_id'],
                admin_state_up=fwg['admin_state_up'],
                shared=fwg['shared'])
            context.session.add(fwg_db)
            self._set_ports_for_firewall_group(context, fwg_db, fwg)
        return self._make_firewall_group_dict(fwg_db)

    def create_firewall_group(self, context, firewall_group, status=None):
        self._ensure_not_default_resource(firewall_group, 'firewall_group')
        return self._create_firewall_group(context, firewall_group, status)

    def update_firewall_group(self, context, id, firewall_group):
        LOG.debug("update_firewall() called")
        fwg = firewall_group['firewall_group']
        # make sure that no group can be updated to have name=default
        self._ensure_not_default_resource(firewall_group, 'firewall_group')
        with context.session.begin(subtransactions=True):
            fwg_db = self.get_firewall_group(context, id)
            # XXX NOTE(ivasilevskaya) because delete_firewall_group method in
            # plugin calls DB's update_firewall_group when fw has rules
            # the check above is necessary for admin to be able to delete
            # default group. It's clumsy and should be improved I believe
            if (not context.is_admin or
                    fwg.get("status", "") != nl_constants.PENDING_DELETE):
                self._ensure_not_default_resource(fwg_db,
                                             'firewall_group', action="update")
            self._validate_tenant_for_fwg_policies(context,
                                                   fwg, fwg_db['tenant_id'])
            if 'ports' in fwg:
                LOG.debug("Ports are updated in Firewall Group")
                self._delete_ports_in_firewall_group(context, id)
                self._set_ports_for_firewall_group(context, fwg_db, fwg)
                del fwg['ports']
            count = context.session.query(
                FirewallGroup).filter_by(id=id).update(fwg)
            if not count:
                raise f_exc.FirewallGroupNotFound(firewall_id=id)
        return self.get_firewall_group(context, id)

    def update_firewall_group_status(self, context, id, status, not_in=None):
        """Conditionally update firewall_group status.
        Status transition is performed only if firewall is not in the specified
        states as defined by 'not_in' list.
        """
        # filter in_ wants iterable objects, None isn't.
        not_in = not_in or []
        with context.session.begin(subtransactions=True):
            return (context.session.query(FirewallGroup).
                    filter(FirewallGroup.id == id).
                    filter(~FirewallGroup.status.in_(not_in)).
                    update({'status': status}, synchronize_session=False))

    def delete_firewall_group(self, context, id):
        # Note: Plugin should ensure that it's okay to delete if the
        # firewall is active
        LOG.debug("delete_firewall_group() called")

        def _is_default(fwg_db):
            return fwg_db['name'] == const.DEFAULT_FWG

        with context.session.begin(subtransactions=True):
            # if no such group exists -> don't raise an exception according to
            # 80fe2ba1, return None
            try:
                fwg_db = self._get_firewall_group(context, id)
            except f_exc.FirewallGroupNotFound:
                return

            if _is_default(fwg_db):
                if context.is_admin:
                    # Like Rules in Default SG, when the Default FWG is deleted
                    # its associated Rules and policies would also be deleted.
                    # Delete fwg first and then associated policies
                    context.session.query(
                        FirewallGroup).filter_by(id=id).delete()
                    fwp = [fwg_db['ingress_firewall_policy_id'],
                           fwg_db['egress_firewall_policy_id']]
                    for fwp_id in fwp:
                        self.delete_firewall_policy(context, fwp_id)
                else:
                    # only admin can delete default fwg
                    raise f_exc.FirewallGroupCannotRemoveDefault()
            else:
                context.session.query(
                    FirewallGroup).filter_by(id=id).delete()

    def _ensure_not_default_resource(self, resource_dict, r_type, action=None):
        """Checks that a resource is not default by checking its name

        A resource_dict can be either a dictionary in form {r_type : {}} or a
        serialized object from db.

        Action is used to determine type of exception to be raised.
        """
        resource = resource_dict.get(r_type) or resource_dict
        if r_type == 'firewall_group':
            if resource.get('name', '') == const.DEFAULT_FWG:
                if action == "update":
                    raise FirewallDefaultObjectUpdateRestricted(
                        resource_type='Firewall Group',
                        resource_id=resource['id'])
                raise FirewallDefaultParameterExists(
                    resource_type='Firewall Group', name=resource['name'])
        elif r_type == 'firewall_policy':
            if resource.get('name', '') in [const.DEFAULT_FWP_INGRESS,
                                            const.DEFAULT_FWP_EGRESS]:
                if action == "update":
                    raise FirewallDefaultObjectUpdateRestricted(
                        resource_type='Firewall Group',
                        resource_id=resource['id'])
                raise FirewallDefaultParameterExists(
                    resource_type='Firewall Policy', name=resource['name'])

    def get_firewall_group(self, context, id, fields=None):
        LOG.debug("get_firewall_group() called")
        fw = self._get_firewall_group(context, id)
        return self._make_firewall_group_dict(fw, fields)

    def get_firewall_groups(self, context, filters=None, fields=None):
        LOG.debug("get_firewall_groups() called")
        if context.tenant_id:
            tenant_id = filters.get('tenant_id') if filters else None
            tenant_id = tenant_id[0] if tenant_id else context.tenant_id
            self._ensure_default_firewall_group(context, tenant_id)
        return self._get_collection(context, FirewallGroup,
                                    self._make_firewall_group_dict,
                                    filters=filters, fields=fields)

    def get_firewall_group_for_port(self, context, port_id):
        """Get firewall group is associated with a port

        :param context: context object
        :param port_id: Port ID.
        """
        filters = {'port_id': [port_id]}
        fwg_port_binding = self._get_collection_query(
            context, FirewallGroupPortAssociation, filters=filters).first()
        if fwg_port_binding:
            fwg_id = fwg_port_binding['firewall_group_id']
            return self._make_firewall_group_dict_with_rules(context, fwg_id)
