"""Map authored empirical statements to their actual supplied evidence."""
from research.paper.evidence import TOKEN


def draft_bindings(draft, evidence):
    metrics = {row['id']: row for row in evidence.get('metrics', [])}
    figures = {row['id']: row for row in evidence.get('figures', [])}
    passages = {row['id']: source['id'] for source in evidence.get('sources', []) for row in source.get('passages', [])}
    bindings = []

    def bind(kind, identifier, pointer, **extra):
        binding = {'source_kind': kind, 'source_id': identifier, 'target_path': pointer, **extra}
        if binding not in bindings: bindings.append(binding)

    def metric(key, pointer):
        row = metrics[key]
        bind('run', row['run_id'], pointer, metric_pointer=row['pointer'], metric_id=key)
        for reference in row.get('source_refs', []):
            if reference in metrics:
                source = metrics[reference]
                bind('run', source['run_id'], pointer, metric_pointer=source['pointer'], metric_id=reference)

    def visit(value, pointer=''):
        if isinstance(value, str):
            for kind, key in TOKEN.findall(value):
                if kind == 'metric' and key in metrics: metric(key, pointer)
                elif kind == 'source': bind('source', key, pointer)
                elif kind == 'passage' and key in passages: bind('source', passages[key], pointer, passage_id=key)
        elif isinstance(value, list):
            for index, row in enumerate(value): visit(row, pointer+'/'+str(index))
        elif isinstance(value, dict):
            if value.get('type') == 'figure' and value.get('figure_id') in figures:
                figure = figures[value['figure_id']]
                bind('figure', figure['id'], pointer)
                for run_id in figure.get('source_run_ids', [figure['run_id']]): bind('run', run_id, pointer)
            for key, row in value.items():
                path = pointer+'/'+str(key).replace('~', '~0').replace('/', '~1')
                if key == 'claim_ids' and isinstance(row, list):
                    for identifier in row: bind('claim', identifier, path)
                else: visit(row, path)
    visit(draft)
    return bindings


def bind_source_files(bindings, runs, root):
    """Convert producer metric paths into project-relative editable file scope."""
    from pathlib import Path
    files = {row['id']: (Path(row['directory'])/row['metrics_file']).relative_to(root).as_posix() for row in runs}
    result = list(bindings)
    for binding in bindings:
        if binding['source_kind'] == 'run' and binding['source_id'] in files:
            result.append({**binding, 'source_kind': 'file', 'source_id': files[binding['source_id']],
                           'source_run_id': binding['source_id'], 'artifact_path': files[binding['source_id']]})
    return result
