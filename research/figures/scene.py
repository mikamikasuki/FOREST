"""Editable scientific scenes with physical typography and observed render checks.

The scene language is data, never executable drawing code.  Scientific topology
is validated before rendering; native labels, vectors and connections remain
separate elements in the SVG master.  Illustrative image assets occupy only their
declared object bounds.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import heapq
import io
import json
import math
from pathlib import Path
import re
from xml.etree import ElementTree

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import colors
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Circle, Ellipse, FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle
import numpy as np
from PIL import Image

_PALETTE = ("#29669D", "#8C60A7", "#258777", "#C67C34", "#BF5776", "#53758D")
_INK = "#253647"
_LIGHT = "#E7EDF3"
_PORTS = {"left": (-1, 0), "west": (-1, 0), "right": (1, 0), "east": (1, 0),
          "top": (0, 1), "north": (0, 1), "bottom": (0, -1), "south": (0, -1)}


def _color(value, default):
    value = value if isinstance(value, str) and colors.is_color_like(value) else default
    return colors.to_hex(value)


def _tint(value, amount=.88):
    amount = min(1., max(0., amount))
    rgb = colors.to_rgb(value)
    return tuple(channel * (1 - amount) + amount for channel in rgb)


def _wrap_chunks(text):
    """Preserve notation while breaking prose at existing word separators."""
    chunks = []
    for word_index, word in enumerate(str(text).split()):
        breaks = r"(?<=[→⇒↔⇄])|(?<=[a-z][-–])(?=[A-Za-z])|(?<=[A-Z][-–])(?=[a-z])|(?<=[a-z]/)(?=[a-z])"
        for part_index, part in enumerate(re.split(breaks, word)):
            if part:
                chunks.append((part, " " if word_index and part_index == 0 else ""))
    return chunks


def _box(bbox, width, height):
    x, y, w, h = (float(value) for value in bbox)
    return x * width, (1 - y - h) * height, w * width, h * height


def _intersects(a, b, pad=0):
    return (a[0] < b[0] + b[2] + pad and a[0] + a[2] + pad > b[0]
            and a[1] < b[1] + b[3] + pad and a[1] + a[3] + pad > b[1])


def _inside(a, b, tolerance=.015):
    return (a[0] >= b[0] - tolerance and a[1] >= b[1] - tolerance
            and a[0] + a[2] <= b[0] + b[2] + tolerance
            and a[1] + a[3] <= b[1] + b[3] + tolerance)


def _segment_clear(a, b, obstacles):
    """Check the open segment against rectangle interiors; boundaries are legal."""
    epsilon = 1e-8
    if abs(a[0] - b[0]) < epsilon:
        lo, hi = sorted((a[1], b[1]))
        return not any(x + epsilon < a[0] < x + w - epsilon
                       and max(lo, y + epsilon) < min(hi, y + h - epsilon)
                       for x, y, w, h in obstacles)
    if abs(a[1] - b[1]) < epsilon:
        lo, hi = sorted((a[0], b[0]))
        return not any(y + epsilon < a[1] < y + h - epsilon
                       and max(lo, x + epsilon) < min(hi, x + w - epsilon)
                       for x, y, w, h in obstacles)
    return False


def _wire_penalty(a, b, wires):
    """Reject ambiguous shared spans and price distinct wire crossings."""
    horizontal = abs(a[1] - b[1]) < 1e-7
    crossings = 0
    for path in wires:
        for old_a, old_b in zip(path[:-1], path[1:]):
            old_horizontal = abs(old_a[1] - old_b[1]) < 1e-7
            if horizontal == old_horizontal:
                fixed_axis, varying_axis = (1, 0) if horizontal else (0, 1)
                if abs(a[fixed_axis] - old_a[fixed_axis]) < 2e-5:
                    overlap = (min(max(a[varying_axis], b[varying_axis]), max(old_a[varying_axis], old_b[varying_axis]))
                               - max(min(a[varying_axis], b[varying_axis]), min(old_a[varying_axis], old_b[varying_axis])))
                    if overlap > 2e-5:
                        return None
            else:
                hx0, hx1 = sorted((a[0], b[0])) if horizontal else sorted((old_a[0], old_b[0]))
                hy = a[1] if horizontal else old_a[1]
                vx = old_a[0] if horizontal else a[0]
                vy0, vy1 = sorted((old_a[1], old_b[1])) if horizontal else sorted((a[1], b[1]))
                current_inside = (hx0 - 2e-5 <= vx <= hx1 + 2e-5) if horizontal else (vy0 - 2e-5 <= hy <= vy1 + 2e-5)
                previous_inside = (vy0 + 2e-5 < hy < vy1 - 2e-5) if horizontal else (hx0 + 2e-5 < vx < hx1 - 2e-5)
                if current_inside and previous_inside:
                    crossings += 1
    return crossings * .12


def _route(start, finish, obstacles, width, height, previous_wires=None, lane_sep=.065, bounds=None):
    """Visibility-grid A* with physical bends, parallel lanes and wire costs."""
    wires = previous_wires or []
    xmin, ymin, xmax, ymax = .035, .035, width - .035, height - .035
    if bounds is not None:
        x, y, w, h = bounds
        xmin, ymin = max(xmin, x + .015), max(ymin, y + .015)
        xmax, ymax = min(xmax, x + w - .015), min(ymax, y + h - .015)
        if (xmin >= xmax or ymin >= ymax
                or any(not xmin <= point[0] <= xmax or not ymin <= point[1] <= ymax
                       for point in (start, finish))):
            return None
    clamp_x = lambda value: min(xmax, max(xmin, value))
    clamp_y = lambda value: min(ymax, max(ymin, value))
    xs = {start[0], finish[0], xmin, xmax}
    ys = {start[1], finish[1], ymin, ymax}
    for x, y, w, h in obstacles:
        xs.update((clamp_x(x), clamp_x(x + w)))
        ys.update((clamp_y(y), clamp_y(y + h)))
    for path in wires:
        for a, b in zip(path[:-1], path[1:]):
            xs.update((clamp_x(a[0]), clamp_x(b[0])))
            ys.update((clamp_y(a[1]), clamp_y(b[1])))
            if abs(a[1] - b[1]) < 1e-7:
                ys.update(clamp_y(a[1] + direction * lane_sep) for direction in (-1, 1))
            else:
                xs.update(clamp_x(a[0] + direction * lane_sep) for direction in (-1, 1))
    xs = sorted(xs); ys = sorted(ys)
    begin = (xs.index(start[0]), ys.index(start[1]), -1)
    target = (xs.index(finish[0]), ys.index(finish[1]))
    queue = [(0., 0., begin)]
    best = {begin: 0.}; previous = {}
    while queue:
        _, distance, current = heapq.heappop(queue)
        if distance > best.get(current, math.inf) + 1e-9:
            continue
        ix, iy, axis = current
        if (ix, iy) == target:
            path = [(xs[ix], ys[iy])]
            while current in previous:
                current = previous[current]
                path.append((xs[current[0]], ys[current[1]]))
            path.reverse()
            simplified = []
            for point in path:
                if len(simplified) > 1 and ((abs(simplified[-2][0] - simplified[-1][0]) < 1e-8
                                            and abs(simplified[-1][0] - point[0]) < 1e-8)
                                           or (abs(simplified[-2][1] - simplified[-1][1]) < 1e-8
                                               and abs(simplified[-1][1] - point[1]) < 1e-8)):
                    simplified[-1] = point
                else:
                    simplified.append(point)
            return simplified
        for dx, dy, direction in ((-1, 0, 0), (1, 0, 0), (0, -1, 1), (0, 1, 1)):
            nx, ny = ix + dx, iy + dy
            if not (0 <= nx < len(xs) and 0 <= ny < len(ys)):
                continue
            if not _segment_clear((xs[ix], ys[iy]), (xs[nx], ys[ny]), obstacles):
                continue
            wire_cost = _wire_penalty((xs[ix], ys[iy]), (xs[nx], ys[ny]), wires)
            if wire_cost is None:
                continue
            neighbor = (nx, ny, direction)
            cost = distance + abs(xs[ix] - xs[nx]) + abs(ys[iy] - ys[ny]) + wire_cost
            if axis != -1 and direction != axis:
                cost += .07
            if cost + 1e-9 < best.get(neighbor, math.inf):
                best[neighbor] = cost; previous[neighbor] = current
                heuristic = abs(xs[nx] - finish[0]) + abs(ys[ny] - finish[1])
                heapq.heappush(queue, (cost + heuristic, cost, neighbor))
    return None


def _port(box, direction, offset_in=0):
    x, y, w, h = box
    dx, dy = _PORTS[direction]
    return (x + w * (1 + dx) / 2 + (offset_in if dy else 0),
            y + h * (1 + dy) / 2 + (offset_in if dx else 0)), (dx, dy)


class _Canvas:
    def __init__(self, scene, style):
        self.width = float(scene["width_in"]); self.height = float(scene["height_in"])
        self.font = max(8., float(style.get("font_pt", style.get("font_size", 9))))
        self.minimum = max(8., float(style.get("minimum_font_pt", 8)))
        self.font = max(self.minimum, self.font)
        proposed_palette = scene.get("palette", list(_PALETTE))
        self.palette = tuple(_color(value, _PALETTE[index % len(_PALETTE)])
                             for index, value in enumerate(proposed_palette)) or _PALETTE
        self.fig = plt.figure(figsize=(self.width, self.height), dpi=120)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set(xlim=(0, self.width), ylim=(0, self.height), aspect="equal")
        self.ax.axis("off")
        self.text_records = []; self.shape_count = 0; self.ids = set(); self.issues = []
        self.renderer = self.fig.canvas.get_renderer()

    def identifier(self, owner, role):
        raw = re.sub(r"[^A-Za-z0-9_.-]", "_", str(owner) + "-" + role)
        candidate = raw; number = 1
        while candidate in self.ids:
            number += 1; candidate = raw + "-" + str(number)
        self.ids.add(candidate)
        return candidate

    def patch(self, patch, owner, role="shape", zorder=3):
        patch.set_gid(self.identifier(owner, role)); patch.set_zorder(zorder)
        self.ax.add_patch(patch); self.shape_count += 1
        return patch

    def line(self, points, owner, color=_INK, lw=.9, role="line", zorder=4, **kwargs):
        item, = self.ax.plot(*zip(*points), color=color, linewidth=lw, zorder=zorder, **kwargs)
        item.set_gid(self.identifier(owner, role)); self.shape_count += 1
        return item

    def wrap(self, text, width, font, weight="normal"):
        prop = FontProperties(family="DejaVu Sans", size=font, weight=weight)
        if "$" in text or "\n" in text:
            return text
        lines = []; line = ""
        for word, separator in _wrap_chunks(text):
            proposed = line + (separator if line else "") + word
            pixels = self.renderer.get_text_width_height_descent(proposed, prop, False)[0]
            if pixels / self.fig.dpi > width and line:
                lines.append(line); line = word
            else:
                line = proposed
        if line:
            lines.append(line)
        return "\n".join(lines)

    def text(self, text, box, owner, role="label", font=None, weight="normal", color=_INK,
             align="center", valign="center", background=False, wrap=True):
        text = str(text)
        if not text.strip():
            return None
        x, y, w, h = box
        requested = max(self.minimum, float(font or self.font))
        chosen = requested; rendered = text
        while chosen >= self.minimum - .001:
            rendered = self.wrap(text, w, chosen, weight) if wrap else text
            line_count = len(rendered.splitlines())
            if line_count * chosen / 72 * 1.25 <= h + .015:
                break
            chosen -= .25
        chosen = max(self.minimum, chosen)
        px = x if align == "left" else x + w if align == "right" else x + w / 2
        py = y + h if valign == "top" else y if valign == "bottom" else y + h / 2
        item = self.ax.text(px, py, rendered, fontsize=chosen, family="DejaVu Sans", weight=weight,
                            ha=align, va=valign, color=color, linespacing=1.2, zorder=8,
                            bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.5} if background else None)
        item.set_gid(self.identifier(owner, role))
        self.text_records.append({"artist": item, "id": owner, "role": role,
                                  "box": box, "font_pt": chosen, "source_text": text})
        return item


def _label_region(c, box, label, owner):
    x, y, w, h = box
    if not str(label).strip():
        return box, None
    wrapped = c.wrap(str(label), w, c.font)
    line_count = len(wrapped.splitlines()) or 1
    prop = FontProperties(family="DejaVu Sans", size=c.font)
    observed_line_height = max(c.renderer.get_text_width_height_descent(line or "lp", prop, "$" in line)[1]
                               for line in wrapped.splitlines() or ["lp"]) / c.fig.dpi
    label_height = max(line_count * c.font / 72 * 1.25,
                       line_count * observed_line_height * 1.2) + .025
    available = h - label_height - .05
    if available < .12 or w <= .05:
        c.issues.append({"id": owner, "code": "insufficient_glyph_space", "severity": "error",
                         "detail": "The exact wrapped operation label leaves insufficient room for its scientific depiction",
                         "required_label_height_in": label_height, "object_height_in": h,
                         "repair": "Increase the object bounds or reserve another panel; keep the exact label and minimum font"})
    return (x + .025, y + label_height + .025, max(.015, w - .05), max(.04, available)), (x, y, w, label_height)


def _tensor(c, item, body, color):
    x, y, w, h = body; params = item.get("params", {}); owner = item["id"]
    shape = params.get("shape", [4, 4, 3])
    axis_labels = params.get("axis_labels", [str(dimension) for dimension in shape])
    if axis_labels:
        label_h = c.minimum / 72 * 1.3
        c.text(" × ".join(str(value) for value in axis_labels), (x, y + h - label_h, w, label_h),
               owner, "tensor-dimensions", font=c.minimum)
        h = max(.04, h - label_h - .025)
    layers = min(5, max(1, int(shape[-1]) if isinstance(shape, list) and shape else 3))
    rows = min(8, max(1, int(shape[0]) if isinstance(shape, list) and shape else 4))
    cols = min(8, max(1, int(shape[1]) if isinstance(shape, list) and len(shape) > 1 else 4))
    face_w = w * .74; face_h = h * .73; offset_x = w * .055; offset_y = h * .052
    bx = x + (w - face_w - (layers - 1) * offset_x) / 2
    by = y + (h - face_h - (layers - 1) * offset_y) / 2
    for layer in reversed(range(layers)):
        lx = bx + layer * offset_x; ly = by + layer * offset_y
        c.patch(Rectangle((lx, ly), face_w, face_h, facecolor=_tint(color, .78 - .055 * layer),
                          edgecolor=color, linewidth=.8), owner, "tensor-layer")
        for index in range(1, cols):
            c.line([(lx + face_w * index / cols, ly), (lx + face_w * index / cols, ly + face_h)],
                   owner, color=color, lw=.35, role="tensor-cell")
        for index in range(1, rows):
            c.line([(lx, ly + face_h * index / rows), (lx + face_w, ly + face_h * index / rows)],
                   owner, color=color, lw=.35, role="tensor-cell")


def _matrix(c, item, body, color):
    x, y, w, h = body; params = item.get("params", {}); owner = item["id"]
    values = params.get("values")
    symbolic = values is not None and any(isinstance(value, str) for row in values for value in row)
    if values is not None:
        array = np.asarray(values, dtype=object if symbolic else float)
        if array.ndim != 2 or (not symbolic and not np.isfinite(array).all()) or max(array.shape) > 64:
            raise ValueError("Matrix cells require a finite 2D array with at most 64 rows and columns")
        rows, cols = array.shape
    else:
        rows = max(1, min(64, int(params.get("rows", 6)))); cols = max(1, min(64, int(params.get("cols", 6))))
        array = np.zeros((rows, cols))
    row_labels = params.get("row_labels", []); col_labels = params.get("col_labels", [])
    if row_labels:
        if len(row_labels) != rows: raise ValueError("Matrix row labels must match every row")
        label_w = max(c.renderer.get_text_width_height_descent(str(label), FontProperties(size=c.minimum), False)[0]
                      for label in row_labels) / c.fig.dpi + .035
        x += label_w; w -= label_w
    else: label_w = 0
    if col_labels:
        if len(col_labels) != cols: raise ValueError("Matrix column labels must match every column")
        label_h = c.minimum / 72 * 1.4
        h -= label_h
    else: label_h = 0
    if w <= .04 or h <= .04:
        raise ValueError("Matrix label bounds leave no space for its cells")
    size = min(w / cols, h / rows); mw = size * cols; mh = size * rows
    bx = x + (w - mw) / 2; by = y + (h - mh) / 2
    mask = params.get("mask"); mask_matrix = params.get("mask_matrix"); highlight = params.get("highlight", [])
    if mask_matrix is not None and (not isinstance(mask_matrix, list) or len(mask_matrix) != rows
            or any(not isinstance(row, list) or len(row) != cols or any(not isinstance(value, bool) for value in row)
                   for row in mask_matrix)):
        raise ValueError("Attention block mask must match its boolean rows and columns")
    if len(highlight) == 2 and all(isinstance(value, (int, float)) for value in highlight):
        highlight = [highlight]
    selected = {tuple(pair) for pair in highlight if isinstance(pair, (list, tuple)) and len(pair) == 2}
    if values is not None and not symbolic:
        lo, hi = float(array.min()), float(array.max())
        normal = (array - lo) / (hi - lo) if hi > lo else np.ones_like(array) * .5
    for row in range(rows):
        for col in range(cols):
            active = (bool(mask_matrix[row][col]) if mask_matrix is not None else
                      (row, col) in selected or mask == "diagonal" and row == col or mask == "causal" and row >= col)
            if symbolic:
                amount = .96 if str(array[row, col]).strip().lower() in ("−∞", "-∞", "-inf", "masked") else .70
            else:
                amount = .96 - .72 * float(normal[row, col]) if values is not None else .40 if active else .94
            c.patch(Rectangle((bx + col * size, by + (rows - row - 1) * size), size, size,
                              facecolor=_tint(color, amount), edgecolor="white", linewidth=.5), owner, "matrix-cell")
            cell_text = str(array[row, col]) if symbolic else (
                str(params["cell_labels"][row][col]) if params.get("cell_labels") else None)
            if cell_text is not None:
                c.text(cell_text, (bx + col * size, by + (rows - row - 1) * size, size, size),
                       owner, "matrix-cell-label", font=c.minimum)
    c.patch(Rectangle((bx, by), mw, mh, fill=False, edgecolor=color, linewidth=.8), owner, "matrix-border")
    for index, label in enumerate(row_labels):
        c.text(label, (bx - label_w, by + (rows - index - 1) * size, label_w - .025, size),
               owner, "matrix-row-label", font=c.minimum, align="right")
    for index, label in enumerate(col_labels):
        c.text(label, (bx + index * size, by + mh + .015, size, label_h), owner,
               "matrix-column-label", font=c.minimum)


def _tokens(c, item, body, color):
    x, y, w, h = body; params = item.get("params", {}); owner = item["id"]
    items = params.get("items", [])
    if not isinstance(items, list) or not items:
        items = ["x₁", "x₂", "…", "xₙ"]
    if len(items) > 16:
        raise ValueError("Token strips support at most 16 explicitly labelled items")
    columns = min(len(items), max(1, int(w / .26))); rows = math.ceil(len(items) / columns)
    gap = .035; cw = (w - (columns - 1) * gap) / columns; ch = min(.28, (h - (rows - 1) * gap) / rows)
    top = y + (h + rows * ch + (rows - 1) * gap) / 2
    active = params.get("active", [])
    for index, text in enumerate(items):
        col = index % columns; row = index // columns
        box = (x + col * (cw + gap), top - (row + 1) * ch - row * gap, cw, ch)
        c.patch(FancyBboxPatch(box[:2], cw, ch, boxstyle="round,pad=0,rounding_size=.035",
                              facecolor=_tint(color, .45 if index in active else .9), edgecolor=color,
                              linewidth=.65), owner, "token")
        c.text(text, (box[0] + .01, box[1], cw - .02, ch), owner, "token-text", font=c.minimum)


def _document(c, item, body, color):
    x, y, w, h = body; owner = item["id"]; params = item.get("params", {})
    pw = min(w * .73, h * .8); ph = min(h * .92, pw * 1.3); bx = x + (w - pw) / 2; by = y + (h - ph) / 2
    fold = min(pw * .22, .18)
    c.patch(Polygon([(bx, by), (bx + pw, by), (bx + pw, by + ph - fold),
                     (bx + pw - fold, by + ph), (bx, by + ph)], closed=True,
                    facecolor="white", edgecolor=color, linewidth=1), owner, "document")
    c.patch(Polygon([(bx + pw - fold, by + ph), (bx + pw - fold, by + ph - fold),
                     (bx + pw, by + ph - fold)], facecolor=_tint(color, .75), edgecolor=color,
                    linewidth=.7), owner, "fold")
    line_count = min(32, max(1, int(params.get("lines", 6))))
    for index in range(line_count):
        ly = by + ph * (.78 - .57 * index / max(1, line_count - 1))
        c.line([(bx + pw * .14, ly), (bx + pw * (.7 if index % 3 == 2 else .86), ly)],
               owner, color=_tint(color, .45), lw=1.3 if index == 0 else .65, role="document-line")


def _memory(c, item, body, color):
    x, y, w, h = body; owner = item["id"]; params = item.get("params", {})
    items = params.get("items", [])
    slots = min(32, max(1, int(params.get("slots", len(items) or 4))))
    gap = .035; ch = min(.26, (h - (slots - 1) * gap) / slots)
    bh = slots * ch + (slots - 1) * gap; by = y + (h - bh) / 2; cw = w * .85; bx = x + (w - cw) / 2
    for index in range(slots):
        ly = by + (slots - index - 1) * (ch + gap)
        c.patch(FancyBboxPatch((bx, ly), cw, ch, boxstyle="round,pad=0,rounding_size=.025",
                              facecolor=_tint(color, .78 + .025 * index), edgecolor=color,
                              linewidth=.7), owner, "memory-slot")
        c.patch(Rectangle((bx, ly), cw * .10, ch, facecolor=_tint(color, .2), edgecolor="none"), owner, "memory-key")
        if index < len(items):
            c.text(items[index], (bx + cw * .15, ly, cw * .8, ch), owner, "memory-text", font=c.minimum)
        else:
            for part in range(3):
                c.patch(Rectangle((bx + cw * (.20 + part * .21), ly + ch * .33), cw * .13, ch * .33,
                                  facecolor=_tint(color, .55), edgecolor="none"), owner, "memory-value")


def _graph(c, item, body, color):
    x, y, w, h = body; owner = item["id"]; params = item.get("params", {})
    nodes = params.get("nodes", [])
    if not isinstance(nodes, list) or not nodes:
        # A motif represents connectivity, not an invented scientific measurement.
        nodes = [{"id": str(index), "x": px, "y": py} for index, (px, py) in
                 enumerate(((.12, .5), (.38, .15), (.38, .82), (.67, .28), (.67, .72), (.90, .5)))]
        edges = [{"source": str(a), "target": str(b)} for a, b in ((0, 1), (0, 2), (1, 3), (2, 4), (3, 5), (4, 5), (1, 4))]
    else:
        edges = params.get("edges", [])
    if len(nodes) > 40 or len(edges) > 200:
        raise ValueError("Graph motifs support at most 40 nodes and 200 edges")
    positions = {}
    for index, node in enumerate(nodes):
        angle = 2 * math.pi * index / max(1, len(nodes))
        px = float(node.get("x", .5 + .35 * math.cos(angle)))
        py = float(node.get("y", .5 + .35 * math.sin(angle)))
        if not 0 <= px <= 1 or not 0 <= py <= 1:
            raise ValueError("Graph node positions must use local normalized coordinates")
        positions[str(node["id"])] = (x + w * px, y + h * (1 - py))
    radius = min(.065, w / 9, h / 6)
    for edge in edges:
        a = positions[str(edge["source"])]; b = positions[str(edge["target"])]
        c.line([a, b], owner, color=_tint(color, .38), lw=.85, role="graph-edge")
    for node in nodes:
        px, py = positions[str(node["id"])]
        c.patch(Circle((px, py), radius, facecolor=_tint(color, .4), edgecolor=color, linewidth=.8), owner, "graph-node")
        if node.get("label"):
            c.text(node["label"], (px - .14, py + radius + .008, .28, .17), owner, "graph-text", font=c.minimum)


def _operator(c, item, body, color):
    x, y, w, h = body; owner = item["id"]; params = item.get("params", {}); shape = params.get("shape", "capsule")
    if shape == "circle":
        radius = min(w, h) * .46
        c.patch(Circle((x + w / 2, y + h / 2), radius, facecolor=_tint(color, .92), edgecolor=color,
                       linewidth=1), owner, "operator")
    elif shape == "diamond":
        c.patch(Polygon([(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2)],
                        facecolor=_tint(color, .92), edgecolor=color, linewidth=1), owner, "operator")
    else:
        c.patch(FancyBboxPatch((x + .015, y + .015), w - .03, h - .03,
                              boxstyle="round,pad=0,rounding_size=.07", facecolor=_tint(color, .93),
                              edgecolor=color, linewidth=1), owner, "operator")
    formula = params.get("formula", params.get("text", ""))
    if formula:
        c.text(formula, (x + w * .1, y + h * .12, w * .8, h * .76), owner, "formula",
               font=c.font if params.get("_overview_label") else max(c.font, 10), wrap=True)


def _geometry(c, item, body, color):
    x, y, w, h = body; owner = item["id"]; params = item.get("params", {}); kind = params.get("type", "manifold")
    if kind == "camera":
        c.patch(Polygon([(x + w * .10, y + h * .30), (x + w * .34, y + h * .30),
                         (x + w * .34, y + h * .65), (x + w * .10, y + h * .65)],
                        facecolor=_tint(color, .80), edgecolor=color, linewidth=.9), owner, "camera")
        c.patch(Polygon([(x + w * .34, y + h * .34), (x + w * .48, y + h * .20),
                         (x + w * .48, y + h * .75), (x + w * .34, y + h * .61)],
                        facecolor=_tint(color, .6), edgecolor=color, linewidth=.9), owner, "lens")
        for fraction in (.1, .85):
            c.line([(x + w * .48, y + h * .48), (x + w * .95, y + h * fraction)], owner, color=color,
                   lw=.8, linestyle="--", role="frustum")
    elif kind == "points":
        points = params.get("points", [])
        if not points:
            raise ValueError("Point geometry needs explicitly supplied conceptual coordinates")
        if len(points) > 400:
            raise ValueError("Geometry supports at most 400 points")
        for point in points:
            if len(point) != 2 or not all(0 <= float(value) <= 1 for value in point):
                raise ValueError("Geometry coordinates must lie in the normalized object")
            c.patch(Circle((x + w * float(point[0]), y + h * (1 - float(point[1]))),
                           min(.025, w / 50), facecolor=color, edgecolor="none"), owner, "point")
    else:
        # A regular parametric surface is explicitly illustrative, with no score axis.
        for axis in (0, 1):
            for line in np.linspace(.12, .88, 7):
                points = []
                for along in np.linspace(.08, .92, 30):
                    u, v = (along, line) if axis == 0 else (line, along)
                    px = .10 + .70 * u + .13 * v
                    py = .20 + .42 * v + (.13 * math.sin(2.5 * math.pi * u) * math.sin(math.pi * v)
                                           if kind == "manifold" else .1 * u)
                    points.append((x + w * px, y + h * py))
                c.line(points, owner, color=_tint(color, .28 if axis == 0 else .1), lw=.6, role="surface-grid")


def _custom(c, item, body, color):
    x, y, w, h = body; owner = item["id"]; primitives = item.get("params", {}).get("primitives", [])
    if not isinstance(primitives, list) or not primitives or len(primitives) > 400:
        raise ValueError("Custom scientific motifs require 1–400 safe geometry primitives")
    def local_point(point):
        if not isinstance(point, (list, tuple)) or len(point) != 2 or not all(math.isfinite(float(v)) and 0 <= float(v) <= 1 for v in point):
            raise ValueError("Primitive points must be normalized coordinates inside their object")
        return x + w * float(point[0]), y + h * (1 - float(point[1]))
    for primitive in primitives:
        kind = primitive.get("kind"); fill = primitive.get("fill", _tint(color, .84))
        stroke = "none" if primitive.get("stroke") == "none" else _color(primitive.get("stroke"), color)
        if fill == "none": fill = "none"
        elif isinstance(fill, str): fill = _color(fill, _tint(color, .84))
        lw = min(3., max(.35, float(primitive.get("stroke_pt", .75))))
        if kind in ("rect", "ellipse"):
            b = primitive.get("bbox", [])
            if len(b) != 4 or not all(math.isfinite(float(v)) for v in b) or min(b) < 0 or b[0] + b[2] > 1 or b[1] + b[3] > 1:
                raise ValueError("Primitive boxes must remain within the normalized object")
            bx, by = local_point((b[0], b[1] + b[3])); bw = b[2] * w; bh = b[3] * h
            patch = Rectangle((bx, by), bw, bh, facecolor=fill, edgecolor=stroke, linewidth=lw) if kind == "rect" else Ellipse((bx + bw / 2, by + bh / 2), bw, bh, facecolor=fill, edgecolor=stroke, linewidth=lw)
            c.patch(patch, owner, "custom-" + kind)
        elif kind == "circle":
            center = primitive.get("center", [.5, .5]); radius = float(primitive.get("radius", .1))
            if not math.isfinite(radius) or radius <= 0 or min(center) - radius < 0 or max(center) + radius > 1:
                raise ValueError("Primitive circles must remain inside the normalized object")
            c.patch(Ellipse(local_point(center), 2 * radius * w, 2 * radius * h, facecolor=fill,
                            edgecolor=stroke, linewidth=lw), owner, "custom-circle")
        elif kind in ("line", "arrow", "polygon"):
            points = [local_point(point) for point in primitive.get("points", [])]
            if not 2 <= len(points) <= 400: raise ValueError("Primitive paths require 2–400 points")
            if kind == "polygon":
                c.patch(Polygon(points, closed=True, facecolor=fill, edgecolor=stroke, linewidth=lw), owner, "custom-polygon")
            else:
                c.line(points, owner, color=stroke, lw=lw, role="custom-path")
                if kind == "arrow":
                    c.patch(FancyArrowPatch(points[-2], points[-1], arrowstyle="-|>", mutation_scale=7,
                                            color=stroke, linewidth=lw, shrinkA=0, shrinkB=0), owner, "custom-arrow")
        elif kind == "text":
            font = max(c.minimum, float(primitive.get("font_pt", c.minimum)))
            align = primitive.get("align", "center"); valign = primitive.get("valign", "center")
            if align not in ("left", "center", "right") or valign not in ("top", "center", "bottom"):
                raise ValueError("Custom scientific text requires supported native alignment")
            if "bbox" in primitive:
                b = primitive["bbox"]
                if (not isinstance(b, list) or len(b) != 4 or not all(math.isfinite(float(v)) for v in b)
                        or b[0] < 0 or b[1] < 0 or b[2] <= 0 or b[3] <= 0
                        or b[0] + b[2] > 1 or b[1] + b[3] > 1):
                    raise ValueError("Custom text bounds must be a positive normalized box inside its object")
                px, py = local_point((b[0], b[1] + b[3])); box = (px, py, b[2] * w, b[3] * h)
            else:
                px, py = local_point(primitive.get("position", [.5, .5]))
                tw = float(primitive.get("width", .7)) * w
                wrapped = c.wrap(str(primitive.get("text", "")), tw, font)
                th = max(1, len(wrapped.splitlines())) * font / 72 * 1.3 + .015
                bx = px if align == "left" else px - tw if align == "right" else px - tw / 2
                by = py - th if valign == "top" else py if valign == "bottom" else py - th / 2
                box = (bx, by, tw, th)
            text_color = _color(primitive.get("color"), _INK)
            c.text(primitive.get("text", ""), box, owner, "custom-text", font=font,
                   color=text_color, align=align, valign=valign)
        else:
            raise ValueError("Unsupported safe primitive kind: " + str(kind))


def _asset(c, item, body, assets):
    asset_id = item.get("params", {}).get("asset_id", item.get("asset_id", item["id"]))
    record = assets.get(asset_id)
    encoded = record.get("base64", record.get("data")) if isinstance(record, dict) else record
    if not isinstance(encoded, str) or len(encoded) > 28_000_000:
        raise ValueError("Asset object requires a bounded embedded PNG payload: " + str(asset_id))
    if encoded.startswith("data:"):
        if not encoded.startswith("data:image/png;base64,"): raise ValueError("Only embedded PNG assets are accepted")
        encoded = encoded.split(",", 1)[1]
    try:
        decoded = base64.b64decode(encoded, validate=True)
        picture = Image.open(io.BytesIO(decoded))
        if picture.format != "PNG" or picture.width * picture.height > 16_000_000:
            raise ValueError("Asset must be PNG with at most 16 million pixels")
        picture.load(); picture = picture.convert("RGBA")
    except Exception as error:
        raise ValueError("Invalid PNG asset: " + str(asset_id)) from error
    x, y, w, h = body; aspect = picture.width / picture.height
    dw = min(w, h * aspect); dh = dw / aspect
    artist = c.ax.imshow(np.asarray(picture), extent=(x + (w - dw) / 2, x + (w + dw) / 2,
                                                    y + (h - dh) / 2, y + (h + dh) / 2),
                         interpolation="antialiased", zorder=3, aspect="auto")
    artist.set_gid(c.identifier(item["id"], "illustrative-asset"))
    return {"id": asset_id, "width_px": picture.width, "height_px": picture.height,
            "print_width_in": dw, "raster_pixels_per_inch": picture.width / dw,
            "role": "conceptual_illustration", "editable": "embedded raster asset; labels and framework remain native"}


def _text_width(c, text, font=None):
    prop = FontProperties(size=font or c.font, family="DejaVu Sans")
    return max((c.renderer.get_text_width_height_descent(line, prop, "$" in line)[0]
                for line in str(text).splitlines()), default=0.) / c.fig.dpi


def _measure_edge_label(c, text, width_in, font_pt):
    if not isinstance(text, str) or not math.isfinite(width_in) or width_in <= 0:
        raise ValueError("Dependency typography needs text and a positive physical width")
    if not math.isfinite(font_pt) or font_pt < 8:
        raise ValueError("Dependency typography requires a finite font of at least 8pt")
    if not text.strip():
        return {"min_width_in": 0., "width_in": 0., "height_in": 0.,
                "wrapped_text": "", "lines": 0, "font_pt": font_pt}
    units = [text] if "$" in text else [word for word, _ in _wrap_chunks(text)]
    wrapped = c.wrap(text, max(.015, width_in - .035), font_pt)
    lines = wrapped.splitlines() or [""]
    return {"min_width_in": max((_text_width(c, unit, font_pt) for unit in units), default=0.) + .045,
            "width_in": _text_width(c, wrapped, font_pt) + .045,
            "height_in": len(lines) * font_pt / 72 * 1.35 + .025,
            "wrapped_text": wrapped, "lines": len(lines), "font_pt": font_pt}


def measure_edge_label(text, width_in, font_pt=9.):
    """Measure exact dependency text before reserving host-owned label lanes.

    Width and height include native text clearance in physical inches. Existing
    hyphens and prose slashes can carry a line break; math remains atomic.
    """
    c = _Canvas({"width_in": 2., "height_in": 1.}, {"minimum_font_pt": font_pt, "font_pt": font_pt})
    try:
        return _measure_edge_label(c, text, float(width_in), float(font_pt))
    finally:
        plt.close(c.fig)


def _minimum_object_size(c, item, width=None):
    """Quantify native content space at the final print font, before packing."""
    label = item.get("display_label", item.get("label", "")); params = item.get("params", {})
    kind = item["kind"]; unit = c.minimum / 72 * 1.35
    word_width = max((_text_width(c, word) for word, _ in _wrap_chunks(label)), default=0.)
    minimum_width = max(.45, word_width + .055)
    body_height = .44
    formula = params.get("formula", "")
    inside = kind == "operator" and (not formula or formula in (label, item.get("label")))
    if kind == "tensor":
        dimensions = params.get("axis_labels", [str(value) for value in params.get("shape", [])])
        minimum_width = max(minimum_width, .65, _text_width(c, " × ".join(str(value) for value in dimensions), c.minimum) + .05)
        body_height = .45 + unit + .025
    elif kind == "matrix":
        rows = params.get("rows", 4); cols = params.get("cols", 4)
        cells = params.get("cell_labels", params.get("values", []))
        symbolic = cells and any(isinstance(value, str) for row in cells for value in row)
        cell_width = max((_text_width(c, value, c.minimum) + .035 for row in cells for value in row), default=.12) if symbolic else .09
        if symbolic or params.get("row_labels") or params.get("col_labels"):
            cell_width = max(cell_width, unit)
        minimum_width = max(minimum_width, cols * cell_width + .05,
                            cols * max((_text_width(c, value, c.minimum) + .025 for value in params.get("col_labels", [])), default=0) + .05)
        if params.get("row_labels"):
            minimum_width += max(_text_width(c, value, c.minimum) for value in params["row_labels"]) + .035
        body_height = max(.45, rows * (unit if symbolic or params.get("row_labels") else .09))
        if params.get("col_labels"): body_height += unit + .025
    elif kind == "tokens":
        items = params.get("items", [])
        chip = max(.26, max((_text_width(c, value, c.minimum) + .06 for value in items), default=.26))
        minimum_width = max(minimum_width, min(4, len(items)) * chip + max(0, min(4, len(items)) - 1) * .035 + .05)
        body_height = .28
    elif kind == "memory":
        minimum_width = max(minimum_width, max((_text_width(c, value, c.minimum) / .68 + .07
                                              for value in params.get("items", [])), default=.70))
        body_height = params.get("slots", 4) * max(.18, unit) + max(0, params.get("slots", 4) - 1) * .035
    elif kind == "operator":
        if inside:
            minimum_width = max(.45, (word_width + .05) / .8)
        elif formula:
            units = [formula] if "$" in formula else [part for part, _ in _wrap_chunks(formula)]
            minimum_width = max(minimum_width, max((_text_width(c, part, max(c.font, 10)) for part in units), default=0) / .8 + .05)
            body_height = max(c.font, 10) / 72 * 1.35 / .76 + .035
    elif kind == "custom":
        minimum_width = max(minimum_width, .65)
        for primitive in params.get("primitives", []):
            if primitive.get("kind") != "text": continue
            text = primitive.get("text", ""); font = max(c.minimum, float(primitive.get("font_pt", c.minimum)))
            fraction = primitive.get("bbox", [0, 0, primitive.get("width", .7), 1])[2]
            if "bbox" not in primitive:
                position_x = primitive.get("position", [.5, .5])[0]
                align = primitive.get("align", "center")
                capacity = 1 - position_x if align == "left" else position_x if align == "right" else 2 * min(position_x, 1 - position_x)
                fraction = min(fraction, capacity)
            units = [text] if "$" in text else [word for word, _ in _wrap_chunks(text)]
            longest = max((_text_width(c, word, font) for word in units), default=0)
            minimum_width = max(minimum_width, (longest + .035) / max(.01, float(fraction)) + .05)
    actual_width = max(.015, width if width is not None else minimum_width)
    body_width = max(.015, actual_width - .05)
    if kind == "operator" and not inside and formula:
        font = max(c.font, 10)
        wrapped = c.wrap(str(formula), body_width * .8, font)
        body_height = max(1, len(wrapped.splitlines())) * font / 72 * 1.35 / .76 + .035
    if kind == "tokens":
        columns = max(1, min(len(params.get("items", [])), int(body_width / .26)))
        rows = math.ceil(len(params.get("items", [])) / columns)
        body_height = max(.18, unit) * rows + .035 * max(0, rows - 1)
    if kind == "custom":
        for primitive in params.get("primitives", []):
            if primitive.get("kind") != "text": continue
            font = max(c.minimum, float(primitive.get("font_pt", c.minimum)))
            if "bbox" in primitive:
                _, _, fraction_width, fraction_height = primitive["bbox"]
            else:
                fraction_width = primitive.get("width", .7)
                position_y = primitive.get("position", [.5, .5])[1]
                valign = primitive.get("valign", "center")
                fraction_height = 1 - position_y if valign == "top" else position_y if valign == "bottom" else 2 * min(position_y, 1 - position_y)
            wrapped = c.wrap(str(primitive.get("text", "")), max(.015, fraction_width * body_width), font)
            required = max(1, len(wrapped.splitlines())) * font / 72 * 1.35 + .025
            body_height = max(body_height, required / max(.01, fraction_height))
    if inside:
        wrapped = c.wrap(str(label), actual_width * .8, c.font)
        label_height = 0.
        total_height = max(.30, max(1, len(wrapped.splitlines())) * c.font / 72 * 1.35 / .76 + .03)
    else:
        wrapped = c.wrap(str(label), actual_width, c.font)
        label_height = (max(1, len(wrapped.splitlines())) * c.font / 72 * 1.35 + .025) if label else 0.
        total_height = body_height + label_height + .075
    return {"id": item["id"], "minimum_width_in": round(minimum_width, 5),
            "minimum_height_in": round(total_height, 5), "label_height_in": round(label_height, 5),
            "body_height_in": round(body_height, 5), "font_pt": c.minimum,
            "display_label": label, "canonical_label": item.get("label", "")}


def measure_scene_layout(scene, contract, style=None):
    """Return reproducible physical content requirements without drawing files."""
    settings = {**(style or {}), "minimum_font_pt": contract["minimum_font_pt"]}
    c = _Canvas(scene, settings)
    try:
        return {item["id"]: _minimum_object_size(c, item,
                item["bbox"][2] * c.width if item.get("bbox") else None) for item in scene.get("objects", [])}
    finally:
        plt.close(c.fig)


def layout_scene(scene, contract, style=None):
    """Pack model-selected groups using measured content, without changing science.

    A panel's optional ``layout`` defines order and flow. Native measurements
    determine all object bounds. A physically impossible group raises a concrete
    fit error instead of dropping content or shrinking the contracted font.
    """
    derived = deepcopy(scene)
    if not any(panel.get("layout") for panel in derived.get("panels", [])):
        return derived
    c = _Canvas(derived, {**(style or {}), "minimum_font_pt": contract["minimum_font_pt"]})
    objects = {item["id"]: item for item in derived.get("objects", [])}
    try:
        for panel in derived.get("panels", []):
            plan = panel.get("layout")
            if not plan: continue
            order = plan.get("object_ids", [])
            if not order or len(order) != len(set(order)) or any(identifier not in objects or objects[identifier].get("panel") != panel["id"] for identifier in order):
                raise ValueError("Panel packing requires unique actual object IDs belonging to the panel")
            group = [objects[identifier] for identifier in order]
            fixed = [_box(item["bbox"], c.width, c.height) for item in derived.get("objects", [])
                     if item.get("panel") == panel["id"] and item["id"] not in order and item.get("bbox")]
            fixed += [_box(item["bbox"], c.width, c.height) for item in derived.get("annotations", [])
                      if item.get("panel") == panel["id"] and item.get("bbox")]
            px, py, pw, ph = _box(panel["bbox"], c.width, c.height)
            pad = max(.04, float(plan.get("padding_in", .10)))
            gutter = max(.06, float(plan.get("gap_in", .12)) + float(plan.get("routing_gutter_in", .10)))
            top = py + ph - pad
            if derived.get("title") and top > c.height - .30:
                top = c.height - .34
            panel_title = panel.get("title") or ""
            title_lines = len(c.wrap(panel_title, max(.05, pw - 2 * pad), max(9., c.font)).splitlines())
            header = max(.23, title_lines * max(9., c.font) / 72 * 1.35 + .025) if panel_title else 0.
            if header:
                panel["header_bbox"] = [(px + pad) / c.width, 1 - top / c.height, (pw - 2 * pad) / c.width, header / c.height]
            else:
                panel.pop("header_bbox", None)
            available_width = pw - 2 * pad; available_height = top - py - pad - header - (.07 if header else 0)
            flow = plan.get("flow", "grid")
            if flow not in ("horizontal", "vertical", "grid"):
                raise ValueError("Panel flow must be horizontal, vertical or grid")
            candidates = [len(group)] if flow == "horizontal" else [1] if flow == "vertical" else (
                [int(plan["columns"])] if "columns" in plan else list(range(1, min(len(group), 6) + 1)))
            best = None; failures = []
            for columns in candidates:
                if not 1 <= columns <= len(group): continue
                cell_width = (available_width - (columns - 1) * gutter) / columns
                if cell_width <= 0: continue
                requirements = [_minimum_object_size(c, item, cell_width) for item in group]
                rows = math.ceil(len(group) / columns)
                row_heights = [max(item["minimum_height_in"] for item in requirements[row * columns:(row + 1) * columns]) for row in range(rows)]
                required_width = columns * max(item["minimum_width_in"] for item in requirements) + (columns - 1) * gutter
                required_height = sum(row_heights) + (rows - 1) * gutter
                failures.append({"columns": columns, "required_width_in": round(required_width, 4), "required_height_in": round(required_height, 4)})
                if required_width <= available_width + .001 and required_height <= available_height + .001:
                    cursor = top - header - (.07 if header else 0)
                    placements = []; feasible = True
                    for row, row_height in enumerate(row_heights):
                        row_group = group[row * columns:(row + 1) * columns]
                        while True:
                            row_boxes = [(px + pad + col * (cell_width + gutter), cursor - row_height, cell_width, row_height)
                                         for col in range(len(row_group))]
                            collisions = [obstacle for obstacle in fixed
                                          if any(_intersects(box, obstacle, gutter / 2) for box in row_boxes)]
                            if not collisions: break
                            lower_cursor = min(obstacle[1] - gutter / 2 for obstacle in collisions)
                            if lower_cursor >= cursor - .001:
                                feasible = False; break
                            cursor = lower_cursor
                        if not feasible or cursor - row_height < py + pad - .001:
                            feasible = False; break
                        placements.extend(zip(row_group, row_boxes))
                        cursor -= row_height + gutter
                    if not feasible:
                        failures[-1]["fixed_content_conflict"] = True
                        continue
                    score = (top - header - (.07 if header else 0) - cursor) + .03 * columns
                    if best is None or score < best[0]: best = (score, columns, placements, requirements)
            if best is None:
                raise ValueError("Panel " + panel["id"] + " cannot fit its source-bound content at " + str(c.minimum)
                                 + "pt: " + json.dumps({"available_width_in": round(available_width, 4),
                                     "available_height_in": round(available_height, 4), "required_layouts": failures}, ensure_ascii=False)
                                 + "; split or regroup the panel, or revise presentation aliases; do not drop source content")
            _, columns, placements, requirements = best
            for item, (x, y, w, h) in placements:
                item["bbox"] = [x / c.width, 1 - (y + h) / c.height, w / c.width, h / c.height]
            panel["layout_measurements"] = {"columns": columns, "routing_gutter_in": gutter,
                                             "objects": requirements, "available_width_in": available_width,
                                             "available_height_in": available_height}
        return derived
    finally:
        plt.close(c.fig)


def _render_object(c, item, index, assets):
    box = _box(item["bbox"], c.width, c.height)
    display_label = item.get("display_label", item.get("label", ""))
    kind = item["kind"]
    formula = item.get("params", {}).get("formula", "")
    label_inside = kind == "operator" and (not formula or formula in (display_label, item.get("label")))
    if label_inside:
        body, label = box, None
        item = {**item, "params": {**item.get("params", {}), "formula": display_label, "_overview_label": True}}
    else:
        body, label = _label_region(c, box, display_label, item["id"])
    color = _color(item.get("params", {}).get("color", item.get("color")), c.palette[index % len(c.palette)])
    native = {"tensor": _tensor, "matrix": _matrix, "graph": _graph, "tokens": _tokens,
              "document": _document, "memory": _memory, "operator": _operator,
              "geometry": _geometry, "custom": _custom}
    result = None
    if kind == "asset": result = _asset(c, item, body, assets)
    elif kind in native: native[kind](c, item, body, color)
    else: raise ValueError("Unsupported scientific scene object kind: " + str(kind))
    if label:
        c.text(display_label, label, item["id"], "label", font=c.font)
    return result


def _edge_label_placement(c, text, points, obstacles, bounds=None):
    """Search native wrapped label footprints near the actual routed wire.

    The supplied scientific label stays exact; only line breaks and physical
    placement change. Candidate dimensions are measured with the render font,
    and accepted footprints become obstacles for subsequent connections.
    """
    segments = sorted(zip(points[:-1], points[1:]),
                      key=lambda pair: abs(pair[0][0] - pair[1][0]) + abs(pair[0][1] - pair[1][1]),
                      reverse=True)
    dimensions = {}
    for a, b in segments:
        length = abs(a[0] - b[0]) + abs(a[1] - b[1])
        if length < .08:
            continue
        horizontal = abs(a[1] - b[1]) < 1e-7
        preferred_width = max(.40, min(1.4, length - .04)) if horizontal else 1.4
        widths = [preferred_width, 1.4, 1.1, .85, .65, .50, .40]
        widths = list(dict.fromkeys(round(value, 4) for value in widths))
        for width in widths:
            if width not in dimensions:
                dimensions[width] = _measure_edge_label(c, str(text), width, c.minimum)
            measurement = dimensions[width]
            footprint_width, height, wrapped = measurement["width_in"], measurement["height_in"], measurement["wrapped_text"]
            if footprint_width > width + .020:
                continue
            for fraction in (.5, .35, .65, .2, .8):
                lx = a[0] + (b[0] - a[0]) * fraction
                ly = a[1] + (b[1] - a[1]) * fraction
                if horizontal:
                    candidates = [(lx - footprint_width / 2, ly + .045, footprint_width, height),
                                  (lx - footprint_width / 2, ly - .045 - height, footprint_width, height)]
                else:
                    candidates = [(lx + .045, ly - height / 2, footprint_width, height),
                                  (lx - .045 - footprint_width, ly - height / 2, footprint_width, height)]
                for candidate in candidates:
                    if (_inside(candidate, (.015, .015, c.width - .03, c.height - .03), 0)
                            and (bounds is None or _inside(candidate, bounds, -.015))
                            and all(not _intersects(candidate, obstacle, .007) for obstacle in obstacles)):
                        return candidate, wrapped
    return None, None


def _connection_route(source, target, item, obstacles, previous, width, height, margin, bounds=None):
    """Choose feasible native ports using the observed routed path cost."""
    dx = target[0] + target[2] / 2 - source[0] - source[2] / 2
    dy = target[1] + target[3] / 2 - source[1] - source[3] / 2
    distance = max(.001, abs(dx) + abs(dy))
    source_request = item.get("from_port", "auto"); target_request = item.get("to_port", "auto")
    for request in (source_request, target_request):
        if request not in _PORTS and request != "auto":
            raise ValueError("Connections require left/right/top/bottom ports")
    source_ports = [source_request] if source_request != "auto" else ["right", "top", "bottom", "left"]
    target_ports = [target_request] if target_request != "auto" else ["left", "bottom", "top", "right"]
    offset = margin + .006
    candidates = []
    independent = [record["path_in"] for record in previous]
    waypoints = [(float(point[0]) * width, (1 - float(point[1])) * height)
                 for point in item.get("waypoints", [])]
    def stub_obstacles(box):
        own = (box[0] - margin, box[1] - margin, box[2] + 2 * margin, box[3] + 2 * margin)
        return [obstacle for obstacle in obstacles
                if any(abs(a - b) > 1e-8 for a, b in zip(obstacle, own))]
    source_stub_obstacles = stub_obstacles(source)
    target_stub_obstacles = stub_obstacles(target)
    def anchors(box, port, requested_offset=None):
        _, direction = _port(box, port)
        limit = (box[2] if direction[1] else box[3]) / 2 - .04
        result = []
        lanes = [requested_offset] if requested_offset is not None else [0., -.065, .065, -.13, .13, -.195, .195]
        for lane in lanes:
            if not math.isfinite(lane):
                raise ValueError("Native port offsets must be finite physical lengths")
            if abs(lane) > limit: continue
            point, vector = _port(box, port, lane)
            outside = tuple(point[axis] + vector[axis] * offset for axis in (0, 1))
            if _wire_penalty(point, outside, independent) is not None:
                result.append((lane, point, vector, outside))
        return result
    for source_port in source_ports:
        for target_port in target_ports:
            for source_lane, first, first_direction, start in anchors(source, source_port, item.get("from_port_offset_in")):
                for target_lane, last, last_direction, finish in anchors(target, target_port, item.get("to_port_offset_in")):
                    if any(not .01 <= point[0] <= width - .01 or not .01 <= point[1] <= height - .01
                           for point in (start, finish)):
                        continue
                    if bounds is not None and any(not _inside((*point, 0., 0.), bounds, -.015)
                                                  for point in (start, finish)):
                        continue
                    if (not _segment_clear(first, start, source_stub_obstacles)
                            or not _segment_clear(finish, last, target_stub_obstacles)):
                        continue
                    # Distinct incoming/outgoing attachments on the same side
                    # retain its declared port and avoid an ambiguous shared stub.
                    penalty = .04 * (abs(source_lane) + abs(target_lane)) / .065
                    if source_request == "auto":
                        alignment = (first_direction[0] * dx + first_direction[1] * dy) / distance
                        penalty += .12 * (1 - alignment)
                    if target_request == "auto":
                        alignment = -(last_direction[0] * dx + last_direction[1] * dy) / distance
                        penalty += .12 * (1 - alignment)
                    stops = [start, *waypoints, finish]
                    bound = sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(stops[:-1], stops[1:])) + 2 * offset + penalty
                    candidates.append((bound, penalty, source_port, target_port, first, last, start, finish, source_lane, target_lane))
    best = None; best_cost = math.inf
    for bound, penalty, source_port, target_port, first, last, start, finish, source_lane, target_lane in sorted(candidates):
        if bound >= best_cost - 1e-8:
            continue
        route = [start]
        for a, b in zip([start, *waypoints, finish][:-1], [start, *waypoints, finish][1:]):
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) < 1e-8:
                continue
            wires = [*independent, route] if len(route) > 1 else independent
            leg = _route(a, b, obstacles, width, height, wires, bounds=bounds)
            if leg is None:
                route = None; break
            route.extend(leg[1:])
        if route is None: continue
        points = [first, *route, last]
        cost = penalty
        prior_axis = None
        for a, b in zip(points[:-1], points[1:]):
            cost += abs(a[0] - b[0]) + abs(a[1] - b[1])
            axis = 0 if abs(a[1] - b[1]) < 1e-7 else 1
            if prior_axis is not None and prior_axis != axis: cost += .07
            prior_axis = axis
            wire_cost = _wire_penalty(a, b, independent)
            if wire_cost is None:
                cost = math.inf; break
            cost += wire_cost
        if cost < best_cost:
            best_cost = cost; best = (points, source_port, target_port, cost, source_lane, target_lane)
    return best


def _label_path_distance(box, points):
    """Shortest physical separation between a label footprint and its wire."""
    x, y, w, h = box
    distances = []
    for a, b in zip(points[:-1], points[1:]):
        if abs(a[1] - b[1]) < 1e-7:
            lo, hi = sorted((a[0], b[0]))
            dx = max(0., lo - (x + w), x - hi)
            dy = max(0., a[1] - (y + h), y - a[1])
        else:
            lo, hi = sorted((a[1], b[1]))
            dx = max(0., a[0] - (x + w), x - a[0])
            dy = max(0., lo - (y + h), y - hi)
        distances.append(math.hypot(dx, dy))
    return min(distances, default=math.inf)


def _connections(c, scene, boxes, extra_obstacles):
    results = []; margin = .025
    object_panels = {item["id"]: item.get("panel") for item in scene.get("objects", [])}
    panel_bounds = {panel["id"]: _box(panel["bbox"], c.width, c.height) for panel in scene.get("panels", [])}
    def shared_panel(item):
        identifier = object_panels.get(item["source"])
        return identifier if identifier == object_panels.get(item["target"]) else None
    prepared_labels = {}
    for item in scene.get("connections", []):
        label = item.get("display_label", item.get("label", "")) if item.get("label_visible", True) else ""
        if not label or "label_bbox" not in item: continue
        owner = item["id"]; box = _box(item["label_bbox"], c.width, c.height)
        measurement = _measure_edge_label(c, label, box[2], c.minimum)
        prepared_labels[owner] = (box, measurement)
        routing_panel = shared_panel(item); bounds = panel_bounds.get(routing_panel)
        if (not _inside(box, (.015, .015, c.width - .03, c.height - .03), 0)
                or bounds is not None and not _inside(box, bounds, -.015)):
            c.issues.append({"id": owner, "code": "edge_label_bbox_outside_bounds", "severity": "error",
                             "detail": "The allocated dependency label extends beyond its routing panel or page",
                             "routing_panel": routing_panel, "routing_bounds_in": list(bounds) if bounds else None})
        if measurement["width_in"] > box[2] + .001 or measurement["height_in"] > box[3] + .001:
            c.issues.append({"id": owner, "code": "edge_label_bbox_overflow", "severity": "error",
                             "detail": "The exact dependency label cannot fit its allocated native text box",
                             "label": label, "allocated_bbox_in": list(box), "required_width_in": measurement["width_in"],
                             "required_height_in": measurement["height_in"], "minimum_font_pt": c.minimum})
        collisions = [(identifier, obstacle) for identifier, obstacle in boxes.items()]
        collisions += [("reserved_text", obstacle) for obstacle in extra_obstacles]
        collisions += [(identifier, other[0]) for identifier, other in prepared_labels.items() if identifier != owner]
        for identifier, obstacle in collisions:
            if _intersects(box, obstacle, .007):
                c.issues.append({"id": owner, "code": "edge_label_bbox_overlap", "severity": "error",
                                 "other_id": identifier, "detail": "The allocated dependency label intersects scientific content"})
    label_obstacles = [*boxes.values(), *extra_obstacles]
    label_obstacles += [value[0] for value in prepared_labels.values()]
    obstacles = [(x - margin, y - margin, w + 2 * margin, h + 2 * margin)
                 for x, y, w, h in label_obstacles]
    for item in scene.get("connections", []):
        owner = item["id"]; source = boxes[item["source"]]; target = boxes[item["target"]]
        routing_panel = shared_panel(item)
        routing_bounds = panel_bounds.get(routing_panel)
        invalid_port_offset = False
        for prefix, box in (("from", source), ("to", target)):
            field = prefix + "_port_offset_in"
            if field not in item: continue
            port = item.get(prefix + "_port", "auto")
            if port not in _PORTS:
                raise ValueError("Explicit native port offsets require a named side")
            direction = _PORTS[port]
            maximum = max(0., (box[2] if direction[1] else box[3]) / 2 - .04)
            if not math.isfinite(float(item[field])) or abs(float(item[field])) > maximum + 1e-8:
                c.issues.append({"id": owner, "code": "connection_port_offset_outside_bounds", "severity": "error",
                                 "detail": "The explicit native attachment offset lies beyond its source or target side",
                                 "port_field": field, "requested_offset_in": item[field], "maximum_offset_in": maximum})
                invalid_port_offset = True
        if invalid_port_offset: continue
        blocked_waypoint = False
        for waypoint in item.get("waypoints", []):
            point = (float(waypoint[0]) * c.width, (1 - float(waypoint[1])) * c.height)
            outside = (not _inside((*point, 0., 0.), (.015, .015, c.width - .03, c.height - .03), 0)
                       or routing_bounds is not None and not _inside((*point, 0., 0.), routing_bounds, -.015))
            collision = any(x + 1e-8 < point[0] < x + w - 1e-8 and y + 1e-8 < point[1] < y + h - 1e-8
                            for x, y, w, h in obstacles)
            if outside or collision:
                c.issues.append({"id": owner, "code": "connection_waypoint_outside_bounds" if outside else "connection_waypoint_blocked",
                                 "severity": "error", "detail": "A mandatory routing waypoint is outside its panel or inside scientific content",
                                 "waypoint_in": list(point), "routing_panel": routing_panel,
                                 "routing_bounds_in": list(routing_bounds) if routing_bounds else None})
                blocked_waypoint = True
        if blocked_waypoint: continue
        chosen = _connection_route(source, target, item, obstacles, results, c.width, c.height, margin,
                                   bounds=routing_bounds)
        if chosen is None:
            c.issues.append({"id": owner, "code": "connection_unroutable", "severity": "error",
                             "detail": "No obstacle-free, unambiguous rectilinear path exists between the available native ports"
                                       + (" within their shared panel" if routing_bounds else ""),
                             "routing_panel": routing_panel, "routing_bounds_in": list(routing_bounds) if routing_bounds else None,
                             "repair": "Reserve a routing corridor or move the connected objects; preserve the source dependency"})
            continue
        points, source_port, target_port, route_cost, source_lane, target_lane = chosen
        if any(not 0 <= point[0] <= c.width or not 0 <= point[1] <= c.height for point in points):
            c.issues.append({"id": owner, "code": "connection_clipping", "severity": "error",
                             "detail": "Routed connection crosses the physical page boundary"})
        edge_color = _color(item.get("color"), _INK)
        for previous in results:
            overlap = False
            for a, b in zip(points[:-1], points[1:]):
                for pa, pb in zip(previous["path_in"][:-1], previous["path_in"][1:]):
                    if abs(a[0] - b[0]) < 1e-7 and abs(pa[0] - pb[0]) < 1e-7 and abs(a[0] - pa[0]) < 2e-5:
                        overlap = min(max(a[1], b[1]), max(pa[1], pb[1])) - max(min(a[1], b[1]), min(pa[1], pb[1])) > .05
                    elif abs(a[1] - b[1]) < 1e-7 and abs(pa[1] - pb[1]) < 1e-7 and abs(a[1] - pa[1]) < 2e-5:
                        overlap = min(max(a[0], b[0]), max(pa[0], pb[0])) - max(min(a[0], b[0]), min(pa[0], pb[0])) > .05
                    if overlap: break
                if overlap: break
            if overlap:
                c.issues.append({"id": owner, "code": "connection_overlap", "severity": "error",
                                 "other_id": previous["id"],
                                 "detail": "Independent dependencies share a line segment without a declared junction"})
        # A narrow white under-stroke keeps independent wire crossings visually
        # distinct. It never covers a scientific object: routing excludes them.
        edge_layer = 4 + len(results) * .001
        c.line(points, owner, color="white", lw=2.7, zorder=edge_layer, role="connection-clearance", solid_capstyle="round")
        c.line(points, owner, color=edge_color, lw=.85, zorder=edge_layer + .0005, role="connection", solid_capstyle="round")
        c.patch(FancyArrowPatch(points[-2], points[-1], arrowstyle="-|>", mutation_scale=7,
                                color=edge_color, linewidth=.85, shrinkA=0, shrinkB=0), owner, "arrowhead", zorder=5)
        canonical_label = item.get("label", "")
        label_visible = item.get("label_visible", True)
        label = item.get("display_label", canonical_label) if label_visible else ""
        label_box = None; wrapped_label = None; label_distance = None
        if label:
            if owner in prepared_labels:
                label_box, measurement = prepared_labels[owner]
                wrapped_label = measurement["wrapped_text"]
            else:
                label_box, wrapped_label = _edge_label_placement(c, label, points, label_obstacles, bounds=routing_bounds)
            if label_box is None:
                c.issues.append({"id": owner, "code": "edge_label_unplaceable", "severity": "error",
                                 "detail": "The exact dependency label has no readable obstacle-free placement beside its routed wire",
                                 "label": label, "minimum_font_pt": c.minimum,
                                 "routing_panel": routing_panel, "routing_bounds_in": list(routing_bounds) if routing_bounds else None,
                                 "repair": "Reserve label space or move the connected objects/ports; keep the exact scientific label"})
            else:
                artist = c.text(wrapped_label, label_box, owner, "connection-label", font=c.minimum, background=True, wrap=False)
                if artist is not None: c.text_records[-1]["source_text"] = label
                label_distance = _label_path_distance(label_box, points)
                if owner in prepared_labels and label_distance > .16:
                    c.issues.append({"id": owner, "code": "edge_label_detached", "severity": "error",
                                     "detail": "The allocated dependency label is too far from its own rendered wire",
                                     "label_route_distance_in": label_distance, "maximum_distance_in": .16})
                if owner not in prepared_labels:
                    x, y, w, h = label_box
                    obstacles.append((x - margin, y - margin, w + 2 * margin, h + 2 * margin))
                    label_obstacles.append(label_box)
        results.append({"id": owner, "source": item["source"], "target": item["target"],
                        "path_in": [[round(x, 5), round(y, 5)] for x, y in points],
                        "obstacle_free": True, "semantic_edge": item.get("semantic_edge"),
                        "routing_panel": routing_panel, "routing_bounds_in": list(routing_bounds) if routing_bounds else None,
                        "from_port": source_port, "to_port": target_port, "route_cost": round(route_cost, 5),
                        "from_port_offset_in": source_lane, "to_port_offset_in": target_lane,
                        "canonical_label": canonical_label,
                        "label_text": canonical_label if label_visible else "", "display_label": label,
                        "label_visible": label_visible,
                        "label_visibility_reason": item.get("label_visibility_reason", ""),
                        "label_bbox_in": list(label_box) if label_box else None,
                        "label_preallocated": owner in prepared_labels, "label_route_distance_in": label_distance,
                        "mandatory_waypoints": len(item.get("waypoints", [])),
                        "label_lines": len(wrapped_label.splitlines()) if wrapped_label else 0})
    return results


def _measure(c, object_boxes):
    c.fig.canvas.draw(); renderer = c.fig.canvas.get_renderer(); measured = []
    for record in c.text_records:
        extent = record["artist"].get_window_extent(renderer)
        xy = c.ax.transData.inverted().transform(extent.get_points())
        observed = (float(xy[0, 0]), float(xy[0, 1]), float(xy[1, 0] - xy[0, 0]), float(xy[1, 1] - xy[0, 1]))
        measured.append({"id": record["id"], "role": record["role"], "font_pt": record["font_pt"],
                         "bbox_in": [round(value, 5) for value in observed], "text": record["source_text"]})
        if not _inside(observed, record["box"]):
            c.issues.append({"id": record["id"], "code": "text_overflow", "severity": "error",
                             "detail": "Measured text extends beyond its allocated bounds", "role": record["role"],
                             "measured_bbox_in": list(observed), "allocated_bbox_in": list(record["box"])})
        if not _inside(observed, (0, 0, c.width, c.height), .004):
            c.issues.append({"id": record["id"], "code": "page_clipping", "severity": "error",
                             "detail": "Measured text crosses the physical page boundary"})
        if record["id"] in object_boxes and not _inside(observed, object_boxes[record["id"]]):
            c.issues.append({"id": record["id"], "code": "text_outside_object", "severity": "error",
                             "detail": "A scientific label extends outside its containing object", "role": record["role"]})
        for owner, box in object_boxes.items():
            if owner != record["id"] and _intersects(observed, box, -.007):
                c.issues.append({"id": record["id"], "code": "text_object_overlap", "severity": "error",
                                 "other_id": owner, "detail": "Measured text intersects another object"})
    for index, a in enumerate(measured):
        for b in measured[index + 1:]:
            if _intersects(a["bbox_in"], b["bbox_in"], -.008):
                c.issues.append({"id": a["id"], "code": "text_overlap", "severity": "error",
                                 "other_id": b["id"], "detail": "Two measured text elements overlap",
                                 "roles": [a["role"], b["role"]]})
    return measured


def render_scene(output_dir, data, style=None):
    """Render an already source-grounded scene and return actual artifact paths.

    Layout defects are reported from measured geometry rather than silently
    accepted.  The production workflow consumes ``quality_issues`` for targeted
    repair; rendering alone never certifies publication quality.
    """
    from research.figures.spec import validate_scene, resolve_scene_metrics
    if not isinstance(data, dict) or not isinstance(data.get("production_scene"), dict):
        raise ValueError("Scientific scene rendering requires production_scene and production_contract")
    contract = data.get("production_contract", {})
    laid_out = layout_scene(data["production_scene"], contract, style)
    validation = validate_scene(laid_out, contract)
    scene = resolve_scene_metrics(laid_out, contract)
    style = dict(style or {})
    style["minimum_font_pt"] = max(8., float(contract.get("minimum_font_pt", style.get("minimum_font_pt", 8))))
    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    assets = data.get("asset_data", {})
    with matplotlib.rc_context({"svg.fonttype": "none", "pdf.fonttype": 42, "font.family": "DejaVu Sans",
                                "savefig.pad_inches": 0, "axes.unicode_minus": False}):
        c = _Canvas(scene, style)
        try:
            panels = {}; reserved = []
            if scene.get("title"):
                title_box = (.08, c.height - .30, c.width - .16, .24)
                c.text(scene["title"], title_box, "figure", "title", font=max(c.font, 11), weight="bold", align="left")
                reserved.append(title_box)
            for index, panel in enumerate(scene.get("panels", [])):
                box = _box(panel["bbox"], c.width, c.height); panels[panel["id"]] = box
                x, y, w, h = box; color = c.palette[index % len(c.palette)]
                c.patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=.045",
                                      facecolor=_tint(color, .98), edgecolor=_tint(color, .6), linewidth=.65),
                        panel["id"], "panel", zorder=0)
                if panel.get("title"):
                    title = (_box(panel["header_bbox"], c.width, c.height) if panel.get("header_bbox")
                             else (x + .08, y + h - .28, w - .16, .23))
                    c.text(panel["title"], title, panel["id"], "panel-title", font=max(9., c.font), weight="bold", align="left")
                    reserved.append(title)
            boxes = {item["id"]: _box(item["bbox"], c.width, c.height) for item in scene["objects"]}
            for index, item in enumerate(scene["objects"]):
                if item.get("panel") in panels and not _inside(boxes[item["id"]], panels[item["panel"]], 0):
                    c.issues.append({"id": item["id"], "code": "object_outside_panel", "severity": "error",
                                     "detail": "Scientific object extends beyond its declared panel"})
            for index, a in enumerate(scene["objects"]):
                for b in scene["objects"][index + 1:]:
                    if _intersects(boxes[a["id"]], boxes[b["id"]], -.007):
                        c.issues.append({"id": a["id"], "code": "object_overlap", "severity": "error",
                                         "other_id": b["id"], "detail": "Two scientific objects occupy intersecting bounds"})
            for note in scene.get("annotations", []):
                box = _box(note["bbox"], c.width, c.height); reserved.append(box)
                is_claim = note.get("role") == "claim"
                if is_claim:
                    x, y, w, h = box
                    c.patch(Rectangle((x, y), .025, h, facecolor=c.palette[2 % len(c.palette)], edgecolor="none"), note["id"], "claim-accent", zorder=1)
                    box = (x + .075, y, w - .075, h)
                c.text(note["text"], box, note["id"], "annotation", font=note.get("font_pt", c.font),
                       weight="bold" if is_claim else "normal", align="left")
            routes = _connections(c, scene, boxes, reserved)
            asset_records = []
            for index, item in enumerate(scene["objects"]):
                asset = _render_object(c, item, index, assets)
                if asset: asset_records.append(asset)
            measured = _measure(c, boxes)
            for asset in asset_records:
                if asset["raster_pixels_per_inch"] < 250:
                    c.issues.append({"id": asset["id"], "code": "raster_resolution", "severity": "error",
                                     "detail": "Illustrative asset is below 250 pixels per printed inch"})
            for extension in ("svg", "pdf", "png"):
                c.fig.savefig(output / ("figure." + extension), dpi=300, facecolor="white",
                              bbox_inches=None, metadata={"Creator": "FOREST scientific figure renderer"} if extension in ("pdf", "svg") else None)
            tree = ElementTree.parse(output / "figure.svg")
            native_text = len(tree.findall(".//{http://www.w3.org/2000/svg}text"))
            report = {"kind": "method", "narrative_mode": contract.get("narrative_mode", "scientific_story"),
                      "render_engine": "production_scene", "evidence_role": "conceptual_illustration",
                      "width_in": c.width, "height_in": c.height, "layout_width_in": c.width,
                      "minimum_font_pt": min((item["font_pt"] for item in measured), default=c.minimum),
                      "font_measurement_scope": "Actual rendered text operators at the declared physical page size",
                      "vector_formats": ["svg", "pdf"], "native_text_elements": native_text,
                      "native_shape_elements": c.shape_count, "raster_assets": asset_records,
                      "objects": len(scene["objects"]), "panels": len(scene.get("panels", [])),
                      "connections": routes, "coverage": validation, "text_measurements": measured,
                      "quality_issues": c.issues, "geometry_passed": not any(issue["severity"] == "error" for issue in c.issues),
                      "layout_requirements": measure_scene_layout(scene, contract, style),
                      "quality_scope": "Source contract, physical geometry and actual artifact checks; independent pixel review is required",
                      "element_ids": sorted(c.ids)}
        finally:
            plt.close(c.fig)
    (output / "figure_data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
    (output / "style.json").write_text(json.dumps(style, ensure_ascii=False, indent=2))
    (output / "render_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (output / "scene.json").write_text(json.dumps(scene, ensure_ascii=False, indent=2))
    argument = contract.get("scientific_argument", {})
    source_caption = contract.get("source_context", {}).get("caption", "")
    caption_text = scene.get("caption_text", source_caption)
    caption = {"caption_text": caption_text, "source_caption": source_caption,
               "title": argument.get("title", ""), "claim": argument.get("central_message", ""),
               "display": argument.get("display", {}), "consequence": argument.get("consequence", {}),
               "evidence_role": "conceptual_illustration", "source_refs": sorted(contract.get("evidence_catalog", {})),
               "operations": contract.get("nodes", []), "connections": contract.get("edges", [])}
    (output / "figure_caption_context.json").write_text(json.dumps(caption, ensure_ascii=False, indent=2))
    source = """from pathlib import Path
import json
from research.figures.scene import render_scene

if __name__ == '__main__':
    folder = Path(__file__).resolve().parent
    render_scene(folder, json.loads((folder / 'figure_data.json').read_text()),
                 json.loads((folder / 'style.json').read_text()))
"""
    (output / "render_scene.py").write_text(source)
    return {key: str(output / filename) for key, filename in
            {"png": "figure.png", "pdf": "figure.pdf", "svg": "figure.svg", "source": "render_scene.py",
             "data": "figure_data.json", "style": "style.json", "report": "render_report.json",
             "caption_context": "figure_caption_context.json", "scene": "scene.json"}.items()}
