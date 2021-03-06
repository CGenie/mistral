# Copyright 2014 - Mirantis, Inc.
# Copyright 2015 - StackStorm, Inc.
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

from mistral.db.v2 import api as db_api
from mistral.engine1 import base
from mistral.engine1 import rpc
from mistral import expressions
from mistral.services import scheduler
from mistral.utils import wf_trace
from mistral.workflow import states
from mistral.workflow import utils


_ENGINE_CLIENT_PATH = 'mistral.engine1.rpc.get_engine_client'


def _log_task_delay(task_ex, delay_sec):
    wf_trace.info(
        task_ex,
        "Task '%s' [%s -> %s, delay = %s sec]" %
        (task_ex.name, task_ex.state, states.DELAYED, delay_sec)
    )


def build_policies(policies_spec, wf_spec):
    task_defaults = wf_spec.get_task_defaults()
    wf_policies = task_defaults.get_policies() if task_defaults else None

    if not (policies_spec or wf_policies):
        return []

    return construct_policies_list(policies_spec, wf_policies)


def get_policy_factories():
    return [
        build_wait_before_policy,
        build_wait_after_policy,
        build_retry_policy,
        build_timeout_policy,
        build_pause_before_policy,
        build_concurrency_policy
    ]


def construct_policies_list(policies_spec, wf_policies):
    policies = []

    for factory in get_policy_factories():
        policy = factory(policies_spec)

        if wf_policies and not policy:
            policy = factory(wf_policies)

        if policy:
            policies.append(policy)

    return policies


def build_wait_before_policy(policies_spec):
    if not policies_spec:
        return None

    wait_before = policies_spec.get_wait_before()

    return WaitBeforePolicy(wait_before) if wait_before > 0 else None


def build_wait_after_policy(policies_spec):
    if not policies_spec:
        return None

    wait_after = policies_spec.get_wait_after()

    return WaitAfterPolicy(wait_after) if wait_after > 0 else None


def build_retry_policy(policies_spec):
    if not policies_spec:
        return None

    retry = policies_spec.get_retry()

    if not retry:
        return None

    return RetryPolicy(
        retry.get_count(),
        retry.get_delay(),
        retry.get_break_on()
    )


def build_timeout_policy(policies_spec):
    if not policies_spec:
        return None

    timeout_policy = policies_spec.get_timeout()

    return TimeoutPolicy(timeout_policy) if timeout_policy > 0 else None


def build_pause_before_policy(policies_spec):
    if not policies_spec:
        return None

    pause_before_policy = policies_spec.get_pause_before()

    return (PauseBeforePolicy(pause_before_policy)
            if pause_before_policy else None)


def build_concurrency_policy(policies_spec):
    if not policies_spec:
        return None

    concurrency_policy = policies_spec.get_concurrency()

    return (ConcurrencyPolicy(concurrency_policy)
            if concurrency_policy else None)


def _ensure_context_has_key(runtime_context, key):
    if not runtime_context:
        runtime_context = {}

    if key not in runtime_context:
        runtime_context.update({key: {}})

    return runtime_context


class WaitBeforePolicy(base.TaskPolicy):
    _schema = {
        "properties": {
            "delay": {"type": "integer"}
        }
    }

    def __init__(self, delay):
        self.delay = delay

    def before_task_start(self, task_ex, task_spec):
        super(WaitBeforePolicy, self).before_task_start(task_ex, task_spec)

        context_key = 'wait_before_policy'

        runtime_context = _ensure_context_has_key(
            task_ex.runtime_context,
            context_key
        )

        task_ex.runtime_context = runtime_context

        policy_context = runtime_context[context_key]

        if policy_context.get('skip'):
            # Unset state 'DELAYED'.
            wf_trace.info(
                task_ex,
                "Task '%s' [%s -> %s]"
                % (task_ex.name, states.DELAYED, states.RUNNING)
            )

            task_ex.state = states.RUNNING

            return

        policy_context.update({'skip': True})

        _log_task_delay(task_ex, self.delay)

        task_ex.state = states.DELAYED

        scheduler.schedule_call(
            _ENGINE_CLIENT_PATH,
            'run_task',
            self.delay,
            task_id=task_ex.id
        )


class WaitAfterPolicy(base.TaskPolicy):
    _schema = {
        "properties": {
            "delay": {"type": "integer"}
        }
    }

    def __init__(self, delay):
        self.delay = delay

    def after_task_complete(self, task_ex, task_spec, result):
        super(WaitAfterPolicy, self).after_task_complete(
            task_ex, task_spec, result
        )
        context_key = 'wait_after_policy'

        runtime_context = _ensure_context_has_key(
            task_ex.runtime_context,
            context_key
        )

        task_ex.runtime_context = runtime_context

        policy_context = runtime_context[context_key]

        if policy_context.get('skip'):
            # Need to avoid terminal states.
            if not states.is_completed(task_ex.state):
                # Unset state 'DELAYED'.

                wf_trace.info(
                    task_ex,
                    "Task '%s' [%s -> %s]"
                    % (task_ex.name, states.DELAYED, states.RUNNING)
                )

                task_ex.state = states.RUNNING

            return

        policy_context.update({'skip': True})

        _log_task_delay(task_ex, self.delay)

        # Set task state to 'DELAYED'.
        task_ex.state = states.DELAYED

        serializers = {
            'result': 'mistral.workflow.utils.TaskResultSerializer'
        }

        scheduler.schedule_call(
            _ENGINE_CLIENT_PATH,
            'on_task_result',
            self.delay,
            serializers,
            task_id=task_ex.id,
            result=result
        )


class RetryPolicy(base.TaskPolicy):
    _schema = {
        "properties": {
            "delay": {"type": "integer"},
            "count": {"type": "integer"}
        }
    }

    def __init__(self, count, delay, break_on):
        self.count = count
        self.delay = delay
        self.break_on = break_on

    def after_task_complete(self, task_ex, task_spec, result):
        """Possible Cases:

        1. state = SUCCESS
           No need to move to next iteration.
        2. retry:count = 5, current:count = 2, state = ERROR,
           state = IDLE/DELAYED, current:count = 3
        3. retry:count = 5, current:count = 4, state = ERROR
        Iterations complete therefore state = #{state}, current:count = 4.
        """
        super(RetryPolicy, self).after_task_complete(
            task_ex, task_spec, result
        )

        context_key = 'retry_task_policy'

        runtime_context = _ensure_context_has_key(
            task_ex.runtime_context,
            context_key
        )

        task_ex.runtime_context = runtime_context

        state = states.ERROR if result.is_error() else states.SUCCESS

        if state != states.ERROR:
            return

        wf_trace.info(
            task_ex,
            "Task '%s' [%s -> ERROR]"
            % (task_ex.name, task_ex.state)
        )

        outbound_context = task_ex.result

        policy_context = runtime_context[context_key]

        retry_no = 0

        if 'retry_no' in policy_context:
            retry_no = policy_context['retry_no']
            del policy_context['retry_no']

        retries_remain = retry_no + 1 < self.count

        break_early = (
            expressions.evaluate(self.break_on, outbound_context)
            if self.break_on and outbound_context else False
        )

        if not retries_remain or break_early:
            return

        _log_task_delay(task_ex, self.delay)

        task_ex.state = states.DELAYED

        policy_context['retry_no'] = retry_no + 1
        runtime_context[context_key] = policy_context

        scheduler.schedule_call(
            _ENGINE_CLIENT_PATH,
            'run_task',
            self.delay,
            task_id=task_ex.id
        )


class TimeoutPolicy(base.TaskPolicy):
    _schema = {
        "properties": {
            "delay": {"type": "integer"},
        }
    }

    def __init__(self, timeout_sec):
        self.delay = timeout_sec

    def before_task_start(self, task_ex, task_spec):
        super(TimeoutPolicy, self).before_task_start(task_ex, task_spec)

        scheduler.schedule_call(
            None,
            'mistral.engine1.policies.fail_task_if_incomplete',
            self.delay,
            task_id=task_ex.id,
            timeout=self.delay
        )

        wf_trace.info(
            task_ex,
            "Timeout check scheduled [task=%s, timeout(s)=%s]." %
            (task_ex.id, self.delay)
        )


class PauseBeforePolicy(base.TaskPolicy):
    _schema = {
        "properties": {
            "expr": {"type": "boolean"},
        }
    }

    def __init__(self, expression):
        self.expr = expression

    def before_task_start(self, task_ex, task_spec):
        super(PauseBeforePolicy, self).before_task_start(task_ex, task_spec)

        if not expressions.evaluate(self.expr, task_ex.in_context):
            return

        wf_trace.info(
            task_ex,
            "Workflow paused before task '%s' [%s -> %s]" %
            (task_ex.name, task_ex.workflow_execution.state, states.PAUSED)
        )

        task_ex.workflow_execution.state = states.PAUSED
        task_ex.state = states.IDLE


class ConcurrencyPolicy(base.TaskPolicy):
    _schema = {
        "properties": {
            "delay": {"concurrency": "integer"},
        }
    }

    def __init__(self, concurrency):
        self.concurrency = concurrency

    def before_task_start(self, task_ex, task_spec):
        super(ConcurrencyPolicy, self).before_task_start(task_ex, task_spec)

        context_key = 'concurrency'

        runtime_context = _ensure_context_has_key(
            task_ex.runtime_context,
            context_key
        )

        runtime_context[context_key] = self.concurrency
        task_ex.runtime_context = runtime_context


def fail_task_if_incomplete(task_id, timeout):
    task_ex = db_api.get_task_execution(task_id)

    if not states.is_completed(task_ex.state):
        msg = "Task timed out [task=%s, timeout(s)=%s]." % (task_id, timeout)

        wf_trace.info(task_ex, msg)

        wf_trace.info(
            task_ex,
            "Task '%s' [%s -> ERROR]"
            % (task_ex.name, task_ex.state)
        )

        rpc.get_engine_client().on_task_result(
            task_id,
            utils.TaskResult(error=msg)
        )
