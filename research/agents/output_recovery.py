"""Account for failed model turns and bound incremental code recovery.

An incomplete function call is never executable. A recovery is a new billed
request under the original run's remaining budget, not a transport retry.
"""
from __future__ import annotations

from .code_writes import CODE_CHUNK_CHARS


def account_usage(state, usage, elapsed):
    usage = usage or {}
    state['active_seconds'] = state.get('active_seconds', 0) + elapsed
    totals = state['totals']
    for key in ('input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_tokens'):
        totals[key] = totals.get(key, 0) + (usage.get(key) or 0)
    if usage.get('input_tokens') is None or usage.get('output_tokens') is None:
        state['usage_complete'] = False
    if 'known_cost' not in totals:
        totals['known_cost'] = totals.get('cost') or 0
    if usage.get('cost') is None:
        state['cost_complete'] = False
    else:
        totals['known_cost'] += usage['cost']
    totals['cost'] = totals['known_cost'] if state.get('cost_complete', True) else None


def record_provider_failure(state, error, elapsed, model):
    """Save public failure/usage; partial text is diagnostic, never an action."""
    account_usage(state, error.usage, elapsed)
    turn = {'step': len(state['transcript']) + 1, 'model': error.model or model,
            'status': 'incomplete' if error.code == 'incomplete_response' else 'failed',
            'content': '', 'usage': error.usage, 'elapsed_seconds': elapsed,
            'request_id': error.request_id, 'response_id': error.response_id,
            'error': {'code': error.code, 'message': str(error), 'ambiguous': error.ambiguous,
                      'incomplete_reason': error.incomplete_reason},
            'tool_calls': [], 'tool_executed': False}
    if error.partial_output:
        turn['incomplete_public_output'] = error.partial_output
        turn['partial_output_policy'] = 'Diagnostic fragments only; never parsed or executed as tool actions.'
    state['transcript'].append(turn)
    state['status'] = 'provider_error'
    state['last_provider_error'] = turn['error']
    return turn


def prepare_output_recovery(state, error, config, allowed):
    """Only recover a known output ceiling; ambiguous outcomes never retry."""
    maximum = config.get('output_recovery_attempts', 1)
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0:
        raise ValueError('output_recovery_attempts must be a nonnegative integer')
    recovery = state.get('output_recovery', {})
    usage = error.usage or {}
    if (error.code != 'incomplete_response' or error.incomplete_reason != 'max_output_tokens'
            or error.ambiguous or 'write_file_chunk' not in allowed
            or usage.get('input_tokens') is None or usage.get('output_tokens') is None
            or recovery.get('used', 0) >= maximum):
        return False
    state['output_recovery'] = {'used': recovery.get('used', 0) + 1, 'pending': True,
                                'failed_step': len(state['transcript']), 'request_id': error.request_id}
    state['status'] = 'recovering_output'
    state['messages'].append({'role': 'user', 'content': (
        'The last model response ended at its output-token ceiling. No tool from that response was executed. '
        'Its actual tokens, cost, and elapsed time are retained and count against this same run. '
        f'Continue with one write_file_chunk call containing at most {CODE_CHUNK_CHARS} raw text characters: '
        'write a small module header or one function, not the complete implementation. '
        'Use offset=0 for a new file or a previously observed exact byte offset for an existing file. '
        'The tool returns next_offset for the following chunk. Do not reproduce truncated JSON or treat it as saved code. '
        'Then build and execute the implementation incrementally under the existing budget. '
        'Diagnostic fragments remain available in read_transcript; they are not executable evidence.')})
    return True


def model_turn_tools(state, allowed):
    """A corrected request can only produce a small file edit, never finish."""
    if state.get('output_recovery', {}).get('pending'):
        return ['write_file_chunk'] if 'write_file_chunk' in allowed else []
    return list(allowed)
