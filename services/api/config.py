from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from .config_paths import resolve_database_url

ROOT = Path(__file__).resolve().parents[2]
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='FOREST_', env_file=ROOT / '.env', extra='ignore')
    database_url: str = 'sqlite:///' + str(ROOT / 'var' / 'forest.db')
    data_dir: Path = ROOT / 'var'
    host: str = '127.0.0.1'
    port: int = 8000
    ollama_url: str = 'http://127.0.0.1:11434'
    model: str = ''
    owner_token: str = ''
    worker_concurrency: int = 2
    max_upload_mb: int = 100
settings = Settings()
if not settings.data_dir.is_absolute():
    settings.data_dir = (ROOT / settings.data_dir).resolve()
else:
    # macOS /var aliases and configured symlinks must use the same root as
    # safe_path() when returning project-relative file and artifact locators.
    settings.data_dir = settings.data_dir.resolve()
settings.database_url = resolve_database_url(
    settings.database_url,
    'database_url' in settings.model_fields_set,
    settings.data_dir,
)
settings.data_dir.mkdir(parents=True, exist_ok=True)
(settings.data_dir / 'projects').mkdir(exist_ok=True)
