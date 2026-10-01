"""Prepare evidence-bound statistical tables and empirical paper figures."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

from research.figures.model_workflow import reviewed_render
from research.figures.render import render_figure
from research.figures.statistical import validate_statistical_data
from research.paper.layout import normalize_layout
from research.paper.tables import default_statistical_tables


_COMPANIONS = ('source', 'data', 'style', 'report', 'selection', 'caption_context')
_OUTPUTS = ('pdf', 'svg', 'png', *_COMPANIONS)
CAPTION_CONTRACT = {
    'caption': 'State the scientific comparison, its evaluated condition, and the strongest supported finding. Keep the final standing argument rather than analysis chronology.',
    'notes': 'Define metric direction and units, repetition counts, aggregation, uncertainty type and sampling unit, and the conditions needed to interpret the comparison. Do not repeat the caption.',
    'results': 'Explain the practical value of the supported conditional advantage. Give each experiment an explicit argumentative duty; use actual paired differences and uncertainty when available.',
    'numerical_references': 'Use [[metric:ID]] references from the supplied bound measurements. Preserve the dataset, method, metric, condition and uncertainty identity of every reference.',
    'scope': 'Choose the main narrative around the strongest supported contribution. Retain the declared comparison matrix and distinguish descriptive seed variation from inferential intervals. Narrow a claim to the conditions supported by the data.',
    'accuracy': 'Do not turn an unmeasured mechanism into an explanation, an association into causation, overlapping intervals into equivalence, or a best point estimate into a significance claim.',
    'style': 'Use concise, direct scientific prose. Omit lab-log chronology, generic praise, internal verification reports, and self-weakening commentary.',
}


def _numeric_x(record):
    value = record.get('x')
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _key(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _plot_plans(statistics):
    """Keep every metric and measured scope; design never selects a seed."""
    records = statistics.get('records', [])
    comparisons = statistics.get('comparisons', [])
    metrics = list(dict.fromkeys(row['metric'] for row in [*records, *comparisons]))
    plans = []
    for metric in metrics:
        rows = [row for row in records if row['metric'] == metric]
        contrasts = [row for row in comparisons if row['metric'] == metric]
        curve = [row for row in rows if row.get('x') is not None]
        if any(not _numeric_x(row) for row in curve):
            raise ValueError('Measured curve checkpoints must be finite numeric x values')
        if curve:
            by_series = {}
            for row in curve:
                series = (row['dataset'], row['method'], _key(row.get('condition')))
                by_series.setdefault(series, set()).add(row['x'])
            # A single measured checkpoint is a point, not a continuous curve.
            kind = 'line' if all(len(points) >= 2 for points in by_series.values()) else 'scatter'
            plans.append({'kind': kind, 'metric': metric, 'records': curve,
                          'comparisons': [row for row in contrasts if row.get('x') is not None]})
        snapshots = [row for row in rows if row.get('x') is None]
        snapshot_contrasts = [row for row in contrasts if row.get('x') is None]
        if snapshots or snapshot_contrasts:
            intervals = [row for row in snapshot_contrasts
                         if row.get('ci_low') is not None and row.get('ci_high') is not None]
            # Partial interval availability must not hide the other comparisons.
            if intervals and len(intervals) == len(snapshot_contrasts):
                kind = 'forest'
            elif len({row['dataset'] for row in snapshots}) > 1:
                kind = 'heatmap'
            else:
                kind = 'bar'
            if not snapshots and kind != 'forest':
                raise ValueError('Comparison-only plotting requires a computed interval for every contrast')
            plans.append({'kind': kind, 'metric': metric, 'records': snapshots,
                          'comparisons': snapshot_contrasts})
    return plans


def _condition_label(value):
    if value in (None, '', 'default', {}):
        return ''
    if isinstance(value, dict):
        return ', '.join(str(key) + '=' + str(item) for key, item in value.items())
    return str(value)


def _paginate_plans(plans):
    """Keep complete facets at readable size instead of shrinking tall plots."""
    pages = []
    for plan in plans:
        source = plan['comparisons'] if plan['kind'] == 'forest' else plan['records']
        method_order = list(dict.fromkeys(row.get('method', row.get('candidate')) for row in source))
        if plan['kind'] == 'heatmap':
            conditions = list(dict.fromkeys(_key(row.get('condition')) for row in source))
            chunks = []
            for condition in conditions:
                names = list(dict.fromkeys(row['dataset'] for row in source if _key(row.get('condition')) == condition))
                for offset in range(0, len(names), 8):
                    wanted = set(names[offset:offset + 8])
                    chunks.append([row for row in source if row['dataset'] in wanted and _key(row.get('condition')) == condition])
        else:
            grouped = {}
            for row in source:
                grouped.setdefault((row['dataset'], _key(row.get('condition'))), []).append(row)
            if plan['kind'] == 'forest':
                # Effect lists are paginated within each facet; every declared
                # contrast is retained once and each page identifies its scope.
                facets = [values[offset:offset + 8] for values in grouped.values() for offset in range(0, len(values), 8)]
                chunks, current, keys = [], [], set()
                for facet in facets:
                    identity = (facet[0]['dataset'], _key(facet[0].get('condition')))
                    if len(current) == 2 or identity in keys:
                        chunks.append([row for values in current for row in values])
                        current, keys = [], set()
                    current.append(facet)
                    keys.add(identity)
                if current:
                    chunks.append([row for values in current for row in values])
            else:
                facets, per_page = list(grouped.values()), 4
                chunks = [[row for values in facets[offset:offset + per_page] for row in values]
                          for offset in range(0, len(facets), per_page)]
        for index, chunk in enumerate(chunks):
            identities = {(row['dataset'], _key(row.get('condition'))) for row in chunk}
            scope = ', '.join(dict.fromkeys(row['dataset'] + (' (' + _condition_label(row.get('condition')) + ')' if row.get('condition') is not None else '') for row in chunk))
            page = {**plan, 'records': chunk if plan['kind'] != 'forest' else
                    [row for row in plan['records'] if (row['dataset'], _key(row.get('condition'))) in identities],
                    'comparisons': chunk if plan['kind'] == 'forest' else
                    [row for row in plan['comparisons'] if (row['dataset'], _key(row.get('condition'))) in identities],
                    'method_order': method_order, 'scope': scope, 'page_index': index + 1, 'page_count': len(chunks)}
            pages.append(page)
    return pages


def _caption(plan, statistics):
    metric = plan['metric'].replace('_', ' ')
    kind = plan['kind']
    if kind in ('line', 'scatter'):
        axis = statistics.get('axes', {}).get('x', {})
        name = axis.get('label', axis.get('name', 'the measured input'))
        if kind == 'scatter':
            return f'{metric.capitalize()} at the measured {name} checkpoints across evaluated methods and conditions.'
        return f'{metric.capitalize()} as {name} varies across evaluated methods and conditions.'
    if kind == 'forest':
        supported = next((row for row in plan['comparisons'] if row['ci_low'] > 0), None)
        finding = ''
        if supported:
            condition = _condition_label(supported.get('condition'))
            scope = str(supported['dataset']) + (' under ' + condition if condition else '')
            finding = f'{supported["candidate"]} improves {metric} over {supported["baseline"]} on {scope}. '
        return finding + f'Paired differences in {metric}; positive differences favor the candidate.'
    return f'{metric.capitalize()} across the evaluated methods, datasets and conditions.'


def _source_runs(plan, evidence):
    rows = plan['comparisons'] if plan['kind'] == 'forest' else plan['records']
    bindings = {item['id']: item for item in evidence.get('metrics', [])}
    wanted = {str(location['run_id']) for row in rows for location in row.get('source_locations', [])}
    wanted.update(str(bindings[reference]['run_id']) for row in rows
                  for reference in row.get('source_refs', []) if reference in bindings)
    available = [str(run['id']) for run in evidence.get('runs', [])]
    if not wanted or not wanted <= set(available):
        raise ValueError('Statistical figures require actual source runs from the supplied evidence')
    return [identifier for identifier in available if identifier in wanted]


def _figure_id(metric, kind, used):
    slug = re.sub(r'[^A-Za-z0-9_-]+', '-', metric).strip('-') or 'metric'
    base = 'statistical-' + slug[:64] + '-' + kind
    identifier, number = base, 2
    while identifier in used:
        identifier = base + '-' + str(number)
        number += 1
    used.add(identifier)
    return identifier


def _read_object(filename, name):
    value = json.loads(Path(filename).read_text())
    if not isinstance(value, dict):
        raise ValueError('Statistical ' + name + ' must be an actual JSON object')
    return value


def _validate_outputs(paths, directory):
    if not isinstance(paths, dict):
        raise ValueError('Statistical rendering must return actual output paths')
    root = Path(directory).resolve()
    admitted = {}
    for name in _OUTPUTS:
        filename = paths.get(name)
        if filename is None:
            continue
        if not isinstance(filename, (str, Path)):
            raise ValueError('Statistical output paths must identify actual files')
        path = Path(filename).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Statistical output must remain inside its rendering directory')
        admitted[name] = path
    required = {'pdf', 'png', 'source', 'data', 'style', 'report', 'caption_context'}
    if not required <= set(admitted):
        raise ValueError('Statistical rendering did not produce the required editable and caption artifacts')
    return admitted


def prepare_statistical_presentation(evidence, output_dir, layout=None, client=None):
    """Return an enriched copy; all empirical figures use computed bindings.

    Provider-backed visual review operates on real renderings in a temporary
    workspace. Only the selected scientific artifacts enter the paper bundle.
    Without a provider, the native figures have no claimed model verdict.
    """
    if not isinstance(evidence, dict):
        raise ValueError('Statistical presentation requires an evidence object')
    prepared = deepcopy(evidence)
    statistics = prepared.get('statistics')
    if statistics is None:
        return prepared
    if not isinstance(statistics, dict):
        raise ValueError('Statistical evidence must be a structured analysis result')
    if not statistics.get('records') and not statistics.get('comparisons'):
        return prepared
    data = {'statistical_results': statistics, 'metric_bindings': prepared.get('metrics', [])}
    validate_statistical_data(data)
    config = normalize_layout(layout)
    tables = default_statistical_tables(prepared)
    plans = _paginate_plans(_plot_plans(statistics))
    previous = prepared.get('statistical_presentation', {}).get('figure_ids', [])
    prepared['figures'] = [figure for figure in prepared.get('figures', [])
                           if figure.get('id') not in previous or figure.get('origin') != 'statistical_presentation']
    used = {figure['id'] for figure in prepared['figures']}
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    staged = []
    with TemporaryDirectory(prefix='.statistical-presentation-', dir=destination) as temporary:
        workspace = Path(temporary)
        for plan in plans:
            identifier = _figure_id(plan['metric'], plan['kind'], used)
            source_runs = _source_runs(plan, prepared)
            selected = {**statistics, 'records': plan['records'], 'comparisons': plan['comparisons']}
            plot_data = {'statistical_results': selected, 'metric_bindings': prepared['metrics']}
            datasets = {row['dataset'] for row in [*plan['records'], *plan['comparisons']]}
            methods = {row.get('method', row.get('candidate')) for row in [*plan['records'], *plan['comparisons']]}
            conditions = {_key(row.get('condition')) for row in [*plan['records'], *plan['comparisons']]}
            span = 'page' if len(datasets) > 1 or len(conditions) > 1 or len(methods) > 3 else 'column'
            style = {'metric': plan['metric'], 'paper_layout': config, 'span': span,
                     'font_size': max(9, config['min_font_pt']), 'argumentative_duty': 'effectiveness',
                     'method_order': plan['method_order'], 'comparison_scope': 'Evaluated comparisons on ' + plan['scope']}
            folder = workspace / identifier
            context = {'caption': _caption(plan, statistics), 'caption_contract': CAPTION_CONTRACT,
                       'argumentative_duty': 'effectiveness', 'actual_statistical_results': selected,
                       'metric_bindings': prepared['metrics'], 'source_run_ids': source_runs,
                       'publication_style': 'A publication-size statistical figure for a leading journal or conference, with clear comparisons, exact empirical values, legible uncertainty and restrained typography.'}
            if plan['page_count'] > 1:
                context['caption'] += ' Evaluated scope: ' + plan['scope'] + '.'
            if client is not None:
                paths, selection = reviewed_render(client, folder, plot_data, style, plan['kind'], context=context)
            else:
                paths, selection = render_figure(folder, plot_data, style, plan['kind']), None
            admitted = _validate_outputs(paths, folder)
            if selection is not None and 'selection' not in admitted:
                selection_path = folder / 'selection.json'
                selection_path.write_text(json.dumps(selection, ensure_ascii=False, indent=2, allow_nan=False))
                admitted['selection'] = selection_path
            report = _read_object(admitted['report'], 'render report')
            if report.get('quality_issues'):
                raise ValueError('The selected statistical figure has unresolved typography defects')
            if report.get('height_in', 0) > 8:
                raise ValueError('The statistical plot exceeds readable publication page height; divide its complete facets')
            caption_context = _read_object(admitted['caption_context'], 'caption context')
            caption_context.update(caption_contract=deepcopy(CAPTION_CONTRACT),
                                   suggested_caption=context['caption'], source_run_ids=source_runs)
            admitted['caption_context'].write_text(json.dumps(caption_context, ensure_ascii=False, indent=2, allow_nan=False))
            staged.append((identifier, admitted, source_runs, context['caption'], report, caption_context))
        # Publish only after every planned empirical figure rendered successfully.
        figures = []
        for identifier, admitted, source_runs, caption, report, caption_context in staged:
            directory = destination / identifier
            directory.mkdir(parents=True, exist_ok=True)
            promoted = {}
            for name, source in admitted.items():
                target = directory / source.name
                shutil.copyfile(source, target)
                promoted[name] = target.name
            figures.append({'id': identifier, 'run_id': source_runs[0], 'source_run_ids': source_runs,
                            'directory': str(directory), 'path': promoted['pdf'], 'caption': caption,
                            'purpose': 'effectiveness', 'anchor': None,
                            'artifacts': {name: promoted[name] for name in _COMPANIONS if name in promoted},
                            'render_metadata': report, 'caption_context': caption_context,
                            'origin': 'statistical_presentation', 'trust': 'untrusted_run_artifact'})
        prepared['figures'].extend(figures)
    prepared['statistical_presentation'] = {'tables': tables, 'figure_ids': [figure['id'] for figure in figures],
                                           'caption_contract': deepcopy(CAPTION_CONTRACT),
                                           'table_usage': 'Use the main comparison and mechanism evidence in the Results argument. Detailed checkpoint tables can accompany the complete curves in supplementary material.'}
    return prepared
