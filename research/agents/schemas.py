"""Strict native function schemas for the research runtime's real tools.

Open-ended research records use a JSON string argument because strict function
schemas require closed objects. Decoding never grants extra tool authority.
"""
from __future__ import annotations

import copy
import json
import math
import re

from .policy import TOOLS
from .code_writes import CODE_CHUNK_CHARS


def string(description=''):
    return {'type': 'string', **({'description': description} if description else {})}


def integer(minimum=0, maximum=None):
    return {'type': 'integer', 'minimum': minimum, **({'maximum': maximum} if maximum is not None else {})}


def optional(schema):
    return {'anyOf': [schema, {'type': 'null'}]}


def closed(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


COMMAND = {'anyOf': [string('An actual shell command'), {'type': 'array', 'items': string(), 'minItems': 1}]}
PROCESS = {'command': COMMAND, 'cwd': optional(string()), 'env_json': optional(string('JSON object of string environment values, or null')), 'timeout': optional({'type': 'number', 'minimum': 0.001})}
OPEN_RECORDS = {'update_memory', 'graph_command', 'experiment_run', 'verification_run', 'context_update', 'theory_check'}
SPECS = {
    'read_file': ('Read an existing workspace file, in bytes.', {'path': string(), 'offset': optional(integer()), 'limit': optional(integer(1, 1_000_000))}),
    'write_file': ('Write a short complete editable file. Build larger source files incrementally with write_file_chunk.',
                   {'path': string(), 'content': {**string('Small complete file; use write_file_chunk for larger source.'), 'pattern': rf'^[\s\S]{{0,{CODE_CHUNK_CHARS}}}$'}}),
    'write_file_chunk': ('Append one small source fragment at the exact current byte offset. Start a new file at offset 0; use returned next_offset for the next chunk. Files remain editable and have no total size or chunk-count cap.',
                         {'path': string(), 'offset': integer(), 'content': {**string(f'Raw text, 1–{CODE_CHUNK_CHARS} characters; write one function or a small part at a time.'), 'pattern': rf'^[\s\S]{{1,{CODE_CHUNK_CHARS}}}$'}}),
    'list_files': ('List actual workspace files.', {'offset': optional(integer()), 'limit': optional(integer(1, 1000))}),
    'start_process': ('Launch an actual supervised process. Inspect or wait for its terminal state.', PROCESS),
    'run_command': ('Launch an actual supervised command. Submission is not completion.', PROCESS),
    'python': ('Write a small raw Python snippet, without Markdown fences, and launch the actual interpreter. Build substantial code with write_file_chunk and execute saved files with start_process.', {'code': {**string(), 'pattern': rf'^[\s\S]{{1,{CODE_CHUNK_CHARS}}}$'}, 'path': optional(string()), 'cwd': optional(string()), 'env_json': optional(string('JSON object of string environment values, or null')), 'timeout': optional({'type': 'number', 'minimum': 0.001})}),
    'inspect_process': ('Inspect an actual process receipt.', {'process_id': string()}),
    'read_process_output': ('Read actual process output incrementally.', {'process_id': string(), 'stream': optional({'type': 'string', 'enum': ['stdout', 'stderr']}), 'offset': optional(integer()), 'limit': optional(integer(1, 1_000_000))}),
    'wait_for_process': ('Release the agent slot until an actual process reaches a terminal state.', {'process_id': string(), 'poll_seconds': optional({'type': 'number', 'minimum': 1})}),
    'cancel_process': ('Cancel a launched process.', {'process_id': string()}),
    'read_context_segment': ('Read retained original context or its catalog in pages. Original control authority is recorded separately from untrusted source evidence. Preserve a concise public requirement checklist in notes while reading required pages.', {'segment_id': string('A segment ID or catalog'), 'offset': optional(integer()), 'limit': optional(integer(1, 32000)), 'notes': optional(string('Public requirement notes from the previous page, never private reasoning'))}),
    'read_transcript': ('Read previous actual agent turns from the editable transcript.', {'offset': optional(integer()), 'limit': optional(integer(1, 100))}),
    'literature_search': ('Search a literature source for real bibliographic records.', {'query': string(), 'source': optional(string()), 'limit': optional(integer(1, 100))}),
    'literature_import': ('Import a real DOI/arXiv/public PDF or workspace PDF into this project library. Supply pdf_url for full-text retrieval and acceptance_url for the actual official proceedings/journal record or decision page. This saves original page-indexed passages; a preprint alone is not acceptance evidence.', {'identifier':string(), 'pdf_url':optional(string()), 'acceptance_url':optional(string()), 'title':optional(string())}),
    'literature_read': ('Read saved publication passages and available citation metadata (authors, year, DOI and journal) in pages, or list project sources with citation metadata when source_id is null. Read full text and official decision/proceedings evidence before benchmarking accepted-paper scale.', {'source_id':optional(string()), 'offset':optional(integer()), 'limit':optional(integer(1,30))}),
    'results': ('Inspect task states and recorded metrics. The current agent task stays running until finish.', {'limit': optional(integer(1, 100))}),
    'figure_render': ('Submit a real rendering task for an existing figure.', {'figure_id': string()}),
    'figure_create': ('Create an editable figure specification from all relevant completed scientific runs. Statistical plots read identified statistics.observations or per_seed results, merge runs and retain datasets, methods, conditions, repetitions and measured checkpoints. Line charts require a measured x axis; seed is not x. Calibration uses saved predictions with datasets separated. Creation does not render outputs; call figure_render and inspect the result. data_json must not invent measurements.', {'title':string(), 'kind':{**string(),'enum':['bar','line','forest','heatmap','scatter','calibration','method','image']}, 'caption':string(), 'purpose':string('Concrete argumentative duty/role for this visual'), 'run_ids':{'type':'array','items':string(),'minItems':1}, 'style_json':optional(string('JSON object of editable figure presentation settings')), 'data_json':optional(string('JSON object of actual method structure or conceptual image prompt'))}),
    'paper_generate': ('Submit full-paper drafting and compilation from all explicitly selected completed evidence runs and rendered figure IDs. Identified statistical results automatically produce grouped publication tables, full measured curves or comparison panels, sampling notes and evidence-led interpretation. Save metrics.statistics with observations {dataset,method,metric,value,seed or unit_id,condition?,x?}, metric definitions {direction,unit,precision?}, expected design, uncertainty and named axes before invoking. Include the full relevant experiment matrix. Inspect actual results and delivery-quality gaps.', {'title':optional(string()), 'run_ids':{'type':'array','items':string(),'minItems':1}, 'figure_ids':optional({'type':'array','items':string()}), 'instructions':optional(string()), 'template':optional(string())}),
    'paper_compile': ('Submit actual LaTeX compilation. source_scope=project (or null) compiles the current project manuscript. For your own files use source_scope=workspace and source_path, with explicit asset_paths relative to the workspace. Assets and bibliography must be beneath the source file directory. The resulting bundle is published with revision protection; manual edits produce a review proposal.', {
        'source_scope': optional({'type': 'string', 'enum': ['project', 'workspace']}),
        'source_path': optional(string('Workspace-relative .tex file; required for workspace scope')),
        'bibliography_path': optional(string('Workspace-relative bibliography, or null for references.bib beside the source if present')),
        'asset_paths': optional({'type': 'array', 'items': string('Workspace-relative file or directory beneath the source directory')}),
        'title': optional(string('Title used for the compiled workspace manuscript'))}),
    'finish': ('Finish only after required artifacts exist, launched processes are terminal and actual evidence supports the outcome.', {'summary': string('A concise public summary grounded in observed evidence'), 'artifacts': {'type': 'array', 'items': string('Relative artifact path')}}),
    'update_memory': ('Update editable public research notes; this does not execute a computation.', {'arguments_json': string('JSON object with public goal, decisions, evidence, unresolved_questions and next_experiment as needed')}),
    'graph_command': ('Apply an editable graph command.', {'arguments_json': string('JSON object containing the graph command and its arguments')}),
    'experiment_run': ('Submit a real experiment task.', {'arguments_json': string('JSON object containing the experiment run configuration')}),
    'verification_run': ('Execute an existing editable verification node. The worker checks actual source artifacts and rerun outputs; an agent verdict cannot grant acceptance. Create the node through graph_command first, and inspect its completed verification state with results.', {'arguments_json': string('JSON object containing node_id and optional request_id; the node must have config.kind=verification')}),
    'context_update': ('Update editable node context metadata.', {'arguments_json': string('JSON object containing node context overrides')}),
    'theory_check': ('Submit a real symbolic or numerical theory check.', {'arguments_json': string('JSON object containing the theory check configuration')}),
}


def tool_definitions(allowed=None):
    names = TOOLS if allowed is None else allowed
    unknown = set(names) - set(SPECS)
    if unknown:
        raise ValueError('Enabled tools have no native schema: ' + ', '.join(sorted(unknown)))
    return [{'type': 'function', 'name': name, 'description': SPECS[name][0],
             'parameters': closed(copy.deepcopy(SPECS[name][1])), 'strict': True} for name in dict.fromkeys(names)]


def _validate(value, schema):
    if 'anyOf' in schema:
        for choice in schema['anyOf']:
            try:
                _validate(value, choice)
                return
            except ValueError:
                pass
        raise ValueError('Argument does not match an allowed type')
    kind = schema['type']
    valid = {'string': isinstance(value, str), 'integer': isinstance(value, int) and not isinstance(value, bool),
             'number': isinstance(value, (int, float)) and not isinstance(value, bool),
             'object': isinstance(value, dict), 'array': isinstance(value, list), 'null': value is None}[kind]
    if not valid:
        raise ValueError('Argument must have type ' + kind)
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Argument is outside its allowed values')
    if kind == 'string' and 'pattern' in schema and not re.fullmatch(schema['pattern'], value):
        raise ValueError('Argument text does not match its allowed pattern or size')
    if kind in ('number', 'integer') and not math.isfinite(value):
        raise ValueError('Numeric argument must be finite')
    if kind in ('number', 'integer') and (value < schema.get('minimum', value) or value > schema.get('maximum', value)):
        raise ValueError('Numeric argument is outside its allowed range')
    if kind == 'object':
        if set(value) - set(schema['properties']) or set(schema['required']) - set(value):
            raise ValueError('Arguments contain unknown fields or omit required fields')
        for key, item in value.items():
            _validate(item, schema['properties'][key])
    elif kind == 'array':
        if len(value) < schema.get('minItems', 0):
            raise ValueError('Argument array is empty')
        for item in value:
            _validate(item, schema['items'])


def decode_tool_call(call, allowed):
    """Validate native arguments before a persistent executable intent is saved."""
    name = call['name']
    if name not in allowed or name not in SPECS:
        raise ValueError('Model selected a tool that is not enabled')
    arguments = call['arguments']
    _validate(arguments, closed(SPECS[name][1]))
    args = {key: value for key, value in arguments.items() if value is not None}
    if name in OPEN_RECORDS:
        args = json.loads(args['arguments_json'])
        if not isinstance(args, dict):
            raise ValueError('arguments_json must encode an object')
    if 'env_json' in args:
        env = json.loads(args.pop('env_json'))
        if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()):
            raise ValueError('env_json must encode an object of string values')
        args['env'] = env
    return {'tool': name, 'arguments': args, 'call_id': call['call_id']}


def json_action_schema(allowed):
    """Constrain JSON transports to the currently enabled FOREST actions."""
    definitions = tool_definitions(allowed)
    return closed({'tool': {'type': 'string', 'enum': [d['name'] for d in definitions]},
                   'arguments': {'anyOf': [d['parameters'] for d in definitions]},
                   'summary': string('Short public action rationale')})
