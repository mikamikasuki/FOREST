"""Versioned built-in role defaults; preserve explicit/custom tool selections."""
from __future__ import annotations

from .policy import ROLES, TOOLS, LEGACY_ROLE_INSTRUCTIONS

TOOLSET_VERSION = 4
VERSION_THREE_TOOLS = [tool for tool in TOOLS if tool != 'read_context_segment']
LEGACY_TOOLS = ['read_file', 'write_file', 'list_files', 'run_command', 'python',
                'literature_search', 'graph_command', 'experiment_run', 'results',
                'figure_render', 'paper_compile', 'context_update', 'theory_check', 'finish']
VERSION_TWO_TOOLS = ['read_file', 'write_file', 'list_files', 'run_command', 'python',
                     'start_process', 'inspect_process', 'read_process_output', 'wait_for_process', 'cancel_process',
                     'update_memory', 'read_transcript', 'literature_search', 'graph_command', 'experiment_run',
                     'results', 'figure_render', 'paper_compile', 'context_update', 'theory_check', 'finish']


def default_config(role):
    return {'builtin_role': role, 'builtin_toolset_version': TOOLSET_VERSION}


def upgrade_default_tools(agent):
    """Upgrade only exact factory records, never approximate custom matches.

    Historical unversioned records have no edit provenance: an exact factory
    match is the only recognizable legacy state. New explicit edits are marked
    so even an identical-looking restriction is not upgraded later.
    """
    config = dict(agent.config or {})
    role = agent.role
    if role not in ROLES or config.get('tools_customized'):
        return False
    if (agent.name != role or agent.instructions not in (ROLES[role], LEGACY_ROLE_INSTRUCTIONS.get(role)) or
            agent.provider_id is not None or not agent.enabled):
        return False
    version = config.get('builtin_toolset_version')
    versioned = config.get('builtin_role') == role and version in (1, 2, 3)
    if config and not versioned:
        return False
    expected = VERSION_THREE_TOOLS if versioned and version == 3 else VERSION_TWO_TOOLS if versioned and version == 2 else LEGACY_TOOLS
    if list(agent.tools or []) != expected:
        return False
    agent.tools = list(TOOLS)
    agent.instructions = ROLES[role]
    agent.config = {**config, **default_config(role)}
    return True


def explicit_agent_config(config):
    """An API-created/edited tool selection is user-owned, even if default-like."""
    return {**(config or {}), 'tools_customized': True}
