"""Editable verification bindings participate in real kernel operations."""
import pytest

from research.kernel import GraphCommandService, GraphError, ImpactAnalyzer, execution_edges, topological_order


def route():
    nodes = [dict(id=n, project_id='project', branch_id='main', type=kind, title=n,
                  revision=0, instructions=n, config=config, inputs=inputs, outputs=[], position={'x': 0, 'y': 0})
             for n, kind, config, inputs in [
                 ('producer', 'experiment', {'kind': 'experiment'}, []),
                 ('verifier', 'verification', {'kind': 'verification', 'verification': {
                     'producer_node_id': 'producer', 'checks': []}}, []),
                 ('consumer', 'analysis', {'required_verification': ['verifier']}, [
                     {'node_id': 'producer', 'path': 'metrics.json', 'verification_node_id': 'verifier'}]),
             ]]
    return {'project_id': 'project', 'revision': 0, 'nodes': nodes, 'edges': [],
            'branches': [{'id': 'main', 'name': 'Main', 'workspace': '.', 'status': 'active', 'is_main': True}]}


def apply(graph, root, operation, targets=None, **params):
    return GraphCommandService(graph, root).apply({
        'expected_revision': graph['revision'], 'operation': operation, 'targets': targets or [], 'params': params})


def test_implicit_verification_edges_are_ordered_and_reject_cycles(tmp_path):
    graph = route()
    assert topological_order(graph) == ['producer', 'verifier', 'consumer']
    assert len(execution_edges(graph)) == 3
    with pytest.raises(GraphError, match='acyclic'):
        apply(graph, tmp_path, 'edit_node', ['producer'], config={'required_verification': ['consumer']})


def test_contract_changes_invalidate_only_relevant_downstream_nodes(tmp_path):
    graph = route()
    result = apply(graph, tmp_path, 'edit_node', ['verifier'], config={'verification': {
        'checks': [{'id': 'score', 'kind': 'numeric_compare', 'source': 'metrics.json', 'repeat': 'metrics.json'}]}})
    assert set(result['impact']['rerun_nodes']) == {'verifier', 'consumer'}
    assert 'producer' not in result['impact']['affected_nodes']
    layout = apply(result['graph'], tmp_path, 'edit_node', ['verifier'], position={'x': 50, 'y': 80})
    assert layout['impact']['rerun_nodes'] == []
    assert next(n for n in layout['graph']['nodes'] if n['id'] == 'verifier')['revision'] == 1


def test_fork_remaps_verification_and_preserves_editable_source(tmp_path):
    (tmp_path/'method.py').write_text('def score(x): return x*x\n')
    graph = route()
    graph['nodes'][0]['verification_status'] = 'accepted'
    result = apply(graph, tmp_path, 'fork_branch', ['producer'], include_descendants=True,
                   copy_policy={'code': True, 'results': False}, name='Changed method')
    cloned = [n for n in result['graph']['nodes'] if n.get('forked_from')]
    assert len(cloned) == 3
    mapping = {n['forked_from']: n for n in cloned}
    producer, verifier, consumer = (mapping[k] for k in ('producer', 'verifier', 'consumer'))
    assert verifier['config']['verification']['producer_node_id'] == producer['id']
    assert consumer['config']['required_verification'] == [verifier['id']]
    assert consumer['inputs'][0]['verification_node_id'] == verifier['id']
    assert 'verification_status' not in producer and producer['results_current'] is False
    branch = next(b for b in result['graph']['branches'] if b['id'] == producer['branch_id'])
    source = tmp_path/branch['workspace']/'method.py'
    source.write_text('def score(x): return x*x + 1\n')
    assert (tmp_path/'method.py').read_text() == 'def score(x): return x*x\n'
    undo = apply(result['graph'], tmp_path, 'undo')
    assert len(undo['graph']['nodes']) == 3
    assert source.read_text().endswith('+ 1\n')
