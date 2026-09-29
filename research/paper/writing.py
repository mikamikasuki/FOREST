"""Literal, span-level editing proposals; semantic judgments belong to review."""
from __future__ import annotations

import re
from .style import STYLE_VERSION, writing_profile

# Replacements remove redundant wording only. Ambiguous hedges retain their
# uncertainty until an author or evidence-aware reviewer supplies a judgment.
RULES = (
    (r'\bmay potentially\b', 'may', 'Redundant possibility markers'),
    (r'\bcould possibly\b', 'could', 'Redundant possibility markers'),
    (r'\bUnfortunately,?\s*', '', 'Editorial self-weakening; retain the actual observation'),
    (r'\bmerely\s+', '', 'Evaluative minimizer; retain the concrete quantity'),
    (r'\bstill lags behind\b', None, 'Name the metric, condition and measured difference'),
    (r'\blimited improvement\b', None, 'Replace the vague verdict with the measured effect and its scope'),
    (r'\bmore research is needed\b', None, 'State the decisive unknown and the cheapest discriminating experiment'),
    (r'\bit is difficult to know\b', None, 'State the best estimate and the evidence that controls its uncertainty'),
    (r'\bit depends\b', None, 'Name the controlling condition and the most likely outcome'),
    (r'\binsufficient evidence to say anything\b', None, 'State what the evidence supports and the decisive missing observation'),
    (r'\b(?:the |our )?(?:underlying )?ingredients are not new\b', None, 'Attribute the prior components, then state the specific supported contribution without a global self-verdict'),
    (r'\bour contribution is (?:therefore )?not\b', None, 'State what the contribution establishes, with its condition and evidence'),
    (r'\b(?:we (?:cannot|can not|do not) claim|we make no claim)\b', None, 'State the supported claim directly; preserve any condition that changes its interpretation'),
    (r'\b(?:our|this|the) (?:method|approach|paper|work) is (?:only|merely|not (?:a )?(?:new|novel))\b', None, 'Replace whole-method minimization with the concrete contribution and its evidenced scope'),
    (r'\b(?:we (?:first )?(?:tried|attempted)|our (?:initial|earlier) attempt)\b', None, 'Keep the final scientific rationale; move development chronology to the research record unless it is itself evidence'),
    (r'\b(?:strongest contrary result|main contrary evidence)\b', None, 'Name the actual comparator, metric and condition instead of framing a result as self-rebuttal'),
    (r'\b(?:further (?:work|research|experiments) (?:is|are) (?:needed|required)|both outcomes are (?:equally )?possible)\b', None, 'State the current best estimate and the one discriminating observation; keep decision worksheets outside manuscript prose'),
)


def review_defensive_writing(source):
    proposals = []
    for pattern, replacement, reason in RULES:
        for match in re.finditer(pattern, source, re.I):
            proposals.append({'original': match.group(), 'replacement': replacement,
                              'reason': reason, 'start': match.start(), 'end': match.end(),
                              'line': source.count('\n', 0, match.start()) + 1,
                              'requires_evidence_judgment': replacement is None})
    return {'edits': sorted(proposals, key=lambda item: item['start']), 'style_version': STYLE_VERSION,
            'profile': writing_profile(),
            'coverage': 'Literal phrase matches only; no claim of exhaustive semantic review',
            'editing_policy': 'Apply selected spans only; preserve paragraph structure and unaffected wording'}


def validate_revision_proposal(source, proposal):
    """Reject invented original spans and paragraph replacement proposals."""
    edits = proposal.get('edits', [])
    if not isinstance(edits, list):
        raise ValueError('edits must be an array')
    occupied = []
    paragraphs = [(match.start(), match.end()) for match in re.finditer(r'[^\n]+(?:\n(?!\n)[^\n]+)*', source)]
    for edit in edits:
        original, replacement = edit.get('original'), edit.get('replacement')
        if not isinstance(original, str) or not original or original not in source:
            raise ValueError('Every revision must reference an exact existing source span')
        if not isinstance(replacement, str):
            raise ValueError('A proposed replacement must be a string')
        if '\n\n' in original or '\n\n' in replacement:
            raise ValueError('A minimal revision cannot replace multiple paragraphs')
        if source.count(original) > 1 and 'start' not in edit:
            raise ValueError('Repeated spans require an explicit character offset')
        start = edit.get('start', source.index(original))
        if not isinstance(start, int) or source[start:start + len(original)] != original:
            raise ValueError('The revision offset does not match its original span')
        end = start + len(original)
        for paragraph_start, paragraph_end in paragraphs:
            paragraph = source[paragraph_start:paragraph_end]
            sentence_count = len(re.findall(r'[.!?](?:\s|$)', paragraph))
            if (sentence_count > 1 and original.strip() == paragraph.strip()
                    and original != replacement):
                raise ValueError('Replace the necessary exact spans, not an entire multi-sentence paragraph')
        if any(start < b and end > a for a, b in occupied):
            raise ValueError('Proposed revisions overlap')
        occupied.append((start, end))
        edit.update(start=start, end=end)
    return proposal
