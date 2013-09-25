# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2013 Mirantis, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import logging
from nose.plugins.attrib import attr

from fuel_health import heatmanager


LOG = logging.getLogger(__name__)


class MuranoTestLoadBalancer(heatmanager.HeatBaseTest):
    """Test class verifies Heat is able to create a stack with load balancer.
    Special requirements:
        1. Murano component should be installed.
        2. Heat component should be installed.
    """

    @attr(type=["fuel", "smoke"])
    def test_load_balancer(self):
        """Create stack with load balancer and check its status is "Active".
        Target component: Heat

        1. Verify autogenerated Murano keypair exists.
        2. Verify image with load balancer is available in Glance.
        3. Create the stack with load balancer.
        4. Wait for stack status to become 'CREATE_COMPLETE'.
        5. Create floating IP.
        6. Assign floating IP to instance with load balancer.
        7. Verify load balancer instance is available via SSH.
        8. Verify load balancer service is active.
        9. Delete the stack.
        10. Wait for stack to be deleted.

        Duration: 150 s.
        """

        # check if keypair exists
        keyname = "murano-lb-key"
        fail_msg = ("Murano component wasn't installed or it failed to create "
                    "%s keypair." % keyname)
        self.verify(10, self.is_keypair_available, 1, fail_msg,
                    "checking in %s keypair is available" % keyname,
                    keyname)

        # check if image exists
        imagename = 'F17-x86_64-cfntools'
        fail_msg = ("Unable to detect %s image of Fedora OS registered "
                    "in Glance that is required for this test." % imagename)
        check_image = lambda: (
            imagename in [i.name for i in self.compute_client.images.list()])
        self.verify(10, check_image, 2, fail_msg,
                    "checking in %s image is available in Glance" % imagename)

        # make sure flavor is available
        fl_name = "m1.small"
        if fl_name not in [f.name for f in self.compute_client.flavors.list()]:
            fl = self.compute_client.flavors.create(fl_name,
                                                    2048, 1, 20, 'auto')
            self.set_resource(fl_name, fl)

        self.template = self.load_template(__file__,
                                           'HAProxy_Single_Instance.template')
        self.parameters = {
            'KeyName': keyname,
            'ImageId': imagename,
            'InstanceType': fl_name,
            'Server1': '192.168.1.151:1234'  # fake value
        }

        # create stack
        fail_msg = "Stack was not created properly."
        stack = self.verify(20, self.create_stack, 3, fail_msg,
                            "stack creation",
                            self.heat_client, self.template, self.parameters)

        self.verify(100, self.wait_for_stack_status, 4, fail_msg,
                    "stack status becoming 'CREATE_COMPLETE'",
                    stack.id, 'CREATE_COMPLETE')

        # find just created instance
        for instance in self.compute_client.servers.list():
            if instance.name.startswith(stack.stack_name):
                self.instance = instance
                break
        else:
            self.verify_response_true(False,
                                      "Instance for the %s stack "
                                      "was not created." % stack.stack_name)

        floating_ip = self.verify(10, self._create_floating_ip, 5,
                                  "Floating IP can not be created.",
                                  'floating IP creation')

        self.verify(10, self._assign_floating_ip_to_instance, 6,
                    "Floating IP can not be assigned.",
                    'assigning floating IP',
                    self.compute_client, self.instance, floating_ip)

        fail_msg = "Load balancer is not available."
        resp = self.verify(100,
                           self.is_haproxy_active_on_vm, 7,
                           fail_msg,
                           'checking if load balancer service is available',
                           floating_ip.ip,
                           'ec2-user',
                           '/root/.ssh/murano-lb-key_rsa')

        self.verify_response_body_content(True, resp,
                                          'Load balancer service is not active',
                                          8)

        fail_msg = "Cannot delete stack."
        self.verify(20, self.heat_client.stacks.delete, 9,
                    fail_msg, "stack deletion", stack.id)

        self.verify(100, self.wait_for_stack_deleted, 10,
                    fail_msg, "deleting stack", stack.id)