"""Select explicit task execution isolation while retaining existing local work."""
from research.agents.processes import ManagedProcesses


def process_manager(workspace, config=None):
    backend = (config or {}).get('execution_backend', 'local')
    if backend == 'container':
        from runners.container import ContainerProcesses
        return ContainerProcesses(workspace, (config or {}).get('container', {}))
    if backend != 'local':
        raise ValueError('execution_backend must be local or container')
    return ManagedProcesses(workspace)
