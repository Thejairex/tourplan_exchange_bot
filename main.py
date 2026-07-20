"""
main.py
-------
Orquestador diario. Este es el script que se programa en cron a las 9am.

Flujo:
    1. Scrapea Dólar MEP y Dólar Oficial (ambos de dolarhoy.com).
    2. Calcula Dólar Emisivo = Oficial + 10.
    3. Valida que los valores no se desvíen demasiado del día anterior.
    4. Carga los 3 tipos de cambio en Tourplan NX vía Playwright.
    5. Loguea todo en logs/tourplan_fx_bot.log (con rotación diaria).
    6. Si algo falla en cualquier paso, envía un email de alerta y corta
       ANTES de dejar Tourplan en un estado a medio actualizar.

Configuración: ver .env.example (copiar a .env y completar).
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

from alertas import enviar_alerta
from scrapers import ScrapingError, obtener_cotizaciones
from tourplan_automation import TourplanAutomationError, cargar_tipos_de_cambio
from validacion import ValidacionError, validar_y_registrar

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"


def _setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(exist_ok=True)
    logger = logging.getLogger("tourplan_fx_bot")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = TimedRotatingFileHandler(
        LOG_DIR / "tourplan_fx_bot.log", when="midnight", backupCount=90, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    return logger


def _config_desde_env() -> dict:
    load_dotenv(BASE_DIR / ".env")
    cfg = {
        "tourplan_url": os.environ.get("TOURPLAN_URL", ""),
        "tourplan_user": os.environ.get("TOURPLAN_USER", ""),
        "tourplan_password": os.environ.get("TOURPLAN_PASSWORD", ""),
        "headless": os.environ.get("HEADLESS", "true").lower() != "false",
        "umbral_variacion_pct": float(os.environ.get("UMBRAL_VARIACION_PCT", "15")),
        "smtp_host": os.environ.get("SMTP_HOST", ""),
        "smtp_port": int(os.environ.get("SMTP_PORT", "587")),
        "smtp_user": os.environ.get("SMTP_USER", ""),
        "smtp_password": os.environ.get("SMTP_PASSWORD", ""),
        "alerta_email_to": os.environ.get("ALERTA_EMAIL_TO", ""),
    }
    faltantes = [k for k in ("tourplan_url", "tourplan_user", "tourplan_password") if not cfg[k]]
    if faltantes:
        raise RuntimeError(
            f"Faltan variables de entorno obligatorias en .env: {', '.join(faltantes)}"
        )
    return cfg


def _alertar(cfg: dict, asunto: str, cuerpo: str, logger: logging.Logger) -> None:
    logger.error(cuerpo)
    enviar_alerta(
        asunto=asunto,
        cuerpo=cuerpo,
        smtp_host=cfg["smtp_host"],
        smtp_port=cfg["smtp_port"],
        smtp_user=cfg["smtp_user"],
        smtp_password=cfg["smtp_password"],
        destinatario=cfg["alerta_email_to"],
    )


def main() -> int:
    logger = _setup_logging()
    hoy = date.today().isoformat()
    logger.info("=== Inicio actualización de tipos de cambio Tourplan (%s) ===", hoy)

    try:
        cfg = _config_desde_env()
    except RuntimeError as exc:
        # Sin config no podemos ni siquiera mandar el mail con SMTP; solo logueamos.
        logger.error(str(exc))
        return 1

    try:
        cot = obtener_cotizaciones()
    except ScrapingError as exc:
        _alertar(
            cfg,
            "[Tourplan FX Bot] Falló la extracción de cotizaciones",
            f"No se pudieron obtener las cotizaciones el {hoy}.\n\nDetalle: {exc}",
            logger,
        )
        return 1

    try:
        validar_y_registrar(cot, umbral_pct=cfg["umbral_variacion_pct"])
    except ValidacionError as exc:
        _alertar(
            cfg,
            "[Tourplan FX Bot] Cotización sospechosa - NO se cargó en Tourplan",
            (
                f"El {hoy} se detectó una variación anormal en las cotizaciones y "
                f"la carga en Tourplan se canceló como medida de seguridad.\n\n"
                f"Detalle: {exc}\n\n"
                f"Valores obtenidos: MEP={cot.dolar_mep} | Oficial={cot.dolar_oficial} | "
                f"Emisivo={cot.dolar_emisivo}\n\n"
                f"Si los valores son correctos (movimiento real del mercado), "
                f"cargalos manualmente en Tourplan y no hace falta ninguna otra acción: "
                f"mañana el bot va a tomar este valor como referencia."
            ),
            logger,
        )
        return 1

    try:
        cargar_tipos_de_cambio(
            mep=cot.dolar_mep,
            oficial=cot.dolar_oficial,
            emisivo=cot.dolar_emisivo,
            base_url=cfg["tourplan_url"],
            usuario=cfg["tourplan_user"],
            password=cfg["tourplan_password"],
            headless=cfg["headless"],
        )
    except TourplanAutomationError as exc:
        _alertar(
            cfg,
            "[Tourplan FX Bot] Falló la carga en Tourplan",
            (
                f"Las cotizaciones se obtuvieron correctamente el {hoy} "
                f"(MEP={cot.dolar_mep} | Oficial={cot.dolar_oficial} | Emisivo={cot.dolar_emisivo}) "
                f"pero falló la carga automática en Tourplan.\n\n"
                f"Detalle: {exc}\n\n"
                f"Revisar el screenshot de error en la carpeta logs/ del servidor "
                f"y cargar manualmente si es necesario."
            ),
            logger,
        )
        return 1

    logger.info(
        "=== Proceso completado OK (%s) -> MEP=%s | Oficial=%s | Emisivo=%s ===",
        hoy, cot.dolar_mep, cot.dolar_oficial, cot.dolar_emisivo,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
