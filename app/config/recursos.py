"""Amostra de irradiancia usada apenas nos testes do codigo-fonte."""
from pathlib import Path


def irradiancia_padrao():
    """CSV de teste no repositorio; o executavel distribuido nao o inclui."""
    caminho = Path(__file__).resolve().parent / "dados" / "NASA_2024_IRRAD.csv"
    return caminho if caminho.is_file() else None
