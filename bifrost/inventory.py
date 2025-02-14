#!/usr/bin/env python
#
# Copyright (c) 2015 Hewlett-Packard Development Company, L.P.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

DOCUMENTATION = '''
Bifrost Inventory Module
========================

This is a dynamic inventory module intended to provide a platform for
consistent inventory information for Bifrost.

The inventory currently supplies two distinct groups:

    - localhost
    - baremetal

The localhost group is required for Bifrost to perform local actions to
bifrost for local actions such as installing Ironic.

The baremetal group contains the hosts defined by the data source along with
variables extracted from the data source. The variables are defined on a
per-host level which allows explicit actions to be taken based upon the
variables.

Presently, the base mode of operation reads a CSV file in the format
originally utilized by bifrost and returns structured JSON that is
interpreted by Ansible.  This has since been extended to support the
parsing of JSON and YAML data if they are detected in the file.

Conceivably, this inventory module can be extended to allow for direct
processing of inventory data from other data sources such as a configuration
management database or other inventory data source to provide a consistent
user experience.

How to use?
-----------

    export BIFROST_INVENTORY_SOURCE=/tmp/baremetal.[csv|json|yaml]
    ansible-playbook playbook.yaml -i inventory/bifrost_inventory.py

One can also just directly invoke bifrost_inventory.py in order to see the
resulting JSON output.  This module also has a feature to support the
pass-through of a pre-existing JSON document, which receives updates and
formatting to be supplied to Ansible.  Ultimately the use of JSON will be
far more flexible and should be the preferred path forward.

Example JSON Element:

{
  "node1": {
    "uuid": "a8cb6624-0d9f-c882-affc-046ebb96ec01",
    "driver_info": {
      "power": {
        "ipmi_target_channel": "0",
        "ipmi_username": "ADMIN",
        "ipmi_address": "192.168.122.1",
        "ipmi_target_address": "0",
        "ipmi_password": "undefined",
        "ipmi_bridging": "single"
      }
    },
    "nics": [
      {
        "mac": "00:01:02:03:04:05"
      }.
      {
        "mac": "00:01:02:03:04:06"
      }
   ],
   "driver": "agent_ipmitool",
   "ipv4_address": "192.168.122.2",
   "properties": {
      "cpu_arch": "x86_64",
      "ram": "3072",
      "disk_size": "10",
      "cpus": "1"
    },
    "name": "node1"
  }
}

Utilizing ironic as the data source
-----------------------------------

The functionality exists to allow a user to query an existing ironic
installation for the inventory data.  This is an advanced feature,
as the node may not have sufficient information to allow for node
deployment or automated testing, unless DHCP reservations are used.

This setting can be invoked by setting the source to "ironic"::

  export BIFROST_INVENTORY_SOURCE=ironic

Known Issues
------------

At present, this module only supports inventory list mode and is not
intended to support specific host queries.
'''

import csv
import json
import os
import sys
import yaml

from oslo_config import cfg
from oslo_log import log

try:
    import shade
    SHADE_LOADED = True
except ImportError:
    SHADE_LOADED = False

LOG = log.getLogger(__name__)

opts = [
    cfg.BoolOpt('list',
                default=True,
                help='List active hosts'),
    cfg.BoolOpt('convertcsv',
                default=False,
                help='Converts a CSV inventory to JSON'),
]


def _parse_config():
    config = cfg.ConfigOpts()
    log.register_options(config)
    config.register_cli_opts(opts)
    config(prog='bifrost_inventory.py')
    log.set_defaults()
    log.setup(config, "bifrost_inventory.py")
    return config


def _prepare_inventory():
    hostvars = {}
    groups = {}
    groups.update({'baremetal': {'hosts': []}})
    groups.update({'localhost': {'hosts': ["127.0.0.1"]}})
    return (groups, hostvars)


def _val_or_none(array, location):
    """Return any value that has a length"""
    try:
        if len(array[location]) > 0:
            return array[location]
        return None
    except IndexError:
        LOG.debug(("Out of range value encountered.  Requested "
                  "field %s Had: %s" % (location, array)))


def _process_baremetal_data(data_source, groups, hostvars):
    """Process data through as pre-formatted data"""
    with open(data_source, 'rb') as file_object:
        try:
            file_data = json.load(file_object)
        except Exception as e:
            LOG.debug("Attempting to parse JSON: %s" % e)
            try:
                file_object.seek(0)
                file_data = yaml.load(file_object)
            except Exception as e:
                LOG.debug("Attempting to parse YAML: %s" % e)
                raise Exception("Failed to parse JSON and YAML")

        for name in file_data:
            host = file_data[name]
            # Perform basic validation
            if ('ipv4_address' not in host or
                    not host['ipv4_address']):
                host['addressing_mode'] = "dhcp"
            else:
                host['ansible_ssh_host'] = host['ipv4_address']

            if ('provisioning_ipv4_address' not in host and
                    'addressing_mode' not in host):
                host['provisioning_ipv4_address'] = host['ipv4_address']
            # Add each host to the values to be returned.
            groups['baremetal']['hosts'].append(host['name'])
            hostvars.update({host['name']: host})
    return (groups, hostvars)


def _process_baremetal_csv(data_source, groups, hostvars):
    """Process legacy baremetal.csv format"""
    with open(data_source, 'r') as file_data:
        for row in csv.reader(file_data, delimiter=','):
            if not row:
                break
            if len(row) is 1:
                LOG.debug("Single entry line found when attempting "
                          "to parse CSV file contents. Breaking "
                          "out of processing loop.")
                raise Exception("Invalid CSV file format detected, "
                                "line ends with a single element")
            host = {}
            driver = None
            driver_info = {}
            power = {}
            properties = {}
            host['nics'] = [{
                'mac': _val_or_none(row, 0)}]
            # Temporary variables for ease of reading
            management_username = _val_or_none(row, 1)
            management_password = _val_or_none(row, 2)
            management_address = _val_or_none(row, 3)

            properties['cpus'] = _val_or_none(row, 4)
            properties['ram'] = _val_or_none(row, 5)
            properties['disk_size'] = _val_or_none(row, 6)
            # Default CPU Architecture
            properties['cpu_arch'] = "x86_64"
            host['uuid'] = _val_or_none(row, 9)
            host['name'] = _val_or_none(row, 10)
            host['ipv4_address'] = _val_or_none(row, 11)
            if ('ipv4_address' not in host or
                    not host['ipv4_address']):
                host['addressing_mode'] = "dhcp"
                host['provisioning_ipv4_address'] = None
            else:
                host['ansible_ssh_host'] = host['ipv4_address']
            # Note(TheJulia): We can't assign ipv4_address if we are
            # using DHCP.
            if (len(row) > 17 and 'addressing_mode' not in host):
                host['provisioning_ipv4_address'] = row[18]
            else:
                host['provisioning_ipv4_address'] = host['ipv4_address']

            # Default Driver unless otherwise defined or determined.
            host['driver'] = "agent_ssh"

            if len(row) > 15:
                driver = _val_or_none(row, 16)
                if driver:
                    host['driver'] = driver

            if "ipmi" in host['driver']:
                # Set agent_ipmitool by default
                host['driver'] = "agent_ipmitool"
                power['ipmi_address'] = management_address
                power['ipmi_username'] = management_username
                power['ipmi_password'] = management_password
                if len(row) > 12:
                    power['ipmi_target_channel'] = _val_or_none(row, 12)
                    power['ipmi_target_address'] = _val_or_none(row, 13)
                    if (power['ipmi_target_channel'] and
                            power['ipmi_target_address']):
                        power['ipmi_bridging'] = 'single'
                if len(row) > 14:
                    power['ipmi_transit_channel'] = _val_or_none(row, 14)
                    power['ipmi_transit_address'] = _val_or_none(row, 15)
                    if (power['ipmi_transit_channel'] and
                            power['ipmi_transit_address']):
                        power['ipmi_bridging'] = 'dual'

            if "ssh" in host['driver']:
                # Under another model, a user would define
                # and value translations to load these
                # values.  Since we're supporting the base
                # model bifrost was developed with, then
                # we need to make sure these are present as
                # they are expected values.
                power['ssh_virt_type'] = "virsh"
                power['ssh_address'] = management_address
                power['ssh_port'] = 22
                # NOTE: The CSV format is desynced from the enrollment
                # playbook at present, so we're hard coding ironic here
                # as that is what the test is known to work with.
                power['ssh_username'] = "ironic"
                power['ssh_key_filename'] = "/home/ironic/.ssh/id_rsa"

            # Group variables together under host.
            # NOTE(TheJulia): Given the split that this demonstrates, where
            # deploy details could possible be imported from a future
            # inventory file format
            driver_info['power'] = power
            host['driver_info'] = driver_info
            host['properties'] = properties

            groups['baremetal']['hosts'].append(host['name'])
            hostvars.update({host['name']: host})
    return (groups, hostvars)


def _identify_shade_auth():
    """Return shade credentials"""
    # Note(TheJulia): A logical progression is to support a user defining
    # an environment variable that triggers use of os-client-config to allow
    # environment variables or clouds.yaml auth configuration.  This could
    # potentially be passed in as variables which could then be passed
    # to modules for authentication allowing the basic tooling to be
    # utilized in the context of a larger cloud supporting ironic.
    options = dict(
        auth_type="None",
        auth=dict(endpoint="http://localhost:6385/",)
    )
    return options


def _process_shade(groups, hostvars):
    """Retrieve inventory utilizing Shade"""
    options = _identify_shade_auth()
    cloud = shade.operator_cloud(**options)
    machines = cloud.list_machines()
    for machine in machines:
        if 'properties' not in machine:
            machine = cloud.get_machine(machine['uuid'])
        if machine['name'] is None:
            name = machine['uuid']
        else:
            name = machine['name']
        new_machine = {}
        for key, value in machine.items():
            # NOTE(TheJulia): We don't want to pass infomrational links
            # nor do we want to pass links about the ports since they
            # are API endpoint URLs.
            if key not in ['links', 'ports']:
                new_machine[key] = value

        # NOTE(TheJulia): Collect network information, enumerate through
        # and extract important values, presently MAC address. Once done,
        # return the network information to the inventory.
        nics = cloud.list_nics_for_machine(machine['uuid'])
        new_nics = []
        new_nic = {}
        for nic in nics:
            if 'address' in nic:
                new_nic['mac'] = nic['address']
            new_nics.append(new_nic)
        new_machine['nics'] = new_nics

        new_machine['addressing_mode'] = "dhcp"
        groups['baremetal']['hosts'].append(name)
        hostvars.update({name: new_machine})
    return (groups, hostvars)


def main():
    """Generate a list of hosts."""
    config = _parse_config()

    if not config.list:
        LOG.error("This program must be executed in list mode.")
        sys.exit(1)

    (groups, hostvars) = _prepare_inventory()

    if 'BIFROST_INVENTORY_SOURCE' not in os.environ:
        LOG.error('Please define a BIFROST_INVENTORY_SOURCE environment'
                  'variable with a comma separated list of data sources')
        sys.exit(1)

    try:
        data_source = os.environ['BIFROST_INVENTORY_SOURCE']
        if os.path.isfile(data_source):
            try:
                (groups, hostvars) = _process_baremetal_data(
                    data_source,
                    groups,
                    hostvars)
            except Exception as e:
                LOG.debug("File does not appear to be JSON or YAML - %s" % e)
                try:
                    (groups, hostvars) = _process_baremetal_csv(
                        data_source,
                        groups,
                        hostvars)
                except Exception as e:
                    LOG.debug("CSV fallback processing failed, "
                              "received: &s" % e)
                    LOG.error("BIFROST_INVENTORY_SOURCE does not define "
                              "a file that could be processed: "
                              "Tried JSON, YAML, and CSV formats")
                    sys.exit(1)
        elif "ironic" in data_source:
            if SHADE_LOADED:
                (groups, hostvars) = _process_shade(groups, hostvars)
            else:
                LOG.error("BIFROST_INVENTORY_SOURCE is set to ironic "
                          "however the shade library failed to load, and may "
                          "not be present.")
                sys.exit(1)
        else:
            LOG.error('BIFROST_INVENTORY_SOURCE does not define a file')
            sys.exit(1)

    except Exception as error:
        LOG.error('Failed processing: %s' % error)
        sys.exit(1)

    # General Data Conversion

    if not config.convertcsv:
        inventory = {'_meta': {'hostvars': hostvars}}
        inventory.update(groups)
        print(json.dumps(inventory, indent=2))
    else:
        print(json.dumps(hostvars, indent=2))

if __name__ == '__main__':
    main()
