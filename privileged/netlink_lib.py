# Copyright (c) 2017 Fujitsu Limited
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
#
# Some parts are based on python-conntrack:
# Copyright (c) 2009-2011,2015 Andrew Grigorev <andrew@ei-grad.ru>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#

import ctypes
from ctypes import util

from oslo_log import log as logging

from neutron_lib import constants

from neutron_fwaas import privileged
from neutron_fwaas.privileged import netlink_constants as nl_constants
from neutron_fwaas.privileged import utils as fwaas_utils

LOG = logging.getLogger(__name__)

nfct = ctypes.CDLL(util.find_library('netfilter_conntrack'))
libc = ctypes.CDLL(util.find_library('libc.so.6'))

IP_VERSIONS = [constants.IP_VERSION_4, constants.IP_VERSION_6]
DATA_CALLBACK = None

ATTR_POSITIONS = {
    'icmp': [('type', 6), ('code', 7), ('src', 4), ('dst', 5), ('id', 8)],
    'tcp': [('sport', 7), ('dport', 8), ('src', 5), ('dst', 6)],
    'udp': [('sport', 6), ('dport', 7), ('src', 4), ('dst', 5)]
}

TARGET = {'src': {4: nl_constants.ATTR_IPV4_SRC,
                  6: nl_constants.ATTR_IPV6_SRC},
          'dst': {4: nl_constants.ATTR_IPV4_DST,
                  6: nl_constants.ATTR_IPV6_DST},
          'ipversion': {4: nl_constants.ATTR_L3PROTO,
                        6: nl_constants.ATTR_L3PROTO},
          'protocol': {4: nl_constants.ATTR_L4PROTO,
                       6: nl_constants.ATTR_L4PROTO},
          'code': {4: nl_constants.ATTR_ICMP_CODE,
                   6: nl_constants.ATTR_ICMP_CODE},
          'type': {4: nl_constants.ATTR_ICMP_TYPE,
                   6: nl_constants.ATTR_ICMP_TYPE},
          'id': {4: nl_constants.ATTR_ICMP_ID,
                 6: nl_constants.ATTR_ICMP_ID},
          'sport': {4: nl_constants.ATTR_PORT_SRC,
                    6: nl_constants.ATTR_PORT_SRC},
          'dport': {4: nl_constants.ATTR_PORT_DST,
                    6: nl_constants.ATTR_PORT_DST}}

NFCT_CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int,
                                 ctypes.c_void_p, ctypes.c_void_p)


class ConntrackOpenFailedExit(SystemExit):
    """Raised if we fail to open a new conntrack or conntrack handler"""


class ConntrackManager(object):
    def __init__(self, family_socket=None):
        self.family_socket = family_socket
        self.set_functions = {
            'src': {4: nfct.nfct_set_attr_u32,
                    6: nfct.nfct_set_attr_u64},
            'dst': {4: nfct.nfct_set_attr_u32,
                    6: nfct.nfct_set_attr_u64},
            'ipversion': {4: nfct.nfct_set_attr_u8,
                          6: nfct.nfct_set_attr_u8},
            'protocol': {4: nfct.nfct_set_attr_u8,
                         6: nfct.nfct_set_attr_u8},
            'type': {4: nfct.nfct_set_attr_u8,
                     6: nfct.nfct_set_attr_u8},
            'code': {4: nfct.nfct_set_attr_u8,
                     6: nfct.nfct_set_attr_u8},
            'id': {4: nfct.nfct_set_attr_u16,
                   6: nfct.nfct_set_attr_u16},
            'sport': {4: nfct.nfct_set_attr_u16,
                      6: nfct.nfct_set_attr_u16},
            'dport': {4: nfct.nfct_set_attr_u16,
                      6: nfct.nfct_set_attr_u16}, }
        self.converters = {'src': libc.inet_addr,
                      'dst': libc.inet_addr,
                      'ipversion': nl_constants.IPVERSION_SOCKET.get,
                      'protocol': constants.IP_PROTOCOL_MAP.get,
                      'code': int,
                      'type': int,
                      'id': libc.htons,
                      'sport': libc.htons,
                      'dport': libc.htons, }

    def list_entries(self):
        entries = []
        raw_entry = ctypes.create_string_buffer(nl_constants.BUFFER)

        @NFCT_CALLBACK
        def callback(type_, conntrack, data):
            nfct.nfct_snprintf(raw_entry, nl_constants.BUFFER,
                               conntrack, type_,
                               nl_constants.NFCT_O_PLAIN,
                               nl_constants.NFCT_OF_TIME)
            entries.append(raw_entry.value)
            return nl_constants.NFCT_CB_CONTINUE

        self._callback_register(nl_constants.NFCT_T_ALL,
                                callback, DATA_CALLBACK)

        data_ref = self._get_ref(self.family_socket or
                                 nl_constants.IPVERSION_SOCKET[4])
        self._query(nl_constants.NFCT_Q_DUMP, data_ref)
        return entries

    def delete_entries(self, entries):
        conntrack = nfct.nfct_new()
        try:
            for entry in entries:
                self._set_attributes(conntrack, entry)
                self._query(nl_constants.NFCT_Q_DESTROY, conntrack)
        except Exception as e:
            msg = "Failed to delete conntrack entries %s" % e
            LOG.critical(msg)
            raise ConntrackOpenFailedExit(msg)
        finally:
            nfct.nfct_destroy(conntrack)

    def flush_entries(self):
        data_ref = self._get_ref(self.family_socket or
                                 nl_constants.IPVERSION_SOCKET[4])
        self._query(nl_constants.NFCT_Q_FLUSH, data_ref)

    def _query(self, query_type, query_data):
        result = nfct.nfct_query(self.conntrack_handler, query_type,
                                 query_data)
        if result == nl_constants.NFCT_CB_FAILURE:
            LOG.warning("Netlink query failed")

    def _set_attributes(self, conntrack, entry):
        ipversion = entry.get('ipversion', 4)
        for attr, value in entry.items():
            set_function = self.set_functions[attr][ipversion]
            target = TARGET[attr][ipversion]
            converter = self.converters[attr]
            set_function(conntrack, target, converter(value))

    def _callback_register(self, message_type, callback_func, data):
        nfct.nfct_callback_register(self.conntrack_handler,
                                    message_type, callback_func, data)

    def _get_ref(self, data):
        return ctypes.byref(ctypes.c_int(data))

    def __enter__(self):
        self.conntrack_handler = nfct.nfct_open(
                nl_constants.CONNTRACK,
                nl_constants.NFNL_SUBSYS_CTNETLINK)
        if not self.conntrack_handler:
            msg = "Failed to open new conntrack handler"
            LOG.critical(msg)
            raise ConntrackOpenFailedExit(msg)
        return self

    def __exit__(self, *args):
        nfct.nfct_close(self.conntrack_handler)


def _parse_entry(entry, ipversion):
    """Parse entry from text to Python tuple

    :param entry: conntrack entry in text
    :param ipversion: ipversion 4 or 6
    :return: conntrack entry in Python tuple
    example: (4, 'tcp', '1', '2', '1.1.1.1', '2.2.2.2')
    The attributes are ordered to be easy to compare with other entries
    and compare with firewall rule
    """
    protocol = entry[1]
    parsed_entry = [ipversion, protocol]
    for attr, position in ATTR_POSITIONS[protocol]:
        val = entry[position].partition('=')[2]
        parsed_entry.append(int(val) if attr in ['sport', 'dport', 'type',
                                                 'code', 'id'] else val)
    return tuple(parsed_entry)


@privileged.default.entrypoint
def flush_entries(namespace=None):
    """Delete all conntrack entries

    :param namespace: namespace to delete conntrack entries
    :return: None
    """
    with fwaas_utils.in_namespace(namespace):
        for ipversion in IP_VERSIONS:
            with ConntrackManager(nl_constants.IPVERSION_SOCKET[ipversion]) \
                    as conntrack:
                conntrack.flush_entries()


@privileged.default.entrypoint
def list_entries(namespace=None):
    """List and parse all conntrack entries

    :param namespace: namespace to get conntrack entries
    :return: sorted list of conntrack entries in Python tuple
    example: [(4, 'icmp', '8', '0', '1.1.1.1', '2.2.2.2', '1234'),
              (4, 'tcp', '1', '2', '1.1.1.1', '2.2.2.2')]
    """
    parsed_entries = []
    with fwaas_utils.in_namespace(namespace):
        for ipversion in IP_VERSIONS:
            with ConntrackManager(nl_constants.IPVERSION_SOCKET[ipversion]) \
                    as conntrack:
                raw_entries = conntrack.list_entries()
            for raw_entry in raw_entries:
                parsed_entry = _parse_entry(raw_entry.split(), ipversion)
                parsed_entries.append(parsed_entry)
    return sorted(parsed_entries)


@privileged.default.entrypoint
def delete_entries(entries, namespace=None):
    """Delete selected entries

    :param entries: list of parsed (as tuple) entries to delete
    :param namespace: namespace to delete conntrack entries
    :return: None
    """
    entry_args = []
    for entry in entries:
        entry_arg = {'ipversion': entry[0], 'protocol': entry[1]}
        for idx, attr in enumerate(ATTR_POSITIONS[entry_arg['protocol']]):
            entry_arg[attr[0]] = entry[idx + 2]
        entry_args.append(entry_arg)

    with fwaas_utils.in_namespace(namespace):
        with ConntrackManager() as conntrack:
            conntrack.delete_entries(entry_args)
