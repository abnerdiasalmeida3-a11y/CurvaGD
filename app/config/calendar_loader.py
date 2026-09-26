from datetime import date
from pathlib import Path
import holidays
import yaml
from ..domain.calendario import CalendarioMunicipal
from ..core.errors import ErroBDGD

UF = dict(zip("11 12 13 14 15 16 17 21 22 23 24 25 26 27 28 29 31 32 33 35 41 42 43 50 51 52 53".split(),
              "RO AC AM RR PA AP TO MA PI CE RN PB PE AL SE BA MG ES RJ SP PR SC RS MS MT GO DF".split()))


class CalendarLoader:
    _uf_codes = frozenset(UF)

    def __init__(self, path: Path, ano: int):
        self.ano = ano
        self.document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self.entries = self.document.get("municipios", {})
        self._states = {}
        self._cache = {}
        # Validar inclusive entradas não usadas pela distribuidora.
        for municipio in self.entries:
            self.for_municipio(str(municipio))

    def for_municipio(self, municipio: str) -> CalendarioMunicipal:
        if municipio in self._cache:
            return self._cache[municipio]
        uf = UF.get(municipio[:2]) if len(municipio) == 7 and municipio.isdigit() else None
        if not uf:
            return CalendarioMunicipal()
        if uf not in self._states:
            self._states[uf] = frozenset(holidays.country_holidays("BR", subdiv=uf, years=[self.ano], observed=False))
        datas = set(self._states[uf])
        entry = self.entries.get(municipio, {})
        if entry.get("uf", uf) != uf:
            raise ErroBDGD("CALENDARIO_INCOMPLETO", "UF incompatível com código IBGE.")
        seen = set()
        for ajuste in entry.get("feriados", []) + entry.get("ajustes", []):
            try:
                data = date.fromisoformat(str(ajuste["data"]))
                fonte = ajuste["fonte"]
                acao = ajuste.get("acao", "incluir")
                if not fonte or data in seen or acao not in {"incluir", "excluir"}:
                    raise ValueError()
                if ajuste in entry.get("ajustes", []) and not ajuste.get("justificativa"):
                    raise ValueError()
            except (KeyError, ValueError, TypeError) as exc:
                raise ErroBDGD("CALENDARIO_INCOMPLETO", "Ajuste municipal exige data única, fonte e justificativa.") from exc
            seen.add(data)
            if acao == "excluir":
                datas.discard(data)
            else:
                datas.add(data)
        cobertura = {}
        if c := entry.get("cobertura"):
            try:
                inicio, fim = date.fromisoformat(str(c["inicio"])), date.fromisoformat(str(c["fim"]))
                if inicio > fim or type(c["declarada_completa"]) is not bool:
                    raise ValueError()
            except (ValueError, KeyError, TypeError) as exc:
                raise ErroBDGD("CALENDARIO_INCOMPLETO", "Cobertura municipal inválida.") from exc
            cobertura[municipio] = (inicio, fim, c["declarada_completa"])
        result = CalendarioMunicipal({municipio: frozenset(datas)}, cobertura)
        self._cache[municipio] = result
        return result
