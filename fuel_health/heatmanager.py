# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2012 OpenStack, LLC
# Copyright 2013 Mirantis, Inc.
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

import logging
import os
import time

import heatclient.v1.client

from fuel_health.common.ssh import Client as SSHClient
from fuel_health.common.utils.data_utils import rand_name
from fuel_health.exceptions import SSHExecCommandFailed
import fuel_health.nmanager
import fuel_health.test


LOG = logging.getLogger(__name__)


class HeatManager(fuel_health.nmanager.OfficialClientManager):
    """
    HeatManager provides access to the official python client of Heat.
    """

    def __init__(self):
        super(HeatManager, self).__init__()
        self.heat_client = self._get_heat_client()
        self.client_attr_names.append('heat_client')

    def _get_heat_client(self, username=None, password=None):
        keystone = self._get_identity_client()
        token = keystone.auth_token
        auth_url = self.config.identity.uri

        if 'orchestration' not in [s.type for s in keystone.services.list()]:
            return None

        endpoint = keystone.service_catalog.url_for(
            service_type='orchestration', endpoint_type='publicURL')
        if not username:
            username = self.config.identity.admin_username
        if not password:
            password = self.config.identity.admin_password

        return heatclient.v1.client.Client(endpoint,
                                           auth_url=auth_url, token=token,
                                           username=username,
                                           password=password)


class HeatBaseTest(fuel_health.nmanager.OfficialClientTest):
    """
    Base class for Heat openstack sanity and smoke tests.
    """

    manager_class = HeatManager

    simple_template = """
        {
            "AWSTemplateFormatVersion": "2010-09-09",
            "Parameters": {
                "ImageId" : {
                      "Type" : "String"
                },
                "InstanceType" : {
                      "Type" : "String"
                }
            },

            "Resources": {
                "MyInstance": {
                    "Type": "AWS::EC2::Instance",
                    "Properties": {
                        "ImageId": {"Ref": "ImageId"},
                        "InstanceType": {"Ref": "InstanceType"},
                        "UserData": {"Fn::Base64": "80"}
                    }
                }
            },
            "Outputs": {
                "InstanceIp": {
                    "Value": {"Fn::Join": ["", ["ssh ec2-user@",
                                                {"Fn::GetAtt":["MyInstance",
                                                               "PublicIp"]}]]},
                    "Description": "My ssh command"
                }
            }
        }
        """

    @classmethod
    def setUpClass(cls):
        super(HeatBaseTest, cls).setUpClass()
        cls.stacks = []
        cls.flavor = None

        cls.wait_interval = cls.config.compute.build_interval
        cls.wait_timeout = cls.config.compute.build_timeout

    def setUp(self):
        super(HeatBaseTest, self).setUp()
        if self.heat_client is None:
            self.fail('Heat is unavailable.')

    @classmethod
    def tearDownClass(cls):
        if cls.heat_client is not None:
            cls.clean_stacks()
        super(HeatBaseTest, cls).tearDownClass()

    @classmethod
    def clean_stacks(cls):
        """
        Delete stacks that were created by OSTF tests.
        """
        for s in cls.heat_client.stacks.list():
            if s.stack_name in cls.stacks:
                if s.stack_status in ('CREATE_COMPLETE', 'ERROR'):
                    try:
                        cls.heat_client.stacks.delete(s.id)
                    except Exception as exc:
                        cls.error_msg.append(exc)
                        LOG.debug(exc)

    def list_stacks(self, client):
        return client.stacks.list()

    def _prepare_stack_resources(self):
        if self.flavor is None:
            self.flavor = self._create_nano_flavor()

    def find_stack(self, client, key, value):
        for stack in self.list_stacks(client):
            if hasattr(stack, key) and getattr(stack, key) == value:
                return stack
        return None

    def create_stack(self, client, template=None, parameters={}):
        stack_name = rand_name('ost1_test-stack')
        self._prepare_stack_resources()

        if template is None:
            template = self.simple_template

        if 'InstanceType' not in parameters:
            parameters['InstanceType'] = self.flavor.name
        if 'ImageId' not in parameters:
            parameters['ImageId'] = self.config.compute.image_name

        client.stacks.create(stack_name=stack_name,
                             template=template,
                             parameters=parameters)
        # heat client doesn't return stack details after creation
        # so need to request them:
        stack = self.find_stack(client, 'stack_name', stack_name)
        if stack is not None:
            self.stacks.append(stack_name)
        return stack

    def update_stack(self, client, stack_id, template=None):
        if template is None:
            template = self.simple_template
        self._prepare_stack_resources()
        client.stacks.update(stack_id=stack_id,
                             template=template,
                             parameters={
                                 'ImageId': self.config.compute.image_name,
                                 'InstanceType': self.flavor.name
                             })
        return self.find_stack(client, 'id', stack_id)

    def wait_for_stack_status(self, stack_id, expected_status):
        """
        The method is a customization of test.status_timeout().
        It addresses `stack_status` instead of `status` field.
        The rest is the same.
        """

        def check_status():
            stack = self.heat_client.stacks.get(stack_id)
            new_status = stack.stack_status
            if new_status == 'ERROR':
                self.fail("Failed to get to expected status. In ERROR state.")
            elif new_status == expected_status:
                return True  # All good.
            LOG.debug("Waiting for %s to get to %s status. "
                      "Currently in %s status",
                      stack, expected_status, new_status)

        if not fuel_health.test.call_until_true(check_status,
                                                self.wait_timeout,
                                                self.wait_interval):
            self.fail("Timed out waiting to become %s"
                      % expected_status)

    def wait_for_stack_deleted(self, stack_id):
        f = lambda: self.find_stack(self.heat_client, 'id', stack_id) is None
        if not fuel_health.test.call_until_true(f,
                                                self.wait_timeout,
                                                self.wait_interval):
            self.fail("Timed out waiting for stack to be deleted.")

    def is_haproxy_active_on_vm(self, vm_ip, vm_user, vm_key):
        """
        Check if haproxy service is active on the specified instance.

        :params vm_ip: Floating ip of target virtual machine.
        :params vm_user: User name to login to VM.
        :params vm_key: Path to private key file on controller node.
        """

        CMD = '/etc/init.d/haproxy status'
        # TODO hook for local run
        self.key = "/home/yyekovenko/.ssh/id_rsa"

        if not self.host:
            self.fail('Wrong tests configuration: '
                      'controller_nodes parameter is empty ')
        host = self.host[0]
        ssh_timeout = self.timeout > 30 and self.timeout or 30
        ssh = SSHClient(host, self.usr, password=self.pwd,
                        key_filename=self.key, timeout=ssh_timeout)

        attempts = 10
        while True:
            time.sleep(15)
            try:
                result = ssh.exec_command_on_vm(command=CMD,
                                                user=vm_user,
                                                pkey=vm_key,
                                                vm=vm_ip)
                LOG.debug('Get ssh to instance and run command there')
                return "active (running)" in result

            except SSHExecCommandFailed as exc:
                output_msg = "Running of %s command on %s machine failed." % (
                    CMD, vm_ip)
                LOG.debug(exc)
                if attempts:
                    attempts -= 1
                else:
                    self.fail(output_msg)

            except Exception as exc:
                LOG.debug(exc)
                self.fail("Connection failed.")

    def load_template(self, base_file, file_name):
        filepath = os.path.join(os.path.dirname(os.path.realpath(base_file)),
                                file_name)
        with open(filepath) as f:
            return f.read()

    def is_keypair_available(self, keyname):
        return keyname in [k.id for k in self.compute_client.keypairs.list()]
