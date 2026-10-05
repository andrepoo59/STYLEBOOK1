import os
import sys

import pytest

os.environ.setdefault("SUPABASE_URL", "https://prueba.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.firma-de-prueba")
os.environ["FLASK_SECRET_KEY"] = "clave-de-pruebas"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tests.fake_supabase import FakeDB, FakeClient  # noqa: E402


@pytest.fixture()
def db():
    return FakeDB()


@pytest.fixture()
def app(db, monkeypatch):
    import supabase_client, utilidades
    import blueprints.auth as auth, blueprints.profesionales as prof
    import blueprints.servicios as serv, blueprints.citas as citas
    import app as modulo_app

    falso = FakeClient(db)
    como_usuario = lambda access, refresh: FakeClient(db)
    for modulo, nombre, valor in [
        (supabase_client, "sb", falso), (auth, "sb", falso), (prof, "sb", falso),
        (serv, "sb", falso), (citas, "sb", falso),
        (utilidades, "sb_como_usuario", como_usuario), (auth, "sb_como_usuario", como_usuario),
    ]:
        monkeypatch.setattr(modulo, nombre, valor)
    modulo_app.app.config.update(TESTING=True)
    return modulo_app.app


@pytest.fixture()
def cliente_http(app):
    return app.test_client()
