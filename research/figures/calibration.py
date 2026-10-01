"""Seed-aware, faceted reliability diagrams from actual saved predictions.

The estimand is the equal-seed mean of within-seed bin probability and event
frequency, conditional on a seed populating that bin. Repeated test objects in
different seeds are never treated as independent pooled observations. The
default error bars describe seed SD, not a confidence interval. SE/t/bootstrap
require uncertainty={type:..., unit:'seed', independent:true}. Empty bins remain
explicit and break connecting lines. Count panels include zero-count seeds.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json
import math
from pathlib import Path
import textwrap

import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from matplotlib.colors import is_color_like
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd

from research.analysis.presentation import _describe, _uncertainty


COLORS = ['#0077BB', '#EE7733', '#009988', '#CC3311', '#33BBEE', '#EE3377', '#666666', '#000000']
MARKERS = ['o', 's', '^', 'D', 'v', 'P', 'X', '*']
LINES = ['-', '--', '-.', ':']


def _key(value):
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError) as exc:
        raise ValueError('Calibration identities must be finite JSON values') from exc


def _read(data):
    if isinstance(data, (str, Path)):
        path = Path(data)
        data = json.loads(path.read_text()) if path.suffix.lower() == '.json' else pd.read_csv(path).to_dict('records')
    if isinstance(data, dict):
        data = data.get('predictions', data.get('rows'))
    if not isinstance(data, list) or not data:
        raise ValueError('Calibration requires the complete saved prediction rows')
    required = {'dataset', 'seed', 'method', 'y_true', 'probability'}
    rows = deepcopy(data)
    seen, object_labels = set(), {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not required <= row.keys():
            raise ValueError('Prediction rows require dataset, seed, method, y_true and probability')
        for label in ('dataset', 'method'):
            if not isinstance(row[label], str) or not row[label].strip():
                raise ValueError('Dataset and method identities must be nonempty strings')
        if row['seed'] is None or isinstance(row['seed'], (bool, list, dict)):
            raise ValueError('Saved predictions require an explicit seed identity')
        _key(row['seed'])
        probability = row['probability']
        if isinstance(probability, bool) or not isinstance(probability, (int, float)) or not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError('Saved probabilities must be finite numbers in [0,1]')
        if row['y_true'] not in (0, 1) or isinstance(row['y_true'], (str, dict, list)):
            raise ValueError('Reliability diagrams require binary labels')
        identifier = row.get('sample_id', row.get('unit_id'))
        if identifier is not None:
            identity = (row['dataset'], row['method'], _key(row['seed']), _key(identifier))
            if identity in seen:
                raise ValueError('Duplicate test-object predictions within one seed/method are not admissible')
            seen.add(identity)
            object_key = (row['dataset'], _key(identifier))
            if object_key in object_labels and object_labels[object_key] != row['y_true']:
                raise ValueError('Saved test-object identities have inconsistent binary labels')
            object_labels[object_key] = row['y_true']
        row['_source_row'] = index
    return rows


def _selection(rows, style):
    selected = rows
    scope = {'datasets': list(dict.fromkeys(row['dataset'] for row in rows)),
             'methods': list(dict.fromkeys(row['method'] for row in rows)),
             'seeds': list({_key(row['seed']): row['seed'] for row in rows}.values())}
    for plural, singular in (('datasets', 'dataset'), ('seeds', 'seed'), ('methods', 'method')):
        if plural in style and singular in style:
            raise ValueError('Use either a singular or plural calibration selector')
        choices = style.get(plural, [style[singular]] if singular in style else None)
        if choices is None:
            continue
        if not isinstance(choices, list) or not choices or len({_key(value) for value in choices}) != len(choices):
            raise ValueError('Calibration selectors require distinct observed identities')
        wanted = {_key(value) for value in choices}
        observed = {_key(row[singular]) for row in rows}
        if not wanted <= observed:
            raise ValueError('Calibration selection contains an unobserved group')
        if wanted != observed:
            explanation = style.get('comparison_scope') if plural == 'methods' else style.get('selection_scope')
            if not isinstance(explanation, str) or not explanation.strip():
                raise ValueError('A calibration subset requires an explicit selection_scope or comparison_scope')
        selected = [row for row in selected if _key(row[singular]) in wanted]
    if not selected:
        raise ValueError('Calibration selection has no saved predictions')
    methods = style.get('methods', [style['method']] if 'method' in style else list(dict.fromkeys(row['method'] for row in selected)))
    datasets = style.get('datasets', [style['dataset']] if 'dataset' in style else list(dict.fromkeys(row['dataset'] for row in selected)))
    present = {(row['dataset'], row['method'], _key(row['seed'])) for row in selected}
    missing = []
    for dataset in datasets:
        seeds = {_key(row['seed']): row['seed'] for row in selected if row['dataset'] == dataset}
        if not seeds:
            raise ValueError('Every selected dataset must retain actual predictions')
        for method in methods:
            for seed_key, seed in seeds.items():
                if (dataset, method, seed_key) not in present:
                    missing.append({'dataset': dataset, 'method': method, 'seed': seed})
    if missing:
        raise ValueError('Calibration requires the complete selected dataset/seed/method grid: ' + json.dumps(missing))
    if all(row.get('sample_id', row.get('unit_id')) is not None for row in selected):
        object_sets = defaultdict(dict)
        for row in selected:
            identity = (row['dataset'], _key(row['seed']))
            object_sets[identity].setdefault(row['method'], set()).add(_key(row.get('sample_id', row.get('unit_id'))))
        if any(len({tuple(sorted(objects)) for objects in values.values()}) != 1 for values in object_sets.values()):
            raise ValueError('Calibration method comparisons require the same saved test-object set within each dataset/seed')
    return selected, datasets, methods, scope


def _edges(style):
    supplied = style.get('bin_edges')
    if supplied is None:
        count = style.get('bins', 10)
        if isinstance(count, bool) or not isinstance(count, int) or not 2 <= count <= 50:
            raise ValueError('Calibration bin count must be an integer between two and fifty')
        return np.linspace(0, 1, count + 1)
    if not isinstance(supplied, list) or len(supplied) < 3 or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in supplied):
        raise ValueError('Calibration bin edges must be finite numbers')
    edges = np.array(supplied, dtype=float)
    if edges[0] != 0 or edges[-1] != 1 or np.any(np.diff(edges) <= 0):
        raise ValueError('Calibration bin edges must increase strictly from zero to one')
    return edges


def _build_calibration(data, style=None):
    style = dict(style or {})
    original = _read(data)
    rows, datasets, methods, original_scope = _selection(original, style)
    edges = _edges(style)
    config = _uncertainty(style.get('uncertainty', {'type': 'sd', 'unit': 'seed'}), False)
    if config['unit'] != 'seed':
        raise ValueError('This seed-aware reliability estimand requires uncertainty.unit=seed')
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row['dataset'], row['method'], _key(row['seed']))].append(row)
    per_seed, per_bin, metrics = [], [], []
    seed_labels = {_key(row['seed']): row['seed'] for row in rows}
    for dataset in datasets:
        for method in methods:
            seed_groups = [(seed, values) for (name, candidate, seed), values in grouped.items()
                           if name == dataset and candidate == method]
            by_bin = defaultdict(list)
            for seed, values in seed_groups:
                probabilities = np.array([row['probability'] for row in values])
                labels = np.array([row['y_true'] for row in values], dtype=float)
                membership = np.minimum(np.searchsorted(edges, probabilities, side='right') - 1, len(edges) - 2)
                ece = 0.0
                for index in range(len(edges) - 1):
                    mask = membership == index
                    count = int(mask.sum())
                    probability = float(probabilities[mask].mean()) if count else None
                    frequency = float(labels[mask].mean()) if count else None
                    entry = {'dataset': dataset, 'method': method, 'seed': seed_labels[seed],
                             'bin': index, 'left': float(edges[index]), 'right': float(edges[index + 1]),
                             'count': count, 'mean_probability': probability, 'event_frequency': frequency,
                             'source_rows': [values[position]['_source_row'] for position in np.flatnonzero(mask)],
                             'empty': count == 0}
                    per_seed.append(entry)
                    by_bin[index].append(entry)
                    if count:
                        ece += count / len(values) * abs(probability - frequency)
                metrics.append({'dataset': dataset, 'method': method, 'seed': seed_labels[seed],
                                'ece': ece, 'prediction_rows': len(values)})
            for index in range(len(edges) - 1):
                bins = by_bin[index]
                present = [entry for entry in bins if not entry['empty']]
                counts = [entry['count'] for entry in bins]
                estimate_x = _describe([entry['mean_probability'] for entry in present], config) if present else None
                estimate_y = _describe([entry['event_frequency'] for entry in present], config) if present else None
                entry = {'dataset': dataset, 'method': method, 'bin': index,
                         'left': float(edges[index]), 'right': float(edges[index + 1]),
                         'bin_midpoint': float((edges[index] + edges[index + 1]) / 2),
                         'n_seeds': len(present), 'n_seeds_total': len(bins),
                         'empty_seeds': [entry['seed'] for entry in bins if entry['empty']],
                         'observed_seeds': [entry['seed'] for entry in present], 'empty': not present,
                         'x': estimate_x, 'y': estimate_y,
                         'counts': {'mean': float(np.mean(counts)),
                                    'sd': float(np.std(counts, ddof=1)) if len(counts) > 1 else None,
                                    'minimum': min(counts), 'maximum': max(counts), 'total': sum(counts)},
                         'source_bins': [{'dataset': dataset, 'method': method, 'seed': entry['seed'], 'bin': index}
                                         for entry in bins]}
                per_bin.append(entry)
    ece_summaries = []
    for dataset in datasets:
        for method in methods:
            values = [entry['ece'] for entry in metrics if entry['dataset'] == dataset and entry['method'] == method]
            ece_summaries.append({'dataset': dataset, 'method': method, **_describe(values, config), 'n_seeds': len(values)})
    report = {'kind': 'calibration', 'version': 1, 'input_rows': len(original), 'selected_rows': len(rows),
              'datasets': datasets, 'methods': methods,
              'seeds_by_dataset': {dataset: list({_key(row['seed']): row['seed'] for row in rows if row['dataset'] == dataset}.values())
                                   for dataset in datasets},
              'scope': {'original': original_scope, 'selection_scope': style.get('selection_scope'),
                        'comparison_scope': style.get('comparison_scope'), 'all_saved_groups_retained': len(rows) == len(original)},
              'bin_edges': edges.tolist(), 'per_seed_bins': per_seed, 'aggregate_bins': per_bin,
              'per_seed_metrics': metrics, 'ece_summary': ece_summaries,
              'estimand': 'Equal-seed mean of within-seed bin statistics, conditional on nonempty seed/bin; no pooled sample inference',
              'uncertainty': {'type': config['type'], 'unit': 'seed', 'confidence': config['confidence'] if config['type'] in {'t_ci', 'bootstrap'} else None,
                              'independent': config.get('independent', False),
                              'scope': 'Across supplied seeds, conditional on fitted-data protocol and test objects; no new-task or test-population inference'},
              'empty_bins': 'Retained with zero counts; entirely empty bins break lines; uncertainty unavailable with fewer than two populated seeds',
              'count_estimand': 'Mean observations per bin across every supplied seed, including zero counts',
              'coverage': {'complete_selected_method_seed_grid': True,
                           'object_id_available': all(row.get('sample_id', row.get('unit_id')) is not None for row in rows),
                           'paired_method_object_sets_verified': all(row.get('sample_id', row.get('unit_id')) is not None for row in rows)},
              'vector_formats': ['pdf', 'svg']}
    segments = []
    for dataset in datasets:
        for method in methods:
            current, contiguous = [], []
            for entry in per_bin:
                if entry['dataset'] != dataset or entry['method'] != method:
                    continue
                if entry['empty']:
                    if current:
                        contiguous.append(current)
                        current = []
                else:
                    current.append(entry['bin'])
            if current:
                contiguous.append(current)
            segments.append({'dataset': dataset, 'method': method, 'connected_bin_segments': contiguous})
    report['curve_segments'] = segments
    return rows, report, config


def _bounds(description, config):
    if not description or description['uncertainty']['type'] == 'none' or config['type'] == 'none':
        return None
    if config['type'] == 'sd':
        spread = description['sd']
    elif config['type'] == 'se':
        spread = description['se']
    else:
        return description['ci_low'], description['ci_high']
    return description['estimate'] - spread, description['estimate'] + spread


def render_calibration(output_dir, data, style=None):
    """Render every selected dataset, method and saved seed at paper dimensions."""
    from research.figures.render import figure_dimensions
    style = dict(style or {})
    rows, report, config = _build_calibration(data, style)
    width, supplied_height, font = figure_dimensions(style)
    palette = style.get('palette', COLORS)
    if not isinstance(palette, list) or not palette or any(not is_color_like(color) for color in palette):
        raise ValueError('Calibration palettes must contain real colors')
    methods, datasets = report['methods'], report['datasets']
    color_map = {method: palette[index % len(palette)] for index, method in enumerate(methods)}
    custom_colors = style.get('colors', {})
    if not isinstance(custom_colors, dict) or any(method not in methods or not is_color_like(color) for method, color in custom_colors.items()):
        raise ValueError('Calibration colors must map observed method identities to actual colors')
    color_map.update(custom_colors)
    columns = min(2 if width >= 5.3 else 1, len(datasets))
    facet_rows = math.ceil(len(datasets) / columns)
    legend_columns = style.get('legend_columns', min(len(methods), max(1, int(width / 1.8))))
    if isinstance(legend_columns, bool) or not isinstance(legend_columns, int) or not 1 <= legend_columns <= 8:
        raise ValueError('Calibration legend_columns must be an integer between one and eight')
    legend_rows = math.ceil(len(methods) / legend_columns)
    labels = style.get('labels', {})
    dataset_labels = style.get('dataset_labels', {})
    from research.figures.statistical import validate_display_labels
    validate_display_labels(style, report['methods'], report['datasets'])
    if not isinstance(labels, dict) or not isinstance(dataset_labels, dict):
        raise ValueError('Calibration display labels must preserve identity-to-label mappings')
    max_legend_length = max(len(str(labels.get(method, method))) for method in methods)
    legend_wrap = max(12, int(width * 72 / legend_columns / (font * .55) - 5))
    legend_line_count = max(1, math.ceil(max_legend_length / legend_wrap))
    legend_height = legend_rows * legend_line_count * font / 72 * 1.45
    note = {'sd': 'Error bars: seed SD; each bin uses its populated seeds.',
            'se': 'Error bars: SE across declared independent seeds.',
            't_ci': f'Error bars: {config["confidence"]:.0%} pointwise t intervals across declared independent seeds.',
            'bootstrap': f'Error bars: {config["confidence"]:.0%} pointwise seed-bootstrap intervals.',
            'none': 'Bin statistics are shown without uncertainty bars.'}[config['type']]
    note_lines = textwrap.wrap(note, max(25, int(width * 72 / (font * .5))))
    footer = legend_height + len(note_lines) * font / 72 * 1.25 + .14
    height = max(supplied_height, facet_rows * 3.55 + footer + .3)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths = {}
    with plt.rc_context({'font.family': 'DejaVu Serif', 'font.size': font,
                         'axes.labelsize': font, 'axes.titlesize': font,
                         'xtick.labelsize': font, 'ytick.labelsize': font,
                         'legend.fontsize': font, 'pdf.fonttype': 42, 'svg.fonttype': 'none',
                         'axes.spines.top': False, 'axes.spines.right': False}):
        fig = plt.figure(figsize=(width, height))
        grid = fig.add_gridspec(facet_rows * 2, columns,
            height_ratios=[ratio for _ in range(facet_rows) for ratio in (2.3, 1)],
            left=.14 if columns == 1 else .09, right=.98,
            top=.93, bottom=(footer + .4) / height, hspace=.82, wspace=.43)
        try:
            for panel, dataset in enumerate(datasets):
                row, column = divmod(panel, columns)
                ax = fig.add_subplot(grid[row * 2, column])
                count_ax = fig.add_subplot(grid[row * 2 + 1, column])
                ax.plot([0, 1], [0, 1], linestyle='--', color='#888888', linewidth=.8, zorder=1)
                ranges_x, ranges_y = [0, 1], [0, 1]
                for index, method in enumerate(methods):
                    bins = [entry for entry in report['aggregate_bins'] if entry['dataset'] == dataset and entry['method'] == method]
                    bins.sort(key=lambda entry: entry['bin'])
                    x = [entry['x']['estimate'] if entry['x'] else np.nan for entry in bins]
                    y = [entry['y']['estimate'] if entry['y'] else np.nan for entry in bins]
                    ax.plot(x, y, color=color_map[method], marker=MARKERS[index % len(MARKERS)],
                            linestyle=LINES[(index // len(COLORS)) % len(LINES)], markersize=3.5,
                            linewidth=1.2, zorder=3)
                    for entry in bins:
                        if entry['empty']:
                            continue
                        x_bounds, y_bounds = _bounds(entry['x'], config), _bounds(entry['y'], config)
                        if x_bounds:
                            ax.hlines(entry['y']['estimate'], *x_bounds, color=color_map[method], linewidth=.8, alpha=.7, zorder=2)
                            ranges_x.extend(x_bounds)
                        if y_bounds:
                            ax.vlines(entry['x']['estimate'], *y_bounds, color=color_map[method], linewidth=.8, alpha=.7, zorder=2)
                            ranges_y.extend(y_bounds)
                    count_ax.step([entry['bin_midpoint'] for entry in bins], [entry['counts']['mean'] for entry in bins],
                                  where='mid', color=color_map[method], linewidth=1.15,
                                  linestyle=LINES[(index // len(COLORS)) % len(LINES)])
                heading = str(dataset_labels.get(dataset, dataset))
                if len(datasets) > 1:
                    heading = '(' + chr(97 + panel) + ') ' + heading
                ax.set_title(textwrap.fill(heading, max(18, int(width / columns * 8))), loc='left', pad=9)
                # SD is a spread rather than a bounded confidence set. Retain
                # the complete bars instead of silently clipping them to [0,1].
                ax.set_xlim(min(ranges_x) - .025, max(ranges_x) + .025)
                ax.set_ylim(min(ranges_y) - .025, max(ranges_y) + .025)
                ax.set_xticks([0, .5, 1])
                ax.set_yticks([0, .5, 1])
                ax.set_xlabel('Mean predicted probability')
                ax.set_ylabel('Observed event frequency')
                ax.grid(alpha=.14, linewidth=.45)
                ax.set_axisbelow(True)
                count_ax.set_xlim(-.025, 1.025)
                count_ax.set_ylim(bottom=0)
                count_ax.set_xticks([0, .5, 1])
                count_ax.set_xlabel('Predicted probability')
                count_ax.set_ylabel('Mean count\nper seed')
                count_ax.ticklabel_format(axis='y', style='sci', scilimits=(-2, 4), useMathText=True)
                count_ax.grid(axis='y', alpha=.14, linewidth=.45)
                count_ax.set_axisbelow(True)
            handles = [Line2D([0], [0], color=color_map[method], marker=MARKERS[index % len(MARKERS)],
                linestyle=LINES[(index // len(COLORS)) % len(LINES)], markersize=3.5, linewidth=1.2,
                label=textwrap.fill(str(labels.get(method, method)), legend_wrap)) for index, method in enumerate(methods)]
            fig.legend(handles=handles, loc='lower center', ncol=legend_columns, frameon=False,
                       bbox_to_anchor=(.5, (len(note_lines) * font / 72 * 1.25 + .08) / height),
                       handlelength=1.5, columnspacing=1.2)
            fig.text(.5, .06 / height, '\n'.join(note_lines), fontsize=font, ha='center', va='bottom')
            fig.canvas.draw()
            # Correct physical margins from actual font extents, keeping the
            # paper font size fixed rather than shrinking dense labels.
            boxes = [artist.get_window_extent(fig.canvas.get_renderer())
                     for artist in fig.findobj(match=lambda item: isinstance(item, matplotlib.text.Text))
                     if artist.get_visible() and artist.get_text()]
            left_overflow = max(0, -min(box.x0 for box in boxes)) / fig.bbox.width
            right_overflow = max(0, max(box.x1 for box in boxes) - fig.bbox.width) / fig.bbox.width
            top_overflow = max(0, max(box.y1 for box in boxes) - fig.bbox.height) / fig.bbox.height
            if left_overflow or right_overflow or top_overflow:
                grid.update(left=grid.left + left_overflow + (.012 if left_overflow else 0),
                            right=grid.right - right_overflow - (.012 if right_overflow else 0),
                            top=grid.top - top_overflow - (.012 if top_overflow else 0))
                fig.canvas.draw()
            geometry = []
            for artist in fig.findobj(match=lambda item: isinstance(item, matplotlib.text.Text)):
                if artist.get_visible() and artist.get_text():
                    box = artist.get_window_extent(fig.canvas.get_renderer())
                    geometry.append({'text': artist.get_text(), 'font_pt': artist.get_fontsize(),
                                     'inside_canvas': bool(box.x0 >= -1 and box.y0 >= -1 and
                                     box.x1 <= fig.bbox.width + 1 and box.y1 <= fig.bbox.height + 1)})
            if any(not entry['inside_canvas'] for entry in geometry):
                raise ValueError('Calibration typography does not fit the physical figure canvas: ' +
                                 ', '.join(entry['text'] for entry in geometry if not entry['inside_canvas']))
            for extension in ('pdf', 'svg', 'png'):
                path = output / ('figure.' + extension)
                fig.savefig(path, dpi=300, facecolor='white')
                paths[extension] = str(path)
        finally:
            plt.close(fig)
    report.update(width_in=width, height_in=height, minimum_font_pt=font, method_colors=color_map, production_pipeline=True,
                  typography=geometry, raster_dpi=300,
                  graphical_scope='One reliability panel and count-distribution panel per dataset; shared method legend')
    caption_context = {'argumentative_duty': style.get('argumentative_duty', 'verify_probability_alignment'),
        'caption': 'Reliability across the evaluated datasets. The diagonal indicates calibrated probabilities; lower panels show the corresponding prediction-count distributions.',
        'notes': note + ' Bin means give each populated seed equal weight. Count means include empty seed/bin cells. Entirely empty bins break the curves.',
        'estimand': report['estimand'], 'uncertainty': report['uncertainty'],
        'scope': report['scope'], 'datasets': datasets, 'methods': methods,
        'evidence_summary': report['ece_summary'],
        'interpretation_contract': 'State advantages only in datasets and conditions supported by these measured summaries; seed SD is not statistical significance and calibration does not establish discrimination or causal effects.'}
    # Keep the complete input in the editable companion. Applying the saved
    # selection again must reproduce the same source-row indices and scope.
    clean_rows = [{key: value for key, value in row.items() if key != '_source_row'} for row in _read(data)]
    files = {'data': ('figure_data.json', clean_rows), 'style': ('style.json', style),
             'report': ('figure_report.json', report), 'caption_context': ('caption_context.json', caption_context)}
    for key, (filename, value) in files.items():
        path = output / filename
        path.write_text(json.dumps(value, indent=2, allow_nan=False))
        paths[key] = str(path)
    source = output / 'plot.py'
    source.write_text('# Editable reliability-diagram source; requires FOREST.\n'
        'import json, sys\nfrom pathlib import Path\np = Path(__file__).resolve().parent\n'
        'for parent in p.parents:\n    if (parent / "research/figures/calibration.py").is_file():\n'
        '        sys.path.insert(0, str(parent)); break\n'
        'from research.figures.calibration import render_calibration\n'
        'render_calibration(p, json.loads((p / "figure_data.json").read_text()), json.loads((p / "style.json").read_text()))\n')
    paths['source'] = str(source)
    return paths
