"""Audita os 12 meses de uma UC com um cenario FV informado."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from app.data.repositories.uc import FiltrosUC
from app.services.application import ApplicationService


def main():
    parser = argparse.ArgumentParser(description="Auditar curvas e balanco de uma UC com GD")
    parser.add_argument("--workspace", type=Path, default=ROOT / "workspace")
    parser.add_argument("--irradiancia", type=Path, default=ROOT / "app" / "config" / "dados" / "NASA_2024_IRRAD.csv")
    parser.add_argument("--uc", default="", help="Identificador exato da UC; vazio seleciona a primeira UC com GD")
    parser.add_argument("--modulos-kwp", type=float, required=True)
    parser.add_argument("--inversor-kw", type=float, required=True)
    parser.add_argument("--saida", type=Path, default=ROOT / "docs" / "auditoria_balanco_gd.json")
    args = parser.parse_args()

    service = ApplicationService(args.workspace, args.workspace / "settings.toml")
    versoes = service.versions()
    if not versoes:
        raise SystemExit("Nenhuma versao importada no workspace.")
    import_id = versoes[0]["import_id"]
    filtros = FiltrosUC(busca=args.uc) if args.uc else FiltrosUC(codigo_gd="sim")
    linhas = service.page(import_id, filtros, None)["linhas"]
    if args.uc:
        linhas = [linha for linha in linhas if linha["id_uc"] == args.uc]
    if not linhas:
        raise SystemExit("UC com GD nao encontrada.")
    uc = linhas[0]
    meses = []
    for mes in range(1, 13):
        resultado = service.curva_uc(
            import_id, uc["entidade"], uc["linha_origem"], mes,
            irradiancia_path=str(args.irradiancia),
            potencia_modulos_kwp=args.modulos_kwp,
            potencia_inversor_kw=args.inversor_kw,
        )
        balanco = resultado["balanco"]
        meses.append({
            "mes": mes,
            "intervalos": len(resultado["timestamps"]),
            "energia_importada_kwh": balanco.energia_importada_kwh,
            "energia_gerada_kwh": balanco.energia_gerada_kwh,
            "energia_exportada_kwh": balanco.energia_exportada_kwh,
            "energia_carga_kwh": balanco.energia_carga_kwh,
            "residuo_calibracao_kwh": balanco.residuo_calibracao_kwh,
            "residuo_balanco_kwh": balanco.residuo_balanco_rede_kwh,
        })
    documento = {
        "uc": uc["id_uc"],
        "codigo_gd": uc["codigo_gd"],
        "cenario": {"modulos_kwp": args.modulos_kwp, "inversor_kw": args.inversor_kw},
        "meses": meses,
        "maior_residuo_calibracao_kwh": max(abs(m["residuo_calibracao_kwh"]) for m in meses),
        "maior_residuo_balanco_kwh": max(abs(m["residuo_balanco_kwh"]) for m in meses),
    }
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(documento, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(documento, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
