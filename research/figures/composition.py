"""Compile a local mechanism design around a host-owned scientific graph.

The designer controls explanatory geometry inside one native hero glyph. The
compiler owns page geometry, source identities and every overview dependency.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re

from research.figures.spec import OBJECT_KINDS, validate_scene


_FIELDS = frozenset({'version', 'overview_position', 'display_aliases', 'edge_aliases',
                     'hero', 'title', 'overview_title', 'hero_title', 'caption_text', 'palette',
                     'overview_groups', 'overview_chain', 'overview_glyphs'})
_HERO_FIELDS = frozenset({'kind', 'params', 'detail', 'evidence_refs', 'display_label'})
_PARAMETERS = {
    'tensor': {'shape', 'axis_labels', 'color'},
    'matrix': {'rows', 'cols', 'mask', 'highlight', 'schematic', 'values', 'cell_labels',
               'mask_matrix', 'row_labels', 'col_labels', 'source_ref', 'color'},
    'graph': {'nodes', 'edges', 'color'},
    'tokens': {'items', 'active', 'color'},
    'document': {'lines', 'color'}, 'memory': {'slots', 'items', 'color'},
    'operator': {'formula', 'shape', 'color'},
    'geometry': {'type', 'schematic', 'points', 'color'},
    'custom': {'primitives', 'color'}, 'asset': {'asset_id', 'color'},
}
_PRIMITIVE_FIELDS = {
    'rect': {'bbox'}, 'ellipse': {'bbox'}, 'circle': {'center', 'radius'},
    'line': {'points'}, 'arrow': {'points'}, 'polygon': {'points'},
    'text': {'bbox', 'text', 'color', 'align', 'valign', 'font_pt'},
}
_LOCAL_ID = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,79}\Z')
_OVERVIEW_FRACTIONS = (.25, .30, .35, .40, .45, .50)


class FigureCompositionCapacityError(ValueError):
    """A physical fit failure with source-preserving local repair guidance."""
    def __init__(self, message, layout_feedback):
        self.layout_feedback = layout_feedback
        super().__init__(message + '; local_fit_feedback=' + json.dumps(layout_feedback, ensure_ascii=False))


class _OverviewCapacityError(ValueError):
    def __init__(self, message, **feedback):
        self.feedback = {'detail': message, **feedback}
        super().__init__(message)


def composition_schema():
    """Describe model-editable content; global geometry is deliberately absent."""
    from research.figures.spec import scene_schema
    native = scene_schema()
    return {
        'version': 1, 'overview_position': 'top|left',
        'display_aliases': {'supplied_operation_id': 'short faithful visible label, <=64 characters'},
        'edge_aliases': {'zero_based_dependency_index': {
            'display_label': 'optional short faithful label, <=48 characters',
            'label_visible': 'optional boolean; defaults to true',
            'label_visibility_reason': 'required scientific reason when hiding redundant typography'}},
        'overview_groups': [{'id': 'unique simple local group ID, at most 80 characters',
                             'operation_ids': ['actual supplied operation IDs; complete unique partition'],
                             'flow': 'LR|TB'}],
        'overview_chain': ['optional unique main-spine source IDs; each consecutive pair must be a real directed dependency'],
        'overview_glyphs': {'actual_operation_id': {
            'kind': 'existing native kind except asset',
            'params': 'existing native parameters, coordinates only local to this glyph body',
            'detail': 'required source-grounded explanation for scientific content beyond a label-only operator',
            'evidence_refs': ['required actual catalog references for that scientific content']}},
        'hero': {'kind': '|'.join(sorted(OBJECT_KINDS)),
                 'params': 'existing native glyph parameters; all coordinates local to the hero body',
                 'detail': 'source-grounded explanation of the key transformation',
                 'evidence_refs': ['actual evidence_catalog identity'],
                 'display_label': 'optional short faithful key-operation alias'},
        'title': 'optional short scientific figure title',
        'overview_title': 'optional short overview header; null omits the header',
        'hero_title': 'optional short mechanism header; null omits the header',
        'caption_text': 'optional scientific companion caption',
        'palette': ['optional RGB hex colors with scientific roles'],
        'parameters': native['parameters'],
        'coordinate_system': 'Only hero.params and overview_glyphs[*].params contain coordinates. Custom primitives use local '
                             'normalized boxes in the drawable body above the host-owned operation label. '
                             'Typed graph nodes and geometry points are also local. The host owns all '
                             'panels, object bounds and page coordinates.',
        'local_primitive_style': 'Each custom primitive may have an optional id: a unique local '
                                 'identifier starting with a letter, containing only letters, digits, '
                                 'underscores and hyphens, at most 80 characters. These IDs locate local '
                                 'repairs inside one glyph and never bind global connections or operations. '
                                 'Each primitive may use fill and stroke as RGB hex or none, '
                                 'and positive finite stroke_pt. Text uses color, font_pt, align and valign. '
                                 'These are primitive fields, never composition top-level fields. '
                                 'parameters, coordinate_system, ownership and this entry are schema '
                                 'documentation; do not return them in the composition.',
        'overview_layout': 'Groups only condense visual stages: they never merge or hide source nodes. If groups are '
                           'provided, cover every supplied operation exactly once. The host ranks groups by real '
                           'dependencies and preserves the directed main chain; a grouping with an incompatible '
                           'forward cycle must be revised. All physical boxes, ports, feedback tracks and labels '
                           'are host-owned. Groups, chain and glyphs are optional; defaults use source graph hierarchy.',
        'ownership': 'The host supplies exact source labels, all operation and dependency IDs, panels, '
                     'ports and physical boxes. Do not return objects, connections, page coordinates, '
                     'a replacement graph, or hero operation_id. Custom text requires a local bbox.',
    }


def _known_fields(value, allowed, name):
    if not isinstance(value, dict):
        raise ValueError(name + ' must be an object')
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(name + ' contains host-owned or unsupported fields: ' + ', '.join(sorted(map(str, unknown))))


def _label(value, maximum, name):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or len(value.splitlines()) > 2):
        raise ValueError(name + ' must contain concise nonempty text, at most ' + str(maximum) + ' characters and two lines')


def _local_glyph(glyph, contract, name, *, hero=False):
    _known_fields(glyph, _HERO_FIELDS if hero else _HERO_FIELDS - {'display_label'}, name)
    kind = glyph.get('kind')
    if not isinstance(kind, str) or kind not in OBJECT_KINDS or not hero and kind == 'asset':
        raise ValueError(name + ' requires a supported native scientific glyph kind')
    _known_fields(glyph.get('params'), _PARAMETERS[kind], name + ' parameters')
    substantive = hero or kind != 'operator' or bool(glyph['params'].get('formula'))
    if substantive and (not isinstance(glyph.get('detail'), str) or len(glyph['detail'].strip()) < 20):
        raise ValueError(name + ' requires a concrete source-grounded transformation explanation')
    refs = glyph.get('evidence_refs', [])
    if (not isinstance(refs, list) or substantive and not refs or any(not isinstance(ref, str)
            or ref not in contract['evidence_catalog'] for ref in refs)):
        raise ValueError(name + ' must reference actual source catalog identities')
    if 'display_label' in glyph:
        _label(glyph['display_label'], 64, name + ' display alias')
    if kind == 'custom':
        primitives = glyph['params'].get('primitives')
        if not isinstance(primitives, list):
            raise ValueError('Custom glyph requires descriptive local primitives')
        local_ids = set()
        for primitive in primitives:
            if not isinstance(primitive, dict) or primitive.get('kind') not in _PRIMITIVE_FIELDS:
                raise ValueError('Custom glyph requires supported descriptive local primitives')
            allowed = {'id', 'kind', 'fill', 'stroke', 'stroke_pt'} | _PRIMITIVE_FIELDS[primitive['kind']]
            _known_fields(primitive, allowed, 'Local scientific primitive')
            if 'id' in primitive:
                identifier = primitive['id']
                if (not isinstance(identifier, str) or not _LOCAL_ID.fullmatch(identifier)
                        or identifier in local_ids):
                    raise ValueError('Local primitive IDs must be unique simple identifiers of at most 80 characters')
                local_ids.add(identifier)
            if 'stroke_pt' in primitive and (isinstance(primitive['stroke_pt'], bool)
                    or not isinstance(primitive['stroke_pt'], (int, float))
                    or not math.isfinite(primitive['stroke_pt']) or primitive['stroke_pt'] <= 0):
                raise ValueError('Local primitive stroke_pt must be positive and finite')
            if primitive['kind'] == 'text' and 'bbox' not in primitive:
                raise ValueError('Local scientific text requires a real allocated bbox')
    if kind == 'graph':
        if not isinstance(glyph['params'].get('nodes'), list) or not isinstance(glyph['params'].get('edges'), list):
            raise ValueError('Local graph requires actual node and edge lists')
        for node in glyph['params']['nodes']:
            _known_fields(node, {'id', 'x', 'y', 'label'}, 'Local graph node')
        for edge in glyph['params']['edges']:
            _known_fields(edge, {'source', 'target'}, 'Local graph edge')


def _overview_hints(composition, contract):
    identifiers = {node['id'] for node in contract['nodes']}
    chain = composition.get('overview_chain', [])
    if (not isinstance(chain, list) or any(not isinstance(node, str) or node not in identifiers for node in chain)
            or len(chain) != len(set(chain))):
        raise ValueError('Overview chain must contain unique actual supplied operation IDs')
    links = {(edge['source'], edge['target']) for edge in contract['edges']}
    if any(pair not in links for pair in zip(chain, chain[1:])):
        raise ValueError('Every consecutive overview chain pair must be an actual directed source dependency')
    groups = composition.get('overview_groups')
    if groups is not None:
        if not isinstance(groups, list) or not groups or len(groups) > min(32, len(identifiers)):
            raise ValueError('Overview groups must be a bounded nonempty partition of supplied operations')
        group_ids, covered = set(), []
        for group in groups:
            _known_fields(group, {'id', 'operation_ids', 'flow'}, 'Overview group')
            identifier = group.get('id')
            if not isinstance(identifier, str) or not _LOCAL_ID.fullmatch(identifier) or identifier in group_ids:
                raise ValueError('Overview group IDs must be unique simple identifiers of at most 80 characters')
            group_ids.add(identifier)
            members = group.get('operation_ids')
            if (not isinstance(members, list) or not members
                    or any(not isinstance(node, str) or node not in identifiers for node in members)):
                raise ValueError('Overview groups must contain actual supplied operation IDs')
            if group.get('flow') not in ('LR', 'TB'):
                raise ValueError('Overview group flow must be LR or TB')
            covered.extend(members)
        if len(covered) != len(set(covered)) or set(covered) != identifiers:
            raise ValueError('Overview groups must cover every supplied operation exactly once')
    glyphs = composition.get('overview_glyphs', {})
    if not isinstance(glyphs, dict) or any(identifier not in identifiers for identifier in glyphs):
        raise ValueError('Overview glyphs must identify actual supplied operations')
    for identifier, glyph in glyphs.items():
        _local_glyph(glyph, contract, 'Overview glyph ' + identifier)
    return groups, chain


def _parts(composition, contract):
    _known_fields(composition, _FIELDS, 'Figure composition')
    if composition.get('version') != 1 or isinstance(composition.get('version'), bool):
        raise ValueError('Figure composition must use supported version 1')
    if composition.get('overview_position', 'top') not in ('top', 'left'):
        raise ValueError('Overview position must be top or left')
    key = contract.get('key_operation_id')
    nodes = {node['id']: node for node in contract['nodes']}
    if key not in nodes:
        raise ValueError('Figure composition requires the frozen source contract to identify its key operation')
    _overview_hints(composition, contract)
    aliases = composition.get('display_aliases', {})
    if not isinstance(aliases, dict) or any(identifier not in nodes for identifier in aliases):
        raise ValueError('Overview display aliases must identify actual supplied operations')
    for identifier, value in aliases.items():
        _label(value, 64, 'Operation ' + identifier + ' display alias')
    edge_aliases = composition.get('edge_aliases', {})
    if not isinstance(edge_aliases, dict):
        raise ValueError('Dependency display aliases must be indexed objects')
    indices = set()
    for identifier, alias in edge_aliases.items():
        # JSON object keys are decimal strings; Python callers may use integers.
        if (isinstance(identifier, bool) or not isinstance(identifier, (int, str))
                or isinstance(identifier, str) and (not identifier.isdecimal()
                    or identifier != str(int(identifier)))):
            raise ValueError('Dependency aliases require canonical zero-based dependency indices')
        index = int(identifier)
        if not 0 <= index < len(contract['edges']) or index in indices:
            raise ValueError('Dependency alias does not identify one actual supplied dependency')
        indices.add(index)
        _known_fields(alias, {'display_label', 'label_visible', 'label_visibility_reason'}, 'Dependency alias')
        if 'display_label' in alias:
            _label(alias['display_label'], 48, 'Dependency display alias')
    hero = composition.get('hero')
    _local_glyph(hero, contract, 'Mechanism hero', hero=True)
    refs = hero['evidence_refs']
    for field in ('title', 'overview_title', 'hero_title'):
        if composition.get(field) is not None:
            _label(composition[field], 80, 'Figure ' + field)
    if 'caption_text' in composition and not isinstance(composition['caption_text'], str):
        raise ValueError('Figure caption companion must be scientific text')
    from research.figures.scene import _PALETTE
    palette = composition.get('palette') or _PALETTE
    key_color = hero['params'].get('color', palette[len(contract['nodes']) % len(palette)])
    objects = []
    for node in contract['nodes']:
        item = {'id': 'overview_' + node['id'], 'panel': 'overview', 'operation_id': node['id'],
                'kind': 'operator', 'label': node['label'], 'params': {'shape': 'capsule'},
                'evidence_refs': ['operation:' + node['id']]}
        item['params']['color'] = key_color if node['id'] == key else '#6B7280'
        glyph = composition.get('overview_glyphs', {}).get(node['id'])
        if glyph:
            item.update(deepcopy(glyph))
            item['evidence_refs'] = list(dict.fromkeys(['operation:' + node['id'], *glyph.get('evidence_refs', [])]))
        if node['id'] in aliases:
            item['display_label'] = aliases[node['id']]
        objects.append(item)
    hero_object = {'id': 'hero_' + key, 'panel': 'mechanism', 'operation_id': key,
                   'label': nodes[key]['label'], **deepcopy(hero)}
    hero_object['evidence_refs'] = list(dict.fromkeys(['operation:' + key, *refs]))
    if 'display_label' not in hero_object and key in aliases:
        hero_object['display_label'] = aliases[key]
    objects.append(hero_object)
    connections = []
    for index, edge in enumerate(contract['edges']):
        connection = {'id': 'dependency_' + str(index), 'source': 'overview_' + edge['source'],
                      'target': 'overview_' + edge['target'], 'semantic_edge': index,
                      'label': edge.get('label', ''), 'evidence_refs': ['dependency:' + str(index)]}
        if edge.get('display_label'):
            connection['display_label'] = edge['display_label']
        connection.update(deepcopy(edge_aliases.get(str(index), edge_aliases.get(index, {}))))
        connections.append(connection)
    return objects, connections


def validate_composition(composition, contract):
    """Check local design and source bindings before any host layout search."""
    objects, connections = _parts(composition, contract)
    for item in objects:
        item['bbox'] = [.05, .05 if item['panel'] == 'overview' else .5, .9, .4]
    scene = {'version': 1, 'width_in': contract['width_in'], 'height_in': contract['height_in'],
             'panels': [{'id': 'overview', 'role': 'overview', 'bbox': [0, 0, 1, .45]},
                        {'id': 'mechanism', 'role': 'mechanism', 'bbox': [0, .45, 1, .55]}],
             'objects': objects, 'connections': connections, 'annotations': []}
    if 'palette' in composition:
        scene['palette'] = deepcopy(composition['palette'])
    if 'caption_text' in composition:
        scene['caption_text'] = composition['caption_text']
    result = validate_scene(scene, contract)
    if contract['key_operation_id'] not in result['detailed_operations']:
        raise ValueError('Mechanism hero must substantively depict the supplied key transformation')
    return result


def _source_hierarchy(contract, chain):
    """Freeze real feedback edges once, preserving a supplied directed spine."""
    original = [node['id'] for node in contract['nodes']]
    position = {node: index for index, node in enumerate(original)}
    adjacency = {node: [] for node in original}
    for edge in contract['edges']:
        adjacency[edge['source']].append(edge['target'])
    indices, low, stack, active, components = {}, {}, [], set(), []
    def visit(node):
        indices[node] = low[node] = len(indices)
        stack.append(node); active.add(node)
        for target in adjacency[node]:
            if target not in indices:
                visit(target); low[node] = min(low[node], low[target])
            elif target in active:
                low[node] = min(low[node], indices[target])
        if low[node] == indices[node]:
            members = []
            while True:
                member = stack.pop(); active.remove(member); members.append(member)
                if member == node:
                    break
            components.append(members)
    for node in original:
        if node not in indices:
            visit(node)
    membership = {node: index for index, members in enumerate(components) for node in members}
    # Only the real main-chain constraints may change the stable order inside an
    # SCC. Removing backward edges from that order yields a DAG without treating
    # later physical layout choices as scientific feedback classification.
    local_position = {}
    for members in components:
        constraints = [(a, b) for a, b in zip(chain, chain[1:]) if a in members and b in members]
        order, _ = _dag_ranks(members, constraints, position)
        local_position.update({node: index for index, node in enumerate(order)})
    feedback = {index for index, edge in enumerate(contract['edges'])
                if membership[edge['source']] == membership[edge['target']]
                and local_position[edge['source']] >= local_position[edge['target']]}
    protected = set(zip(chain, chain[1:]))
    if any((contract['edges'][index]['source'], contract['edges'][index]['target']) in protected
           for index in feedback):
        raise ValueError('Overview chain conflicts with the source cycle hierarchy')
    forward = [(edge['source'], edge['target']) for index, edge in enumerate(contract['edges'])
               if index not in feedback]
    order, ranks = _dag_ranks(original, forward, position)
    return order, ranks, feedback


def _dag_ranks(nodes, links, priority):
    outgoing = {node: set() for node in nodes}; incoming = {node: set() for node in nodes}
    for source, target in links:
        if source == target:
            raise ValueError('Overview groups create an incompatible forward dependency cycle')
        outgoing[source].add(target); incoming[target].add(source)
    remaining = {node: len(incoming[node]) for node in nodes}
    ranks = dict.fromkeys(nodes, 0); ready = [node for node in nodes if not remaining[node]]; order = []
    while ready:
        ready.sort(key=lambda node: priority[node])
        node = ready.pop(0); order.append(node)
        for target in sorted(outgoing[node], key=lambda value: priority[value]):
            ranks[target] = max(ranks[target], ranks[node] + 1)
            remaining[target] -= 1
            if not remaining[target]:
                ready.append(target)
    if len(order) != len(nodes):
        raise ValueError('Overview groups create an incompatible forward dependency cycle; revise the source-ID grouping')
    return order, ranks


def _hierarchical_groups(composition, contract):
    supplied, chain = _overview_hints(composition, contract)
    order, _, feedback = _source_hierarchy(contract, chain)
    priority = {node: index for index, node in enumerate(order)}
    forward = [(edge['source'], edge['target']) for index, edge in enumerate(contract['edges']) if index not in feedback]
    if supplied is not None:
        variants = [deepcopy(supplied)]
    else:
        incoming = {node: set() for node in order}; outgoing = {node: set() for node in order}
        for source, target in forward:
            incoming[target].add(source); outgoing[source].add(target)
        variants = []
        # Compress only an unbranched real chain, retaining every native node.
        # Different caps are physical alternatives, never arbitrary node swaps.
        for limit in (3, 2, 1):
            used = set(); groups = []
            for node in order:
                if node in used:
                    continue
                members = [node]; used.add(node)
                while len(members) < limit and len(outgoing[members[-1]]) == 1:
                    target = next(iter(outgoing[members[-1]]))
                    if target in used or len(incoming[target]) != 1:
                        break
                    members.append(target); used.add(target)
                groups.append({'id': 'group_' + str(len(groups)), 'operation_ids': members,
                               'flow': 'TB' if composition.get('overview_position', 'top') == 'top' else 'LR'})
            if groups not in variants:
                variants.append(groups)
    result = []
    for groups in variants:
        membership = {node: group['id'] for group in groups for node in group['operation_ids']}
        macro_links = {(membership[a], membership[b]) for a, b in forward if membership[a] != membership[b]}
        group_priority = {group['id']: min(priority[node] for node in group['operation_ids']) for group in groups}
        _, ranks = _dag_ranks([group['id'] for group in groups], macro_links, group_priority)
        for group in groups:
            members = group['operation_ids']
            internal = [(a, b) for a, b in forward if a in members and b in members]
            # Within a semantic group, a real dependency determines order; only
            # independent peers use the model's stable supplied ordering.
            group['operation_ids'], _ = _dag_ranks(members, internal,
                                                  {node: index for index, node in enumerate(members)})
        layers = [[group['id'] for group in groups if ranks[group['id']] == rank]
                  for rank in range(max(ranks.values()) + 1)]
        parents = {group['id']: set() for group in groups}; children = deepcopy(parents)
        for source, target in macro_links:
            parents[target].add(source); children[source].add(target)
        # Barycenters may reorder independent peers within one rank only.
        for iteration in range(4):
            positions = {node: index for layer in layers for index, node in enumerate(layer)}
            for layer in (layers if iteration % 2 == 0 else reversed(layers)):
                neighbors = parents if iteration % 2 == 0 else children
                layer.sort(key=lambda node: (sum(positions[other] for other in neighbors[node]) / len(neighbors[node])
                                            if neighbors[node] else positions[node], group_priority[node]))
        result.append((groups, layers, membership, feedback))
    return result


def _overview_content(c, panel):
    x, y, w, h = [value * dimension for value, dimension in zip(panel['bbox'], (c.width, c.height, c.width, c.height))]
    pad = .08; title = panel.get('title') or ''
    lines = len(c.wrap(title, max(.05, w - 2 * pad), max(9., c.font)).splitlines())
    header = max(.23, lines * max(9., c.font) / 72 * 1.35 + .025) if title else 0.
    if header:
        panel['header_bbox'] = [(x + pad) / c.width, (y + pad) / c.height,
                                (w - 2 * pad) / c.width, header / c.height]
    else:
        panel.pop('header_bbox', None)
    return x + pad, y + pad + header + (.07 if header else 0), w - 2 * pad, h - 2 * pad - header - (.07 if header else 0)


def _physical_bbox(box, width, height):
    return [box[0] / width, box[1] / height, box[2] / width, box[3] / height]


def _overview_ports(edges, contract, groups, membership, feedback, axis):
    """Declare physical attachment sides before measuring their native spans."""
    by_group = {group['id']: group for group in groups}
    slots = {}
    for index, edge in edges.items():
        source, target = contract['edges'][index]['source'], contract['edges'][index]['target']
        same = membership[source] == membership[target]
        flow = by_group[membership[source]]['flow'] if same else axis
        edge['from_port'], edge['to_port'] = ('bottom', 'top') if flow == 'TB' else ('right', 'left')
        if not same:
            source_group = by_group[membership[source]]; target_group = by_group[membership[target]]
            if source_group['flow'] == axis and source != source_group['operation_ids'][-1]:
                edge['from_port'] = 'right' if axis == 'TB' else 'bottom'
            if target_group['flow'] == axis and target != target_group['operation_ids'][0]:
                edge['from_port'] = edge['to_port'] = 'left' if axis == 'TB' else 'top'
        if index in feedback:
            edge['from_port'], edge['to_port'] = ('right', 'left') if axis == 'LR' else ('right', 'right')
        for node, prefix, remote in ((source, 'from', target), (target, 'to', source)):
            slots.setdefault((node, edge[prefix + '_port']), []).append((index, prefix, remote))
    return slots


def _assign_port_offsets(edges, slots, boxes):
    for (node, side), endpoints in slots.items():
        # Native offsets are positive upward on a vertical side and rightward
        # on a horizontal side. Preserve the remote endpoints' physical order.
        horizontal_side = side in ('top', 'bottom')
        dimension = 0 if horizontal_side else 1
        endpoints.sort(key=lambda item: (boxes[item[2]][dimension] + boxes[item[2]][dimension + 2] / 2,
                                        item[0], item[1]))
        for position, (index, prefix, _) in enumerate(endpoints):
            offset = (position - (len(endpoints) - 1) / 2) * .065
            edges[index][prefix + '_port_offset_in'] = round(offset if horizontal_side else -offset, 6)


def _pack_hierarchy(scene, contract, structure, node_width, label_width, style=None):
    """Place measured native nodes and reserved edge-label corridors by rank."""
    from research.figures.scene import _Canvas, _measure_edge_label, _minimum_object_size, plt
    groups, layers, membership, feedback = structure
    c = _Canvas(scene, {**(style or {}), 'minimum_font_pt': contract['minimum_font_pt']})
    try:
        panel = scene['panels'][0]
        x, y, available_w, available_h = _overview_content(c, panel)
        axis = 'LR' if panel['bbox'][2] > panel['bbox'][3] else 'TB'
        # Panel shape is not a semantic choice: the explicit overview position
        # defines macro flow even when physical aspect ratios are unusual.
        axis = scene.pop('_overview_flow', axis)
        parallel = available_w if axis == 'LR' else available_h
        perpendicular = available_h if axis == 'LR' else available_w
        objects = {item['operation_id']: item for item in scene['objects'] if item['panel'] == 'overview'}
        edges = {edge['semantic_edge']: edge for edge in scene['connections']}
        port_slots = _overview_ports(edges, contract, groups, membership, feedback, axis)
        rank = {identifier: index for index, layer in enumerate(layers) for identifier in layer}
        locations, dimensions, internal_gaps = {}, {}, {}
        cross_labels = {index: [] for index in range(max(0, len(layers) - 1))}
        feedback_labels = []
        internal_labels = {}
        measurements = {}
        for index, edge in edges.items():
            text = edge.get('display_label', edge.get('label', '')) if edge.get('label_visible', True) else ''
            if not text:
                continue
            measurement = _measure_edge_label(c, text, label_width, c.minimum)
            if measurement['width_in'] > label_width + .001:
                raise _OverviewCapacityError('Visible dependency ' + str(index) + ' needs more unbreakable text width',
                    code='edge_label_width', dependency_index=index, allocated_width_in=label_width,
                    minimum_width_in=measurement['min_width_in'], wrapped_text=measurement['wrapped_text'],
                    capacity_ratio=measurement['min_width_in'] / label_width)
            measurements[index] = measurement
            source, target = contract['edges'][index]['source'], contract['edges'][index]['target']
            if index in feedback:
                feedback_labels.append(index)
            elif membership[source] == membership[target]:
                internal_labels[index] = measurement
            else:
                first, last = rank[membership[source]], rank[membership[target]]
                # Long-edge label dummies can use any intervening rank corridor.
                corridor = min(range(first, last), key=lambda value: (
                    sum(measurements[other]['height_in' if axis == 'LR' else 'width_in'] + .07
                        for other in cross_labels[value]), value))
                cross_labels[corridor].append(index)
        group_by_id = {group['id']: group for group in groups}
        for group in groups:
            members = group['operation_ids']; flow = group['flow']
            requirements = {node: _minimum_object_size(c, objects[node], node_width) for node in members}
            for node, needed in requirements.items():
                for side in ('left', 'right', 'top', 'bottom'):
                    count = len(port_slots.get((node, side), []))
                    if count:
                        field = 'minimum_height_in' if side in ('left', 'right') else 'minimum_width_in'
                        needed[field] = max(needed[field], .08 + (count - 1) * .065)
                if needed['minimum_width_in'] > node_width + .001:
                    raise _OverviewCapacityError('Overview operation ' + node + ' needs more native text width',
                        code='operation_width', operation_id=node, allocated_width_in=node_width,
                        minimum_width_in=needed['minimum_width_in'],
                        capacity_ratio=needed['minimum_width_in'] / node_width)
            gaps = [.10] * max(0, len(members) - 1)
            # Real visible labels allocate native physical space before nodes.
            for index, measurement in internal_labels.items():
                source = contract['edges'][index]['source']; target = contract['edges'][index]['target']
                if source not in members or target not in members:
                    continue
                slot = members.index(source)
                if slot >= len(gaps):
                    raise ValueError('Internal directed dependency contradicts its group hierarchy')
                gaps[slot] += measurement['height_in' if flow == 'TB' else 'width_in'] + .10
            cross_count = sum(1 for index, edge in enumerate(contract['edges']) if index not in feedback
                              and edge['source'] in members and edge['target'] in members
                              and members.index(edge['target']) - members.index(edge['source']) > 1)
            side = .08 + cross_count * .065
            if flow == 'TB':
                gw = max(node_width, max((m['width_in'] for index, m in internal_labels.items()
                                         if contract['edges'][index]['source'] in members), default=0.)) + 2 * side
                gh = sum(requirements[node]['minimum_height_in'] for node in members) + sum(gaps)
            else:
                gw = len(members) * node_width + sum(gaps)
                gh = max(requirements[node]['minimum_height_in'] for node in members) + 2 * side
                gh = max(gh, max((m['height_in'] + .16 for index, m in internal_labels.items()
                                 if contract['edges'][index]['source'] in members), default=0.))
            dimensions[group['id']] = (gw, gh); internal_gaps[group['id']] = (gaps, requirements, side)
        feedback_count = len(feedback)
        label_perp = max((measurements[index]['height_in' if axis == 'LR' else 'width_in']
                          for index in feedback_labels), default=0.)
        feedback_band = (.07 + max(0, feedback_count - 1) * .065 + label_perp + (.06 if feedback_labels else 0)) if feedback_count else 0.
        if feedback_labels:
            extent = sum(measurements[index]['width_in' if axis == 'LR' else 'height_in'] + .08 for index in feedback_labels)
            if extent > parallel + .001:
                raise ValueError('Visible feedback labels exceed the measured outer-track capacity')
        perpendicular -= feedback_band
        if perpendicular <= .10:
            raise ValueError('Feedback text and native tracks leave no overview body capacity')
        layer_parallel = [max(dimensions[node][0 if axis == 'LR' else 1] for node in layer) for layer in layers]
        layer_perp = [sum(dimensions[node][1 if axis == 'LR' else 0] for node in layer) + .12 * (len(layer) - 1)
                      for layer in layers]
        gaps = []
        for index in range(len(layers) - 1):
            labels = cross_labels[index]
            if labels:
                gap_parallel = max(measurements[edge]['width_in' if axis == 'LR' else 'height_in'] for edge in labels)
                gaps.append(gap_parallel + .20 + len(labels) * .045)
                label_extent = sum(measurements[edge]['height_in' if axis == 'LR' else 'width_in'] + .07 for edge in labels) - .07
                layer_perp.append(label_extent)
            else:
                gaps.append(.14)
        required_parallel = sum(layer_parallel) + sum(gaps)
        required_perp = max(layer_perp, default=0.)
        if required_parallel > parallel + .001 or required_perp > perpendicular + .001:
            width_coefficients = {group['id']: len(group['operation_ids']) if group['flow'] == 'LR' else 1
                                  for group in groups}
            width_coefficient = (sum(max(width_coefficients[node] for node in layer) for layer in layers)
                                 if axis == 'LR' else max(sum(width_coefficients[node] for node in layer) for layer in layers))
            raise _OverviewCapacityError('Directed overview exceeds its measured node, label and feedback-track capacity',
                code='overview_capacity', macro_flow=axis,
                available_body_width_in=available_w, available_body_height_in=available_h,
                required_along_flow_in=required_parallel, available_along_flow_in=parallel,
                required_across_flow_in=required_perp + feedback_band,
                available_across_flow_in=perpendicular + feedback_band,
                node_width_in=node_width, node_width_coefficient=width_coefficient,
                capacity_ratio=max(required_parallel / parallel, (required_perp + feedback_band) / (perpendicular + feedback_band)))
        # Center peers only within their true dependency rank.
        cursor = (x if axis == 'LR' else y) + (parallel - required_parallel) / 2
        corridor_positions = {}
        for layer_index, layer in enumerate(layers):
            peer = (y if axis == 'LR' else x) + (perpendicular - layer_perp[layer_index]) / 2
            for identifier in layer:
                gw, gh = dimensions[identifier]
                locations[identifier] = (cursor, peer, gw, gh) if axis == 'LR' else (peer, cursor, gw, gh)
                peer += (gh if axis == 'LR' else gw) + .12
            cursor += layer_parallel[layer_index]
            if layer_index < len(gaps):
                corridor_positions[layer_index] = cursor
                cursor += gaps[layer_index]
        boxes = {}
        for group in groups:
            gx, gy, gw, gh = locations[group['id']]; flow = group['flow']
            gaps_local, requirements, side = internal_gaps[group['id']]
            cursor = gy if flow == 'TB' else gx
            for index, node in enumerate(group['operation_ids']):
                h = requirements[node]['minimum_height_in']
                box = (gx + (gw - node_width) / 2, cursor, node_width, h) if flow == 'TB' else (
                    cursor, gy + (gh - h) / 2, node_width, h)
                objects[node]['bbox'] = _physical_bbox(box, c.width, c.height); boxes[node] = box
                cursor += (h if flow == 'TB' else node_width) + (gaps_local[index] if index < len(gaps_local) else 0.)
        def waypoint(edge, px, py):
            edge.setdefault('waypoints', []).append([px / c.width, py / c.height])
        _assign_port_offsets(edges, port_slots, boxes)
        for corridor, labels in cross_labels.items():
            offset = (y if axis == 'LR' else x) + (perpendicular - sum(
                measurements[index]['height_in' if axis == 'LR' else 'width_in'] + .07 for index in labels) + .07) / 2
            origin = corridor_positions[corridor]
            for lane, index in enumerate(labels):
                m = measurements[index]; edge = edges[index]
                reserve = .07 + len(labels) * .045
                if axis == 'LR':
                    label_box = (origin + reserve, offset, m['width_in'], m['height_in'])
                    waypoint(edge, origin + .035 + lane * .045, offset + m['height_in'] / 2)
                    offset += m['height_in'] + .07
                else:
                    label_box = (offset, origin + reserve, m['width_in'], m['height_in'])
                    waypoint(edge, offset + m['width_in'] / 2, origin + .035 + lane * .045)
                    offset += m['width_in'] + .07
                edge['label_bbox'] = _physical_bbox(label_box, c.width, c.height)
        internal_offsets = {}
        for index, m in internal_labels.items():
            source = contract['edges'][index]['source']; group_id = membership[source]
            group = group_by_id[group_id]; gx, gy, gw, gh = locations[group_id]
            sx, sy, sw, sh = boxes[source]; edge = edges[index]
            slot = (group_id, source); offset = internal_offsets.get(slot, 0.)
            if group['flow'] == 'TB':
                label_box = (gx + .07, sy + sh + .075 + offset, m['width_in'], m['height_in'])
                waypoint(edge, gx + .035, label_box[1] + m['height_in'] / 2)
                internal_offsets[slot] = offset + m['height_in'] + .10
            else:
                label_box = (sx + sw + .075 + offset, gy + .07, m['width_in'], m['height_in'])
                waypoint(edge, label_box[0] + m['width_in'] / 2, gy + .035)
                internal_offsets[slot] = offset + m['width_in'] + .10
            edge['label_bbox'] = _physical_bbox(label_box, c.width, c.height)
        feedback_boxes = []
        for lane, index in enumerate(sorted(feedback)):
            source, target = contract['edges'][index]['source'], contract['edges'][index]['target']
            sx, sy, sw, sh = boxes[source]; tx, ty, tw, th = boxes[target]
            sg = locations[membership[source]]; tg = locations[membership[target]]; edge = edges[index]
            if axis == 'LR':
                track = y + available_h - .035 - lane * .065
                edge['from_port'], edge['to_port'] = 'right', 'left'
                waypoint(edge, sg[0] + sg[2] + .035, track)
                waypoint(edge, tg[0] - .035, track)
                if index in measurements:
                    m = measurements[index]
                    start, end = sorted((sg[0] + sg[2] + .035, tg[0] - .035))
                    label_box = (max(x, min(x + available_w - m['width_in'],
                        (start + end - m['width_in']) / 2)), track - .04 - m['height_in'], m['width_in'], m['height_in'])
            else:
                track = x + available_w - .035 - lane * .065
                edge['from_port'], edge['to_port'] = 'right', 'right'
                waypoint(edge, track, sy + sh / 2)
                waypoint(edge, track, ty + th / 2)
                if index in measurements:
                    m = measurements[index]
                    start, end = sorted((sy + sh / 2, ty + th / 2))
                    label_box = (track - .04 - m['width_in'], max(y, min(y + available_h - m['height_in'],
                        (start + end - m['height_in']) / 2)), m['width_in'], m['height_in'])
            if index in measurements:
                # Keep each return label next to its own actual outer segment;
                # independent returns cannot stack their typography at one spot.
                dimension = 0 if axis == 'LR' else 1
                span = m['width_in' if axis == 'LR' else 'height_in']
                if span > end - start - .04:
                    raise _OverviewCapacityError('Visible feedback label cannot fit beside its actual return segment',
                        code='feedback_label_span', dependency_index=index, label_length_in=span,
                        available_track_length_in=max(0., end - start - .04),
                        capacity_ratio=span / max(.001, end - start - .04))
                options = [label_box]
                for fraction in (.25, .75, .1, .9):
                    proposed = list(label_box)
                    proposed[dimension] = max(x if axis == 'LR' else y, min(
                        end - span - .02,
                        max(start + .02, start + fraction * (end - start) - span / 2)))
                    options.append(tuple(proposed))
                from research.figures.scene import _intersects
                label_box = next((candidate for candidate in options if all(
                    not _intersects(candidate, other, .007) for other in feedback_boxes)), None)
                if label_box is None:
                    raise ValueError('Visible feedback labels cannot occupy distinct native outer-track slots')
                feedback_boxes.append(label_box)
                edge['label_bbox'] = _physical_bbox(label_box, c.width, c.height)
        # Route true spine before branches and real returns. Canonical edge IDs
        # and semantic indices remain frozen regardless of paint order.
        chain = scene.pop('_overview_chain', [])
        protected = set(zip(chain, chain[1:]))
        scene['connections'].sort(key=lambda edge: (0 if (contract['edges'][edge['semantic_edge']]['source'],
            contract['edges'][edge['semantic_edge']]['target']) in protected else 2 if edge['semantic_edge'] in feedback else 1,
            abs(rank[membership[contract['edges'][edge['semantic_edge']]['target']]] -
                rank[membership[contract['edges'][edge['semantic_edge']]['source']]]), edge['semantic_edge']))
        return scene
    finally:
        plt.close(c.fig)


def _routing_score(scene, contract, style):
    """Measure native object text, headers, wires and labels without exports."""
    from research.figures.scene import (_Canvas, _box, _connections, _label_region,
                                       _measure, _render_object, plt)
    c = _Canvas(scene, {**(style or {}), 'minimum_font_pt': contract['minimum_font_pt']})
    try:
        boxes = {item['id']: _box(item['bbox'], c.width, c.height) for item in scene['objects']}
        reserved = [_box(panel['header_bbox'], c.width, c.height)
                    for panel in scene['panels'] if panel.get('header_bbox')]
        if scene.get('title'):
            title_box = (.08, c.height - .30, c.width - .16, .24)
            reserved.append(title_box)
            c.text(scene['title'], title_box, 'figure', 'title', font=max(c.font, 11), weight='bold', align='left')
        for panel in scene['panels']:
            if panel.get('title'):
                c.text(panel['title'], _box(panel['header_bbox'], c.width, c.height), panel['id'],
                       'panel-title', font=max(9., c.font), weight='bold', align='left')
        for index, item in enumerate(scene['objects']):
            if item['kind'] != 'asset':
                _render_object(c, item, index, {})
            else:
                # Asset pixels are generated separately; their native label has
                # the same footprint regardless of the isolated illustration.
                _, label = _label_region(c, boxes[item['id']], item.get('display_label', item['label']), item['id'])
                if label:
                    c.text(item.get('display_label', item['label']), label, item['id'], font=c.font)
        records = _connections(c, scene, boxes, reserved)
        _measure(c, boxes)
        return (len(c.issues), sum(item['route_cost'] for item in records)), records, deepcopy(c.issues)
    finally:
        plt.close(c.fig)


def _semantic_route_cost(scene, routes, structure, position):
    """Prefer clear forward paths and distinct native labels before panel area."""
    objects = {item['id']: item for item in scene['objects']}
    groups, _, membership, feedback = structure
    flows = {group['id']: group['flow'] for group in groups}
    cost = 0.
    for route in routes:
        points = route['path_in']; index = route['semantic_edge']
        axes = [abs(first[0] - last[0]) < 1e-7 for first, last in zip(points, points[1:])]
        bends = sum(first != last for first, last in zip(axes, axes[1:]))
        cost += route['route_cost'] + .20 * bends
        if index in feedback:
            continue
        source = objects[route['source']]['operation_id']; target = objects[route['target']]['operation_id']
        flow = flows[membership[source]] if membership[source] == membership[target] else ('LR' if position == 'top' else 'TB')
        horizontal = flow == 'LR'
        for first, last in zip(points, points[1:]):
            reverse = first[0] - last[0] if horizontal else last[1] - first[1]
            cost += 2 * max(0., reverse)
    return cost


def _panel_geometry(contract, position, has_title, overview_fraction=.4):
    width, height = contract['width_in'], contract['height_in']
    margin, separation = .08, .12
    top = .38 if has_title else margin
    available_width, available_height = width - 2 * margin, height - top - margin
    if position == 'top':
        overview = [margin / width, top / height, available_width / width,
                    (available_height - separation) * overview_fraction / height]
        hero_y = top + overview[3] * height + separation
        mechanism = [margin / width, hero_y / height, available_width / width,
                     (height - margin - hero_y) / height]
    else:
        overview = [margin / width, top / height, (available_width - separation) * overview_fraction / width,
                    available_height / height]
        hero_x = margin + overview[2] * width + separation
        mechanism = [hero_x / width, top / height, (width - margin - hero_x) / width,
                     available_height / height]
    return overview, mechanism


def _hero_body_geometry(scene, contract, style=None):
    """Measure the host's full hero frame before attempting to fit its content."""
    from research.figures.scene import _Canvas, _box, _label_region, layout_scene, plt
    hero = next(item for item in scene['objects'] if item['panel'] == 'mechanism')
    if hero.get('bbox'):
        bounds = hero['bbox']
    else:
        # Probe only host geometry with a minimal unlabelled operator. Scientific
        # content and canonical labels stay in the original, unmodified scene.
        probe = deepcopy(scene)
        panel = next(panel for panel in probe['panels'] if panel['id'] == 'mechanism')
        panel['layout'] = {'flow': 'horizontal', 'object_ids': [hero['id']], 'padding_in': .08}
        probe['panels'] = [panel]
        probe['objects'] = [{'id': hero['id'], 'panel': 'mechanism', 'kind': 'operator',
                             'label': '', 'params': {'shape': 'capsule'}}]
        probe['connections'] = []; probe['annotations'] = []
        placed = layout_scene(probe, contract, style)['objects'][0]
        bounds = placed['bbox']
        bounds[3] = panel['bbox'][1] + panel['bbox'][3] - .08 / contract['height_in'] - bounds[1]
    c = _Canvas(scene, {**(style or {}), 'minimum_font_pt': contract['minimum_font_pt']})
    try:
        frame = _box(bounds, c.width, c.height)
        body, label = _label_region(c, frame, hero.get('display_label', hero['label']), hero['id'])
        return {'object_width_in': frame[2], 'object_height_in': frame[3],
                'available_body_width_in': body[2], 'available_body_height_in': body[3],
                'operation_label_height_in': label[3] if label else 0.}
    finally:
        plt.close(c.fig)


def _local_fit_feedback(scene, contract, style=None):
    """Identify tight local text allocations using the native renderer's wrap."""
    from research.figures.scene import _Canvas, _text_width, _wrap_chunks, plt
    geometry = _hero_body_geometry(scene, contract, style)
    hero = next(item for item in scene['objects'] if item['panel'] == 'mechanism')
    width, height = geometry['available_body_width_in'], geometry['available_body_height_in']
    c = _Canvas(scene, {**(style or {}), 'minimum_font_pt': contract['minimum_font_pt']})
    constraints = []
    try:
        for index, primitive in enumerate(hero.get('params', {}).get('primitives', [])):
            if primitive['kind'] != 'text':
                continue
            bbox = primitive['bbox']; text = primitive['text']
            font = max(c.minimum, float(primitive.get('font_pt', c.minimum)))
            available_width, available_height = bbox[2] * width, bbox[3] * height
            wrapped = c.wrap(text, max(.015, available_width), font)
            lines = max(1, len(wrapped.splitlines()))
            required_height = lines * font / 72 * 1.35 + .025
            # Math and explicit line breaks stay unwrapped in the native renderer.
            chunks = text.splitlines() if '$' in text or '\n' in text else [part for part, _ in _wrap_chunks(text)]
            minimum_width = max((_text_width(c, part, font) for part in chunks), default=0.) + .035
            constraints.append({
                'primitive_id': primitive.get('id'), 'primitive_index': index,
                'bbox': deepcopy(bbox), 'font_pt': font, 'wrapped_text': wrapped, 'wrapped_lines': lines,
                'available_text_width_in': available_width, 'available_text_height_in': available_height,
                'required_text_height_in': required_height, 'minimum_text_width_in': minimum_width,
                'minimum_bbox_width_fraction': minimum_width / width,
                'minimum_bbox_height_fraction': required_height / height,
                'capacity_ratio': max(required_height / available_height, minimum_width / available_width),
            })
    finally:
        plt.close(c.fig)
    constraints.sort(key=lambda item: (-item['capacity_ratio'], item['primitive_index']))
    def rounded(record):
        return {key: round(value, 6) if isinstance(value, float) else value for key, value in record.items()}
    return {**rounded(geometry), 'hero_id': hero['id'],
            'tightest_primitive': rounded(constraints[0]) if constraints else None,
            'tight_text_allocations': [rounded(item) for item in constraints[:6]],
            'repair': 'Keep the supplied science, text, font and canonical dependencies. Increase the identified '
                      'local text boxes, reduce wrapping through wider boxes, or revise the layout; do not shrink text.'}


def layout_geometry_guidance(contract, style=None):
    """Provide conservative host-owned drawable sizes before local authoring."""
    key = contract['key_operation_id']
    node = next(node for node in contract['nodes'] if node['id'] == key)
    sizes = {}; fractions = {}
    for position in ('top', 'left'):
        sizes[position] = {}
        for has_title in (False, True):
            _, bounds = _panel_geometry(contract, position, has_title, max(_OVERVIEW_FRACTIONS))
            scene = {'version': 1, 'width_in': contract['width_in'], 'height_in': contract['height_in'],
                     'panels': [{'id': 'mechanism', 'role': 'mechanism', 'title': 'Key mechanism', 'bbox': bounds}],
                     'objects': [{'id': 'hero_' + key, 'panel': 'mechanism', 'kind': 'custom',
                                  'label': node['label'], 'params': {}}]}
            if has_title:
                scene['title'] = 'Figure title'
            measured = _hero_body_geometry(scene, contract, style)
            sizes[position]['with_figure_title' if has_title else 'without_figure_title'] = {
                key: round(value, 6) for key, value in measured.items()}
    for fraction in _OVERVIEW_FRACTIONS:
        fraction_sizes = {}
        for position in ('top', 'left'):
            fraction_sizes[position] = {}
            for has_title in (False, True):
                _, bounds = _panel_geometry(contract, position, has_title, fraction)
                probe = {'version': 1, 'width_in': contract['width_in'], 'height_in': contract['height_in'],
                         'panels': [{'id': 'mechanism', 'role': 'mechanism', 'title': 'Key mechanism', 'bbox': bounds}],
                         'objects': [{'id': 'hero_' + key, 'panel': 'mechanism', 'kind': 'custom',
                                      'label': node['label'], 'params': {}}]}
                if has_title:
                    probe['title'] = 'Figure title'
                fraction_sizes[position]['with_figure_title' if has_title else 'without_figure_title'] = {
                    field: round(value, 6) for field, value in _hero_body_geometry(probe, contract, style).items()}
        fractions[str(fraction)] = fraction_sizes
    fonts = sorted({float(contract['minimum_font_pt']), max(9., contract['minimum_font_pt']),
                    max(10., contract['minimum_font_pt'])})
    return {'hero_body': sizes, 'hero_body_by_overview_fraction': fractions,
            'overview_fraction_candidates': list(_OVERVIEW_FRACTIONS),
            'minimum_font_pt': contract['minimum_font_pt'],
            'required_text_height_in': {str(font): {str(lines): round(lines * font / 72 * 1.35 + .025, 6)
                                        for lines in (1, 2, 3, 4)} for font in fonts},
            'assumptions': 'hero_body conservatively uses the largest 50% overview; per-fraction sizes are also supplied. '
                           'Includes a short Key mechanism panel header and the full canonical operation label. '
                           'Short faithful operation aliases or omitted headers can free space; longer headers '
                           'use more space. Local text bbox width and height multiply the drawable body dimensions, '
                           'not the page or panel. Text heights include native layout leading and padding.'}


def compile_composition(composition, contract, style=None):
    """Compile local source-grounded content into a measured directed overview.

    Scientific grouping preserves every canonical operation and dependency. The
    host searches 25–50% overview capacity with fixed native fonts; only peers
    in one dependency rank may change relative physical order.
    """
    from research.figures.scene import layout_scene, measure_scene_layout
    validate_composition(composition, contract)
    objects, connections = _parts(composition, contract)
    structures = _hierarchical_groups(composition, contract)
    width, height = contract['width_in'], contract['height_in']
    position = composition.get('overview_position', 'top')
    candidates = []; failures = []; hero_failures = []
    for fraction in _OVERVIEW_FRACTIONS:
        overview, mechanism = _panel_geometry(contract, position, bool(composition.get('title')), fraction)
        panels = [{'id': 'overview', 'role': 'overview',
                   'title': composition.get('overview_title', 'Method overview'), 'bbox': overview},
                  {'id': 'mechanism', 'role': 'mechanism',
                   'title': composition.get('hero_title', 'Key mechanism'), 'bbox': mechanism}]
        scene = {'version': 1, 'width_in': width, 'height_in': height, 'panels': panels,
                 'objects': deepcopy(objects), 'connections': deepcopy(connections), 'annotations': []}
        for field in ('title', 'palette', 'caption_text'):
            if field in composition:
                scene[field] = deepcopy(composition[field])
        panels[1]['layout'] = {'flow': 'horizontal', 'object_ids': [objects[-1]['id']],
                              'padding_in': .08, 'gap_in': 0, 'routing_gutter_in': 0}
        try:
            packed = layout_scene(scene, contract, style)
        except ValueError as exc:
            feedback = _local_fit_feedback(scene, contract, style)
            feedback['overview_fraction'] = fraction
            hero_failures.append((str(exc), feedback)); continue
        hero = packed['objects'][-1]
        hero['bbox'][3] = mechanism[1] + mechanism[3] - .08 / height - hero['bbox'][1]
        packed['panels'][1].pop('layout')
        needed = measure_scene_layout(packed, contract, style)[hero['id']]
        if (needed['minimum_width_in'] > hero['bbox'][2] * width + .001
                or needed['minimum_height_in'] > hero['bbox'][3] * height + .001):
            feedback = _local_fit_feedback(packed, contract, style)
            feedback['overview_fraction'] = fraction
            hero_failures.append(('Mechanism hero cannot fit its local native content at the contracted minimum font', feedback))
            continue
        visible = any(edge.get('label_visible', True) and edge.get('display_label', edge.get('label'))
                      for edge in connections)
        label_widths = (.85, .60, 1.15, 1.5) if visible else (.85,)
        found_zero = False
        for structure in structures:
            widths = [.85, 1.10, .65, 1.35, 1.65, 2.1]
            for node_width in widths:
                for label_width in label_widths:
                    alternative = deepcopy(packed)
                    alternative['_overview_flow'] = 'LR' if position == 'top' else 'TB'
                    alternative['_overview_chain'] = composition.get('overview_chain', [])
                    try:
                        alternative = _pack_hierarchy(alternative, contract, structure, node_width, label_width, style)
                    except ValueError as exc:
                        native = getattr(exc, 'feedback', {'detail': str(exc)})
                        failures.append({'overview_fraction': fraction, **native})
                        if native.get('code') == 'overview_capacity' and len(widths) < 10:
                            axis = native['macro_flow']
                            needed = native['required_along_flow_in' if axis == 'LR' else 'required_across_flow_in']
                            available = native['available_along_flow_in' if axis == 'LR' else 'available_across_flow_in']
                            if 0 < needed - available < .25:
                                adjusted = round(node_width - (needed - available) / native['node_width_coefficient'] - .008, 4)
                                if adjusted >= .45 and all(abs(adjusted - other) > .005 for other in widths):
                                    # Re-measure the full glyph and all wrapped
                                    # text at this actual width; no text shrinks.
                                    widths.append(adjusted)
                        continue
                    score, routes, issues = _routing_score(alternative, contract, style)
                    semantic_cost = _semantic_route_cost(alternative, routes, structure, position)
                    candidates.append(((score[0], semantic_cost), fraction, alternative, routes, issues))
                    if score[0] == 0:
                        found_zero = True
                        break
                if found_zero:
                    break
            if found_zero:
                break
    if not candidates:
        if hero_failures and not failures:
            message, feedback = min(hero_failures, key=lambda item: (
                item[1]['tightest_primitive']['capacity_ratio'] if item[1]['tightest_primitive'] else math.inf,
                item[1]['overview_fraction']))
            feedback['attempted_overview_fractions'] = list(_OVERVIEW_FRACTIONS)
            raise FigureCompositionCapacityError(message, feedback)
        closest = sorted(failures, key=lambda item: (item.get('capacity_ratio', math.inf), item['overview_fraction']))
        unique = {json.dumps(item, sort_keys=True): item for item in closest}
        feedback = {'attempted_overview_fractions': list(_OVERVIEW_FRACTIONS),
                    'overview_position': position, 'overview_fit_causes': list(unique.values())[:8],
                    'repair': 'Use source-grounded semantic groups, shorter faithful operation/edge aliases, '
                              'or another overview position. Explicitly hide only scientifically redundant '
                              'edge typography with a reason. Keep every real node, arrow and native font.'}
        raise FigureCompositionCapacityError('Canonical directed overview cannot fit at the contracted minimum font', feedback)
    _, _, compiled, _, _ = min(candidates, key=lambda item: (item[0][0], round(item[0][1], 4), item[1]))
    validate_scene(compiled, contract)
    return compiled
