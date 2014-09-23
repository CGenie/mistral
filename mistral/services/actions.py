# Copyright 2013 - Mirantis, Inc.
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

from mistral import context
from mistral.db.v2 import api as db_api
from mistral import exceptions as exc
from mistral.services import trusts
from mistral.workbook import parser as spec_parser


def create_actions(definition):
    wf_list_spec = spec_parser.get_action_list_spec_from_yaml(definition)

    db_wfs = []

    with db_api.transaction():
        for wf_spec in wf_list_spec.get_actions():
            db_wfs.append(create_action(wf_spec, definition))

    return db_wfs


def update_actions(definition):
    action_list_spec = spec_parser.get_action_list_spec_from_yaml(definition)

    db_wfs = []

    with db_api.transaction():
        for action_spec in action_list_spec.get_actions():
            db_wfs.append(create_or_update_action(action_spec, definition))

    return db_wfs


def create_action(action_spec, definition):
    values = {
        'name': action_spec.get_name(),
        'description': action_spec.get_description(),
        'definition': definition,
        'spec': action_spec.to_dict(),
        'is_system': False
    }

    _add_security_info(values)

    return db_api.create_action(values)


def create_or_update_action(action_spec, definition):
    action = db_api.load_action(action_spec.get_name())

    if action and action.is_system:
        raise exc.InvalidActionException(
            "Attempt to modify a system action: %s" %
            action.name
        )

    values = {
        'name': action_spec.get_name(),
        'description': action_spec.get_description(),
        'definition': definition,
        'spec': action_spec.to_dict(),
        'is_system': False
    }

    _add_security_info(values)

    return db_api.create_or_update_action(values['name'], values)


def _add_security_info(values):
    if cfg.CONF.pecan.auth_enable:
        values.update({
            'trust_id': trusts.create_trust().id,
            'project_id': context.ctx().project_id
        })