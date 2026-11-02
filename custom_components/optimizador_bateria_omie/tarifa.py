"""Modelo de tarifa eléctrica española 2.0TD."""
# versión 0.10.1

from datetime import datetime
from typing import Any

from custom_components.optimizador_bateria_omie.const import (
    CONF_APLICAR_IMPUESTOS_PRECIO,
    CONF_CARGO_SISTEMA_LLANO,
    CONF_CARGO_SISTEMA_PUNTA,
    CONF_CARGO_SISTEMA_VALLE,
    CONF_IMPUESTO_ELECTRICO_PCT,
    CONF_IVA_PCT,
    CONF_PEAYE_ACCESO_LLANO,
    CONF_PEAYE_ACCESO_PUNTA,
    CONF_PEAYE_ACCESO_VALLE,
    CONF_POTENCIA_CONTRATADA,
    DEFAULT_APLICAR_IMPUESTOS_PRECIO,
    DEFAULT_CARGO_SISTEMA_LLANO,
    DEFAULT_CARGO_SISTEMA_PUNTA,
    DEFAULT_CARGO_SISTEMA_VALLE,
    DEFAULT_IMPUESTO_ELECTRICO_PCT,
    DEFAULT_IVA_PCT,
    DEFAULT_PEAYE_ACCESO_LLANO,
    DEFAULT_PEAYE_ACCESO_PUNTA,
    DEFAULT_PEAYE_ACCESO_VALLE,
    DEFAULT_POTENCIA_CONTRATADA,
    FESTIVOS_ESPANA,
)


class ConfiguracionTarifa:
    """Configuración de la tarifa eléctrica.

    Desde F4 (deuda #5) los peajes, cargos e impuestos viven en
    ``entry.data`` con ``ConfiguracionTarifa.desde_entry``; los defaults
    de clase siguen siendo los valores confirmados 2.0TD. La potencia
    contratada tiene una única fuente: ``potencia_contratada`` (W) del
    entry (se convierte a kW aquí).
    """

    def __init__(
        self,
        potencia_contratada_kw: float = 5.75,
        impuesto_electrico_pct: float = DEFAULT_IMPUESTO_ELECTRICO_PCT,
        iva_pct: float = DEFAULT_IVA_PCT,
        aplicar_impuestos_precio: bool = DEFAULT_APLICAR_IMPUESTOS_PRECIO,
        peajes_acceso: dict[str, float] | None = None,
        cargos_sistema: dict[str, float] | None = None,
    ) -> None:
        self.potencia_contratada_kw = potencia_contratada_kw
        self.impuesto_electrico_pct = impuesto_electrico_pct
        self.iva_pct = iva_pct
        self.aplicar_impuestos_precio = aplicar_impuestos_precio
        self.peajes_acceso = peajes_acceso or {
            "punta": DEFAULT_PEAYE_ACCESO_PUNTA,
            "llano": DEFAULT_PEAYE_ACCESO_LLANO,
            "valle": DEFAULT_PEAYE_ACCESO_VALLE,
        }
        self.cargos_sistema = cargos_sistema or {
            "punta": DEFAULT_CARGO_SISTEMA_PUNTA,
            "llano": DEFAULT_CARGO_SISTEMA_LLANO,
            "valle": DEFAULT_CARGO_SISTEMA_VALLE,
        }

    @classmethod
    def desde_entry(cls, entry: Any) -> "ConfiguracionTarifa":
        """Configuración desde ``entry.data`` con defaults confirmados.

        La entrada de producción (sin las claves nuevas) obtiene
        exactamente los valores que estaban hardcodeados en F1.
        """
        data = getattr(entry, "data", None) or {}
        return cls(
            potencia_contratada_kw=float(
                data.get(
                    CONF_POTENCIA_CONTRATADA, DEFAULT_POTENCIA_CONTRATADA
                )
            )
            / 1000,
            impuesto_electrico_pct=float(
                data.get(
                    CONF_IMPUESTO_ELECTRICO_PCT,
                    DEFAULT_IMPUESTO_ELECTRICO_PCT,
                )
            ),
            iva_pct=float(data.get(CONF_IVA_PCT, DEFAULT_IVA_PCT)),
            aplicar_impuestos_precio=bool(
                data.get(
                    CONF_APLICAR_IMPUESTOS_PRECIO,
                    DEFAULT_APLICAR_IMPUESTOS_PRECIO,
                )
            ),
            peajes_acceso={
                "punta": float(
                    data.get(
                        CONF_PEAYE_ACCESO_PUNTA, DEFAULT_PEAYE_ACCESO_PUNTA
                    )
                ),
                "llano": float(
                    data.get(
                        CONF_PEAYE_ACCESO_LLANO, DEFAULT_PEAYE_ACCESO_LLANO
                    )
                ),
                "valle": float(
                    data.get(
                        CONF_PEAYE_ACCESO_VALLE, DEFAULT_PEAYE_ACCESO_VALLE
                    )
                ),
            },
            cargos_sistema={
                "punta": float(
                    data.get(
                        CONF_CARGO_SISTEMA_PUNTA, DEFAULT_CARGO_SISTEMA_PUNTA
                    )
                ),
                "llano": float(
                    data.get(
                        CONF_CARGO_SISTEMA_LLANO, DEFAULT_CARGO_SISTEMA_LLANO
                    )
                ),
                "valle": float(
                    data.get(
                        CONF_CARGO_SISTEMA_VALLE, DEFAULT_CARGO_SISTEMA_VALLE
                    )
                ),
            },
        )


class PeriodoTarifa:
    """Horarios de los períodos tarifarios 2.0TD (hora local del sistema)."""

    @classmethod
    def es_punta(cls, fecha_local: datetime) -> bool:
        """Punta: L–V 10:00-14:00 y 18:00-22:00 hora local."""
        hora = fecha_local.hour
        return (10 <= hora < 14) or (18 <= hora < 22)

    @classmethod
    def es_llano(cls, fecha_local: datetime) -> bool:
        """Llano: L–V 08:00-10:00, 14:00-18:00 y 22:00-24:00 hora local."""
        hora = fecha_local.hour
        return (8 <= hora < 10) or (14 <= hora < 18) or (22 <= hora < 24)

    @classmethod
    def es_valle(cls, fecha_local: datetime) -> bool:
        """Valle: L–V 00:00-08:00 hora local."""
        return not cls.es_punta(fecha_local) and not cls.es_llano(fecha_local)


class ModeloTarifa:
    """Modelo de tarifa eléctrica 2.0TD con peajes y cargos."""

    # Peajes de acceso a la red (2.0TD) - €/kWh
    PEAJE_ACCESO_PUNTA = 0.033261
    PEAJE_ACCESO_LLANO = 0.016409
    PEAJE_ACCESO_VALLE = 0.000077

    # Cargos del sistema (2.0TD) - €/kWh
    CARGO_SISTEMA_PUNTA = 0.064292
    CARGO_SISTEMA_LLANO = 0.012858
    CARGO_SISTEMA_VALLE = 0.003215

    def __init__(self, config: ConfiguracionTarifa | None = None):
        self.config = config or ConfiguracionTarifa()

    def obtener_periodo(
        self, fecha_hora_utc: datetime, festivo_manual: bool = False
    ) -> str:
        """Determina el período tarifario (punta, llano, valle).

        ``festivo_manual=True`` fuerza valle todo el día (override del switch
        ``festivo_hoy``).
        """
        fecha_local = fecha_hora_utc.astimezone()

        if festivo_manual:
            return "valle"
        if self.es_festivo(fecha_local) or self.es_fin_de_semana(fecha_local):
            return "valle"
        if PeriodoTarifa.es_punta(fecha_local):
            return "punta"
        if PeriodoTarifa.es_llano(fecha_local):
            return "llano"
        return "valle"

    def obtener_peaje_cargo(self, periodo: str) -> float:
        """Obtiene el peaje + cargo para un período dado.

        Desde F4 (deuda #5) los valores salen de la ``ConfiguracionTarifa``
        (entry.data); las constantes de clase quedan solo como defaults
        documentados.
        """
        return (
            self.config.peajes_acceso.get(periodo, 0.0)
            + self.config.cargos_sistema.get(periodo, 0.0)
        )

    def obtener_precio_efectivo(
        self,
        precio_spot: float,
        fecha_hora_utc: datetime,
        festivo_manual: bool = False,
    ) -> float:
        """Precio efectivo = (spot + peaje + cargo) × (1+IE) × (1+IVA)."""
        periodo = self.obtener_periodo(fecha_hora_utc, festivo_manual)
        precio_con_peajes = precio_spot + self.obtener_peaje_cargo(periodo)

        if self.config.aplicar_impuestos_precio:
            precio_con_impuesto = precio_con_peajes * (
                1 + self.config.impuesto_electrico_pct
            )
            precio_efectivo = precio_con_impuesto * (1 + self.config.iva_pct)
        else:
            precio_efectivo = precio_con_peajes

        return round(precio_efectivo, 6)

    def clasificar_por_periodo(self, fechas_hora_utc: list[datetime]) -> list[str]:
        """Clasifica por período 2.0TD (valle/llano/punta) según hora local."""
        return [self.obtener_periodo(f) for f in fechas_hora_utc]

    def clasificar_por_percentiles(self, precios: list[float]) -> list[str]:
        """Utilidad auxiliar: tercios por percentil. NO usada por el sensor.

        Se conserva para el planner (F3+), que puede necesitar saber si un
        tramo es caro *dentro* del día, independientemente del período 2.0TD.
        """
        if not precios or len(precios) < 3:
            return ["valle"] * 96

        precios_ordenados = sorted(precios)
        n = len(precios_ordenados)
        p33 = int(n * 0.33)
        p66 = int(n * 0.66)

        umbral_valle_llano = precios_ordenados[p33]
        umbral_llano_punta = precios_ordenados[p66]

        return [
            "valle" if p <= umbral_valle_llano
            else "llano" if p <= umbral_llano_punta
            else "punta"
            for p in precios
        ]

    def obtener_estadisticas(self, precios: list[float]) -> dict:
        """Estadísticas (min/max/mediana) de una lista de precios."""
        if not precios:
            return {"min": 0.0, "max": 0.0, "mediana": 0.0}

        precios_ordenados = sorted(precios)
        n = len(precios_ordenados)

        if n % 2 == 0:
            mediana = (precios_ordenados[n // 2 - 1] + precios_ordenados[n // 2]) / 2
        else:
            mediana = precios_ordenados[n // 2]

        return {
            "min": round(precios_ordenados[0], 6),
            "max": round(precios_ordenados[-1], 6),
            "mediana": round(mediana, 6),
        }

    def es_festivo(self, fecha: datetime) -> bool:
        """Festivo nacional (según FESTIVOS_ESPANA)."""
        return (fecha.month, fecha.day) in FESTIVOS_ESPANA

    def es_fin_de_semana(self, fecha: datetime) -> bool:
        """Sábado (5) o domingo (6)."""
        return fecha.weekday() >= 5