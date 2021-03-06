# Copyright 2014 - Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

from oslo.config import cfg

from mistral.db.v2 import api as db_api
from mistral.openstack.common import log as logging
from mistral.services import workbooks as wb_service
from mistral.tests.unit.engine1 import base
from mistral.workflow import states

LOG = logging.getLogger(__name__)

# Use the set_default method to set value otherwise in certain test cases
# the change in value is not permanent.
cfg.CONF.set_default('auth_enable', False, group='pecan')


WORKBOOK = """
---
version: '2.0'

name: my_wb

workflows:
  wf1:
    type: reverse
    input:
      - param1
      - param2

    tasks:
      task1:
        action: std.echo output=<% $.param1 %>
        publish:
          result1: <% $.task1 %>

      task2:
        action: std.echo output="<% $.result1 %> & <% $.param2 %>"
        publish:
          result2: <% $.task2 %>
        requires: [task1]
"""


class ReverseWorkflowEngineTest(base.EngineTestCase):
    def setUp(self):
        super(ReverseWorkflowEngineTest, self).setUp()

        wb_service.create_workbook_v2(WORKBOOK)

    def test_start_task1(self):
        wf_input = {'param1': 'a', 'param2': 'b'}

        wf_ex = self.engine.start_workflow(
            'my_wb.wf1',
            wf_input,
            task_name='task1'
        )

        # Execution 1.
        self.assertIsNotNone(wf_ex)
        self.assertDictEqual(wf_input, wf_ex.input)
        self.assertDictEqual({'task_name': 'task1'}, wf_ex.start_params)

        # Wait till workflow 'wf1' is completed.
        self._await(lambda: self.is_execution_success(wf_ex.id))

        wf_ex = db_api.get_workflow_execution(wf_ex.id)

        self.assertEqual(1, len(wf_ex.task_executions))
        self.assertEqual(1, len(db_api.get_task_executions()))

        self._assert_single_item(
            wf_ex.task_executions,
            name='task1',
            state=states.SUCCESS
        )

        self.assertEqual('a', wf_ex.output['task1']['result1'])
        self._assert_dict_contains_subset({'result1': 'a'}, wf_ex.output)

    def test_start_task2(self):
        wf_input = {'param1': 'a', 'param2': 'b'}

        wf_ex = self.engine.start_workflow(
            'my_wb.wf1',
            wf_input,
            task_name='task2'
        )

        # Execution 1.
        self.assertIsNotNone(wf_ex)
        self.assertDictEqual(wf_input, wf_ex.input)
        self.assertDictEqual({'task_name': 'task2'}, wf_ex.start_params)

        # Wait till workflow 'wf1' is completed.
        self._await(lambda: self.is_execution_success(wf_ex.id))

        wf_ex = db_api.get_workflow_execution(wf_ex.id)

        self.assertEqual(2, len(wf_ex.task_executions))
        self.assertEqual(2, len(db_api.get_task_executions()))

        self._assert_single_item(
            wf_ex.task_executions,
            name='task1',
            state=states.SUCCESS
        )

        self._assert_single_item(
            wf_ex.task_executions,
            name='task2',
            state=states.SUCCESS
        )

        self.assertEqual('a', wf_ex.output['task1']['result1'])
        self.assertEqual('a & b', wf_ex.output['task2']['result2'])
        self._assert_dict_contains_subset({'result1': 'a'}, wf_ex.output)
        self._assert_dict_contains_subset({'result2': 'a & b'}, wf_ex.output)
