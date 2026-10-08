"""Seed the pre-intervention v3 schema for the concurrent-upgrade test.

The fixture creates all v3 tables but seeds only schema versions and one retained
project. It stays in the FOREST checkout so local runs and standard CI use the
same legacy database without depending on a sibling repository checkout.
"""
from services.api.db import Base, Migration, Project, Session, engine
from services.observation import models  # noqa: F401 - register schema-v3 tables.


# Schema v3 predates the intervention models, which are registered by the
# current migrator after this fixture runs. The observation import mirrors the
# legacy migrator's model registration without requiring a second checkout.
Base.metadata.create_all(engine)
with Session.begin() as session:
    session.add_all(Migration(version=version) for version in (1, 2, 3))
    session.add(Project(
        id='legacy-retained-project',
        name='Retained old schema',
        goal='Preserve actual stored data',
    ))
