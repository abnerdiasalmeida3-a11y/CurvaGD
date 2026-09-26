"""Exportação XLSX em fluxo, utilizável pelo EXE sem Excel ou Node instalados.

O formato tabular segue o modelo fornecido pelo usuário. O escritor OOXML usa
apenas a biblioteca padrão para não depender das ferramentas de autoria do
ambiente de desenvolvimento no computador que recebe o aplicativo.
"""
from datetime import date, datetime
import math
from numbers import Real
from pathlib import Path
import re
from uuid import uuid4
from xml.sax.saxutils import escape, quoteattr
from zipfile import ZipFile, ZIP_DEFLATED

from ..core.errors import ErroBDGD
from ..core.jobs import sem_progresso
from ..data.repositories.tratamentos import TratamentosUC
from .alimentadores import carregar_uc

CABECALHO = ("ALIMENTADOR_ID", "UC_ID", "TIPO_UC", "DATA", "HORA", "MES", "DIA_SEMANA", "FERIADO",
             "FIM_SEMANA", "CONSUMO_KW", "GERACAO_KW", "EXPORTADO_KW", "IRRAD_WM2", "POSSUI_GD")
MESES = ("Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro")
DIAS = ("Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo")
MAX_LINHAS = 1048576
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def _coluna(indice):
    texto = ""
    while indice:
        indice, resto = divmod(indice - 1, 26)
        texto = chr(65 + resto) + texto
    return texto


def _texto(valor):
    return escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(valor)))


def _celula(ref, valor, estilo=0):
    if valor is None:
        return ""
    if isinstance(valor, datetime):
        # A planilha apresenta o relógio local do modelo, sem conversão de fuso.
        valor = (valor.date() - date(1899, 12, 30)).days + (valor.hour * 60 + valor.minute) / 1440
        estilo = 6
    elif isinstance(valor, date):
        valor = (valor - date(1899, 12, 30)).days
        estilo = 3
    if isinstance(valor, Real):
        if not math.isfinite(float(valor)):
            return ""
        return f'<c r="{ref}" s="{estilo}"><v>{float(valor):.15g}</v></c>'
    return f'<c r="{ref}" s="{estilo}" t="inlineStr"><is><t xml:space="preserve">{_texto(valor)}</t></is></c>'


class _Xlsx:
    def __init__(self, path):
        self.zip = ZipFile(path, "w", compression=ZIP_DEFLATED, compresslevel=3, allowZip64=True)
        self.nomes = []

    def folha(self, nome, cabecalho, rows, total, widths, estilos=None, token=None):
        if total + 1 > MAX_LINHAS:
            raise ErroBDGD("EXPORTACAO_INVALIDA", "O número de linhas excede o limite de uma aba do Excel.")
        self.nomes.append(nome)
        numero = len(self.nomes)
        cols = [_coluna(i + 1) for i in range(len(cabecalho))]
        estilos = estilos or {}
        ref = f"A1:{cols[-1]}{total+1}"
        with self.zip.open(f"xl/worksheets/sheet{numero}.xml", "w", force_zip64=True) as stream:
            def escrever(texto):
                stream.write(texto.encode("utf-8"))
            escrever(f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{NS}"><dimension ref="{ref}"/>'
                '<sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="16"/><cols>')
            for i, width in enumerate(widths, 1):
                escrever(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>')
            escrever('</cols><sheetData><row r="1" ht="30" customHeight="1">' +
                "".join(_celula(f"{c}1", v, 2) for c, v in zip(cols, cabecalho)) + '</row>')
            buffer = []
            quantidade = 0
            for linha, valores in enumerate(rows, 2):
                if linha > MAX_LINHAS or len(valores) != len(cols):
                    raise ErroBDGD("EXPORTACAO_INVALIDA", "Estrutura de linhas incompatível com a planilha.")
                altura = max([16] + [min(409, 14 * sum(max(1, math.ceil(len(t) / max(8, widths[i] - 3))) for t in str(v).splitlines()))
                                     for i, v in enumerate(valores) if estilos.get(i) == 7 and v is not None])
                buffer.append(f'<row r="{linha}" ht="{altura}" customHeight="1">' + "".join(_celula(f"{c}{linha}", v, estilos.get(i, 0))
                    for i, (c, v) in enumerate(zip(cols, valores))) + '</row>')
                quantidade += 1
                if len(buffer) == 256:
                    if token:
                        token.verificar()
                    escrever("".join(buffer))
                    buffer.clear()
            escrever("".join(buffer))
            if quantidade != total:
                raise ErroBDGD("EXPORTACAO_INVALIDA", "A quantidade exportada não coincide com o período e as UCs selecionadas.")
            escrever(f'</sheetData><autoFilter ref="{ref}"/></worksheet>')

    def finalizar(self):
        folhas = "".join(f'<sheet name={quoteattr(n)} sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(self.nomes, 1))
        self.zip.writestr("xl/workbook.xml", f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{NS}" xmlns:r="{REL}"><sheets>{folhas}</sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>')
        targets = "".join(f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(self.nomes) + 1))
        self.zip.writestr("xl/_rels/workbook.xml.rels", f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{targets}<Relationship Id="rIdStyles" Type="{REL}/styles" Target="styles.xml"/></Relationships>')
        self.zip.writestr("_rels/.rels", f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        content = ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(self.nomes) + 1))
        self.zip.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + content + '</Types>')
        formatos = [(164, "0.000"), (165, "dd/mm/yyyy"), (166, "hh:mm"), (167, "0.00%"), (168, "dd/mm/yyyy hh:mm")]
        nums = ''.join(f'<numFmt numFmtId="{i}" formatCode={quoteattr(f)}/>' for i, f in formatos)
        xfs = '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        xfs += '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        xfs += '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
        xfs += ''.join(f'<xf numFmtId="{i}" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>' for i in (165, 166, 167, 168))
        xfs += '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>'
        self.zip.writestr("xl/styles.xml", f'<styleSheet xmlns="{NS}"><numFmts count="5">{nums}</numFmts><fonts count="2"><font><sz val="10"/><name val="Arial"/></font><font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Arial"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF000000"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="8">{xfs}</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def exportar(catalog, resultado, caminho, *, token=None, progress=sem_progresso, limite_linhas=MAX_LINHAS):
    if not resultado.get("agregacao_individual") or resultado["estado"]["pendentes"]:
        raise ErroBDGD("UCS_PENDENTES", "A exportação exige o tratamento completo de todas as UCs do alimentador.")
    path = Path(caminho)
    if path.suffix.lower() != ".xlsx":
        path = path.with_suffix(".xlsx")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.stem}-{uuid4().hex}.xlsx")
    estado = resultado["estado"]
    timestamps = resultado["timestamps"]
    pontos = len(timestamps)
    por_aba = (min(limite_linhas, MAX_LINHAS) - 1) // pontos
    if por_aba < 1:
        raise ErroBDGD("EXPORTACAO_INVALIDA", "O período de uma UC excede o limite de linhas da aba.")
    store = TratamentosUC(catalog.workspace)
    feriados = set()
    fontes = []
    concluido = 0
    def linhas_ucs(itens):
        nonlocal concluido
        for item in itens:
            if token:
                token.verificar()
            arrays, dias, metadados = carregar_uc(store, item, estado, token)
            uc = item["uc"]
            gd = int(bool(str(uc.get("codigo_gd") or "").strip()))
            fontes.append(("UC " + uc["id_uc"], "Tratamentos salvos por mês", "; ".join(
                f"{m['mes']:02d}: {m['cenario']} · {m.get('fonte_equipamento', '')} · irradiância: {m.get('fonte_irradiancia') or 'não utilizada/disponível'}" for m in metadados)))
            for i, t in enumerate(timestamps):
                feriado = int(dias[i // 96] == "Feriado")
                if feriado:
                    feriados.add((t.date(), uc.get("municipio", "")))
                yield (estado["alimentador"], uc["id_uc"], "BT" if uc["entidade"] == "ucbt" else "MT",
                       t.date(), (t.hour * 60 + t.minute) / 1440, MESES[t.month - 1], DIAS[t.weekday()],
                       feriado, int(t.weekday() >= 5), arrays["carga"][i], arrays["geracao"][i],
                       arrays["exportado"][i], arrays["ghi"][i], gd)
            concluido += 1
            progress("Exportando UCs em intervalos de 15 minutos", concluido, estado["total"])
    book = _Xlsx(temp)
    try:
        # Cada UC permanece inteira e em ordem cronológica, inclusive em anos bissextos.
        for numero, inicio in enumerate(range(0, estado["total"], por_aba), 1):
            grupo = estado["ucs"][inicio:inicio + por_aba]
            nome = "Dados_UCs" if numero == 1 else f"Dados_UCs_{numero:02d}"
            book.folha(nome, CABECALHO, linhas_ucs(grupo), len(grupo) * pontos,
                       [22, 68, 12, 13, 10, 14, 16, 12, 15, 18, 18, 19, 16, 15],
                       {4: 4, 9: 1, 10: 1, 11: 1, 12: 1}, token)
        names = list(resultado["series"])
        rows = ((estado["subestacao"], estado["alimentador"], t, *[resultado["series"][n][i] for n in names])
                for i, t in enumerate(timestamps))
        book.folha("Alimentador", ("SUBESTACAO_ID", "ALIMENTADOR_ID", "DATA_HORA", *names), rows, pontos,
                   [23, 23, 24] + [27] * len(names), {i: 1 for i in range(3, 3 + len(names))}, token)
        rows = ((i["uc_id"], i["tipo"], int(i["possui_gd"]), i["carga_kwh"], i["geracao_kwh"],
                 i["liquida_kwh"], i["contribuicao_pico_kw"], i["participacao_pico"], i["cenarios"])
                for i in resultado["influencias"])
        book.folha("Influencia_UCs", ("UC_ID", "TIPO_UC", "POSSUI_GD", "CARGA_KWH", "GERACAO_KWH", "LIQUIDA_KWH",
                   "CONTRIBUICAO_NO_PICO_KW", "PARTICIPACAO_NO_PICO", "CENARIO_ENE"), rows, estado["total"],
                   [68, 12, 15, 20, 20, 20, 30, 28, 24], {3: 1, 4: 1, 5: 1, 6: 1, 7: 5}, token)
        legenda = [("CAMPO", "SIGNIFICADO", "OBSERVAÇÃO")]
        detalhes = [
            ("ALIMENTADOR_ID", "Identificador do circuito", estado["alimentador"]),
            ("UC_ID", "Identificador da unidade consumidora", "Todos os intervalos do período de uma UC ficam juntos antes da próxima UC."),
            ("TIPO_UC", "Nível de atendimento", "BT ou MT; todas as UCs do circuito estão incluídas."),
            ("DATA / HORA", "Relógio local da curva", f"Ano {estado['ano']}; 15 minutos. {pontos:,} registros por UC no período."),
            ("MES / DIA_SEMANA", "Calendário do registro", "Meses e dias correspondem à data efetiva, incluindo 29/02 em anos bissextos."),
            ("FERIADO / FIM_SEMANA", "Indicadores independentes", "1=sim; 0=não. A curva segue o dia da semana (método da CORRECAO_DEMANDA_BDGD): feriado não muda o perfil."),
            ("CONSUMO_KW", "Carga bruta reconstruída da UC", "Potência média no intervalo, antes de descontar a GD; energia = soma(kW) × 0,25 h."),
            ("GERACAO_KW", "Geração estimada da UC", "Zero para UC sem GD."),
            ("EXPORTADO_KW", "Exportação da UC", "Máximo entre geração menos consumo e zero. A soma não representa a exportação do alimentador."),
            ("IRRAD_WM2", "Irradiância NASA usada no tratamento (15 min)", "W/m². Em branco se não disponível ou não utilizada; não significa irradiância zero."),
            ("POSSUI_GD", "UC possui código de geração", "1=sim; 0=não."),
            ("FONTE", "Versão imutável da BDGD", f"{estado['import_id']} · {estado['source']}"),
            ("SUBESTACAO", "Subestação do circuito", estado["subestacao"]),
            ("PICO", "Maior potência líquida do alimentador no período", resultado["timestamps"][resultado["pico_indice"]].isoformat()),
            ("INFLUENCIA", "Contribuição coincidente no pico", "Participação = potência líquida da UC no instante do pico ÷ potência do alimentador nesse instante; pode ser negativa. Em branco se pico zero."),
            ("ATUALIZACAO", "Resultados calculados pelo software", "Este arquivo é um retrato dos tratamentos. Para atualizar, revise no software e exporte novamente."),
            *[("HIPOTESE", "Método do alimentador", h) for h in resultado["hipoteses"]], *fontes]
        book.folha("Legenda", legenda[0], detalhes, len(detalhes), [68, 46, 120], {0: 7, 1: 7, 2: 7}, token=token)
        book.folha("Feriados", ("DATA", "DESCRICAO", "MUNICIPIO_IBGE"),
                   ((d, "Feriado aplicado no calendário do tratamento", mun) for d, mun in sorted(feriados)),
                   len(feriados), [16, 55, 23], token=token)
        if token:
            token.verificar()
        book.finalizar()
        book.zip.close()
        temp.replace(path)
    finally:
        book.zip.close()
        temp.unlink(missing_ok=True)
    return {"path": str(path.resolve()), "registros_ucs": pontos * estado["total"],
            "abas_dados": math.ceil(estado["total"] / por_aba), "ucs": estado["total"]}


def gravar_quadro_xlsx(quadro, caminho, aba="DEMANDA"):
    """Grava um DataFrame numa planilha XLSX sem depender do openpyxl.

    Usado pela Correcao de demanda para exportar DEM_MAX_01..12: o mesmo escritor
    em fluxo da exportacao do alimentador, entao funciona no executavel.
    """
    path = Path(caminho)
    if len(quadro) + 1 > MAX_LINHAS:
        raise ErroBDGD("EXPORTACAO_INVALIDA", "O número de linhas excede o limite de uma aba do Excel.")
    temp = path.with_name(f".{path.stem}-{uuid4().hex}.xlsx")
    colunas = [str(c) for c in quadro.columns]
    numericas = {i for i, c in enumerate(quadro.columns) if quadro[c].dtype.kind in "fiu"}

    def valor(v):
        if v is None:
            return None
        if isinstance(v, Real) and not isinstance(v, bool):
            return float(v) if math.isfinite(float(v)) else None
        return str(v)

    linhas = ([valor(v) for v in registro] for registro in quadro.itertuples(index=False, name=None))
    book = _Xlsx(temp)
    try:
        book.folha(aba, colunas, linhas, len(quadro), [max(12, min(len(c) + 4, 70)) for c in colunas],
                   {i: 1 for i in numericas})
        book.finalizar()
        book.zip.close()
        temp.replace(path)
    finally:
        book.zip.close()
        temp.unlink(missing_ok=True)
    return str(path)
