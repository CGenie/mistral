# Copyright 2014 - Mirantis, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

from oslo.config import cfg
import testtools

from mistral.db.v2 import api as db_api
from mistral.engine import states
from mistral.openstack.common import log as logging
from mistral.services import workflows as wf_service
from mistral.tests.unit.engine1 import base

LOG = logging.getLogger(__name__)
# Use the set_default method to set value otherwise in certain test cases
# the change in value is not permanent.
cfg.CONF.set_default('auth_enable', False, group='pecan')

WF_FULL_JOIN = """
---
version: 2.0

wf:
  type: direct

  output:
    result: <% $.result3 %>

  tasks:
    task1:
      action: std.echo output=1
      publish:
        result1: <% $.task1 %>
      on-complete:
        - task3

    task2:
      action: std.echo output=2
      publish:
        result2: <% $.task2 %>
      on-complete:
        - task3

    task3:
      join: all
      action: std.echo output="<% $.result1 %>,<% $.result2 %>"
      publish:
        result3: <% $.task3 %>
"""


WF_FULL_JOIN_WITH_ERRORS = """
---
version: 2.0

wf:
  type: direct

  output:
    result: <% $.result3 %>

  tasks:
    task1:
      action: std.echo output=1
      publish:
        result1: <% $.task1 %>
      on-complete:
        - task3

    task2:
      action: std.fail
      on-error:
        - task3

    task3:
      join: all
      action: std.echo output="<% $.result1 %>-<% $.result1 %>"
      publish:
        result3: <% $.task3 %>
"""

WF_FULL_JOIN_WITH_CONDITIONS = """
---
version: 2.0

wf:
  type: direct

  output:
    result: <% $.result4 %>

  tasks:
    task1:
      action: std.echo output=1
      publish:
        result1: <% $.task1 %>
      on-complete:
        - task3

    task2:
      action: std.echo output=2
      publish:
        result2: <% $.task2 %>
      on-complete:
        - task3: <% $.result2 = 11111 %>
        - task4: <% $.result2 = 2 %>

    task3:
      join: all
      action: std.echo output="<% $.result1 %>-<% $.result1 %>"
      publish:
        result3: <% $.task3 %>

    task4:
      action: std.echo output=4
      publish:
        result4: <% $.task4 %>
"""

WF_PARTIAL_JOIN = """
---
version: 2.0

wf:
  type: direct

  output:
    result: <% $.result4 %>

  tasks:
    task1:
      action: std.echo output=1
      publish:
        result1: <% $.task1 %>
      on-complete:
        - task4

    task2:
      action: std.echo output=2
      publish:
        result2: <% $.task2 %>
      on-complete:
        - task4

    task3:
      action: std.fail
      description: |
        Always fails and 'on-success' never gets triggered.
        However, 'task4' will run since its join cardinality
        is 2 which means 'task1' and 'task2' completion is
        enough to trigger it.
      on-success:
        - task4
      on-error:
        - noop

    task4:
      join: 2
      action: std.echo output="<% $.result1 %>,<% $.result2 %>"
      publish:
        result4: <% $.task4 %>
"""

WF_PARTIAL_JOIN_TRIGGERS_ONCE = """
---
version: 2.0

wf:
  type: direct

  output:
    result: <% $.result4 %>

  tasks:
    task1:
      action: std.noop
      publish:
        result1: 1
      on-complete:
        - task5

    task2:
      action: std.noop
      publish:
        result2: 2
      on-complete:
        - task5

    task3:
      action: std.noop
      publish:
        result3: 3
      on-complete:
        - task5

    task4:
      action: std.noop
      publish:
        result4: 4
      on-complete:
        - task5

    task5:
      join: 2
      action: std.echo
      input:
        output: <% $.result1 %>,<% $.result2 %>,<% $.result3 %>,<% $.result4 %>
      publish:
        result5: <% $.task5 %>
"""

WF_DISCRIMINATOR = """
---
version: 2.0

wf:
  type: direct

  output:
    result: <% $.result4 %>

  tasks:
    task1:
      action: std.noop
      publish:
        result1: 1
      on-complete:
        - task4

    task2:
      action: std.noop
      publish:
        result2: 2
      on-complete:
        - task4

    task3:
      action: std.noop
      publish:
        result3: 3
      on-complete:
        - task4

    task4:
      join: one
      action: std.echo output="<% $.result1 %>,<% $.result2 %>,<% $.result3 %>"
      publish:
        result4: <% $.task4 %>
"""


class JoinEngineTest(base.EngineTestCase):
    def test_full_join_without_errors(self):
        wf_service.create_workflows(WF_FULL_JOIN)

        # Start workflow.
        wf_ex = self.engine.start_workflow('wf', {})

        self._await(lambda: self.is_execution_success(wf_ex.id))

        # Note: We need to reread execution to access related tasks.
        wf_ex = db_api.get_workflow_execution(wf_ex.id)

        tasks = wf_ex.task_executions

        task1 = self._assert_single_item(tasks, name='task1')
        task2 = self._assert_single_item(tasks, name='task2')
        task3 = self._assert_single_item(tasks, name='task3')

        self.assertEqual(states.SUCCESS, task1.state)
        self.assertEqual(states.SUCCESS, task2.state)
        self.assertEqual(states.SUCCESS, task3.state)

        self.assertDictEqual({'result': '1,2'}, wf_ex.output)

    def test_full_join_with_errors(self):
        wf_service.create_workflows(WF_FULL_JOIN_WITH_ERRORS)

        # Start workflow.
        wf_ex = self.engine.start_workflow('wf', {})

        self._await(lambda: self.is_execution_success(wf_ex.id))

        # Note: We need to reread execution to access related tasks.
        wf_ex = db_api.get_workflow_execution(wf_ex.id)

        tasks = wf_ex.task_executions

        task1 = self._assert_single_item(tasks, name='task1')
        task2 = self._assert_single_item(tasks, name='task2')
        task3 = self._assert_single_item(tasks, name='task3')

        self.assertEqual(states.SUCCESS, task1.state)
        self.assertEqual(states.ERROR, task2.state)
        self.assertEqual(states.SUCCESS, task3.state)

        self.assertDictEqual({'result': '1-1'}, wf_ex.output)

    def test_full_join_with_conditions(self):
        wf_service.create_workflows(WF_FULL_JOIN_WITH_CONDITIONS)

        # Start workflow.
        wf_ex = self.engine.start_workflow('wf', {})

        self._await(lambda: self.is_execution_success(wf_ex.id))

        # Note: We need to reread execution to access related tasks.
        wf_ex = db_api.get_workflow_execution(wf_ex.id)

        tasks = wf_ex.task_executions

        self.assertEqual(3, len(tasks))

        task1 = self._assert_single_item(tasks, name='task1')
        task2 = self._assert_single_item(tasks, name='task2')
        task4 = self._assert_single_item(tasks, name='task4')

        self.assertEqual(states.SUCCESS, task1.state)
        self.assertEqual(states.SUCCESS, task2.state)
        self.assertEqual(states.SUCCESS, task4.state)

        self.assertDictEqual({'result': 4}, wf_ex.output)

    def test_partial_join(self):
        wf_service.create_workflows(WF_PARTIAL_JOIN)

        # Start workflow.
        wf_ex = self.engine.start_workflow('wf', {})

        self._await(lambda: self.is_execution_success(wf_ex.id))

        # Note: We need to reread execution to access related tasks.
        wf_ex = db_api.get_workflow_execution(wf_ex.id)

        tasks = wf_ex.task_executions

        self.assertEqual(4, len(tasks))

        task1 = self._assert_single_item(tasks, name='task1')
        task2 = self._assert_single_item(tasks, name='task2')
        task3 = self._assert_single_item(tasks, name='task3')
        task4 = self._assert_single_item(tasks, name='task4')

        self.assertEqual(states.SUCCESS, task1.state)
        self.assertEqual(states.SUCCESS, task2.state)
        self.assertEqual(states.ERROR, task3.state)
        self.assertEqual(states.SUCCESS, task4.state)

        self.assertDictEqual(
            {
                'result4': '1,2',
            },
            task4.result
        )

        self.assertDictEqual({'result': '1,2'}, wf_ex.output)

    def test_partial_join_triggers_once(self):
        wf_service.create_workflows(WF_PARTIAL_JOIN_TRIGGERS_ONCE)

        # Start workflow.
        wf_ex = self.engine.start_workflow('wf', {})

        self._await(lambda: self.is_execution_success(wf_ex.id))

        # Note: We need to reread execution to access related tasks.
        wf_ex = db_api.get_workflow_execution(wf_ex.id)

        tasks = wf_ex.task_executions

        self.assertEqual(5, len(tasks))

        task1 = self._assert_single_item(tasks, name='task1')
        task2 = self._assert_single_item(tasks, name='task2')
        task3 = self._assert_single_item(tasks, name='task3')
        task4 = self._assert_single_item(tasks, name='task4')
        task5 = self._assert_single_item(tasks, name='task5')

        self.assertEqual(states.SUCCESS, task1.state)
        self.assertEqual(states.SUCCESS, task2.state)
        self.assertEqual(states.SUCCESS, task3.state)
        self.assertEqual(states.SUCCESS, task4.state)
        self.assertEqual(states.SUCCESS, task5.state)

        result5 = task5.result['result5']

        self.assertIsNotNone(result5)
        self.assertEqual(2, result5.count('None'))

    def test_discriminator(self):
        wf_service.create_workflows(WF_DISCRIMINATOR)

        # Start workflow.
        wf_ex = self.engine.start_workflow('wf', {})

        self._await(lambda: self.is_execution_success(wf_ex.id))

        # Note: We need to reread execution to access related tasks.
        wf_ex = db_api.get_workflow_execution(wf_ex.id)

        tasks = wf_ex.task_executions

        self.assertEqual(4, len(tasks))

        task1 = self._assert_single_item(tasks, name='task1')
        task2 = self._assert_single_item(tasks, name='task2')
        task3 = self._assert_single_item(tasks, name='task3')
        task4 = self._assert_single_item(tasks, name='task4')

        self.assertEqual(states.SUCCESS, task1.state)
        self.assertEqual(states.SUCCESS, task2.state)
        self.assertEqual(states.SUCCESS, task3.state)
        self.assertEqual(states.SUCCESS, task4.state)

        result4 = task4.result['result4']

        self.assertIsNotNone(result4)
        self.assertEqual(2, result4.count('None'))

    def test_full_join_parallel_published_vars(self):
        wfs_tasks_join_complex = """---
        version: 2.0

        main:
          type: direct
          output:
            var1: <% $.var1 %>
            var2: <% $.var2 %>
            is_done: <% $.is_done %>

          tasks:
            init:
              publish:
                var1: false
                var2: false
                is_done: false
              on-success:
                - branch1
                - branch2

            branch1:
              workflow: work
              publish:
                var1: true
              on-success:
                - done

            branch2:
              publish:
                var2: true
              on-success:
                - done
            done:
              join: all
              publish:
                is_done: true

        work:
          type: direct
          tasks:
            do:
              action: std.echo output="Doing..."
              on-success:
                - exit
            exit:
              action: std.echo output="Exiting..."
        """
        wf_service.create_workflows(wfs_tasks_join_complex)

        # Start workflow.
        exec_db = self.engine.start_workflow('main', {})

        self._await(lambda: self.is_execution_success(exec_db.id))

        # Note: We need to reread execution to access related tasks.
        exec_db = db_api.get_execution(exec_db.id)

        self.assertDictEqual(
            {
                'var1': True,
                'is_done': True,
                'var2': True
            },
            exec_db.output
        )

    @testtools.skip('https://bugs.launchpad.net/mistral/+bug/1424461')
    def test_full_join_parallel_published_vars_complex(self):
        wfs_tasks_join_complex = """---
        version: "2.0"

        main:
          type: direct
          output:
            var_a: <% $.var_a %>
            var_b: <% $.var_b %>
            var_c: <% $.var_c %>
            var_d: <% $.var_d %>
          tasks:
            init:
              publish:
                var_a: 0
                var_b: 0
                var_c: 0
              on-success:
                - branch1_0
                - branch2_0

            branch1_0:
              publish:
                var_c: 1
              on-success:
                - branch1_1

            branch2_0:
              publish:
                var_a: 1
              on-success:
                - done

            branch1_1:
              publish:
                var_b: 1
              on-success:
                - done

            done:
              join: all
              publish:
                var_d: 1
        """
        wf_service.create_workflows(wfs_tasks_join_complex)

        # Start workflow.
        exec_db = self.engine.start_workflow('main', {})

        self._await(lambda: self.is_execution_success(exec_db.id))

        # Note: We need to reread execution to access related tasks.
        exec_db = db_api.get_execution(exec_db.id)

        self.assertDictEqual(
            {
                'var_a': 1,
                'var_b': 1,
                'var_c': 1,
                'var_d': 1
            },
            exec_db.output
        )
