"""Real context-file packing checks; no provider responses are substituted."""
import json
from pathlib import Path
from types import SimpleNamespace

from research.agents.context_store import (ContextStore,pack_context,message_chars,
                                           can_attempt_direct,original_task_brief,recover_context_rejection)
from research.agents.provider import ProviderError


def test_instruction_receipt_requires_successful_transport_of_complete_current_controls(tmp_path):
    from research.agents.context_store import PAGE_PREFIX
    instruction = {'id': 'owner-correction', 'text': 'Keep this entire correction. ' * 250,
                   'scope': 'project', 'boundary': 'next_request', 'accepted_at': '2026-10-07T00:00:00Z'}
    state = {'messages': [{'role': 'system', 'content': 'Use actual evidence.'},
        {'role': 'user', 'content': json.dumps({'context': {'controls': {'goal': 'Study',
            'owner_instructions': [instruction]}}})}], 'transcript': []}
    pack_context(state, tmp_path, 8000)
    assert recover_context_rejection(state, ProviderError('Explicit rejection', code='context_window_exceeded'))
    packed = pack_context(state, tmp_path, 8000)
    store = ContextStore(tmp_path)
    identifier = store.index['current']['controls:1']
    assert store.confirm_transport_delivery(packed, [instruction], payload_sha256='initial') == []
    # A local page read whose request failed must not become a delivery receipt.
    first = store.read(identifier, 0)
    assert ContextStore(tmp_path).confirm_transport_delivery([], [instruction], payload_sha256='unrelated') == []
    requests = []
    while store.pending():
        pending = store.pending()
        store.read(pending['segment_id'], pending['offset'])
        requests.append(pack_context(state, tmp_path, 8000))
        store = ContextStore(tmp_path)
    for index, request in enumerate(requests):
        assert ContextStore(tmp_path).confirm_transport_delivery(request, [instruction], payload_sha256=str(index)) == []
    # Re-deliver the missing page after a process restart. Whole coverage now
    # certifies the actual original control, including fields split over pages.
    first_request = [{'role': 'user', 'content': PAGE_PREFIX + json.dumps(first)}]
    assert ContextStore(tmp_path).confirm_transport_delivery(first_request, [instruction], payload_sha256='retry') == [instruction['id']]
    changed = {**instruction, 'text': 'A newly edited authoritative instruction'}
    state['messages'][1]['content'] = json.dumps({'context': {'controls': {'owner_instructions': [changed]}}})
    pack_context(state, tmp_path, 8000)
    assert ContextStore(tmp_path).confirm_transport_delivery(first_request, [changed], payload_sha256='late-old-page') == []
    # An evidence segment containing the same JSON never gains user authority.
    evidence = ContextStore(tmp_path)
    untrusted = evidence.put('source', state['messages'][1]['content'], origin='Source document')
    page = evidence.read(untrusted, 0)
    assert evidence.confirm_transport_delivery([{'role': 'user', 'content': PAGE_PREFIX + json.dumps(page)}],
        [changed], payload_sha256='source') == []


def test_soft_character_hint_does_not_force_full_writer_task_into_paging():
    messages=[{'role':'system','content':'Write the complete manuscript JSON.'},
              {'role':'user','content':'Original manuscript condition. '*4000}]
    remote=SimpleNamespace(api='responses',config={})
    assert message_chars(messages)>64000
    assert can_attempt_direct(remote,messages,64000)
    remote.config={'context_window_tokens':16384,'max_output_tokens':4096}
    assert not can_attempt_direct(remote,messages,64000)
    remote.config={'working_context_chars':1000000}
    assert can_attempt_direct(remote,messages,64000)


def manuscript_task():
    schema='Return a full manuscript JSON with all six section roles and bound numeric references.'
    goal='Write the complete actual manuscript. Preserve overlap and convergence evidence.'
    # Long original materials are retained verbatim. These are task-text lines,
    # not invented scientific observations or provider completions.
    evidence={'original_material':'Original evidence document line.\n'*3500}
    text=schema+'\n'+json.dumps({'goal':goal,'requested_manuscript_type':'full_paper',
        'requested_layout':{'columns':'single'},'evidence':evidence,'required_claim_ids':[]})
    return schema,goal,text


def test_already_read_original_task_remains_visible_and_no_longer_requests_a_restart(tmp_path):
    schema,goal,text=manuscript_task()
    brief=original_task_brief(text)
    assert brief['original_instructions'].strip()==schema
    assert brief['original_control_fields']['goal']==goal
    assert 'evidence' not in brief['original_control_fields']
    state={'messages':[{'role':'system','content':'Use actual evidence.'},{'role':'user','content':text}],
           'transcript':[],'original_task_brief':brief}
    pack_context(state,tmp_path,24000)
    assert recover_context_rejection(state,ProviderError('Context-window protocol case',code='context_window_exceeded'))
    pack_context(state,tmp_path,24000)
    store=ContextStore(tmp_path)
    original_segment=store.index['current']['controls:1']
    while store.pending():
        pending=store.pending();store.read(pending['segment_id'],pending['offset'],6000,'Retain the full manuscript and exact references.')
        store=ContextStore(tmp_path)
    packed=pack_context(state,tmp_path,24000)
    actual=json.dumps(packed,ensure_ascii=False)
    assert goal in actual and schema in actual
    assert 'All pages of these controls have already been read' in actual
    assert state['context_management']['next_required'] is None
    assert (store.root/(original_segment+'.txt')).read_text()==text
    repeated=store.read(original_segment,0,6000)
    assert repeated['already_read'] and repeated['new_characters']==0
    # Lowering the transport page size after an old-page reread must preserve
    # the later original pages that have already actually been delivered.
    state['context_management']['repack_target']=8000
    pack_context(state,tmp_path,24000)
    assert ContextStore(tmp_path).pending() is None


def test_repair_task_retains_whole_original_goal_schema_and_correction():
    schema,goal,text=manuscript_task()
    correction='Fix the exact stale metric reference while preserving all sound manuscript prose.'
    repaired=json.dumps({'original_task':text,'required_correction':correction,
                         'previous_draft_untrusted_model_output':'Previous model document remains separately retrievable.'})
    brief=original_task_brief(repaired)
    assert brief['original_task']['original_control_fields']['goal']==goal
    assert brief['original_task']['original_instructions'].strip()==schema
    assert brief['required_correction']==correction


def test_multiline_original_contract_round_trips_without_shortening(tmp_path):
    schema,goal,text=manuscript_task()
    value=json.loads(text[len(schema)+1:])
    value['goal']=goal+'\nPreserve every original study condition.\n'+goal
    whole_schema=schema+'\nThe output schema and section roles stay intact.'
    text=whole_schema+'\n'+json.dumps(value,ensure_ascii=False)
    brief=original_task_brief(text)
    state={'messages':[{'role':'system','content':'Use the original evidence.'},
                       {'role':'user','content':text}],
           'transcript':[],'original_task_brief':brief}
    packed=pack_context(state,tmp_path,24000)
    label='ORIGINAL TASK AND OUTPUT CONTRACT (whole original fields; source evidence remains in its original segment): '
    contracts=[json.loads(message['content'][len(label):]) for message in packed
               if message.get('content','').startswith(label)]
    assert contracts==[brief]
    assert contracts[0]['original_control_fields']['goal']==value['goal']
    assert contracts[0]['original_instructions'].strip()==whole_schema
