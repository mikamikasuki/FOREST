"""FOREST's editable authoring contract: decisive analysis, focused manuscripts."""
from copy import deepcopy
import re

STYLE_VERSION = 3
STYLE_PROFILE = {
    'id': 'evidence-led-press-release', 'version': STYLE_VERSION, 'language': 'English',
    'narrative': ['important problem', 'specific gap', 'approach', 'strongest supported result'],
    'advantage_fields': ['condition', 'supported mechanism', 'practical value', 'evidence'],
    'experiment_duties': ['effectiveness', 'mechanism', 'scenario_value', 'alternative_explanation'],
    'analysis_fields': ['BEST ESTIMATE', 'PROBABILITY RANGE', 'CONFIDENCE', 'WHY', 'AGAINST',
                        'DECISIVE UNKNOWN', 'CHEAPEST RESOLUTION'],
    'evidence_labels': ['MEASURED', 'REPORTED', 'INFERRED', 'ESTIMATED', 'SPECULATIVE'],
    'revision_policy': 'Exact necessary spans; preserve paragraph structure and unaffected wording',
    'records': 'Keep full observations and run history separately from the manuscript narrative',
    'layout_policy': 'Plan figures and tables at their final column width; validate the compiled output',
    'default_deliverable': 'Full submission manuscript with venue-matched experimental coverage',
    'ai_writing_declarations': 'Do not add AI-writing, AI-use or automated-authorship declarations to the manuscript',
}

MANUSCRIPT_CONTRACT = '''AUTHORING MODE: MANUSCRIPT
Build the paper around its single strongest evidence-supported advantage. Open the abstract and introduction with an important problem, the specific gap, this paper's approach, and its strongest result. Make each advantage explicit: the condition under which it holds, the mechanism supported by evidence, the practical problem it solves, and the measured comparison. Use a coherent final argument, not a project summary, development chronology, lab log or self-audit. Do not begin with implementation details or recount abandoned attempts.
Write direct, precise claims. Remove editorial self-weakening such as "unfortunately", "merely", "still lags behind", "limited improvement", "our contribution is not", and repeated declarations of what the paper cannot claim. Replace a vague verdict with the concrete condition and measured effect. Do not replace calibrated uncertainty with unsupported certainty. The conclusion reinforces the established takeaway; it does not introduce a new negative verdict.
For unfavorable material, first remove what is irrelevant to the stated claim; then narrow that claim, select a scientifically justified evaluation dimension, explain a measured trade-off, reorganize the experiments, or revise the central story. Preserve the declared primary outcomes and metric definitions; explicit protocol changes require comparable reruns and cannot manufacture a winning result. A comparison frame must have a substantive reason and remain explicit about exploratory selection. Preserve facts needed to evaluate the central claim, including contrary comparisons, failed-fit accounting and relevant scope. State a material exception once where it changes interpretation; do not repeatedly promote a local observation into a verdict on the whole method. Full execution history and internal readiness judgments belong in separate research records, unless a specific detail is part of the method or evidence.
Every experiment, figure and table needs an argumentative duty: demonstrate effectiveness, establish the key mechanism, show scenario value, or rule out an alternative explanation. State what the evidence establishes in the adjacent prose; do not expect a reader to discover the contribution from a table. Remove or redesign evidence with no duty. Plan the main evidence around the central claim while retaining necessary comparison coverage.
Research-decision estimates and their WHY/AGAINST/base-case worksheets are separate analysis artifacts; do not paste them into the manuscript as a compulsory self-audit. Use MEASURED/REPORTED/INFERRED/ESTIMATED/SPECULATIVE labels when they clarify a claim's status, not as decoration on every sentence. State necessary assumptions, units, statistical independence and uncertainty specifically. Attribute prior work accurately. Never invent a winning result, citation, baseline or confidence interval.
The default deliverable is a complete submission manuscript, with the depth, dataset coverage, comparator reproduction, mechanism ablations and statistical analysis established by at least fifteen closely related accepted papers. A pilot tests feasibility; it does not replace the submission experiment matrix. Do not silently deliver a demo, brief note or an expanded pilot as a full paper. If resources or observations are missing, retain a concrete completion backlog in separate research records and continue the research loop. Never fill missing experiments with invented results or repetitive prose.
Do not add AI-writing, AI-use, language-model-authorship or automated-generation declarations, acknowledgments, headings or boilerplate to the manuscript. Keep provider and tool provenance in separate execution records. Discuss AI methods when they are the actual scientific subject; that is not an authorship declaration.
Plan single- or double-column layout before rendering. Give plots units, meaningful labels and an explicit definition of any uncertainty bars. Use actual data, vector figures, legible type at final print width, and matched comparator scales. Tables retain dataset/method identity, consistent precision and exact underlying values. Use page-spanning or split tables when a column would force unreadable text. Place introduction overviews beside the introduction's motivating argument, method diagrams beside the mechanism, and empirical tables/plots immediately after their first substantive interpretation. Use explicit paragraph anchors and cross references. Generate several actual visual candidates and obtain independent evidence, readability and placement reviews before selecting one. Respect the selected venue's actual layout. All research choices and source files remain editable.'''

REVISION_CONTRACT = '''AUTHORING MODE: MINIMAL REVISION
Read the supplied manuscript and identify each defensive, evasive, generic-hedge, development-diary or self-negating passage. For every finding return its exact original span, explicit character start when repeated, the smallest sufficient replacement, and a concrete reason tied to the surrounding claim and evidence. Separate findings requiring a scientific decision from edits supported by the existing evidence. Preserve the author's wording habits, paragraph structure and all problem-free text. Never replace an entire multi-sentence paragraph when a local edit suffices. Do not delete measured results, citations, comparison conditions or uncertainty simply to improve the tone. A request to correct an evidenced factual error can change that specific claim, with the evidence and reason stated. If a stronger assertion lacks support, narrow the claim rather than invent support. Do not turn a review finding into a verdict about the entire method. Report zero edits when no relevant defect is found.'''

RESEARCH_MODE_BOUNDARY = '''Keep output modes distinct. In research analysis, make the strongest evidence-calibrated judgment, with the requested estimates, contrary evidence and decisive test. In manuscript authoring, apply the evidence-led Press-Release contract; internal feasibility/novelty judgments and failed development history are not automatic manuscript paragraphs. In revision, produce minimal exact-span proposals without rewriting sound prose.'''


def writing_profile():
    return deepcopy(STYLE_PROFILE)


def ai_declarations(text):
    """Find unsolicited authorship boilerplate without banning AI research."""
    patterns = [r'\bAI[- ](?:use|writing|assistance) statement\b',
        r'\b(?:this|the) (?:paper|manuscript|article|text|draft)[^.!?\n]{0,80}(?:written|drafted|edited|generated|produced)[^.!?\n]{0,80}(?:AI assistance|ChatGPT|Codex|language model|artificial intelligence)\b',
        r'\bwe (?:used|employed)[^.!?\n]{0,80}(?:ChatGPT|Codex|language model|AI)[^.!?\n]{0,80}(?:writing|drafting|language editing|polishing)\b']
    return [match.group(0) for pattern in patterns for match in re.finditer(pattern,text,re.I)]


def writing_contract(mode='manuscript'):
    if mode not in ('manuscript', 'revision'):
        raise ValueError('Writing mode must be manuscript or revision')
    return MANUSCRIPT_CONTRACT + ('\n' + REVISION_CONTRACT if mode == 'revision' else '')
