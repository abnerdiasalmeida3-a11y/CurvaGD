"""Agregação coincidente de todas as UCs tratadas de BT e MT do circuito."""
from collections import Counter
from datetime import datetime
import math

import numpy as np

from ..core.errors import ErroBDGD
from ..core.jobs import sem_progresso
from ..data.repositories.uc import UCRepository, FiltrosUC, consolidar_ativas
from ..data.repositories.tratamentos import TratamentosUC
from ..calculo import gd as gd_calc, potencias
from .curvas import _timestamps_mes, preparar_uc, resultado_mes
from .aneel_solar import potencias_em_lote
from ..data.repositories.bdgd_complemento import potencias_ug_por_ceg


def verificar(catalog, import_id, subestacao, alimentador, mes=0, token=None):
    if type(mes) is not int or mes not in range(13):
        raise ValueError("Mês inválido")
    repo = UCRepository(catalog, import_id)
    if not subestacao and alimentador:
        subs = repo.choices("subestacao", FiltrosUC(alimentador=alimentador), token)
        if len(subs) == 1:
            subestacao = subs[0]
    if not subestacao or not alimentador:
        raise ErroBDGD("ESQUEMA_INVALIDO", "Selecione a subestação e um de seus alimentadores.")
    todas = repo.todas_do_alimentador(subestacao, alimentador, token)
    if not todas:
        raise ErroBDGD("ESQUEMA_INVALIDO", "Não há UCs de BT ou MT vinculadas a esse alimentador da subestação.")
    # Como na CORRECAO_DEMANDA_BDGD: só UCs ativas, uma por COD_ID em cada tabela.
    linhas = consolidar_ativas(todas)
    if not linhas:
        raise ErroBDGD("ESQUEMA_INVALIDO", "O alimentador não tem UCs ativas (SIT_ATIV = AT) de BT ou MT.")
    referencias = TratamentosUC(catalog.workspace).referencias(import_id)
    meses = tuple(range(1, 13)) if mes == 0 else (mes,)
    ucs = []
    for row in linhas:
        if token:
            token.verificar()
        refs = referencias.get((row["entidade"], row["linha_origem"]), {})
        faltantes = [m for m in meses if m not in refs]
        juntas = len(row.get("linhas_consolidadas", ()))
        ucs.append({"uc": row, "referencias": {m: refs[m] for m in meses if m in refs},
                    "faltantes": faltantes, "completo": not faltantes,
                    "motivo": ("Tratar meses: " + ", ".join(f"{m:02d}" for m in faltantes) if faltantes else "Tratamento completo")
                              + (f" · {juntas} linhas ativas somadas" if juntas else "")})
    manifest = catalog.manifest(import_id) or {}
    return {"import_id": import_id, "subestacao": subestacao, "alimentador": alimentador,
            "ano": int(manifest.get("ano_base") or manifest.get("ano") or 0), "mes": mes,
            "meses": meses, "ucs": ucs, "total": len(ucs),
            "bt": sum(u["uc"]["entidade"] == "ucbt" for u in ucs),
            "mt": sum(u["uc"]["entidade"] == "ucmt" for u in ucs),
            "tratadas": sum(u["completo"] for u in ucs),
            "pendentes": sum(not u["completo"] for u in ucs),
            "linhas_bdgd": len(todas), "linhas_fora": len(todas) - sum(len(u["uc"].get("linhas_consolidadas", ())) or 1 for u in ucs),
            "source": manifest.get("source", ""), "directory": str(repo.directory)}


def carregar_uc(store, item, estado, token=None):
    partes = []
    for mes in estado["meses"]:
        if token:
            token.verificar()
        meta, arrays = store.ler(item["referencias"][mes])
        timestamps = _timestamps_mes(estado["ano"], mes)
        uc = item["uc"]
        if (meta["ano"] != estado["ano"] or meta["mes"] != mes or meta["uc_id"] != uc["id_uc"]
                or meta["entidade"] != uc["entidade"] or meta["linha_origem"] != uc["linha_origem"]
                or meta["pontos"] != len(timestamps) or datetime.fromisoformat(meta["inicio"]) != timestamps[0]
                or datetime.fromisoformat(meta["fim"]) != timestamps[-1]):
            raise ErroBDGD("TRATAMENTO_INVALIDO", f"UC {uc['id_uc']}: período ou referência do tratamento incompatível. Trate novamente esta UC.")
        partes.append((meta, arrays))
    return ({campo: np.concatenate([a[campo] for _, a in partes]) for campo in partes[0][1]},
            tuple(t for m, _ in partes for t in m["tipos_dia"]), tuple(m for m, _ in partes))


def gerar(catalog, import_id, subestacao, alimentador, mes=0, token=None, progress=sem_progresso):
    estado = verificar(catalog, import_id, subestacao, alimentador, mes, token)
    if estado["pendentes"]:
        raise ErroBDGD("UCS_PENDENTES", f"Curva final bloqueada: {estado['pendentes']} de {estado['total']} UCs ainda têm pendências no período. Trate todas as UCs de BT e MT antes de gerar ou exportar.")
    store = TratamentosUC(catalog.workspace)
    timestamps = tuple(t for m in estado["meses"] for t in _timestamps_mes(estado["ano"], m))
    carga, geracao, bt, mt = (np.zeros(len(timestamps)) for _ in range(4))
    resumo = []
    tipos = None
    maior_energia = -math.inf
    municipio = ""
    for i, item in enumerate(estado["ucs"]):
        if token:
            token.verificar()
        progress("Somando tratamentos de BT e MT", i, estado["total"])
        arrays, dias, meta = carregar_uc(store, item, estado, token)
        carga += arrays["carga"]
        geracao += arrays["geracao"]
        (bt if item["uc"]["entidade"] == "ucbt" else mt)[:] += arrays["liquida"]
        energia = float(arrays["carga"].sum() * .25)
        if energia > maior_energia:
            maior_energia, tipos, municipio = energia, dias, item["uc"].get("municipio", "")
        resumo.append({"uc_id": item["uc"]["id_uc"], "tipo": "BT" if item["uc"]["entidade"] == "ucbt" else "MT",
                       "carga_kwh": energia, "geracao_kwh": float(arrays["geracao"].sum() * .25),
                       "liquida_kwh": float(arrays["liquida"].sum() * .25),
                       "exportacao_uc_kwh": float(arrays["exportado"].sum() * .25),
                       "cenarios": ", ".join(sorted({m["cenario"] for m in meta})),
                       "possui_gd": bool(str(item["uc"].get("codigo_gd") or "").strip())})
    liquida = carga - geracao
    if not np.allclose(liquida, bt + mt, atol=1e-7, rtol=1e-9):
        raise ErroBDGD("TRATAMENTO_INVALIDO", "A soma BT + MT não fecha com a curva do alimentador.")
    pico = int(np.argmax(liquida))
    # Segunda passagem mantém a memória limitada a uma UC e mede a coincidência no pico do circuito.
    for item, resumo_uc in zip(estado["ucs"], resumo):
        arrays, _, _ = carregar_uc(store, item, estado, token)
        contribuicao = float(arrays["liquida"][pico])
        resumo_uc.update(contribuicao_pico_kw=contribuicao,
                         participacao_pico=contribuicao / float(liquida[pico]) if abs(liquida[pico]) > 1e-10 else None)
    series = {"Carga do alimentador (kW)": liquida, "Carga bruta BT + MT (kW)": carga,
              "Geração BT + MT (kW)": geracao, "BT - líquida (kW)": bt, "MT - líquida (kW)": mt,
              "Importação do alimentador (kW)": np.maximum(liquida, 0),
              "Exportação do alimentador (kW)": np.maximum(-liquida, 0)}
    for a in series.values():
        a.setflags(write=False)
    periodo = f"Ano completo {estado['ano']}" if mes == 0 else f"{mes:02d}/{estado['ano']}"
    progress("Curva final concluída", estado["total"], estado["total"])
    return {"tipo": "alimentador", "agregacao_individual": True,
            "titulo": f"Alimentador {alimentador} · Subestação {estado['subestacao']} - {periodo}",
            "ano": estado["ano"], "mes": mes, "timestamps": timestamps, "tipos_dia": tipos,
            "municipio_calendario": municipio, "series": series, "estado": estado, "influencias": resumo,
            "unidades_incluidas": estado["total"], "unidades_energia_invalida": 0, "grupos_omitidos": (),
            "energia_incluida_kwh": float(liquida.sum() * .25), "energia_reconstituida_kwh": float(liquida.sum() * .25),
            "pico_indice": pico, "pico_kw": float(liquida[pico]),
            "hipoteses": ("Inclui todas as UCs ativas (SIT_ATIV = AT) de BT e MT do circuito, sem filtros de classe, município, busca ou GD; "
                "linhas ativas da mesma UC têm as energias somadas, como na CORRECAO_DEMANDA_BDGD.",
                "A curva líquida é a soma coincidente de carga menos geração das UCs tratadas, sem perdas da rede e sem iluminação pública não cadastrada como UC.",
                "Importação/exportação do alimentador são calculadas após a soma; a soma das exportações das UCs não é a exportação na cabeceira.",
                "Cada UC foi calculada pelo método da CORRECAO_DEMANDA_BDGD: perfil da CRVCRG pelo dia da semana e DEM_MAX mensal que reproduz o ENE como energia importada.",
                "Resultados estimados pelos tratamentos salvos, não medições da cabeceira do alimentador.")}


def contribuicao(catalog, resultado, indice, token=None):
    estado = resultado["estado"]
    item = estado["ucs"][indice]
    arrays, _, _ = carregar_uc(TratamentosUC(catalog.workspace), item, estado, token)
    total = resultado["series"]["Carga do alimentador (kW)"]
    return {"titulo": f"Influência da UC {item['uc']['id_uc']}",
            "series": {"Alimentador completo (kW)": total, "UC selecionada - líquida (kW)": arrays["liquida"],
                       "Alimentador sem esta UC (kW)": total - arrays["liquida"]}}


def tratar_em_lote(catalog, import_id, subestacao, alimentador, mes=0, token=None, progress=sem_progresso,
                   *, performance_ratio=gd_calc.PERFORMANCE_RATIO_PADRAO,
                   cut_in_percentual=gd_calc.CUTIN_PCT_PADRAO,
                   razao_kw_kva=potencias.RAZAO_PADRAO):
    """Trata de uma vez todas as UCs pendentes do circuito, com e sem GD.

    E o fluxo da CORRECAO_DEMANDA_BDGD: potencias da ANEEL buscadas uma vez para
    todos os CEG_GD do alimentador, reserva no POT_INST das UGs, geracao simulada
    por razao kW/kVA (cache) e demanda mensal de cada UC. Meses ja tratados sao
    preservados; so os faltantes sao calculados.
    """
    estado = verificar(catalog, import_id, subestacao, alimentador, mes, token)
    store = TratamentosUC(catalog.workspace)
    candidatas = [i for i in estado["ucs"] if i["faltantes"]]
    falhas = []
    avisos = []
    cegs = sorted({str(i["uc"].get("codigo_gd") or "").strip() for i in candidatas} - {""})
    aneel = {}
    if cegs:
        progress("Buscando potências das GDs na ANEEL", 0, len(candidatas))
        try:
            aneel, _, aviso = potencias_em_lote(catalog.workspace, cegs,
                                                token=token, progress=progress)
        except ErroBDGD as exc:
            if token:
                token.verificar()
            aneel, aviso = {}, f"Base da ANEEL indisponível ({exc}). Potências buscadas no POT_INST das UGs."
        if aviso:
            avisos.append(aviso)
    manifest = catalog.manifest(import_id) or {}
    pot_ugs = potencias_ug_por_ceg(UCRepository(catalog, import_id).directory, manifest.get("source")) if cegs else {}
    from .projeto import nasa_local
    caminho_nasa = nasa_local(catalog.workspace)
    irradiancia = str(caminho_nasa) if caminho_nasa.is_file() else ""
    curvas_cache = {}
    parametros = {"irradiancia_path": irradiancia, "lote": True,
                  "performance_ratio": performance_ratio,
                  "cut_in_percentual": cut_in_percentual,
                  "razao_kw_kva": razao_kw_kva}
    for n, item in enumerate(candidatas):
        if token:
            token.verificar()
        progress("Tratando todas as UCs pendentes · BT e MT", n, len(candidatas))
        uc = item["uc"]
        try:
            contexto = preparar_uc(catalog, import_id, uc, estado["ano"], irradiancia_path=irradiancia,
                                   aneel_em_lote=aneel, pot_ugs=pot_ugs, curvas_cache=curvas_cache,
                                   performance_ratio=performance_ratio,
                                   cut_in_percentual=cut_in_percentual, razao_kw_kva=razao_kw_kva)
            partes = tuple(resultado_mes(contexto, m) for m in item["faltantes"])
            store.salvar(import_id, {"mes": mes, "resultados_mensais": partes}, parametros, token)
        except ErroBDGD as exc:
            if token:
                token.verificar()
            falhas.extend({"uc": uc["id_uc"], "mes": m, "motivo": str(exc)} for m in item["faltantes"])
    resposta = verificar(catalog, import_id, subestacao, alimentador, mes, token)
    resposta["falhas_lote"] = falhas
    resposta["avisos_lote"] = avisos
    progress("Tratamento em lote concluído", len(candidatas), len(candidatas))
    return resposta
