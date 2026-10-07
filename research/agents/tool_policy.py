"""One effective tool set for presentation, transport and execution.

An omitted list inherits; an explicit empty list grants no tools. Chunked writes
are included only when a whole-file write is granted at that same layer.
"""
from .policy import TOOLS


def validate_permissions(config):
    if not isinstance(config, dict): raise ValueError('Configuration must be an object')
    for key in ('tools', 'human_review_tools'):
        if key not in config: continue
        value = config[key]
        if not isinstance(value, list) or any(not isinstance(name, str) for name in value):
            raise ValueError(key+' must be a list of tool names; omit it to inherit')
        known = set(TOOLS) | ({'*'} if key == 'human_review_tools' else set())
        if len(set(value)) != len(value) or set(value)-known:
            raise ValueError(key+' must contain unique known tool names')


def effective_tools(role_tools=None, *, project=None, node=None, run=None):
    def normalized(value):
        if not isinstance(value, (list, tuple)) or any(not isinstance(x, str) for x in value):
            raise ValueError('Tool permissions must be a list of names')
        names = set(value)
        if 'write_file' in names:
            names.add('write_file_chunk')
        return names

    result = normalized(TOOLS if role_tools is None else role_tools)
    for config in (project, node, run):
        if config is not None and 'tools' in config:
            result &= normalized(config['tools'])
    return sorted(result)


def runtime_tools(session, run, fallback=None):
    from services.api.db import Agent, Node, Project
    from sqlalchemy import select
    project = session.get(Project, run.project_id)
    node = session.get(Node, run.node_id) if run.node_id else None
    role = run.config.get('role', 'Researcher')
    agent = (session.get(Agent, run.config['agent_id']) if run.config.get('agent_id') else
             session.scalar(select(Agent).where(Agent.role == role)))
    if not project or (agent and not agent.enabled):
        return []
    allowed = effective_tools(agent.tools if agent else fallback,
                              project=project.config, node=node.config if node else None,
                              run=run.config)
    return allowed
