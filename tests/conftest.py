import pytest
from .fixtures.generate import fixture


@pytest.fixture
def source(tmp_path):
    return fixture(tmp_path / "csv")


@pytest.fixture(autouse=True)
def sem_rede_da_aneel(monkeypatch):
    """Nenhum teste baixa a base pública da ANEEL; os que precisam simulam o portal."""
    from app.services import aneel_solar

    def offline(*_args, **_kwargs):
        raise OSError("Sem rede durante os testes")
    monkeypatch.setattr(aneel_solar, "_abrir", offline)
