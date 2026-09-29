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
    if kind == 'bar' and 'height' not in style and 'method' in frame:
        count = len(style.get('methods', list(frame.method.unique())))
        legend_columns = min(3, max(1, int(width/2)))
        height += max(0, math.ceil(count/legend_columns)-1) * font/72 * 1.35
    report = {'kind': kind, 'input_rows': len(frame), 'metric': metric, 'unit': style.get('unit'),
              'width_in': width, 'height_in': height, 'minimum_font_pt': font, 'vector_formats': ['pdf', 'svg'],
              'transformation': 'none', 'uncertainty': {'type': 'none'}, 'warnings': []}
    if not style.get('unit') and not style.get('ylabel') and kind != 'calibration':
        report['warnings'].append('Metric units were not supplied; the renderer does not invent units.')
    plt.rcParams.update({"font.size": font, "axes.labelsize": font, 'xtick.labelsize': font, 'ytick.labelsize': font,
                         "axes.spines.top": False, "axes.spines.right": False, "svg.fonttype": "none", "pdf.fonttype": 42, "axes.titleweight": "normal"})
    fig, ax = plt.subplots(figsize=(width, height), layout="constrained")
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
        if not isinstance(methods, list) or not methods or any(method not in set(frame.method) for method in methods):
            plt.close(fig); raise ValueError('Select at least one actually observed method')
        datasets = list(frame.dataset.unique())
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
                   label=style.get('labels', {}).get(method, LABELS.get(method, method)), color=style.get("color", COLORS[i % len(COLORS)]),
                   hatch=HATCHES[i % len(HATCHES)], capsize=2, edgecolor='#333333', linewidth=.35)
        ax.set_xticks(x, [textwrap.fill(style.get('dataset_labels', {}).get(s, str(s).replace('_', ' ')), max(10, int(width*5/len(datasets)))) for s in datasets])
        ax.legend(frameon=False, ncol=min(3, max(1, int(width/2))), fontsize=font, loc='upper center', bbox_to_anchor=(.5, -.3 if error else -.2))
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
            ax.plot(rows[xkey], rows[metric], marker=MARKERS[i % len(MARKERS)], label=LABELS.get(label, label), color=style.get("color", COLORS[i % len(COLORS)]))
        ax.legend(frameon=False, fontsize=font)
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
        if not isinstance(selected, list) or not selected or any(method not in set(frame.method) for method in selected):
            plt.close(fig); raise ValueError('Select at least one actually observed calibration method')
        for i, (method, rows) in enumerate(frame.groupby("method")):
            if method not in selected:
                continue
            bins = np.minimum((rows.probability.to_numpy() * 10).astype(int), 9)
            grouped = rows.assign(bin=bins).groupby("bin").agg(probability=("probability", "mean"), frequency=("y_true", "mean"), n=("y_true", "size"))
            ax.plot(grouped.probability, grouped.frequency, marker=MARKERS[i % len(MARKERS)], label=LABELS.get(method, method), color=COLORS[i % len(COLORS)], markersize=4)
        report['transformation'] = {'binning': '10 equal-width probability bins', 'empty_bins': 'omitted', 'aggregation': 'mean probability and binary frequency within each bin'}
        ax.set_xlabel(style.get("xlabel", "Mean predicted probability"))
        ax.set_ylabel(style.get("ylabel", "Observed event frequency"))
        ax.legend(frameon=False, fontsize=font)
    else:
        plt.close(fig)
        raise ValueError("Supported figure kinds: bar, line, calibration, method")
    if style.get('title'):
        ax.set_title(textwrap.fill(style['title'], max(18, int((width-.55)*72/((font+1)*.52)))), loc='left', pad=10, fontsize=font+1)
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
    report['selected_methods'] = methods if kind == 'bar' else selected if kind == 'calibration' else list(frame.method.unique()) if 'method' in frame else ['observed']
    report['omitted_methods'] = [method for method in frame.method.unique() if method not in report['selected_methods']] if 'method' in frame else []
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
