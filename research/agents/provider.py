"""Real model transports. No tool execution or invented fallback responses here."""
from __future__ import annotations

import json
import math
import time
from urllib.parse import urlparse

import httpx


class ProviderError(RuntimeError):
    """A safe public error; raw HTTP bodies, URLs and credentials are excluded."""

    def __init__(self, message, *, code='provider_error', request_id=None, usage=None,
                 retryable=False, ambiguous=False, http_status=None, response_id=None,
                 model=None, incomplete_reason=None, partial_output=None):
        super().__init__(message)
        self.code = code
        self.request_id = request_id
        self.usage = usage
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.http_status = http_status
        self.response_id = response_id
        self.model = model
        self.incomplete_reason = incomplete_reason
        # Public generated content only; never headers, credentials or reasoning.
        # This is diagnostic data and must never be decoded as an executable call.
        self.partial_output = partial_output or []


def incomplete_public_output(value, api):
    """Retain public text/argument fragments without private reasoning items."""
    if api == 'responses':
        selected = []
        for item in value.get('output') or []:
            if not isinstance(item, dict):
                continue
            if item.get('type') == 'function_call':
                selected.append({key: item[key] for key in ('type', 'name', 'arguments', 'call_id', 'status')
                                 if isinstance(item.get(key), str)})
            elif item.get('type') == 'message':
                for part in item.get('content') or []:
                    if isinstance(part, dict) and part.get('type') == 'output_text' and isinstance(part.get('text'), str):
                        selected.append({'type': 'output_text', 'text': part['text']})
        return selected
    if api == 'ollama':
        message = value.get('message') or {}
    else:
        choices = value.get('choices') or []
        message = choices[0].get('message', {}) if choices and isinstance(choices[0], dict) else {}
    text = message.get('content') if isinstance(message, dict) else None
    return [{'type': 'output_text', 'text': text}] if isinstance(text, str) else []


def response_incomplete_reason(value):
    details = value.get('incomplete_details') if isinstance(value, dict) else None
    reason = details.get('reason') if isinstance(details, dict) else None
    return reason if reason in ('max_output_tokens', 'content_filter') else None


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def http_error_policy(status):
    """Only explicit pre-generation rejections can release a reservation.

    A redirect or an unfamiliar response is not evidence that no billable work
    occurred. Keep those reservations until usage can be reconciled.
    """
    retryable = status in (408, 429, 500, 502, 503, 504)
    rejected = status in (400, 401, 403, 404, 405, 413, 415, 422, 429)
    return retryable, not rejected


def context_window_rejection(value, status):
    """Classify only explicit window rejections; never publish raw error bodies."""
    if status not in (400, 413, 422) or not isinstance(value, dict):
        return False
    error = value.get('error')
    if not isinstance(error, dict):
        return False
    return error.get('code') in {'context_length_exceeded', 'context_window_exceeded',
                                 'max_context_length_exceeded', 'prompt_too_long'}


def configured_cost(usage, pricing):
    """Estimate token charges only from explicitly configured USD rates."""
    if not pricing or pricing.get('currency', 'USD') != 'USD':
        return None
    rates = {}
    for key in ('input_per_million', 'output_per_million', 'cached_input_per_million'):
        value = pricing.get(key)
        if value is None and key == 'cached_input_per_million':
            continue
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
            return None
        rates[key] = value
    incoming, outgoing = usage.get('input_tokens'), usage.get('output_tokens')
    if _count(incoming) is None or _count(outgoing) is None:
        return None
    cached = usage.get('cached_input_tokens')
    if 'cached_input_per_million' in rates:
        if _count(cached) is None or cached > incoming:
            return None
    else:
        cached = 0
    return ((incoming - cached) * rates['input_per_million'] + cached * rates.get('cached_input_per_million', 0)
            + outgoing * rates['output_per_million']) / 1_000_000


def normalize_usage(value, api, pricing=None):
    raw = value.get('usage') or {}
    if not isinstance(raw, dict):
        raw = {}
    if api == 'ollama':
        incoming, outgoing = value.get('prompt_eval_count'), value.get('eval_count')
        cached = reasoning = None
    elif api == 'responses':
        incoming, outgoing = raw.get('input_tokens'), raw.get('output_tokens')
        cached = (raw.get('input_tokens_details') or {}).get('cached_tokens')
        reasoning = (raw.get('output_tokens_details') or {}).get('reasoning_tokens')
    else:
        incoming, outgoing = raw.get('prompt_tokens'), raw.get('completion_tokens')
        cached = (raw.get('prompt_tokens_details') or {}).get('cached_tokens')
        reasoning = (raw.get('completion_tokens_details') or {}).get('reasoning_tokens')
    usage = {'input_tokens': _count(incoming), 'output_tokens': _count(outgoing),
             'cached_input_tokens': _count(cached), 'reasoning_tokens': _count(reasoning)}
    usage['cost'] = configured_cost(usage, pricing)
    usage['cost_source'] = 'configured_rates_estimate' if usage['cost'] is not None else 'unknown'
    return usage


def responses_input(messages):
    """Replay native items including encrypted reasoning and correlated outputs."""
    items = []
    for message in messages:
        if message.get('native_output') is not None:
            items.extend(message['native_output'])
        elif message.get('native_call_id'):
            items.append({'type': 'function_call_output', 'call_id': message['native_call_id'], 'output': message['content']})
        else:
            items.append({'role': message['role'], 'content': message['content']})
    return items


def parse_response(value, api, *, request_id=None, pricing=None):
    """Parse an actual HTTP response; reject partial, refused, or invalid actions."""
    if not isinstance(value, dict):
        raise ProviderError('Provider returned a non-object response', code='invalid_response', request_id=request_id)
    usage = normalize_usage(value, api, pricing)

    def fail(message, code, reason=None):
        raise ProviderError(message, code=code, request_id=request_id, usage=usage,
                            response_id=value.get('id'), model=value.get('model'), incomplete_reason=reason,
                            partial_output=incomplete_public_output(value, api) if code == 'incomplete_response' else None)

    calls, output = [], []
    if api == 'responses':
        status = value.get('status')
        if status != 'completed':
            # Reasons are a finite public API vocabulary; never echo arbitrary bodies.
            reason = response_incomplete_reason(value)
            suffix = f' ({reason})' if reason in ('max_output_tokens', 'content_filter') else ''
            fail('Model response did not complete' + suffix, 'incomplete_response' if status == 'incomplete' else 'response_not_completed',
                 reason if reason in ('max_output_tokens', 'content_filter') else None)
        output = value.get('output')
        if not isinstance(output, list):
            fail('Provider response has no output array', 'invalid_response')
        texts = []
        for item in output:
            if not isinstance(item, dict):
                fail('Provider returned an invalid output item', 'invalid_response')
            if item.get('type') == 'message':
                if item.get('status') not in (None, 'completed'):
                    fail('Model message did not complete', 'incomplete_response')
                for part in item.get('content', []):
                    if part.get('type') == 'refusal':
                        fail('Model declined this request', 'refusal')
                    if part.get('type') == 'output_text' and isinstance(part.get('text'), str):
                        texts.append(part['text'])
            elif item.get('type') == 'function_call':
                if item.get('status') not in (None, 'completed'):
                    fail('Model tool call did not complete', 'incomplete_response')
                if not all(isinstance(item.get(key), str) and item[key] for key in ('call_id', 'name', 'arguments')):
                    fail('Provider returned an invalid function call', 'invalid_tool_call')
                try:
                    arguments = json.loads(item['arguments'])
                except (ValueError, TypeError):
                    fail('Provider returned invalid function arguments', 'invalid_tool_call')
                if not isinstance(arguments, dict):
                    fail('Function arguments must be an object', 'invalid_tool_call')
                calls.append({'call_id': item['call_id'], 'name': item['name'], 'arguments': arguments})
        text = ''.join(texts)
    elif api == 'ollama':
        if value.get('done') is not True or value.get('done_reason') == 'length':
            fail('Local model response did not complete', 'incomplete_response', 'max_output_tokens' if value.get('done_reason') == 'length' else None)
        text = (value.get('message') or {}).get('content')
        status = 'completed'
    else:
        choices = value.get('choices') or []
        if not choices or not isinstance(choices[0], dict):
            fail('Provider returned no completion choice', 'invalid_response')
        choice = choices[0]
        if choice.get('finish_reason') != 'stop':
            fail('Model completion did not finish normally', 'incomplete_response', 'max_output_tokens' if choice.get('finish_reason') == 'length' else None)
        message = choice.get('message') or {}
        if message.get('refusal'):
            fail('Model declined this request', 'refusal')
        text = message.get('content')
        status = 'completed'
    if not isinstance(text, str) or (not text.strip() and not calls):
        fail('Provider returned no usable text or function call', 'empty_response')
    return {'text': text, 'usage': usage, 'model': value.get('model'), 'status': status,
            'request_id': request_id, 'response_id': value.get('id'), 'tool_calls': calls, 'output': output}


class ModelClient:
    def __init__(self, provider, key='', allow_paid=False, request_guard=None):
        self.provider, self.key = provider, key
        parsed = urlparse(provider['base_url'])
        local = parsed.hostname in ('localhost', '127.0.0.1', '::1')
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ProviderError('Provider URL must be an HTTP(S) endpoint without credentials, query or fragment', code='configuration')
        if not local and parsed.scheme != 'https':
            raise ProviderError('External model endpoints require HTTPS', code='configuration')
        if not local and not (provider.get('allow_paid') and allow_paid):
            raise ProviderError('External model usage is disabled. Enable provider and project paid/external usage authorization in Settings.', code='authorization')
        self.base = provider['base_url'].rstrip('/')
        self.config = provider.get('config') or {}
        self.api = ('ollama' if provider['kind'] == 'ollama' else self.config.get('api') or
                    ('responses' if parsed.hostname == 'api.openai.com' or provider['kind'] == 'openai_responses' else 'chat_completions'))
        if self.api not in ('responses', 'chat_completions', 'ollama'):
            raise ProviderError('Unsupported provider API selection', code='configuration')
        self.timeout = float(self.config.get('timeout', 120))
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ProviderError('Provider timeout must be a positive finite number', code='configuration')
        self.request_guard = request_guard
        if not local and request_guard is None:
            from .budget import make_request_guard
            self.request_guard = make_request_guard(provider)

    @property
    def native_tools(self):
        return self.api == 'responses'

    def build_request(self, messages, json_mode=True, tools=None):
        cfg, model = self.config, self.provider['model']
        maximum = int(cfg.get('max_output_tokens', cfg.get('max_tokens', 2048)))
        if maximum <= 0:
            raise ProviderError('Maximum output tokens must be positive', code='configuration')
        plain = [{'role': m['role'], 'content': m['content']} for m in messages]
        if self.api == 'ollama':
            payload = {'model': model, 'messages': plain, 'stream': False, 'options': {
                'temperature': cfg.get('temperature', .2), 'num_predict': maximum, 'num_ctx': cfg.get('context_length', 8192)}}
            if json_mode:
                payload['format'] = 'json'
            return '/api/chat', payload
        if self.api == 'chat_completions':
            payload = {'model': model, 'messages': plain, 'temperature': cfg.get('temperature', .2), 'max_tokens': maximum}
            if json_mode and cfg.get('json_mode', True):
                payload['response_format'] = {'type': 'json_object'}
            return '/chat/completions', payload
        payload = {'model': model, 'input': responses_input(messages), 'store': False,
                   'include': ['reasoning.encrypted_content'], 'max_output_tokens': maximum}
        # Only explicitly selected per-model parameters are sent. In particular,
        # legacy temperature defaults are not applied to reasoning models.
        parameters = cfg.get('model_parameters') or {}
        allowed = {'temperature', 'top_p', 'reasoning', 'text', 'service_tier'}
        if not isinstance(parameters, dict) or set(parameters) - allowed:
            raise ProviderError('Unsupported model_parameters; use temperature, top_p, reasoning, text or service_tier', code='configuration')
        if 'text' in parameters and (not isinstance(parameters['text'], dict) or set(parameters['text']) - {'verbosity'}):
            raise ProviderError('model_parameters.text supports only verbosity', code='configuration')
        payload.update(parameters)
        if tools:
            payload.update(tools=tools, tool_choice='required', parallel_tool_calls=False)
        elif json_mode:
            payload['text'] = {**payload.get('text', {}), 'format': {'type': 'json_object'}}
        return '/responses', payload

    def _event(self, phase, **fields):
        if self.request_guard:
            return self.request_guard({'phase': phase, 'provider_id': self.provider.get('id'),
                                       'model': self.provider['model'], 'api': self.api, **fields})
        return None

    def complete(self, messages, json_mode=True, tools=None):
        endpoint, payload = self.build_request(messages, json_mode, tools)
        cfg, start = self.config, time.monotonic()
        retries = max(0, min(4, int(cfg.get('max_retries', 2))))
        headers = {'Authorization': f'Bearer {self.key}'} if self.key else {}
        encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
        with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
            for attempt in range(retries + 1):
                token = self._event('before', attempt=attempt + 1, input_bytes=len(encoded),
                                    max_output_tokens=payload.get('max_output_tokens', payload.get('max_tokens', payload.get('options', {}).get('num_predict'))),
                                    pricing=cfg.get('pricing'))
                try:
                    response = client.post(self.base + endpoint, headers=headers, content=encoded)
                except httpx.RequestError as exc:
                    safe_retry = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
                    self._event('error', reservation=token, attempt=attempt + 1, request_id=None,
                                http_status=None, retryable=safe_retry, ambiguous=not safe_retry)
                    if safe_retry and attempt < retries:
                        time.sleep(min(2 ** attempt, 4))
                        continue
                    raise ProviderError('Model connection failed before a response was received' if safe_retry else 'Model request outcome is unknown after a transport failure; inspect usage before retrying',
                                        code='transport_error', retryable=safe_retry, ambiguous=not safe_retry) from None
                request_id = response.headers.get('x-request-id')
                if not 200 <= response.status_code < 300:
                    code = response.status_code
                    retryable, ambiguous = http_error_policy(code)
                    self._event('error', reservation=token, attempt=attempt + 1, request_id=request_id,
                                http_status=code, retryable=retryable, ambiguous=ambiguous)
                    try:
                        rejected_context = context_window_rejection(response.json(), code)
                    except (ValueError, TypeError):
                        rejected_context = False
                    if rejected_context:
                        raise ProviderError('Provider rejected the input before generation because its context window was exceeded',
                            code='context_window_exceeded', request_id=request_id, http_status=code,
                            usage={'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0, 'reasoning_tokens': 0,
                                   'cost': 0, 'cost_source': 'explicit_pre_generation_rejection'})
                    if retryable and attempt < retries:
                        try:
                            delay = float(response.headers.get('retry-after', 2 ** attempt))
                        except ValueError:
                            delay = 2 ** attempt
                        time.sleep(max(0, min(delay, 10)))
                        continue
                    raise ProviderError(f'Model endpoint returned HTTP {code}', code='http_error', request_id=request_id,
                                        http_status=code, retryable=retryable, ambiguous=ambiguous)
                try:
                    value = response.json()
                    usage = normalize_usage(value, self.api, cfg.get('pricing')) if isinstance(value, dict) else normalize_usage({}, self.api)
                except (ValueError, TypeError, AttributeError):
                    self._event('error', reservation=token, attempt=attempt + 1, request_id=request_id,
                                http_status=response.status_code, retryable=False, ambiguous=True)
                    raise ProviderError('Provider returned an unreadable response; token usage is unknown', code='invalid_response',
                                        request_id=request_id, ambiguous=True) from None
                # Account for usage even when parsing below rejects a refusal,
                # incomplete output, invalid tool arguments, or empty response.
                self._event('after', reservation=token, attempt=attempt + 1, request_id=request_id,
                            response_id=value.get('id') if isinstance(value, dict) else None,
                            usage=usage, status=value.get('status') if isinstance(value, dict) else None, outcome='response_received',
                            incomplete_reason=response_incomplete_reason(value))
                try:
                    result = parse_response(value, self.api, request_id=request_id, pricing=cfg.get('pricing'))
                except (AttributeError, TypeError, KeyError):
                    raise ProviderError('Provider returned an invalid response structure', code='invalid_response', request_id=request_id, usage=usage) from None
                result['elapsed'] = time.monotonic() - start
                result['model'] = result['model'] or self.provider['model']
                return result

    def models(self):
        headers = {'Authorization': f'Bearer {self.key}'} if self.key else {}
        try:
            with httpx.Client(timeout=min(self.timeout, 20), follow_redirects=False) as client:
                response = client.get(self.base + ('/api/tags' if self.api == 'ollama' else '/models'), headers=headers)
            if not 200 <= response.status_code < 300:
                raise ProviderError(f'Model listing returned HTTP {response.status_code}', code='http_error',
                                    http_status=response.status_code, request_id=response.headers.get('x-request-id'))
            return response.json()
        except (httpx.RequestError, ValueError):
            raise ProviderError('Could not read the model listing', code='transport_error') from None
