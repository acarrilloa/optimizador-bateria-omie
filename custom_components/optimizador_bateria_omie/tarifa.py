"""Modelo de tarifa eléctrica española 2.0TD."""
# versión 0.7.1

from datetime import datetime

from custom_components.optimizador_bateria_omie.const import FESTIVOS_ESPANA


class ConfiguracionTarifa:
    """Configuración de la tarifa eléctrica."""

    def __init__(
        self,
        potencia_contratada_kw: float = 5.75,
        impuesto_electrico_pct: float = 0.051127,
        iva_pct: float = 0.21,
        aplicar_impuestos_precio: bool = True,
    ):
        self.potencia_contratada_kw = potencia_contratada_kw
        self.impuesto_electrico_pct = impuesto_electrico_pct
        self.iva_pct = iva_pct
        self.aplicar_impuestos_precio = aplicar_impuestos_precio


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

    def obtener_periodo(self, fecha_hora_utc: datetime) -> str:
        """Determina el período tarifario (punta, llano, valle).

        - Fines de semana y festivos nacionales → valle todo el día.
        - Laborables: ventanas horarias en hora local.
        """
        fecha_local = fecha_hora_utc.astimezone()  # CET/CEST automático

        if self.es_festivo(fecha_local) or self.es_fin_de_semana(fecha_local):
            return "valle"

        if PeriodoTarifa.es_punta(fecha_local):
            return "punta"
        if PeriodoTarifa.es_llano(fecha_local):
            return "llano"
        return "valle"

    def obtener_peaje_cargo(self, periodo: str) -> float:
        """Obtiene el peaje + cargo para un período dado."""
        if periodo == "punta":
            return self.PEAJE_ACCESO_PUNTA + self.CARGO_SISTEMA_PUNTA
        if periodo == "llano":
            return self.PEAJE_ACCESO_LLANO + self.CARGO_SISTEMA_LLANO
        return self.PEAJE_ACCESO_VALLE + self.CARGO_SISTEMA_VALLE

    def obtener_precio_efectivo(
        self, precio_spot: float, fecha_hora_utc: datetime
    ) -> float:
        """Precio efectivo = (spot + peaje + cargo) × (1+IE) × (1+IVA)."""
        periodo = self.obtener_periodo(fecha_hora_utc)
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