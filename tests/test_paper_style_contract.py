"""Style changes remain exact-span proposals, not invented scientific outcomes."""
from types import SimpleNamespace

import pytest

from research.agents.defaults import VERSION_THREE_TOOLS, TOOLSET_VERSION, upgrade_default_tools
from research.agents.policy import LEGACY_ROLE_INSTRUCTIONS, ROLES, TOOLS
from research.paper.style import writing_contract, writing_profile
from research.paper.writing import review_defensive_writing, validate_revision_proposal


def test_factory_writer_upgrades_and_custom_author_voice_survives():
    def writer(instructions):
        return SimpleNamespace(name='Writer', role='Writer', instructions=instructions,
            tools=list(VERSION_THREE_TOOLS), enabled=True, provider_id=None,
            config={'builtin_role':'Writer','builtin_toolset_version':3})
    agent=writer(LEGACY_ROLE_INSTRUCTIONS['Writer'])
    assert upgrade_default_tools(agent)
    assert agent.instructions == ROLES['Writer'] and agent.tools == TOOLS
    assert agent.config['builtin_toolset_version'] == TOOLSET_VERSION
    custom=writer('Keep my exact author voice.')
    assert not upgrade_default_tools(custom)
    assert custom.instructions == 'Keep my exact author voice.'


def test_defensive_framing_is_identified_without_erasing_substantive_evidence():
    source=('Our contribution is not a new calibration formula. '
            'The strongest contrary result is the matched baseline.\n\n'
            'At the same regularization strength, the methods agree within numerical precision.')
    report=review_defensive_writing(source)
    assert len(report['edits']) == 2
    assert all(edit['requires_evidence_judgment'] and edit['replacement'] is None for edit in report['edits'])
    assert report['profile']==writing_profile()
    assert not review_defensive_writing(source.split('\n\n')[1])['edits']


def test_minimal_proposals_preserve_structure_and_require_precise_locations():
    source='The method may potentially help. The mechanism is explicit.\n\nThe second paragraph remains intact.'
    edit={'original':'may potentially','replacement':'can','reason':'Remove duplicated modality'}
    checked=validate_revision_proposal(source,{'edits':[edit]})
    edited=source[:edit['start']]+edit['replacement']+source[edit['end']:]
    assert edited=='The method can help. The mechanism is explicit.\n\nThe second paragraph remains intact.'
    with pytest.raises(ValueError,match='entire multi-sentence paragraph'):
        validate_revision_proposal(source,{'edits':[{'original':source.split('\n\n')[0],'replacement':'New prose.'}]})
    with pytest.raises(ValueError,match='explicit character offset'):
        validate_revision_proposal('Maybe. Maybe.',{'edits':[{'original':'Maybe','replacement':'Likely'}]})
    assert checked['edits'][0]['start']==11


def test_analysis_and_manuscript_have_distinct_contracts():
    profile=writing_profile()
    assert len(profile['analysis_fields'])==7
    assert 'minimal' in writing_contract('revision').lower()
    assert 'evidence' in writing_contract().lower()
