"""
validacion.py
--------------
Guarda un historial simple (JSON) de las cotizaciones cargadas cada día y
valida que el nuevo valor no se desvíe demasiado del último cargado. Esto
evita que un error de scraping (por ejemplo, si dolarhoy.com muestra un
valor corrupto o el BNA está caído y devuelve una página de error) termine
cargando un tipo de cambio disparatado en Tourplan.

Si la variación supera el umbral (por defecto 15%), la corrida se corta
ANTES de tocar Tourplan y se dispara la alerta por email.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date
from pathlib import Path

from scrapers import Cotizaciones

logger = logging.getLogger("tourplan_fx_bot.validacion")

DATA_FILE = Path(__file__).parent / "data" / "historial_cotizaciones.json"


class ValidacionError(RuntimeError):
    """Se lanza cuando un valor nuevo se desvía demasiado del anterior."""


def _cargar_historial() -> dict:
    if not DATA_FILE.exists():
        return {}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("El archivo de historial está corrupto, se reinicia.")
        return {}


def _guardar_historial(historial: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(historial, indent=2, ensure_ascii=False), encoding="utf-8")


def validar_y_registrar(cot: Cotizaciones, umbral_pct: float = 15.0) -> None:
    """Compara contra el último valor registrado; si está OK, lo guarda.

    Lanza ValidacionError si algún valor se desvía más del umbral permitido.
    No lanza nada la primera vez que se corre (no hay valor previo).
    """
    historial = _cargar_historial()
    ultimo = historial.get("ultimo")

    if ultimo:
        for campo, valor_nuevo in asdict(cot).items():
            valor_anterior = ultimo.get(campo)
            if not valor_anterior:
                continue
            variacion_pct = abs(valor_nuevo - valor_anterior) / valor_anterior * 100
            if variacion_pct > umbral_pct:
                raise ValidacionError(
                    f"'{campo}' varió {variacion_pct:.1f}% respecto de ayer "
                    f"({valor_anterior} -> {valor_nuevo}), supera el umbral de {umbral_pct}%. "
                    f"Se corta la corrida sin tocar Tourplan para evitar cargar un valor incorrecto."
                )

    hoy = date.today().isoformat()
    historial["ultimo"] = asdict(cot)
    historial["ultimo"]["fecha"] = hoy
    historial.setdefault("historico", []).append({**asdict(cot), "fecha": hoy})
    _guardar_historial(historial)
    logger.info("Cotizaciones validadas y registradas en historial (%s).", hoy)
