"""Predicción de consumo y FV por tramos de 15 min (F3).

Capa 1 (RF-05): **perfil estadístico** — mediana (p50) del consumo por
``tramo (0–95) × tipo de día`` (laborable / fin-de-semana-o-festivo),
con banda p10–p90 del mismo perfil. Previsión de FV con **Solcast como
fuente preferente** (D8) y el perfil estadístico como fallback por día.
Sin clima y sin ML (F5).

Nunca se inventan valores: los tramos sin datos suficientes quedan a
``None`` (cascada D6) y el greedy los trata como 0 W con warning.
"""
# versión 0.9.0

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from custom_components.optimizador_bateria_omie.const import (
    CONF_ENTITY_ID_PREVISION_FV_HOY,
    CONF_ENTITY_ID_PREVISION_FV_MANANA,
    CONF_FESTIVOS_LOCALES,
    CONF_VENTANA_HISTORICO,
    DEFAULT_VENTANA_HISTORICO,
    DOMAIN,
    FUENTE_FV_NONE,
    FUENTE_FV_PERFIL,
    FUENTE_FV_SOLCAST,
)
from custom_components.optimizador_bateria_omie.historico import (
    FuenteHistoricoRecorder,
    Muestra,
)
from custom_components.optimizador_bateria_omie.tarifa import ModeloTarifa

_LOGGER = logging.getLogger(__name__)

TRAMOS_POR_DIA = 96
MIN_DIAS_PERFIL = 3
# Solcast publica energía en kWh por periodo de 30 min → potencia media W
KWH_30MIN_A_W = 2000.0

TIPO_LABORABLE = "laborable"
TIPO_FINDE = "finde"


class ErrorPrediccion(Exception):
    """Error del predictor de consumo."""


@dataclass
class PercentilesTramo:
    """Banda de predicción de un tramo (W)."""

    p10: float
    p50: float
    p90: float
    fuente: str = "perfil"


@dataclass
class PrediccionDia:
    """Predicción de un día: consumo siempre, FV opcional."""

    fecha: date
    consumo: list[PercentilesTramo | None]
    fv: list[PercentilesTramo | None]
    fuente_fv: str


def _percentil(valores: list[float], p: float) -> float:
    """Percentil por interpolación lineal de una lista ordenada."""
    if not valores:
        raise ValueError("percentil de lista vacía")
    if len(valores) == 1:
        return valores[0]
    k = (len(valores) - 1) * p
    bajo = int(k)
    alto = min(bajo + 1, len(valores) - 1)
    fraccion = k - bajo
    return valores[bajo] + (valores[alto] - valores[bajo]) * fraccion


def _tramo_local(ts_utc: datetime, tz: Any | None = None) -> int:
    """Índice de tramo (0–95, reloj pared local) de un instante."""
    local = ts_utc.astimezone(tz) if tz is not None else ts_utc.astimezone()
    return local.hour * 4 + local.minute // 15


def _parsear_detailed_forecast(
    atributo: Any, fecha: date, tz: Any | None = None
) -> list[PercentilesTramo | None]:
    """Parsea el atributo ``detailedForecast`` de Solcast para una fecha.

    Cada entrada es un periodo de 30 min con ``period_start`` y
    ``pv_estimate``/``pv_estimate10``/``pv_estimate90`` en kWh por
    periodo (D8b). Mapeo 30→15 min **repitiendo** cada estimación en
    los dos tramos (no se interpola). Los periodos de otras fechas se
    ignoran; los tramos sin dato quedan a ``None``.
    """
    tramos: list[PercentilesTramo | None] = [None] * TRAMOS_POR_DIA
    if not isinstance(atributo, list):
        return tramos
    for entrada in atributo:
        if not isinstance(entrada, dict):
            continue
        try:
            inicio = datetime.fromisoformat(str(entrada["period_start"]))
        except (KeyError, TypeError, ValueError):
            continue
        local = (
            inicio.astimezone(tz) if tz is not None else inicio.astimezone()
        )
        if local.date() != fecha:
            continue
        try:
            p50 = float(entrada["pv_estimate"]) * KWH_30MIN_A_W
            p10 = float(entrada["pv_estimate10"]) * KWH_30MIN_A_W
            p90 = float(entrada["pv_estimate90"]) * KWH_30MIN_A_W
        except (KeyError, TypeError, ValueError):
            _LOGGER.warning(
                "detailedForecast con pv_estimate no numérico: %r", entrada
            )
            continue
        tramo = _tramo_local(local)
        par = tramo // 2
        estimacion = PercentilesTramo(
            p10=p10, p50=p50, p90=p90, fuente=FUENTE_FV_SOLCAST
        )
        for indice in (par * 2, par * 2 + 1):
            tramos[indice] = estimacion
    return tramos


@dataclass
class _Perfil:
    """Perfil estadístico por función, tipo de día y tramo."""

    datos: dict[str, dict[str, dict[int, dict]]] = field(default_factory=dict)
    actualizado: datetime | None = None
    ventana_dias: int | None = None

    def entrada(
        self, funcion: str, tipo: str, tramo: int
    ) -> dict | None:
        """Entrada {p10, p50, p90, n_dias} o None si no existe."""
        return self.datos.get(funcion, {}).get(tipo, {}).get(tramo)


class PredictorConsumo:
    """Predictor de consumo y FV basado en el perfil estadístico."""

    def __init__(
        self,
        hass: Any,
        fuente_consumo: FuenteHistoricoRecorder,
        fuente_fv: FuenteHistoricoRecorder | None,
        festivos: ModeloTarifa,
        params: dict[str, Any],
        ruta_perfil: Path | None = None,
    ) -> None:
        """Inicializa el predictor.

        ``params`` es ``entry.data``: ventana de histórico, festivos
        locales y entity_ids de Solcast (hoy/mañana). ``ruta_perfil``
        permite inyectar la ruta de persistencia en tests.
        """
        self.hass = hass
        self.fuente_consumo = fuente_consumo
        self.fuente_fv = fuente_fv
        self.festivos = festivos
        self._params = params
        self._ruta_perfil = ruta_perfil or (
            Path(hass.config.config_dir)
            / "custom_components"
            / DOMAIN
            / "cache"
            / "perfil_consumo.json"
        )
        self._perfil = _Perfil()

    # ─── Tipo de día ───

    def tipo_dia(self, fecha: date, festivo_hoy: bool = False) -> str:
        """Tipo de día del perfil: laborable o finde/festivo.

        Combina festivos nacionales (``ModeloTarifa``), fines de
        semana, ``festivos_locales`` ("MM-DD") y la marca manual
        ``festivo_hoy`` (solo aplica a la fecha de hoy).
        """
        hoy = datetime.now().astimezone().date()
        if festivo_hoy and fecha == hoy:
            return TIPO_FINDE
        if (f"{fecha.month:02d}-{fecha.day:02d}") in (
            self._params.get(CONF_FESTIVOS_LOCALES) or []
        ):
            return TIPO_FINDE
        local = datetime(fecha.year, fecha.month, fecha.day)
        if self.festivos.es_festivo(local) or self.festivos.es_fin_de_semana(
            local
        ):
            return TIPO_FINDE
        return TIPO_LABORABLE

    # ─── Persistencia ───

    async def async_cargar_perfil(self) -> bool:
        """Carga el perfil persistido en disco (si existe)."""
        def _leer() -> dict | None:
            if not self._ruta_perfil.exists():
                return None
            with open(self._ruta_perfil, encoding="utf-8") as fichero:
                return json.load(fichero)

        datos = await self.hass.async_add_executor_job(_leer)
        if not datos:
            _LOGGER.info("Sin perfil de consumo persistido")
            return False

        perfil = _Perfil(
            actualizado=datetime.fromisoformat(datos["actualizado"]),
            ventana_dias=datos.get("ventana_dias"),
        )
        for funcion, tipos in datos.get("perfil", {}).items():
            perfil.datos[funcion] = {
                tipo: {
                    int(tramo): entrada
                    for tramo, entrada in tramos.items()
                }
                for tipo, tramos in tipos.items()
            }
        self._perfil = perfil
        _LOGGER.info(
            "Perfil de consumo cargado (%s), ventana %s día(s)",
            datos["actualizado"],
            datos.get("ventana_dias"),
        )
        return True

    def _serializar(self) -> dict:
        """Perfil listo para JSON."""
        return {
            "version": 1,
            "actualizado": datetime.now(tz=UTC).isoformat(),
            "ventana_dias": self._perfil.ventana_dias,
            "perfil": self._perfil.datos,
        }

    # ─── Recálculo del perfil ───

    async def recalcular_perfil(self) -> None:
        """Recalcula el perfil desde el recorder y lo persiste.

        Consulta consumo y FV de la ventana configurada; si una fuente
        falla (``ErrorHistorico``) se conserva lo que haya y se avisa.
        """
        dias = int(
            self._params.get(CONF_VENTANA_HISTORICO, DEFAULT_VENTANA_HISTORICO)
        )
        perfil = _Perfil(
            actualizado=datetime.now(tz=UTC), ventana_dias=dias
        )
        perfil.datos["consumo"] = self._construir_estadisticas(
            await self._obtener_muestras(self.fuente_consumo, dias)
        )
        perfil.datos["fv"] = self._construir_estadisticas(
            await self._obtener_muestras(self.fuente_fv, dias)
        )
        self._perfil = perfil

        def _escribir() -> None:
            self._ruta_perfil.parent.mkdir(parents=True, exist_ok=True)
            with open(self._ruta_perfil, "w", encoding="utf-8") as fichero:
                json.dump(self._serializar(), fichero, ensure_ascii=False)

        await self.hass.async_add_executor_job(_escribir)
        _LOGGER.info("Perfil de consumo recalculado con %d día(s)", dias)

    async def _obtener_muestras(
        self, fuente: FuenteHistoricoRecorder | None, dias: int
    ) -> list[Muestra]:
        """Muestras de una fuente; [] con warning si no disponible."""
        if fuente is None:
            return []
        try:
            return await fuente.obtener_series(dias)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Histórico no disponible: %s", err)
            return []

    def _construir_estadisticas(
        self, muestras: list[Muestra]
    ) -> dict[str, dict[int, dict]]:
        """Estadísticas por tipo de día y tramo (más el global "todos")."""
        por_tipo: dict[str, dict[int, dict[str, list[float]]]] = {
            TIPO_LABORABLE: defaultdict(dict),
            TIPO_FINDE: defaultdict(dict),
            "todos": defaultdict(dict),
        }
        for muestra in muestras:
            local = muestra.ts_utc.astimezone()
            fecha = local.date()
            tramo = local.hour * 4 + local.minute // 15
            tipo = self.tipo_dia(fecha)
            for clave in (tipo, "todos"):
                valores = por_tipo[clave][tramo].setdefault(
                    str(fecha), []
                )
                valores.append(muestra.valor_w)

        estadisticas: dict[str, dict[int, dict]] = {}
        for tipo, tramos in por_tipo.items():
            estadisticas[tipo] = {}
            for tramo, dias_valor in tramos.items():
                valores = sorted(
                    v
                    for lista in dias_valor.values()
                    for v in lista
                )
                estadisticas[tipo][tramo] = {
                    "p10": round(_percentil(valores, 0.10), 1),
                    "p50": round(_percentil(valores, 0.50), 1),
                    "p90": round(_percentil(valores, 0.90), 1),
                    "n_dias": len(dias_valor),
                }
        return estadisticas

    # ─── Predicción ───

    def _percentiles_perfil(
        self, funcion: str, tipo: str, tramo: int
    ) -> PercentilesTramo | None:
        """Cascada D6: tipo → tipo opuesto → global → None."""
        opuesto = (
            TIPO_LABORABLE if tipo == TIPO_FINDE else TIPO_FINDE
        )
        for tipo_candidato in (tipo, opuesto, "todos"):
            entrada = self._perfil.entrada(funcion, tipo_candidato, tramo)
            if entrada and entrada.get("n_dias", 0) >= MIN_DIAS_PERFIL:
                return PercentilesTramo(
                    p10=entrada["p10"],
                    p50=entrada["p50"],
                    p90=entrada["p90"],
                )
        return None

    def predecir_consumo(
        self, fecha: date, festivo_hoy: bool = False
    ) -> list[PercentilesTramo | None]:
        """Predicción p10/p50/p90 del consumo de los 96 tramos."""
        tipo = self.tipo_dia(fecha, festivo_hoy)
        return [
            self._percentiles_perfil("consumo", tipo, tramo)
            for tramo in range(TRAMOS_POR_DIA)
        ]

    def _predecir_fv_perfil(
        self, fecha: date, festivo_hoy: bool = False
    ) -> list[PercentilesTramo | None]:
        """Fallback estadístico de FV (perfil sobre el recorder)."""
        tipo = self.tipo_dia(fecha, festivo_hoy)
        return [
            self._percentiles_perfil("fv", tipo, tramo)
            for tramo in range(TRAMOS_POR_DIA)
        ]

    def _predecir_fv_solcast(
        self, fecha: date, entity_id: str
    ) -> list[PercentilesTramo | None] | None:
        """Previsión FV de Solcast para la fecha (None si no disponible).

        Devuelve ``None`` si la entidad no existe, está ``unavailable``,
        no tiene ``detailedForecast`` o no hay periodos de esa fecha.
        """
        if not entity_id:
            _LOGGER.info(
                "Sin entity_id de Solcast para %s; se usa el perfil", fecha
            )
            return None
        estado = self.hass.states.get(entity_id)
        if estado is None or estado.state in ("unavailable", "unknown"):
            _LOGGER.info(
                "Entidad Solcast %s inexistente o no disponible; "
                "se usa el perfil",
                entity_id,
            )
            return None
        atributo = (estado.attributes or {}).get("detailedForecast")
        if not atributo:
            _LOGGER.info(
                "La entidad %s no tiene detailedForecast; se usa el perfil",
                entity_id,
            )
            return None
        tramos = _parsear_detailed_forecast(atributo, fecha)
        if not any(tramo is not None for tramo in tramos):
            _LOGGER.info(
                "detailedForecast de %s sin periodos de %s; se usa el perfil",
                entity_id,
                fecha,
            )
            return None
        return tramos

    async def predecir_fv(
        self, fecha: date, festivo_hoy: bool = False
    ) -> tuple[list[PercentilesTramo | None], str]:
        """Previsión FV del día: Solcast preferente, perfil como fallback.

        El fallback es **por día** (M1 v1.3): si falla el de mañana, solo
        mañana cae al perfil. Devuelve (tramos, fuente).
        """
        clave = (
            CONF_ENTITY_ID_PREVISION_FV_HOY
            if fecha == datetime.now().astimezone().date()
            else CONF_ENTITY_ID_PREVISION_FV_MANANA
        )
        entity_id = self._params.get(clave, "")
        tramos = self._predecir_fv_solcast(fecha, entity_id)
        if tramos is not None:
            return tramos, FUENTE_FV_SOLCAST

        perfil = self._predecir_fv_perfil(fecha, festivo_hoy)
        if any(tramo is not None for tramo in perfil):
            return perfil, FUENTE_FV_PERFIL
        return perfil, FUENTE_FV_NONE

    async def predecir(
        self, fecha: date, festivo_hoy: bool = False
    ) -> PrediccionDia:
        """Predicción completa de un día (consumo + FV con su fuente)."""
        consumo = self.predecir_consumo(fecha, festivo_hoy)
        fv, fuente_fv = await self.predecir_fv(fecha, festivo_hoy)
        return PrediccionDia(
            fecha=fecha,
            consumo=consumo,
            fv=fv,
            fuente_fv=fuente_fv,
        )

    @property
    def perfil(self) -> _Perfil:
        """Perfil estadístico cargado (para atributos de la UI)."""
        return self._perfil

    def n_dias_tramo_actual(self, tipo: str, tramo: int) -> int | None:
        """Número de días válidos de un tramo del perfil de consumo."""
        entrada = self._perfil.entrada("consumo", tipo, tramo)
        return entrada.get("n_dias") if entrada else None


def manana_de(fecha: date) -> date:
    """Fecha del día siguiente (utilidad para el coordinador del plan)."""
    return fecha + timedelta(days=1)
