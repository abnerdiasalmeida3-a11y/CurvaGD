from dataclasses import dataclass, replace
import math
from pathlib import Path
from ..db import connection, version_view

SITUACAO_ATIVA = "AT"
# fatia minima das UCs de um municipio para uma subestacao aparecer nele sem ser
# a sua subestacao principal
PARTICIPACAO_MINIMA = 0.10
CAMPOS_ENERGIA = tuple(f"energia_mes_{m:02d}" for m in range(1, 13))


def _numero(valor):
    return float(valor) if isinstance(valor, (int, float)) and not isinstance(valor, bool) and math.isfinite(valor) else 0.0


def consolidar_ativas(linhas):
    """UCs do alimentador como a CORRECAO_DEMANDA_BDGD as enxerga.

    Ficam so as linhas ativas (SIT_ATIV = AT). A mesma UC aparece em mais de uma
    linha quando muda de situacao ou de subclasse no meio do ano -- cada linha
    com os meses da sua fase --, entao as linhas de mesmo COD_ID sao juntadas
    somando as energias e mantendo os demais campos da primeira. A juncao e por
    tabela: a mesma UC na UCBT e na UCMT continua como duas UCs.
    """
    if any(r.get("situacao") is not None for r in linhas):
        linhas = [r for r in linhas if str(r.get("situacao") or "").strip().upper() == SITUACAO_ATIVA]
    grupos = {}
    for r in linhas:
        chave = (r["entidade"], r["id_uc"]) if r.get("id_uc") else (r["entidade"], None, r["linha_origem"])
        grupos.setdefault(chave, []).append(r)
    saida = []
    for grupo in grupos.values():
        grupo = sorted(grupo, key=lambda r: r["linha_origem"])
        base = dict(grupo[0])
        if len(grupo) > 1:
            for campo in CAMPOS_ENERGIA:
                base[campo] = math.fsum(_numero(r.get(campo)) for r in grupo)
            base["meses_invalidos"] = []
            base["linhas_consolidadas"] = [r["linha_origem"] for r in grupo]
        saida.append(base)
    return saida


@dataclass(frozen=True)
class FiltrosUC:
    municipio: str = ""
    subestacao: str = ""
    alimentador: str = ""
    busca: str = ""
    classe: str = ""
    grupo_tensao: str = ""
    codigo_gd: str = ""
    status: str = ""
    ordenar: str = "id_uc"
    descendente: bool = False
    pagina: int = 0


SORTABLE = frozenset({"id_uc", "municipio", "alimentador", "classe", "grupo_tensao", "status", "codigo_gd", "energia_anual_kwh"})


class UCRepository:
    def __init__(self, catalog, import_id):
        self.path = catalog.path
        self.import_id = import_id
        with connection(self.path) as con:
            row = con.execute("SELECT directory FROM versions WHERE import_id=?", [import_id]).fetchone()
        if not row:
            raise ValueError("Versão não encontrada")
        self.directory = Path(row[0])

    def _views(self, con, filters=None):
        bt, mt, ct = (version_view(self.import_id, e) for e in ("ucbt", "ucmt", "ctmt"))
        bt_source, mt_source = bt, mt
        if filters and filters.alimentador:
            partition = filters.alimentador.encode().hex().upper()
            for entity, fallback, name in (("ucbt", bt, "ucbt_src"), ("ucmt", mt, "ucmt_src")):
                files = [str(p) for p in (self.directory / entity / f"ctmt={partition}").glob("*.parquet")]
                if files:
                    con.from_parquet(files, hive_partitioning=False).create_view(name)
                else:
                    con.execute(f"CREATE TEMP VIEW {name} AS SELECT * FROM {fallback} WHERE FALSE")
            bt_source, mt_source = "ucbt_src", "ucmt_src"
        # Campos e nomes de views vêm de listas fechadas/UUID validado.
        energy = " + ".join(f"CASE WHEN energia_mes_{i:02d} >= 0 THEN energia_mes_{i:02d} ELSE 0 END" for i in range(1, 13))
        colunas = {c[0] for c in con.execute(f"SELECT * FROM {bt} LIMIT 0").description}
        # Como na Correcao de demanda: so as linhas ativas (SIT_ATIV = AT) entram.
        ativas = (f"WHERE situacao IS NULL OR upper(trim(situacao)) = '{SITUACAO_ATIVA}'"
                  if "situacao" in colunas else "")
        con.execute(f"CREATE TEMP VIEW ucs AS SELECT *, {energy} AS energia_anual_kwh FROM (SELECT * FROM {bt_source} UNION ALL BY NAME SELECT * FROM {mt_source}) {ativas}")
        con.execute(f"CREATE TEMP VIEW feeders AS SELECT id_alimentador, min(subestacao) AS subestacao FROM {ct} GROUP BY id_alimentador HAVING count(*)=1")

    def _where(self, filters):
        """Filtros da tabela; a regiao vale pelo nivel mais especifico escolhido.

        Alimentador escolhido: todas as UCs dele. So a subestacao: as UCs de todos
        os seus alimentadores. So o municipio: as UCs daquele municipio.
        """
        clauses, params = [], []
        if filters.alimentador:
            clauses.append("u.alimentador=?")
            params.append(filters.alimentador)
            clauses.append("u.ctmt=?")
            params.append(filters.alimentador.encode().hex().upper())
        elif filters.subestacao:
            clauses.append("u.alimentador IN (SELECT id_alimentador FROM feeders WHERE subestacao=?)")
            params.append(filters.subestacao)
        elif filters.municipio:
            clauses.append("u.municipio=?")
            params.append(filters.municipio)
        for name in ("classe", "grupo_tensao"):
            if value := getattr(filters, name):
                clauses.append(f"u.{name}=?")
                params.append(value)
        if filters.busca:
            clauses.append("contains(lower(coalesce(u.id_uc,'')), lower(?))")
            params.append(filters.busca)
        if filters.codigo_gd in ("sim", "nao"):
            clauses.append("nullif(trim(u.codigo_gd),'') IS " + ("NOT NULL" if filters.codigo_gd == "sim" else "NULL"))
        if filters.status:
            clauses.append("list_contains(string_split(u.status,'|'), ?)")
            params.append(filters.status)
        return " AND ".join(clauses) or "TRUE", params

    def indice_regioes(self, token=None):
        """Municipios, subestacoes e alimentadores ATIVOS da base, com os nomes.

        Uma subestacao pertence ao municipio onde esta a maior parte das suas UCs
        ativas (o MUN de algumas UCs aponta para municipios vizinhos, e por isso
        quase toda subestacao atende um pouco de Cuiaba). Um municipio sem
        subestacao propria mostra as que atendem ao menos 10% das suas UCs.
        Um alimentador e ativo quando tem ao menos uma UC ativa.
        """
        with connection(self.path, token) as con:
            self._views(con)
            linhas = con.execute(
                "SELECT u.municipio, f.subestacao, u.alimentador, count(*) FROM ucs u "
                "LEFT JOIN feeders f ON u.alimentador = f.id_alimentador GROUP BY ALL").fetchall()
            nomes_sub = dict(con.execute(
                f"SELECT id_subestacao, min(nome) FROM {version_view(self.import_id, 'sub')} "
                "WHERE id_subestacao IS NOT NULL GROUP BY id_subestacao").fetchall())
            nomes_ctmt = dict(con.execute(
                f"SELECT id_alimentador, min(nome) FROM {version_view(self.import_id, 'ctmt')} "
                "WHERE id_alimentador IS NOT NULL GROUP BY id_alimentador").fetchall())
        municipios, por_sub, contagem, total_mun = set(), {}, {}, {}
        for mun, sub, alimentador, n in linhas:
            if mun is not None:
                municipios.add(str(mun))
                total_mun[str(mun)] = total_mun.get(str(mun), 0) + n
            if sub is None or alimentador is None:
                continue
            por_sub.setdefault(str(sub), set()).add(str(alimentador))
            if mun is not None:
                chave = (str(sub), str(mun))
                contagem[chave] = contagem.get(chave, 0) + n
        principal = {}
        for (sub, mun), n in contagem.items():
            atual = principal.get(sub)
            if atual is None or (n, mun) > (atual[1], atual[0]):
                principal[sub] = (mun, n)
        subs_mun = {}
        for (sub, mun), n in contagem.items():
            if principal[sub][0] == mun or n >= PARTICIPACAO_MINIMA * total_mun.get(mun, 0):
                subs_mun.setdefault(mun, set()).add(sub)
        return {
            "municipios": sorted(municipios),
            "subestacoes_por_municipio": {m: sorted(v) for m, v in subs_mun.items()},
            "alimentadores_por_subestacao": {s: sorted(v) for s, v in por_sub.items()},
            "nomes_subestacoes": {str(k): str(v) for k, v in nomes_sub.items() if v},
            "nomes_alimentadores": {str(k): str(v) for k, v in nomes_ctmt.items() if v},
        }

    def page(self, filters: FiltrosUC, token=None):
        if filters.ordenar not in SORTABLE or type(filters.pagina) is not int or filters.pagina < 0:
            raise ValueError("Ordenação ou página inválida")
        with connection(self.path, token) as con:
            self._views(con, filters)
            where, params = self._where(filters)
            total = con.execute(f"SELECT count(*) FROM ucs u WHERE {where}", params).fetchone()[0]
            direction = "DESC" if filters.descendente else "ASC"
            cur = con.execute(f"SELECT * FROM ucs u WHERE {where} ORDER BY u.{filters.ordenar} {direction} NULLS LAST, u.id_uc NULLS LAST, u.entidade, u.linha_origem LIMIT 200 OFFSET ?", [*params, filters.pagina * 200])
            cols = [c[0] for c in cur.description]
            return {"total": total, "pagina": filters.pagina, "linhas": [dict(zip(cols, r)) for r in cur.fetchall()]}

    def choices(self, kind, filters=FiltrosUC(), token=None, indice=None):
        if kind not in ("municipio", "subestacao", "alimentador", "classe", "grupo_tensao"):
            raise ValueError("Filtro desconhecido")
        if kind in ("classe", "grupo_tensao"):
            with connection(self.path, token) as con:
                self._views(con, filters)
                where, params = self._where(filters)
                rows = con.execute(f"SELECT DISTINCT u.{kind} AS valor FROM ucs u WHERE {where} AND u.{kind} IS NOT NULL ORDER BY valor", params).fetchall()
                return [r[0] for r in rows]
        indice = indice or self.indice_regioes(token)
        return opcoes_regiao(indice, kind, filters)

    def named_choices(self, kind, filters=FiltrosUC(), token=None, indice=None):
        """Códigos elegíveis com nomes do IBGE/SUB/CTMT, mantendo o código como chave."""
        if kind in ("classe", "grupo_tensao"):
            return [(str(code), code) for code in self.choices(kind, filters, token)]
        indice = indice or self.indice_regioes(token)
        return rotular(indice, kind, opcoes_regiao(indice, kind, filters))

    def summary(self, filters, token=None):
        with connection(self.path, token) as con:
            self._views(con, filters)
            where, params = self._where(filters)
            row = con.execute(f"SELECT count(*), sum(energia_anual_kwh), count(*) FILTER (WHERE nullif(trim(codigo_gd),'') IS NOT NULL), count(*) FILTER (WHERE energia_anual_kwh IS NULL OR len(meses_invalidos)>0) FROM ucs u WHERE {where}", params).fetchone()
            classes = con.execute(f"SELECT classe, count(*) FROM ucs u WHERE {where} GROUP BY classe ORDER BY classe", params).fetchall()
            statuses = con.execute(f"SELECT motivo, count(*) FROM (SELECT unnest(string_split(status,'|')) AS motivo FROM ucs u WHERE {where}) GROUP BY motivo ORDER BY motivo", params).fetchall()
            return {"total_ucs": row[0], "energia_anual_informada_kwh": row[1], "com_gd": row[2], "ucs_energia_incompleta_ou_invalida": row[3], "classes": dict(classes), "status": dict(statuses)}

    def detail(self, entidade, linha, token=None):
        view = version_view(self.import_id, entidade)
        with connection(self.path, token) as con:
            cur = con.execute(f"SELECT * FROM {view} WHERE linha_origem=?", [linha])
            row = cur.fetchone()
            return dict(zip([c[0] for c in cur.description], row)) if row else None

    def detalhe_consolidado(self, entidade, linha, token=None):
        """A linha da UC; se ativa, somada as outras linhas ativas da mesma UC no alimentador."""
        row = self.detail(entidade, linha, token)
        if not row or str(row.get("situacao") or "").strip().upper() != SITUACAO_ATIVA or not row.get("id_uc"):
            return row
        view = version_view(self.import_id, entidade)
        with connection(self.path, token) as con:
            cur = con.execute(f"SELECT * FROM {view} WHERE id_uc=? AND ctmt IS NOT DISTINCT FROM ?",
                              [row["id_uc"], row.get("ctmt")])
            colunas = [c[0] for c in cur.description]
            irmas = [dict(zip(colunas, r)) for r in cur.fetchall()]
        for irma in irmas:
            irma.setdefault("entidade", entidade)
        consolidadas = [r for r in consolidar_ativas(irmas) if r["linha_origem"] == linha or linha in r.get("linhas_consolidadas", ())]
        return consolidadas[0] if consolidadas else row

    def grupos_curva(self, filters: FiltrosUC, mes: int, token=None):
        """Agrega energia por tipologia/municipio antes de sintetizar um alimentador."""
        if type(mes) is not int or mes not in range(1, 13):
            raise ValueError("Mes invalido")
        energia = f"energia_mes_{mes:02d}"
        with connection(self.path, token) as con:
            self._views(con, filters)
            where, params = self._where(filters)
            cur = con.execute(
                f"""SELECT u.municipio, u.tipo_curva,
                           sum(u.{energia}) FILTER (WHERE u.{energia} IS NOT NULL AND u.{energia} >= 0) AS energia_kwh,
                           count(*) FILTER (WHERE u.{energia} IS NOT NULL AND u.{energia} >= 0) AS unidades,
                           count(*) FILTER (WHERE u.{energia} IS NULL OR u.{energia} < 0) AS unidades_energia_invalida
                    FROM ucs u
                    WHERE {where}
                    GROUP BY u.municipio, u.tipo_curva
                    ORDER BY u.municipio, u.tipo_curva""",
                params,
            )
            return [
                {"municipio": row[0], "tipo_curva": row[1], "energia_kwh": row[2], "unidades": row[3],
                 "unidades_energia_invalida": row[4]}
                for row in cur.fetchall()
            ]

    def todas_do_alimentador(self, subestacao, alimentador, token=None):
        """Toda BT e MT do circuito, independentemente de filtros da tabela de UCs."""
        if not subestacao or not alimentador:
            raise ValueError("Selecione a subestação e o alimentador.")
        filtros = FiltrosUC(alimentador=alimentador)
        with connection(self.path, token) as con:
            self._views(con, filtros)
            where, params = self._where(filtros)
            # o alimentador precisa ser mesmo daquela subestacao
            where += " AND u.alimentador IN (SELECT id_alimentador FROM feeders WHERE subestacao=?)"
            params.append(subestacao)
            cur = con.execute(f"SELECT u.* FROM ucs u WHERE {where} ORDER BY u.entidade,u.id_uc,u.linha_origem", params)
            colunas = [c[0] for c in cur.description]
            return [dict(zip(colunas, r)) for r in cur.fetchall()]


def opcoes_regiao(indice, kind, filters=FiltrosUC()):
    """Codigos de municipio, subestacao ou alimentador para a selecao atual.

    Subestacoes: as do municipio escolhido (ou todas). Alimentadores: os ativos
    da subestacao escolhida; sem subestacao, os das subestacoes do municipio.
    """
    por_sub = indice["alimentadores_por_subestacao"]
    if kind == "municipio":
        return list(indice["municipios"])
    if kind == "subestacao":
        if filters.alimentador:
            return sorted(s for s, alimentadores in por_sub.items() if filters.alimentador in alimentadores)
        if filters.municipio:
            return list(indice["subestacoes_por_municipio"].get(filters.municipio, []))
        return sorted(por_sub)
    if kind == "alimentador":
        if filters.subestacao:
            return list(por_sub.get(filters.subestacao, []))
        subs = (indice["subestacoes_por_municipio"].get(filters.municipio, []) if filters.municipio else por_sub)
        return sorted({a for s in subs for a in por_sub.get(s, [])})
    raise ValueError("Filtro desconhecido")


def rotular(indice, kind, codigos):
    """(rotulo, codigo): municipio e subestacao pelo nome, alimentador pelo codigo."""
    if kind == "municipio":
        from ..municipios import nomes_municipios
        nomes = nomes_municipios()
        itens = [(f"{nomes[c]} · {c}" if nomes.get(c) else str(c), c) for c in codigos]
        return sorted(itens, key=lambda item: (item[0].casefold(), item[1]))
    if kind == "subestacao":
        nomes = indice["nomes_subestacoes"]
        itens = [(f"{nomes[c]} · {c}" if nomes.get(c) else str(c), c) for c in codigos]
        return sorted(itens, key=lambda item: (item[0].casefold(), item[1]))
    nomes = indice["nomes_alimentadores"]
    return [(f"{c} · {nomes[c]}" if nomes.get(c) else str(c), c) for c in codigos]
