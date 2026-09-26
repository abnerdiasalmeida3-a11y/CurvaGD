"""Cria um ZIP de distribuicao com lista fechada de arquivos, sem dados do autor."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    raiz = Path(__file__).resolve().parents[2]
    executavel = raiz / "CurvaGD.exe"
    instrucoes = raiz / "LEIA_ME_PARA_USUARIO.txt"
    destino = raiz / "CurvaGD_para_compartilhar.zip"
    for arquivo in (executavel, instrucoes):
        if not arquivo.is_file():
            raise SystemExit(f"Arquivo ausente: {arquivo}")
    with ZipFile(destino, "w", compression=ZIP_DEFLATED, compresslevel=6) as pacote:
        for arquivo in (executavel, instrucoes):
            pacote.write(arquivo, arcname=arquivo.name)
    with ZipFile(destino) as pacote:
        esperado = {executavel.name, instrucoes.name}
        assert set(pacote.namelist()) == esperado
        assert pacote.testzip() is None
    print(f"Pacote pronto: {destino} ({destino.stat().st_size / 1024**2:.1f} MB)")


if __name__ == "__main__":
    main()
