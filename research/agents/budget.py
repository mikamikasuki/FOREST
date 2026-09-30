"""Shared API spending reservations. Ordinary editable usage records, no content IDs."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import math
from sqlalchemy import select, text, update
from services.api.db import Session, Provider, ModelRequest, Project, TaskRun


class BudgetExceeded(RuntimeError):
    pass


def agent_budget_update(current, update):
    """Validate an explicit editable run budget without changing outer limits."""
    if not isinstance(update, dict) or not update:
        raise ValueError('agent_budget must be a nonempty object')
    integers = {'steps', 'input_tokens', 'output_tokens'}
    allowed = integers | {'cost', 'active_seconds'}
    if set(update) - allowed:
        raise ValueError('Unknown agent budget field: ' + ', '.join(sorted(set(update) - allowed)))
    for key, value in update.items():
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            raise ValueError(f'agent_budget.{key} must be a positive finite number')
        if key in integers and not isinstance(value, int):
            raise ValueError(f'agent_budget.{key} must be an integer')
    return {**(current or {}), **update}


def number(value, field):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'{field} must be a finite nonnegative number') from None
    if not result.is_finite() or result < 0:
        raise ValueError(f'{field} must be a finite nonnegative number')
    return result


def micro(value):
    return int((number(value, 'USD amount') * 1000000).to_integral_value(rounding=ROUND_CEILING))


def rates(pricing):
    if pricing.get('currency', 'USD') != 'USD':
        raise ValueError('API budgets require USD prices')
    required = ('input_per_million', 'output_per_million')
    if any(pricing.get(k) is None for k in required):
        raise ValueError('Configure input/output token prices before enabling paid API calls')
    input_rate = number(pricing['input_per_million'], required[0])
    output_rate = number(pricing['output_per_million'], required[1])
    cached_rate = number(pricing.get('cached_input_per_million', input_rate), 'cached_input_per_million')
    if input_rate == 0 or output_rate == 0 or cached_rate > input_rate:
        raise ValueError('Paid token prices must be positive, with cached input no higher than normal input')
    return input_rate, cached_rate, output_rate


def usage_micro(usage, pricing):
    if usage.get('input_tokens') is None or usage.get('output_tokens') is None:
        return None
    inp, cached, out = rates(pricing)
    input_tokens = number(usage['input_tokens'], 'input_tokens')
    output_tokens = number(usage['output_tokens'], 'output_tokens')
    cached_tokens = number(usage.get('cached_input_tokens', 0) or 0, 'cached_input_tokens')
    if cached_tokens > input_tokens:
        raise ValueError('Cached input cannot exceed total input')
    # USD / million tokens is numerically equal to micro-USD / token.
    return int(((input_tokens-cached_tokens)*inp+cached_tokens*cached+output_tokens*out).to_integral_value(rounding=ROUND_CEILING))


def lock_provider(s, ident):
    if s.bind.dialect.name == 'sqlite':
        connection = s.connection()
        if not connection.connection.driver_connection.in_transaction:
            s.execute(text('BEGIN IMMEDIATE'))
    provider = s.scalar(select(Provider).where(Provider.id == ident).with_for_update())
    if provider is None:
        raise BudgetExceeded('The model provider is no longer available')
    return provider


def totals(rows):
    estimated = sum(r.estimated_microusd or 0 for r in rows)
    reserved = sum(r.reserved_microusd for r in rows if r.status in ('reserved', 'uncertain'))
    return estimated, reserved


def interrupt_run_reservations(run_id, *, session=None):
    """Retain unacknowledged spend when a run or execution attempt stops.

    Use the caller's transaction when changing run state. This conditional
    update never releases budget or overwrites a concurrently settled response,
    and takes no provider lock while lifecycle code holds a project lock.
    A later real response can still settle an uncertain reservation normally.
    """
    if not run_id:
        return 0
    statement = (update(ModelRequest)
                 .where(ModelRequest.run_id == run_id, ModelRequest.status == 'reserved')
                 .values(status='uncertain'))
    if session is not None:
        return session.execute(statement).rowcount
    with Session.begin() as s:
        return s.execute(statement).rowcount


def usage_summary(provider_id):
    with Session() as s:
        provider = s.get(Provider, provider_id)
        if provider is None:
            raise ValueError('Unknown provider')
        rows = list(s.scalars(select(ModelRequest).where(ModelRequest.provider_id == provider_id).order_by(ModelRequest.created_at.desc())))
        estimated, reserved = totals(rows)
        limit = provider.config.get('budget_usd')
        return {'provider_id':provider_id, 'limit_usd':limit,
                'estimated_cost_usd':estimated/1000000, 'reserved_usd':reserved/1000000,
                'remaining_usd':max(0, micro(limit)-estimated-reserved)/1000000 if limit is not None else None,
                'cost_source':'configured_rates_estimate', 'request_count':len(rows),
                'uncertain_requests':sum(r.status == 'uncertain' for r in rows),
                'requests':[{'id':r.id,'run_id':r.run_id,'project_id':r.project_id,'model':r.model,
                    'api':r.details.get('api', 'text'),
                    'status':r.status,'estimated_cost_usd':r.estimated_microusd/1000000 if r.estimated_microusd is not None else None,
                    'reserved_usd':r.reserved_microusd/1000000 if r.status in ('reserved','uncertain') else 0,
                    'request_id':r.request_id,'usage':r.details.get('usage'), 'created_at':r.created_at} for r in rows[:100]]}


def make_request_guard(provider):
    ident = provider.get('id')
    if not ident:
        raise BudgetExceeded('External model requests require a saved provider and an explicit spending limit')
    context = provider.get('_usage_context') or {}

    def guard(event):
        phase = event['phase']
        with Session.begin() as s:
            current = lock_provider(s, ident)
            if phase == 'before':
                if not current.allow_paid:
                    raise BudgetExceeded('Paid API calls are disabled for this provider')
                limit = current.config.get('budget_usd')
                if limit is None:
                    raise BudgetExceeded('Set an explicit provider budget before making paid API requests')
                api = event.get('api') or 'text'
                if api == 'images':
                    image_config = current.config.get('image_generation')
                    snapshot = event.get('image_generation') or provider.get('config', {}).get('image_generation')
                    if (not isinstance(image_config, dict) or image_config != snapshot
                            or not isinstance(image_config.get('model'), str)
                            or not image_config['model'].strip()
                            or event.get('model') != image_config['model']):
                        raise BudgetExceeded('Image model or settings changed. Queue a new run with the explicitly configured image model.')
                    ceiling = image_config.get('max_request_usd')
                    if (isinstance(ceiling, bool) or not isinstance(ceiling, (int, float))
                            or not math.isfinite(ceiling) or ceiling <= 0):
                        raise BudgetExceeded('An image request requires a positive saved image_generation.max_request_usd upper bound')
                    reservation = micro(ceiling)
                    details = {'api': 'images', 'image_generation': image_config,
                               'max_request_usd': ceiling, 'attempt': event.get('attempt')}
                else:
                    pricing = event.get('pricing') or provider.get('config', {}).get('pricing', {})
                    if event.get('model') != current.model or pricing != current.config.get('pricing', {}):
                        raise BudgetExceeded('Provider model or token prices changed. Queue a new run to use the current configuration.')
                    inp, _, out = rates(pricing)
                    max_output = int(event.get('max_output_tokens') or 0)
                    if max_output < 1:
                        raise BudgetExceeded('A paid request needs an explicit maximum output-token budget')
                    # Byte-count bound includes serialized messages, tools, and generous
                    # protocol overhead; cache discounts never reduce the reservation.
                    input_bound = int(event.get('input_bytes') or 0) + 2048
                    reservation = int((input_bound*inp+max_output*out).to_integral_value(rounding=ROUND_CEILING))
                    details = {'api': api, 'pricing': pricing, 'input_token_upper_bound': input_bound,
                               'max_output_tokens': max_output, 'attempt': event.get('attempt')}
                rows = list(s.scalars(select(ModelRequest).where(ModelRequest.provider_id == ident)))
                used, reserved = totals(rows)
                if used + reserved + reservation > micro(limit):
                    raise BudgetExceeded(f'Provider API budget cannot reserve this request (${reservation/1000000:.4f}); remaining ${max(0,micro(limit)-used-reserved)/1000000:.4f}')
                if context.get('project_id'):
                    project = s.scalar(select(Project).where(Project.id==context['project_id']).with_for_update())
                    if project is None:
                        raise BudgetExceeded('The request project is no longer available')
                    if not project.budget.get('allow_paid'):
                        raise BudgetExceeded('Paid API calls are disabled for this project')
                    project_limit = project.budget.get('cost_usd') if project else None
                    if project_limit is not None:
                        project_rows = list(s.scalars(select(ModelRequest).where(ModelRequest.project_id == project.id)))
                        pu, pr = totals(project_rows)
                        if pu + pr + reservation > micro(project_limit):
                            raise BudgetExceeded('Project API spending limit reached')
                if context.get('run_id'):
                    run = s.scalar(select(TaskRun).where(TaskRun.id == context['run_id']).with_for_update())
                    if (run is None or run.project_id != context.get('project_id')
                            or run.status not in ('running', 'pausing')):
                        raise BudgetExceeded('The request run is no longer executing')
                    run_limit = (run.config.get('agent_budget') or {}).get('cost')
                    if run_limit is not None:
                        run_rows = list(s.scalars(select(ModelRequest).where(ModelRequest.run_id == run.id)))
                        ru, rr = totals(run_rows)
                        if ru + rr + reservation > micro(run_limit):
                            raise BudgetExceeded('Agent API spending limit cannot reserve this request; settled and uncertain requests remain charged against the same run')
                record = ModelRequest(provider_id=ident, project_id=context.get('project_id'),run_id=context.get('run_id'),
                    model=event.get('model') or provider['model'], reserved_microusd=reservation,
                    details=details)
                s.add(record); s.flush()
                return record.id
            record = s.get(ModelRequest, event.get('reservation'))
            if not record or record.provider_id != ident:
                raise ValueError('Unknown API spending reservation')
            if record.status not in ('reserved','uncertain'):
                return record.id
            record.request_id = event.get('request_id') or record.request_id
            record.response_id = event.get('response_id') or record.response_id
            if phase == 'after':
                usage = event.get('usage') or {}
                # Image token units and charges differ from the text model. The
                # compatible API has no verified USD-charge field in its standard
                # response, so keep the saved upper bound until billing is reconciled.
                # Use the reservation's API, never an event-supplied settlement mode.
                measured = None if record.details.get('api') == 'images' else usage_micro(usage, record.details['pricing'])
                record.estimated_microusd = measured
                record.status = 'settled' if measured is not None else 'uncertain'
                record.details = {**record.details,'usage':usage,'outcome':event.get('outcome') or event.get('status'),
                    'response_status': event.get('status'),
                    'incomplete_reason': event.get('incomplete_reason') if event.get('incomplete_reason') in ('max_output_tokens', 'content_filter') else None,
                    'reservation_exceeded':measured is not None and measured>record.reserved_microusd}
                if record.details.get('api') == 'images':
                    record.details = {**record.details, 'cost_source': 'unknown',
                                      'accounting_note': 'Image USD charge is unverified; reserved upper bound retained'}
                if measured is not None and measured > record.reserved_microusd:
                    current.allow_paid = False
                    record.details = {**record.details,'action':'Provider disabled because actual token pricing exceeded reserved bound; review prices before continuing'}
            elif phase == 'error':
                record.status = 'uncertain' if event.get('ambiguous', True) else 'rejected'
                record.details = {**record.details,'http_status':event.get('http_status'),'retryable':bool(event.get('retryable'))}
            else:
                raise ValueError('Unknown API accounting phase')
            return record.id
    return guard
