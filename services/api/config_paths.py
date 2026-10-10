from pathlib import Path


def default_database_url(data_dir: Path) -> str:
    return 'sqlite:///' + str(data_dir / 'forest.db')


def resolve_database_url(configured_url: str, explicitly_configured: bool, data_dir: Path) -> str:
    return configured_url if explicitly_configured else default_database_url(data_dir)
