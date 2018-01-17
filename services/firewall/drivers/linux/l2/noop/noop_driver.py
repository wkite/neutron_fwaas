# Copyright (C) 2017 Fujitsu Limited
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from oslo_log import helpers as log_helpers

from neutron_fwaas.services.firewall.drivers.linux.l2 import driver_base


class NoopFirewallL2Driver(driver_base.FirewallL2DriverBase):

    @log_helpers.log_method_call
    def create_firewall_group(self, ports, firewall_group):
        pass

    @log_helpers.log_method_call
    def update_firewall_group(self, ports, firewall_group):
        pass

    @log_helpers.log_method_call
    def delete_firewall_group(self, ports, firewall_group):
        pass
