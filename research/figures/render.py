from __future__ import annotations

import html
import json
from pathlib import Path
import re
import math
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

COLORS = ["#0077BB", "#EE7733", "#009988", "#CC3311", "#33BBEE", "#EE3377", "#BBBBBB", "#000000"]
MARKERS = ['o', 's', '^', 'D', 'v', 'P', 'X', '*']
HATCHES = ['', '//', '..', '\\\\', 'xx', '++', 'oo', '--']
LABELS = {"balanced_raw": "Balanced raw", "prior_corrected": "Prior corrected", "unweighted_lr": "Unweighted LR", "sigmoid": "Sigmoid", "intercept_only": "Intercept only", "hgb": "Gradient boosting"}


def method_svg(path, style=None):
    style = style or {}
    color = style.get("color", COLORS[0])
    title = html.escape(style.get("title", "Known class weights → corrected probabilities"))
    boxes = [(30, "train", "Training observations", "Stratified split; fit preprocessing"), (330, "weighted", "Balanced logistic model", "Known inverse-frequency weights"), (630, "correct", "Analytic correction", "Subtract log(weight1 / weight0)"), (930, "evaluate", "Held-out evaluation", "Brier; log loss; paired intervals")]
    elements = []
    for x, identifier, heading, detail in boxes:
        elements.append(f'<g id="{identifier}"><rect x="{x}" y="90" width="270" height="105" rx="12" fill="#f1f4ef" stroke="{html.escape(color)}"/><text x="{x+135}" y="130" text-anchor="middle" font-size="18" font-weight="600">{heading}</text><text x="{x+135}" y="160" text-anchor="middle" font-size="12">{detail}</text></g>')
        if x < 930:
            elements.append(f'<path d="M{x+273} 142 H{x+293}" stroke="{html.escape(color)}" stroke-width="2" marker-end="url(#arrow)"/>')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="1230" height="245" viewBox="0 0 1230 245"><defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto"><path d="M0 0 L6 3 L0 6" fill="{html.escape(color)}"/></marker></defs><rect width="1230" height="245" fill="white"/><g font-family="Arial, sans-serif" fill="#22372e"><text x="30" y="42" font-size="25" font-weight="600">{title}</text>{"".join(elements)}<text id="scope" x="30" y="225" font-size="12">Unweighted LR and gradient boosting quantify alternatives; sigmoid and offset ablations identify mechanism.</text></g></svg>'
    Path(path).write_text(svg)
    return str(path)


def render_figure(output_dir, data, style=None, kind="bar"):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    style = dict(style or {})
    if isinstance(data, (str, Path)):
        source = Path(data)
        data = json.loads(source.read_text()) if source.suffix == ".json" else pd.read_csv(source).to_dict("records")
    (output / "figure_data.json").write_text(json.dumps(data, indent=2))
    (output / "style.json").write_text(json.dumps({**style, "kind": kind}, indent=2))
    if isinstance(data, dict) and data.get('production_scene') is not None:
        if kind not in ('method', 'image'):
            raise ValueError('Editable illustration scenes cannot replace empirical plots')
        from research.figures.scene import render_scene
        return render_scene(output, data, style)
    if kind == 'method' and isinstance(data, dict) and (data.get('narrative_mode') == 'scientific_story' or isinstance(data.get('storyboard'), dict) and data['storyboard'].get('mode') == 'scientific_story'):
        return render_scientific_story(output, data, style)
    if kind == 'method' and style.get('example') != 'class_weight_calibration':
        return render_method_graph(output, data, style)
    if kind == "method":
        svg = method_svg(output / "figure.svg", style)
        from matplotlib.patches import FancyBboxPatch
        fig, ax = plt.subplots(figsize=(12.3, 2.45), layout="constrained")
        ax.set_xlim(0, 12.3)
        ax.set_ylim(0, 2.45)
        ax.axis("off")
        ax.text(0.1, 2.18, style.get("title", "Known class weights → corrected probabilities"), fontsize=16, weight="bold", color="#22372e")
        headings = ["Training observations", "Balanced logistic model", "Analytic correction", "Held-out evaluation"]
        details = ["Fit preprocessing on training rows", "Known inverse-frequency weights", "Subtract log(weight1 / weight0)", "Brier; log loss; paired intervals"]
        for i, (heading, detail) in enumerate(zip(headings, details)):
            x = i * 3.05 + 0.05
            ax.add_patch(FancyBboxPatch((x, 0.75), 2.7, 1.0, boxstyle="round,pad=0.05,rounding_size=0.1", edgecolor=style.get("color", COLORS[0]), facecolor="#f1f4ef"))
            ax.text(x + 1.35, 1.35, heading, ha="center", fontsize=10, weight="bold")
            ax.text(x + 1.35, 1.02, detail, ha="center", fontsize=8)
            if i < 3:
                ax.annotate("", (x + 3.0, 1.25), (x + 2.76, 1.25), arrowprops={"arrowstyle": "->", "color": COLORS[0]})
        ax.text(0.05, 0.26, "Unweighted LR and gradient boosting quantify alternatives; sigmoid and offset ablations identify mechanism.", fontsize=9)
        for extension in ("pdf", "png"):
            fig.savefig(output / f"figure.{extension}", dpi=180, facecolor="white")
        plt.close(fig)
        return {"svg": svg, "pdf": str(output / "figure.pdf"), "png": str(output / "figure.png"), "source": svg, "data": str(output / "figure_data.json"), "element_ids": ["train", "weighted", "correct", "evaluate", "scope"]}
    records = data.get("summary", data.get("rows", [])) if isinstance(data, dict) else data
    if not records:
        raise ValueError("Figure rendering requires actual result rows")
    frame = pd.DataFrame(records)
    metric = style.get("metric", "brier")
    width, height, font = figure_dimensions(style)
    palette = style.get('palette', COLORS)
    from matplotlib.colors import is_color_like
    if not isinstance(palette, list) or not palette or any(not isinstance(color, str) or not is_color_like(color) for color in palette):
        raise ValueError('Figure palettes require actual valid colors')
    legend_columns = style.get('legend_columns', min(3, max(1, int(width/2))))
    if isinstance(legend_columns, bool) or not isinstance(legend_columns, int) or not 1 <= legend_columns <= 8:
        raise ValueError('legend_columns must be an integer between 1 and 8')
    if kind == 'bar' and 'height' not in style and 'method' in frame:
        count = len(style.get('methods', list(frame.method.unique())))
        height += max(0, math.ceil(count/legend_columns)-1) * font/72 * 1.35
    report = {'kind': kind, 'input_rows': len(frame), 'metric': metric, 'unit': style.get('unit'),
              'width_in': width, 'height_in': height, 'minimum_font_pt': font, 'vector_formats': ['pdf', 'svg'],
              'transformation': 'none', 'uncertainty': {'type': 'none'}, 'warnings': []}
    report['evidence_density'] = {'observations': len(frame),
        'datasets': int(frame.dataset.nunique()) if 'dataset' in frame else None,
        'methods': int(frame.method.nunique()) if 'method' in frame else None,
        'statistical_units': {key: int(frame[key].nunique()) for key in ('seed', 'repeat', 'subject', 'split') if key in frame}}
    if not style.get('unit') and not style.get('ylabel') and kind != 'calibration':
        report['warnings'].append('Metric units were not supplied; the renderer does not invent units.')
    plt.rcParams.update({"font.size": font, "axes.labelsize": font, 'xtick.labelsize': font, 'ytick.labelsize': font,
                         "axes.spines.top": False, "axes.spines.right": False, "svg.fonttype": "none", "pdf.fonttype": 42, "axes.titleweight": "normal"})
    fig, ax = plt.subplots(figsize=(width, height), layout="constrained")
    displayed_points = len(frame)
    if kind == "bar":
        if not {metric, 'method', 'dataset'} <= set(frame):
            plt.close(fig); raise ValueError(f"Bar charts require dataset, method and {metric} columns")
        if not np.isfinite(pd.to_numeric(frame[metric], errors='coerce')).all():
            plt.close(fig); raise ValueError('Figure measurements must be finite numeric values')
        if frame.duplicated(['dataset', 'method']).any():
            aggregation = style.get('aggregation')
            if not isinstance(aggregation, dict) or aggregation.get('method') != 'mean' or not aggregation.get('unit'):
                plt.close(fig); raise ValueError('Duplicate dataset/method rows require explicit aggregation {method:mean, unit:<column>, uncertainty:sd|se|ci95|none}')
            unit = aggregation['unit']; uncertainty = aggregation.get('uncertainty', 'none')
            if unit not in frame or frame.duplicated(['dataset', 'method', unit]).any() or uncertainty not in ('none', 'sd', 'se', 'ci95'):
                plt.close(fig); raise ValueError('Aggregation requires unique declared statistical units and a supported uncertainty definition')
            grouped = frame.groupby(['dataset', 'method'], sort=False)[metric].agg(['mean', 'std', 'count']).reset_index()
            if uncertainty != 'none' and (grouped['count'] < 2).any():
                plt.close(fig); raise ValueError('Uncertainty across statistical units requires at least two observed units per group')
            frame = grouped.rename(columns={'mean': metric})
            if uncertainty != 'none':
                spread = frame['std'] if uncertainty == 'sd' else frame['std']/np.sqrt(frame['count'])
                if uncertainty == 'ci95':
                    from scipy.stats import t
                    spread *= t.ppf(.975, frame['count']-1)
                frame['_declared_error'] = spread
                style['error'] = {'type': uncertainty, 'column': '_declared_error', 'unit': unit}
            report['transformation'] = {'aggregation': 'arithmetic mean', 'statistical_unit': unit, 'groups': len(frame)}
        methods = style.get("methods") if 'methods' in style else list(frame.method.unique())
        if not isinstance(methods, list) or not methods or len(methods) != len(set(methods)) or any(method not in set(frame.method) for method in methods):
            plt.close(fig); raise ValueError('Select at least one actually observed method')
        datasets = list(frame.dataset.unique())
        displayed_points = len(datasets) * len(methods)
        x = np.arange(len(datasets))
        bar_width = 0.8 / max(len(methods), 1)
        error = style.get('error')
        if error is None and metric + '_std' in frame:
            error = {'type': 'sd', 'column': metric + '_std', 'unit': style.get('statistical_unit')}
        if error:
            if not isinstance(error, dict) or error.get('type') not in ('sd', 'se', 'ci95', 'interval'):
                plt.close(fig); raise ValueError('Error bars require a declared sd, se, ci95 or interval type')
            report['uncertainty'] = error
            if not error.get('unit'):
                report['warnings'].append('Error-bar statistical unit was not supplied; seed uncertainty is not assumed.')
            descriptions = {'sd': 'standard deviation', 'se': 'standard error', 'ci95': '95% confidence interval', 'interval': 'supplied interval'}
            note = 'Error bars: ' + descriptions[error['type']] + (' across ' + str(error['unit']) if error.get('unit') else '; statistical unit not supplied')
            report['uncertainty_note'] = note
            ax.set_xlabel(note, fontsize=font)
        for i, method in enumerate(methods):
            values = frame[frame.method == method].set_index("dataset").reindex(datasets)
            if values[metric].isna().any():
                raise ValueError("Every selected method needs a value for every dataset")
            yerr = None
            if error:
                if error.get('lower') and error.get('upper'):
                    yerr = np.array([values[metric]-values[error['lower']], values[error['upper']]-values[metric]])
                elif error.get('column') in values:
                    yerr = values[error['column']].to_numpy()
                else:
                    plt.close(fig); raise ValueError('The declared uncertainty columns are absent')
                if not np.isfinite(yerr).all() or (np.asarray(yerr) < 0).any():
                    plt.close(fig); raise ValueError('Error bars must be finite nonnegative distances around the plotted value')
            ax.bar(x + (i - (len(methods) - 1) / 2) * bar_width, values[metric], bar_width, yerr=yerr,
                   label=style.get('labels', {}).get(method, LABELS.get(method, method)), color=style.get("color", palette[i % len(palette)]),
                   hatch=HATCHES[i % len(HATCHES)], capsize=2, edgecolor='#333333', linewidth=.35)
        rotation = style.get('rotation', 35 if len(datasets) > 5 else 0)
        if isinstance(rotation, bool) or not isinstance(rotation, (int, float)) or not math.isfinite(rotation) or not -90 <= rotation <= 90:
            plt.close(fig); raise ValueError('Label rotation must be a finite angle between -90 and 90')
        ax.set_xticks(x, [textwrap.fill(style.get('dataset_labels', {}).get(s, str(s).replace('_', ' ')), max(10, int(width*5/len(datasets)))) for s in datasets], rotation=rotation, ha='right' if rotation else 'center')
        ax.legend(frameon=False, ncol=legend_columns, fontsize=font, loc='upper center', bbox_to_anchor=(.5, -.3 if error else -.2))
        ax.set_ylabel(style.get('ylabel', metric.replace('_', ' ').title() + (' (' + str(style['unit']) + ')' if style.get('unit') else '')))
    elif kind == "line":
        xkey = style.get("x", "seed")
        if xkey not in frame or metric not in frame:
            raise ValueError(f"Line chart requires {xkey} and {metric} columns")
        group = "method" if "method" in frame else None
        if frame.duplicated(([group] if group else []) + [xkey]).any():
            plt.close(fig); raise ValueError('Line charts require one value per method and x position; explicitly aggregate statistical units before rendering')
        if not np.isfinite(pd.to_numeric(frame[metric], errors='coerce')).all():
            plt.close(fig); raise ValueError('Line chart measurements must be finite numeric values')
        for i, (label, rows) in enumerate(frame.groupby(group) if group else [("observed", frame)]):
            rows = rows.sort_values(xkey)
            ax.plot(rows[xkey], rows[metric], marker=MARKERS[i % len(MARKERS)], label=style.get('labels', {}).get(label, LABELS.get(label, label)), color=style.get("color", palette[i % len(palette)]))
        ax.legend(frameon=False, fontsize=font, ncol=legend_columns)
        ax.set_ylabel(style.get("ylabel", metric + (' (' + str(style['unit']) + ')' if style.get('unit') else '')))
        ax.set_xlabel(style.get("xlabel", xkey))
    elif kind == "calibration":
        if not {"y_true", "probability", "method"} <= set(frame):
            raise ValueError("Calibration figure requires saved per-observation predictions")
        pooled = {key: int(frame[key].nunique()) for key in ('dataset', 'seed', 'split') if key in frame and frame[key].nunique() > 1}
        if pooled and (not isinstance(style.get('pooling_scope'), str) or not style['pooling_scope'].strip()):
            plt.close(fig); raise ValueError('Calibration curves spanning datasets or splits require an explicit pooling_scope, or select one dataset/split')
        if pooled:
            report['pooling'] = {'scope': style['pooling_scope'], 'observed_groups': pooled}
        ax.plot([0, 1], [0, 1], color="#b7bcb8", linestyle="--", linewidth=1)
        if not frame.y_true.isin([0, 1]).all() or not np.isfinite(frame.probability).all() or not frame.probability.between(0, 1).all():
            plt.close(fig); raise ValueError('Calibration requires binary labels and finite probabilities in [0, 1]')
        selected = style.get('methods') if 'methods' in style else list(frame.method.unique())
        if not isinstance(selected, list) or not selected or len(selected) != len(set(selected)) or any(method not in set(frame.method) for method in selected):
            plt.close(fig); raise ValueError('Select at least one actually observed calibration method')
        displayed_points = 0
        report['bin_observations'] = []
        for i, (method, rows) in enumerate(frame.groupby("method")):
            if method not in selected:
                continue
            bins = np.minimum((rows.probability.to_numpy() * 10).astype(int), 9)
            grouped = rows.assign(bin=bins).groupby("bin").agg(probability=("probability", "mean"), frequency=("y_true", "mean"), n=("y_true", "size"))
            displayed_points += len(grouped)
            report['bin_observations'].extend({'method': method, 'bin': int(bin_id), 'observations': int(row['n']),
                'mean_probability': float(row['probability']), 'event_frequency': float(row['frequency'])}
                for bin_id, row in grouped.iterrows())
            ax.plot(grouped.probability, grouped.frequency, marker=MARKERS[i % len(MARKERS)], label=style.get('labels', {}).get(method, LABELS.get(method, method)), color=palette[i % len(palette)], markersize=4)
        report['transformation'] = {'binning': '10 equal-width probability bins', 'empty_bins': 'omitted', 'aggregation': 'mean probability and binary frequency within each bin'}
        ax.set_xlabel(style.get("xlabel", "Mean predicted probability"))
        ax.set_ylabel(style.get("ylabel", "Observed event frequency"))
        ax.legend(frameon=False, fontsize=font, ncol=legend_columns)
    elif kind == 'heatmap':
        if not {'dataset', 'method', metric} <= set(frame):
            plt.close(fig); raise ValueError('Comparison heatmaps require dataset, method and the selected metric')
        if frame.duplicated(['dataset', 'method']).any():
            plt.close(fig); raise ValueError('Heatmaps require one explicitly aggregated measurement per dataset/method')
        if not np.isfinite(pd.to_numeric(frame[metric], errors='coerce')).all():
            plt.close(fig); raise ValueError('Heatmap measurements must be finite numeric values')
        methods = style.get('methods', list(frame.method.unique()))
        datasets = list(frame.dataset.unique())
        if not isinstance(methods, list) or not methods or len(methods) != len(set(methods)) or any(method not in set(frame.method) for method in methods):
            plt.close(fig); raise ValueError('Select at least one actually observed method')
        matrix = frame.pivot(index='dataset', columns='method', values=metric).reindex(index=datasets, columns=methods)
        if matrix.isna().any().any():
            plt.close(fig); raise ValueError('A comparison heatmap must retain a measurement for every selected dataset/method')
        limits = {key: style.get(key) for key in ('vmin', 'vmax')}
        if any(value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)) for value in limits.values()):
            plt.close(fig); raise ValueError('Heatmap color limits must be explicitly supplied finite numbers')
        observed_min, observed_max = float(matrix.min().min()), float(matrix.max().max())
        low = limits['vmin'] if limits['vmin'] is not None else observed_min
        high = limits['vmax'] if limits['vmax'] is not None else observed_max
        if low > observed_min or high < observed_max or low > high or (low == high and ('vmin' in style or 'vmax' in style)):
            plt.close(fig); raise ValueError('Explicit heatmap color limits must retain every observed measurement without clipping')
        rotation = style.get('rotation', 35)
        if isinstance(rotation, bool) or not isinstance(rotation, (int, float)) or not math.isfinite(rotation) or not -90 <= rotation <= 90:
            plt.close(fig); raise ValueError('Label rotation must be a finite angle between -90 and 90')
        image = ax.imshow(matrix.to_numpy(), aspect='auto', cmap=style.get('cmap', 'viridis'), vmin=limits['vmin'], vmax=limits['vmax'])
        ax.set_xticks(range(len(methods)), [style.get('labels', {}).get(method, str(method)) for method in methods], rotation=rotation, ha='right' if rotation else 'center')
        ax.set_yticks(range(len(datasets)), [style.get('dataset_labels', {}).get(dataset, str(dataset)) for dataset in datasets])
        ax.set_xlabel(style.get('xlabel', 'Method')); ax.set_ylabel(style.get('ylabel', 'Dataset'))
        colorbar = fig.colorbar(image, ax=ax)
        colorbar.set_label(metric.replace('_', ' ').title() + (' (' + str(style['unit']) + ')' if style.get('unit') else ''))
        annotate = style.get('annotate', matrix.size <= 120)
        if not isinstance(annotate, bool):
            plt.close(fig); raise ValueError('Heatmap annotate must be true or false')
        if annotate:
            precision = style.get('annotation_format', '.3g')
            if not isinstance(precision, str) or not re.fullmatch(r'\.\d{1,2}[gfe]', precision):
                plt.close(fig); raise ValueError('Heatmap annotation_format must declare decimal precision, for example .3g')
            for yi in range(len(datasets)):
                for xi in range(len(methods)):
                    value = float(matrix.iloc[yi, xi])
                    red, green, blue, _ = image.cmap(image.norm(value))
                    lightness = .2126*red + .7152*green + .0722*blue
                    ax.text(xi, yi, format(value, precision), ha='center', va='center', fontsize=font,
                            color='#000000' if lightness > .55 else '#FFFFFF')
        report['color_mapping'] = {'minimum': float(image.norm.vmin), 'maximum': float(image.norm.vmax),
            'observed_minimum': observed_min, 'observed_maximum': observed_max,
            'limits_source': 'explicit' if 'vmin' in style or 'vmax' in style else 'observed_range',
            'annotated_values': annotate, 'annotation_format': style.get('annotation_format', '.3g') if annotate else None}
        report['uncertainty'] = {'type': 'none', 'note': 'Cell colors display the supplied aggregate; uncertainty belongs in the accompanying comparison table.'}
        report['selected_methods'] = methods
        report['matrix_shape'] = [len(datasets), len(methods)]
        displayed_points = len(datasets) * len(methods)
    elif kind == 'forest':
        error = style.get('error')
        if not {'dataset', 'method', metric} <= set(frame) or not isinstance(error, dict) or error.get('type') not in ('ci95', 'interval') or not error.get('unit'):
            plt.close(fig); raise ValueError('Forest plots require dataset/method estimates and declared observed intervals with a statistical unit')
        lower, upper = error.get('lower'), error.get('upper')
        if lower not in frame or upper not in frame or frame.duplicated(['dataset', 'method']).any():
            plt.close(fig); raise ValueError('Forest plots require unique dataset/method rows and actual lower/upper interval columns')
        measured = frame[[metric, lower, upper]].apply(pd.to_numeric, errors='coerce')
        if not np.isfinite(measured).all().all() or (measured[lower] > measured[metric]).any() or (measured[upper] < measured[metric]).any():
            plt.close(fig); raise ValueError('Forest intervals must be finite and contain their observed estimate')
        y = np.arange(len(frame))
        for i, row in frame.reset_index(drop=True).iterrows():
            color = palette[list(frame.method.unique()).index(row['method']) % len(palette)]
            ax.errorbar(row[metric], i, xerr=[[row[metric]-row[lower]], [row[upper]-row[metric]]], fmt='o', color=color, capsize=3)
        ax.set_yticks(y, [str(row['dataset']) + ' / ' + style.get('labels', {}).get(row['method'], str(row['method'])) for _, row in frame.iterrows()])
        ax.invert_yaxis()
        ax.set_xlabel(style.get('xlabel', metric.replace('_', ' ').title() + (' (' + str(style['unit']) + ')' if style.get('unit') else '')))
        if 'reference' in style:
            reference = style['reference']
            if isinstance(reference, bool) or not isinstance(reference, (int, float)) or not math.isfinite(reference):
                plt.close(fig); raise ValueError('Forest reference must be an explicitly supplied finite value')
            ax.axvline(reference, color='#777777', linestyle='--', linewidth=1)
        report['uncertainty'] = error
    elif kind == 'scatter':
        xkey = style.get('x')
        if not isinstance(xkey, str) or xkey not in frame or metric not in frame:
            plt.close(fig); raise ValueError('Scatter plots require an explicit actual x column and metric')
        if not np.isfinite(frame[[xkey, metric]].apply(pd.to_numeric, errors='coerce')).all().all():
            plt.close(fig); raise ValueError('Scatter measurements must be finite numeric values')
        groups = frame.groupby('method', sort=False) if 'method' in frame else [('observed', frame)]
        for i, (name, rows) in enumerate(groups):
            ax.scatter(rows[xkey], rows[metric], label=style.get('labels', {}).get(name, str(name)), marker=MARKERS[i % len(MARKERS)], color=palette[i % len(palette)], s=24)
        ax.legend(frameon=False, ncol=legend_columns, fontsize=font)
        ax.set_xlabel(style.get('xlabel', xkey)); ax.set_ylabel(style.get('ylabel', metric + (' (' + str(style['unit']) + ')' if style.get('unit') else '')))
    else:
        plt.close(fig)
        raise ValueError("Supported figure kinds: bar, line, calibration, method, heatmap, forest, scatter")
    if style.get('title'):
        ax.set_title(textwrap.fill(style['title'], max(18, int((width-.55)*72/((font+1)*.52)))), loc='left', pad=10, fontsize=font+1)
    if kind != 'heatmap':
        ax.grid(axis="y", alpha=0.15)
    ax.set_axisbelow(True)
    paths = {}
    for extension in ("svg", "pdf", "png"):
        path = output / f"figure.{extension}"
        fig.savefig(path, dpi=300, facecolor="white")
        paths[extension] = str(path)
    plt.close(fig)
    write_plot_source(output)
    report['plotted_rows'] = len(frame)
    report['displayed_points'] = displayed_points
    report['selected_methods'] = methods if kind in ('bar', 'heatmap') else selected if kind == 'calibration' else list(frame.method.unique()) if 'method' in frame else ['observed']
    report['omitted_methods'] = [method for method in frame.method.unique() if method not in report['selected_methods']] if 'method' in frame else []
    if kind in ('bar', 'heatmap', 'forest'):
        columns = [key for key in ('dataset', 'method', metric) if key in frame]
        error = report.get('uncertainty', {})
        columns.extend(key for key in (error.get('column'), error.get('lower'), error.get('upper')) if key in frame and key not in columns)
        report['displayed_measurements'] = frame[frame.method.isin(report['selected_methods'])][columns].to_dict('records')
    (output / 'figure_report.json').write_text(json.dumps(report, indent=2))
    paths.update({"source": str(output / "plot.py"), "data": str(output / "figure_data.json"), "style": str(output / "style.json"), 'report': str(output / 'figure_report.json')})
    return paths


def figure_dimensions(style):
    """Physical output width, so fonts are specified at the intended paper size."""
    paper = style.get('paper_layout', {})
    from research.paper.layout import normalize_layout
    config = normalize_layout(paper, style.get('paper_template', 'article'))
    full = 5.5 if style.get('paper_template') == 'iclr2027' else 6.5
    default = (full-.25)/2 if config['columns'] == 'double' and style.get('span', 'column') != 'page' else full
    width = float(style.get('layout_width_in', style.get('width', default)))
    height = float(style.get('height', max(2.5, width*.62)))
    font = float(style.get('font_size', 9))
    if not all(math.isfinite(value) for value in (width, height, font)) or width <= 0 or height <= 0 or font < 8:
        raise ValueError('Figures require positive finite dimensions and fonts of at least 8 pt at the target width')
    return width, height, font


def write_plot_source(output):
    (output / 'plot.py').write_text("# Editable figure source; use an installed FOREST environment or keep it inside the source project.\nimport json, sys\nfrom pathlib import Path\np=Path(__file__).resolve().parent\nfor parent in p.parents:\n    if (parent/'research/figures/render.py').is_file():\n        sys.path.insert(0,str(parent)); break\nfrom research.figures.render import render_figure\nstyle=json.loads((p/'style.json').read_text())\nkind=style.pop('kind','bar')\nrender_figure(p,json.loads((p/'figure_data.json').read_text()),style,kind)\n")


def render_scientific_story(output, data, style):
    """Draw concise scientific content on a fixed, physically printable canvas.

    The complete storyboard remains editable in figure_data.json and the caption
    companion. Rationale and authoring instructions are never figure typography.
    Dense content fails explicitly; it cannot silently become a poster or tiny type.
    """
    from matplotlib.patches import FancyBboxPatch
    from matplotlib.text import Text
    from research.figures.narrative import evidence_catalog, validate_storyboard
    story, context = data.get('storyboard'), data.get('story_context')
    if not isinstance(context, dict):
        raise ValueError('Scientific story rendering requires the actual story_context for measurement and source bindings')
    validation = validate_storyboard(story, context, 'scientific_story')
    operations, edges = story['mechanism']['operations'], story['mechanism']['edges']
    if data.get('nodes') != operations or data.get('edges') != edges:
        raise ValueError('Rendered graph must preserve every actual storyboard operation and dependency')
    catalog, bindings = evidence_catalog(context), {}
    def resolve(text):
        def measured(match):
            identifier = match.group(1)
            if identifier not in catalog or catalog[identifier]['kind'] != 'measurement':
                raise ValueError('Scientific story contains an unbound measurement')
            record = catalog[identifier]['record']; bindings[identifier] = record
            value = str(record['value']) if isinstance(record['value'], int) else format(record['value'], '.6g')
            return value + (' ' + str(record['unit']) if record.get('unit') else '')
        return re.sub(r'\[\[metric:([^]]+)\]\]', measured, text)
    width, height, font = figure_dimensions({
        'layout_width_in': story['composition']['print_width_in'],
        'height': story['composition'].get('print_height_in', 4.4),
        'font_size': story['composition']['minimum_font_pt'], **style})
    font = max(font, story['composition']['minimum_font_pt'])
    palette = style.get('palette', COLORS)
    from matplotlib.colors import is_color_like
    if not isinstance(palette, list) or not palette or any(not isinstance(color, str) or not is_color_like(color) for color in palette):
        raise ValueError('Story palettes require actual valid colors')
    accent = style.get('color', palette[0])
    if not is_color_like(accent):
        raise ValueError('Story accent requires an actual valid color')
    plt.rcParams.update({'font.size': font, 'svg.fonttype': 'none', 'pdf.fonttype': 42})
    fig = plt.figure(figsize=(width, height), dpi=100)
    renderer = fig.canvas.get_renderer()
    def text_width(text, size, weight):
        artist = Text(0, 0, text, fontsize=size, fontweight=weight); artist.set_figure(fig)
        return artist.get_window_extent(renderer).width / fig.dpi
    def wrap(text, available, size=font, weight='normal'):
        if available <= .2:
            raise ValueError('Scientific story needs a wider span or separate panels at its declared print size')
        lines = []
        for paragraph in resolve(text).splitlines() or ['']:
            line = ''
            for word in paragraph.split():
                if text_width(word, size, weight) > available:
                    raise ValueError('Scientific story contains an unprintable visible token; keep full IDs in source and use separate panels or concise labels: ' + word)
                candidate = (line + ' ' + word).strip()
                if line and text_width(candidate, size, weight) > available:
                    lines.append(line); line = word
                else:
                    line = candidate
            lines.append(line)
        text = '\n'.join(lines)
        return {'text': text, 'height': len(lines)*size/72*1.2, 'font': size, 'weight': weight}
    margin, pad, gap = .13, .06, .12
    inner = width-2*margin
    display = story.get('display', {})
    example_kind=story['mechanism'].get('example',{}).get('kind')
    operation_labels=' '.join(op['label'] for op in operations).casefold()
    legacy_binding=all(phrase in operation_labels for phrase in ('resolve metric pointer','join value to identity','render bound comparison cell'))
    record_binding=example_kind=='record_binding' or example_kind is None and legacy_binding
    # Legacy projection preserves complete prose in the companion. Labels are
    # taken verbatim from supplied scientific text; no generated summary/claim.
    def first_sentence(text):
        return re.split(r'(?<=[.!?])\s+', text.strip(), maxsplit=1)[0]
    legacy_problem = next((label for label in story['composition']['exact_labels'] if story['problem']['text'].startswith(label)), first_sentence(story['problem']['text']))
    problem_text = display.get('problem', legacy_problem)
    consequence_text = display.get('consequence', first_sentence(story['consequence']['text']))
    heading = {'HYPOTHESIS': 'Testable expectation', 'MEASURED': 'Observed outcome', 'REPORTED': 'Reported finding',
               'INFERRED': 'Inferred consequence', 'METHOD_DEFINITION': 'Method consequence'}[story['consequence']['status']]
    title = wrap(style.get('title', story['title']), inner, font+1, 'bold')
    problem = wrap('Problem bottleneck: ' + problem_text, inner-2*pad)
    consequence = wrap(heading + ': ' + re.sub('^'+re.escape(heading)+r':\s*', '', consequence_text), inner-2*pad)
    scope = display.get('scope')
    if scope is None:
        scope = next((label for label in story['composition']['exact_labels']
                      if any(label in boundary for boundary in story['boundary_conditions'])), '')
    scope_part = wrap('Boundary conditions: '+scope, inner) if scope else None
    title_h = title['height']+.07
    problem_h = problem['height']+2*pad
    test_text = display.get('test', '')
    if not test_text and record_binding:
        full_test=story['consequence'].get('test','').casefold()
        if all(phrase in full_test for phrase in ('bound and unbound','pointer deletion','record reordering')):
            test_text='Bound/unbound: pointer deletion + reordering'
    test_part = wrap('Discriminating test: ' + test_text, inner-2*pad) if test_text else None
    consequence_h = consequence['height']+2*pad+(test_part['height']+.04 if test_part else 0)
    scope_h = scope_part['height']+.04 if scope_part else 0
    mechanism_top = height-margin-title_h-problem_h-gap
    mechanism_bottom = margin+scope_h+consequence_h+gap
    mechanism_h = mechanism_top-mechanism_bottom
    if mechanism_h <= .7:
        plt.close(fig); raise ValueError('Scientific story is too dense at its print size; use concise display labels or separate panels')
    ax = fig.add_axes((0, 0, 1, 1)); ax.set(xlim=(0,width), ylim=(0,height)); ax.axis('off')
    text_artists, text_regions, panels, edge_geometry = [], [], {}, []
    def put(part, x, top, available, *, center=False, color='#17232B', background=None):
        artist=ax.text(x,top,part['text'], fontsize=part['font'],fontweight=part['weight'],va='top',
                       ha='center' if center else 'left',color=color,linespacing=1.2,zorder=5,
                       bbox={'facecolor':background,'edgecolor':'none','pad':.4} if background else None)
        text_artists.append(artist)
        text_regions.append((artist, x-available/2 if center else x, top-part['height'], available, part['height']))
        return top-part['height']
    def box(x, top, w, h, color='white', edge=accent):
        ax.add_patch(FancyBboxPatch((x,top-h),w,h,boxstyle='round,pad=0,rounding_size=.045',
                                  facecolor=color,edgecolor=edge,linewidth=.8,zorder=2))
    def arrow(start,end,label=None,label_pos=None,available=1.4):
        ax.annotate('',end,start,arrowprops={'arrowstyle':'->','color':accent,'lw':1.0},zorder=3)
        if label:
            part=wrap(label,available)
            position=label_pos or ((start[0]+end[0])/2,(start[1]+end[1])/2+.05)
            put(part,*position,available,center=True,background='white')
            edge_geometry.append({'label':label,'start_in':list(start),'end_in':list(end),'label_position_in':list(position)})
    cursor=height-margin
    cursor=put(title,margin,cursor,inner)-.07
    box(margin,cursor,inner,problem_h,'#F6F7F8','#CBD3D8'); put(problem,margin+pad,cursor-pad,inner-2*pad)
    panels['problem']={'x_in':margin,'top_in':cursor,'width_in':inner,'height_in':problem_h}
    box(margin,mechanism_top,inner,mechanism_h,'#EEF5F8')
    panels['mechanism']={'x_in':margin,'top_in':mechanism_top,'width_in':inner,'height_in':mechanism_h}
    # The operation layer is topology, not a wall of action/rationale prose.
    graph_width=inner-2*pad
    count=len(operations)
    if count>6:
        plt.close(fig); raise ValueError('Scientific story has too many operations for one physical panel; split the mechanism into separate panels')
    narrow = width < 5
    columns=min(2,count) if narrow else count
    rows=math.ceil(count/columns)
    node_gap=.20 if narrow else .17
    node_width=(graph_width-node_gap*(columns-1))/columns
    node_parts={op['id']:wrap(op['label'],node_width-2*pad,weight='bold') for op in operations}
    transforms={op['id']:wrap(op['display_transform'],node_width-2*pad) for op in operations if op.get('display_transform')}
    node_height=max(part['height']+2*pad+(transforms[key]['height']+.04 if key in transforms else 0)
                    for key,part in node_parts.items())
    header=wrap('Identity-binding mechanism' if record_binding else 'Key mechanism',graph_width,weight='bold')
    put(header,margin+pad,mechanism_top-pad,graph_width)
    node_top=mechanism_top-pad-header['height']-.06
    def dependency_label(edge):
        label=edge.get('display_label')
        if label is None:
            label=edge['label']
            for original,compact in (('Run ID','Run'),('JSON pointer','pointer'),('exact numeric leaf','value'),('metric identity','metric'),('uncertainty definition','uncertainty'),('Bound visual identity','Visual'),('argumentative paragraph anchor','paragraph anchor'),('Actual independently accumulated total','Independent total'),('Actual integer total','Integer total')):
                label=label.replace(original,compact)
        return label
    # At column width operations occupy two rows rather than microscopic type.
    row_gap=max((wrap(dependency_label(edge),node_width)['height'] for edge in edges),default=0)+.16 if narrow else .0
    positions={}
    for index,operation in enumerate(operations):
        row,column=divmod(index,columns)
        left=margin+pad+column*(node_width+node_gap)
        top=node_top-row*(node_height+row_gap)
        is_key=operation['id']==story['mechanism']['key_change']['operation_id']
        box(left,top,node_width,node_height,'#DDECE9' if is_key else 'white')
        after=put(node_parts[operation['id']],left+pad,top-pad,node_width-2*pad)
        if operation['id'] in transforms:
            put(transforms[operation['id']],left+pad,after-.04,node_width-2*pad)
        positions[operation['id']]=(left,top,node_width,node_height)
    edge_top=node_top-node_height-.06
    edge_height=0
    for index,edge in enumerate(edges):
        first,second=positions[edge['source']],positions[edge['target']]
        label=dependency_label(edge)
        available=min(node_width+.06,max(.7,abs(second[0]-first[0])+node_width-.12))
        part=wrap(label,available)
        left_center=first[0]+first[2]/2;right_center=second[0]+second[2]/2
        if narrow and first[1]!=second[1]:
            start=(left_center,first[1]-first[3]);end=(right_center,second[1])
            # Label the source's own vertical lane; parallel operands keep
            # separate legible labels before they meet at the actual operation.
            arrow(start,end,label,(left_center,start[1]-.06),node_width)
        else:
            lane=edge_top-index*.025
            start=(left_center,first[1]-first[3]);end=(right_center,second[1]-second[3])
            ax.plot([start[0],start[0]],[start[1],lane],color=accent,lw=.8,zorder=3)
            ax.plot([end[0],end[0]],[lane,end[1]],color=accent,lw=.8,zorder=3)
            arrow((left_center,lane),(right_center,lane),label,((left_center+right_center)/2,lane-.045),available)
            edge_height=max(edge_height,part['height']+.045+index*.025)
    zoom_top=(node_top-rows*node_height-(rows-1)*row_gap-.08 if narrow and rows>1
              else edge_top-edge_height-.13)
    zoom_bottom=mechanism_bottom+pad
    zoom_height=zoom_top-zoom_bottom
    refs=story['mechanism'].get('example',{}).get('metric_refs')
    if refs is None:
        refs=list(dict.fromkeys(ref for operation in operations for ref in operation['evidence_refs']
                                if catalog[ref]['kind']=='measurement'))
    # Only cited actual values enter the compact example. Parent identity is
    # dereferenced from the same actual run and JSON pointer, never number equality.
    if len(refs)>4 and 'metric_refs' not in story['mechanism'].get('example',{}):
        plt.close(fig);raise ValueError('Concrete mechanism example needs an explicit printable selection of actual metric_refs, or separate panels; source measurements are never silently omitted')
    example_refs=list(refs)
    identities={};parent_pointer=None;parent_keys=set()
    for ref in example_refs:
        record=catalog[ref]['record']; bindings[ref]=record
        pointer=record.get('pointer');run_id=record.get('run_id')
        if not isinstance(pointer,str) or not run_id:
            continue
        parent_pointer=pointer.rsplit('/',1)[0]
        parent_keys.add((run_id,parent_pointer))
        source=next((run for run in context.get('source_runs',[]) if run.get('id')==run_id),None)
        parent=source.get('metrics') if source else None
        try:
            for token in parent_pointer.strip('/').split('/') if parent_pointer else []:
                token=token.replace('~1','/').replace('~0','~')
                parent=parent[int(token)] if isinstance(parent,list) else parent[token]
            if isinstance(parent,dict):
                if record_binding:
                    leaf=pointer.rsplit('/',1)[-1].replace('~1','/').replace('~0','~')
                    if parent.get(leaf)!=record['value']:
                        raise ValueError('Record-binding example value disagrees with its actual run-scoped parent pointer')
                for key in ('dataset','method'):
                    if isinstance(parent.get(key),str): identities[key]=parent[key]
        except (KeyError,IndexError,TypeError):
            pass
    if zoom_height<.35:
        plt.close(fig); raise ValueError('Scientific story is too dense at its print size; use concise edge labels or separate panels')
    if record_binding and (len(parent_keys)!=1 or not identities):
        plt.close(fig);raise ValueError('A record-binding example requires one actual run-scoped parent record and its supplied dataset/method identity; use separate examples for different parents')
    if len(parent_keys)>1:identities={}
    branches=story['mechanism'].get('representation_branches',[])
    if not branches and identities and record_binding:
        # Compatibility for saved storyboards: only representations explicitly
        # named in supplied actions/output definitions can become branches.
        supplied=' '.join(story['outputs']+[op['action'] for op in operations])
        branches=[{'label':label} for pattern,label in ((r'\btables?\b','Table'),(r'\bprose\b','Prose'),(r'\b(?:vector )?heatmaps?\b','Vector heatmap'))
                  if re.search(pattern,supplied,re.I)]
    if record_binding and identities and example_refs:
        # A real record-level join, enlarged relative to the topology strip.
        source_width=graph_width*.35;cell_width=graph_width*.29;output_width=graph_width*.22
        source_left=margin+pad;cell_left=source_left+source_width+graph_width*.06;out_left=cell_left+cell_width+graph_width*.07
        source_lines=['Run R1 · rounded leaves']
        for ref in example_refs:
            leaf=catalog[ref]['record'].get('pointer','').rsplit('/',1)[-1]
            if leaf in ('seeds','test_samples'): continue
            label='SD' if leaf=='accuracy_std' and any(value.startswith('SD ') for value in story['composition']['exact_labels']) else leaf
            source_lines.append(catalog[ref]['record'].get('pointer',leaf))
            source_lines.append(resolve('[[metric:'+ref+']]'))
        source_part=wrap('\n'.join(source_lines),source_width-2*pad)
        cell_lines=['B1: R1:'+(parent_pointer or ''),identities.get('dataset','')+' / '+identities.get('method','')]
        bound_quantities=[]
        for ref in example_refs:
            leaf=catalog[ref]['record'].get('pointer','').rsplit('/',1)[-1]
            if leaf in ('seeds','test_samples'):continue
            # Metric labels come verbatim from exact_labels when the legacy
            # scientific record declares them; no uncertainty type is inferred.
            label=next((value for value in story['composition']['exact_labels'] if leaf=='accuracy' and value.startswith('Accuracy')
                        or leaf=='accuracy_std' and value.startswith('SD ')),leaf)
            value=resolve('[[metric:'+ref+']]')
            # Pair quantity with its supplied unit/definition, rather than
            # leaving the bound record as a directory of labels.
            if label.startswith('Accuracy ('):
                name,definition='Accuracy',label[len('Accuracy '):]
            elif label.startswith('SD '):
                name,definition='SD',label[len('SD '):]
            else:
                name,definition=label,''
            cell_lines.append(name+' = '+value)
            if definition:cell_lines.append(definition)
            bound_quantities.append({'metric_ref':ref,'label':label,'name':name,'definition':definition,'display_value':value,'exact_value':catalog[ref]['record']['value']})
        cell_part=wrap('\n'.join(cell_lines),cell_width-2*pad)
        if max(source_part['height'],cell_part['height'])+2*pad>zoom_height:
            plt.close(fig);raise ValueError('Concrete mechanism example cannot fit at final print size; use a wider span or separate panels: '+str((source_part['height'],cell_part['height'],zoom_height,mechanism_h,node_height,edge_height)))
        box(source_left,zoom_top,source_width,zoom_height,'#FFFFFF');put(source_part,source_left+pad,zoom_top-pad,source_width-2*pad)
        box(cell_left,zoom_top,cell_width,zoom_height,'#DDECE9');put(cell_part,cell_left+pad,zoom_top-pad,cell_width-2*pad)
        join_y=zoom_top-zoom_height*.50
        # Same key-operation color and an explicit dashed detail connector tie
        # the record-level transformation to its actual upper operation.
        key_pos=positions[story['mechanism']['key_change']['operation_id']]
        ax.plot([key_pos[0]+key_pos[2]*.25,cell_left+cell_width*.10],
                [key_pos[1]-key_pos[3],zoom_top],color='#46746B',lw=.8,linestyle='--',zorder=3)
        if any('compiled placement' in op['label'].casefold() for op in operations):
            placement=next(op for op in operations if 'compiled placement' in op['label'].casefold())
            pos=positions[placement['id']]
            input_part=wrap('compiler positions\n(if available)',pos[2])
            input_top=mechanism_top+.18
            put(input_part,pos[0]+pos[2]/2,input_top,pos[2],center=True,background='white')
            arrow((pos[0]+pos[2]/2,input_top-input_part['height']),
                  (pos[0]+pos[2]/2,pos[1]))
        arrow((source_left+source_width,join_y),(cell_left,join_y),'join',((source_left+source_width+cell_left)/2,join_y+.20),cell_left-source_left-source_width-.03)
        branch_gap=.03
        branch_parts=[]
        branch_padding=.045
        for branch in branches:
            visible_branch=('Heatmap' if branch['label']=='Vector heatmap' else branch['label'])+' · B1'
            heading_part=wrap(visible_branch,output_width-2*pad,weight='bold')
            # An actual populated comparison cell demonstrates propagation.
            # Its number is resolved from the same B1 metric identity, never
            # copied from a hand-written annotation or a different source.
            quantity_part=wrap(bound_quantities[0]['name']+' '+bound_quantities[0]['display_value'],output_width-2*pad) if branch['label'].casefold()=='table' and bound_quantities else None
            minimum=heading_part['height']+2*branch_padding+(quantity_part['height']+.015 if quantity_part else 0)
            branch_parts.append((branch,heading_part,quantity_part,minimum))
        remaining=zoom_height-sum(item[3] for item in branch_parts)-branch_gap*(max(1,len(branch_parts))-1)
        if remaining<0:
            # Keep an actual populated cell when its metric name cannot fit;
            # B1 already exposes the exact metric/units at the same type size.
            revised=[]
            for branch,heading_part,quantity_part,minimum in branch_parts:
                if quantity_part:
                    shorter=wrap(bound_quantities[0]['display_value'],output_width-2*pad)
                    minimum-=quantity_part['height']-shorter['height'];quantity_part=shorter
                revised.append((branch,heading_part,quantity_part,minimum))
            branch_parts=revised
            remaining=zoom_height-sum(item[3] for item in branch_parts)-branch_gap*(max(1,len(branch_parts))-1)
        if remaining<0:
            plt.close(fig);raise ValueError('Populated representation branches cannot fit at final print size; use separate panels')
        top=zoom_top
        for branch,heading_part,quantity_part,minimum in branch_parts:
            branch_height=minimum+remaining/max(1,len(branch_parts))
            box(out_left,top,output_width,branch_height,'#FFFFFF')
            after=put(heading_part,out_left+pad,top-branch_padding,output_width-2*pad)
            if quantity_part:
                # Header/data separation is the actual cell border; B1 points
                # to its visible dataset/method, metric, units and SD definition.
                line_y=after-.006
                ax.plot([out_left,out_left+output_width],[line_y,line_y],color=accent,lw=.45,zorder=3)
                put(quantity_part,out_left+pad,after-.015,output_width-2*pad)
            arrow((cell_left+cell_width,join_y),(out_left,top-branch_height/2))
            top-=branch_height+branch_gap
        put(wrap('reuse',out_left-cell_left-cell_width-.03), (cell_left+cell_width+out_left)/2, zoom_bottom+.19,out_left-cell_left-cell_width-.03,center=True)
    else:
        # General mechanisms retain their short symbolic operation and real
        # cited metrics; no domain-specific join/branch is invented.
        key=story['mechanism']['key_change']
        operation=next(op for op in operations if op['id']==key['operation_id'])
        content=['Inputs: '+story['inputs'][0], 'Outputs: '+story['outputs'][0]]
        if operation.get('display_transform'):content.insert(0,operation['display_transform'])
        content += [resolve('[[metric:'+ref+']]') for ref in example_refs]
        detail=wrap('\n'.join(content),graph_width-2*pad)
        if detail['height']+2*pad>zoom_height:
            plt.close(fig);raise ValueError('Mechanism detail cannot fit at final print size; use separate panels')
        box(margin+pad,zoom_top,graph_width,zoom_height,'white');put(detail,margin+2*pad,zoom_top-pad,graph_width-2*pad)
    consequence_top=mechanism_bottom-gap
    box(margin,consequence_top,inner,consequence_h,'#F2F6F1','#CBD3D8')
    after=put(consequence,margin+pad,consequence_top-pad,inner-2*pad)
    if test_part:put(test_part,margin+pad,after-.04,inner-2*pad)
    panels['consequence']={'x_in':margin,'top_in':consequence_top,'width_in':inner,'height_in':consequence_h}
    if scope_part:put(scope_part,margin,margin+scope_part['height'],inner)
    fig.canvas.draw(); canvas=fig.bbox
    overflow=[artist.get_text() for artist in text_artists if not canvas.contains(artist.get_window_extent().x0,artist.get_window_extent().y0)
              or not canvas.contains(artist.get_window_extent().x1,artist.get_window_extent().y1)]
    if overflow:
        plt.close(fig);raise ValueError('Scientific story text exceeds its fixed physical canvas; use concise labels or separate panels: '+repr(overflow[0]))
    paths={}
    for extension in ('pdf','svg','png'):
        target=output/('figure.'+extension);fig.savefig(target,dpi=300,facecolor='white');paths[extension]=str(target)
    plt.close(fig);write_plot_source(output)
    companion={'title':story['title'],'central_message':story['central_message'],'problem':story['problem'],
               'operation_rationale':operations,'key_change':story['mechanism']['key_change'],
               'consequence':story['consequence'],'inputs':story['inputs'],'outputs':story['outputs'],
               'boundary_conditions':story['boundary_conditions'],'baseline':story.get('baseline'),
               'exact_metric_bindings':bindings,'run_aliases':{'R1':next(iter(bindings.values())).get('run_id')} if record_binding and bindings else {},
               'binding_aliases':{'B1':{'run_alias':'R1','parent_pointer':parent_pointer,'identity':identities,'metric_refs':example_refs}} if record_binding else {},'display_rounding':'Numeric leaves use .6g display rounding; exact values are retained unchanged.', 'scope':'Full source rationale retained; this is not an empirical advantage claim.'}
    if record_binding and bindings:
        run_id=companion['run_aliases']['R1']
        protocol_values={record.get('pointer','').rsplit('/',1)[-1]:record['value'] for record in bindings.values()}
        companion['caption_text']='Run R1 resolves to '+str(run_id)+'. Numeric leaves are resolved by their exact run-scoped JSON pointers to parent '+str(parent_pointer)+'. Binding B1 attaches the supplied dataset, method, metric units and uncertainty definition to those exact values; Table, Prose and vector Heatmap reuse B1. Display values are rounded to six significant digits; exact values remain unchanged in the source. '
        if 'seeds' in protocol_values:
            companion['caption_text']+=str(protocol_values['seeds'])+' random splits reuse source samples; across-split SD is not variation across independent populations. '
        if 'test_samples' in protocol_values:
            companion['caption_text']+=str(protocol_values['test_samples'])+' test samples are evaluated per split. '
        companion['caption_text']+='The compiler-position check is a supplied method operation; no page positions or successful validation are asserted by this diagram. The reporting-error benefit is an unmeasured testable expectation. '+story['consequence'].get('test','')
    (output/'figure_caption_context.json').write_text(json.dumps(companion,ensure_ascii=False,indent=2))
    report={'kind':'method','narrative_mode':'scientific_story','evidence_role':'conceptual_illustration',
            'source':'actual evidence-bound scientific storyboard and operations','validation':validation,
            'nodes':len(operations),'edges':len(edges),'width_in':width,'height_in':height,
            'requested_height_in':height,'minimum_font_pt':min(artist.get_fontsize() for artist in text_artists),
            'vector_formats':['pdf','svg'],'panel_geometry':panels,'reading_order':['problem','mechanism','consequence'],
            'mechanism_area_fraction':mechanism_h/(problem_h+mechanism_h+consequence_h),
            'edge_labels':[edge['label'] for edge in edge_geometry],'source_edge_labels':[edge['label'] for edge in edges],'edge_label_geometry':edge_geometry,
            'inputs':story['inputs'],'outputs':story['outputs'],'boundary_conditions':story['boundary_conditions'],
            'consequence_status':story['consequence']['status'],'metric_bindings':bindings,
            'concrete_example_identity':identities,'bound_quantities':bound_quantities if record_binding else [],'populated_comparison_cells':[{'representation':'Table','binding':'B1',**bound_quantities[0]}] if record_binding and bound_quantities and any(branch['label'].casefold()=='table' for branch in branches) else [],'example_kind':'record_binding' if record_binding else example_kind or 'mechanism_context','representation_branches':[branch['label'] for branch in branches],
            'rationale_companion':'figure_caption_context.json','display_rounding':'.6g','warnings':[],
            'verification_scope':'Actual vector text, physical dimensions and source identities; scientific entailment and pixel quality require independent reviews.'}
    (output/'figure_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return {**paths,'source':str(output/'plot.py'),'data':str(output/'figure_data.json'),
            'style':str(output/'style.json'),'report':str(output/'figure_report.json'),
            'caption_context':str(output/'figure_caption_context.json')}

def render_method_graph(output, data, style):
    """Draw only supplied method nodes/edges; no domain story is substituted."""
    from matplotlib.patches import FancyBboxPatch
    if not isinstance(data, dict) or not isinstance(data.get('nodes'), list) or not data['nodes']:
        raise ValueError('Method diagrams require explicit nodes and edges; the class-weight example must be explicitly selected')
    nodes, edges = data['nodes'], data.get('edges', [])
    if any(not isinstance(node, dict) or not isinstance(node.get('id'), str) or not isinstance(node.get('label'), str) for node in nodes):
        raise ValueError('Method nodes need unique IDs and labels')
    ids = [node['id'] for node in nodes]
    if len(ids) != len(set(ids)):
        raise ValueError('Method nodes need unique IDs and labels')
    if not isinstance(edges, list) or any(not isinstance(edge, dict) or edge.get('source') not in ids or edge.get('target') not in ids for edge in edges):
        raise ValueError('Method edges must connect supplied nodes')
    width, height, font = figure_dimensions(style)
    horizontal = width >= 5
    if 'height' not in style:
        height = max(2.5, math.ceil(len(nodes)/3)*1.1) if horizontal else max(3, len(nodes)*.9)
    fig, ax = plt.subplots(figsize=(width, height), layout='constrained')
    columns = min(3, len(nodes)) if horizontal else 1
    rows = math.ceil(len(nodes)/columns)
    positions = {node['id']: ((i % columns+.5)/columns, 1-(i//columns+.5)/rows) for i, node in enumerate(nodes)}
    ax.set(xlim=(0, 1), ylim=(0, 1)); ax.axis('off')
    bw, bh = .82/columns, .58/rows
    for edge in edges:
        ax.annotate('', positions[edge['target']], positions[edge['source']], arrowprops={'arrowstyle': '->', 'color': '#555555', 'shrinkA': 28, 'shrinkB': 28}, zorder=1)
    for node in nodes:
        x, y = positions[node['id']]
        ax.add_patch(FancyBboxPatch((x-bw/2, y-bh/2), bw, bh, boxstyle='round,pad=.012', facecolor='#F3F6F7', edgecolor=style.get('color', COLORS[0]), zorder=2))
        ax.text(x, y, textwrap.fill(node['label'], max(10, int(width*11/columns))), ha='center', va='center', fontsize=font, zorder=3)
    paths = {}
    for ext in ('pdf', 'svg', 'png'):
        path = output / ('figure.'+ext); fig.savefig(path, dpi=300, facecolor='white'); paths[ext] = str(path)
    plt.close(fig); write_plot_source(output)
    report = {'kind': 'method', 'source': 'explicit supplied nodes and edges', 'nodes': len(nodes), 'edges': len(edges),
              'width_in': width, 'height_in': height, 'minimum_font_pt': font, 'vector_formats': ['pdf', 'svg'], 'warnings': []}
    (output/'figure_report.json').write_text(json.dumps(report, indent=2))
    return {**paths, 'source': str(output/'plot.py'), 'data': str(output/'figure_data.json'), 'style': str(output/'style.json'), 'report': str(output/'figure_report.json')}


def revise_style(instruction, style=None):
    """Small explicit style commands; other instructions must use an actual model."""
    style = dict(style or {})
    changed = False
    color = re.search(r"#[0-9a-fA-F]{6}\b", instruction)
    if color:
        style["color"] = color.group(0)
        changed = True
    for key, pattern in [("font_size", r"(?:font(?: size)?|字号)\s*[:=]?\s*(\d+(?:\.\d+)?)"), ("width", r"(?:width|宽度)\s*[:=]?\s*(\d+(?:\.\d+)?)"), ("height", r"(?:height|高度)\s*[:=]?\s*(\d+(?:\.\d+)?)")]:
        match = re.search(pattern, instruction, re.I)
        if match:
            style[key] = float(match.group(1))
            changed = True
    if not changed:
        raise ValueError("This instruction needs a connected model, or use explicit color #RRGGBB, font size, width, or height")
    return style
