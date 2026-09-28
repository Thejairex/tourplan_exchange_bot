"""
heartbeat.py
------------
Ping a un servicio externo tipo dead-man's-switch (ej. healthchecks.io) al
final de una corrida 100% exitosa.

Por qué existe: el mail de alerta de alertas.py depende de la misma
configuración (.env) que el resto del bot. El 31/08/2026 el bot vino
fallando en silencio ~40 días porque el .env no llegaba al proceso lanzado
por cron - y como el SMTP también vive ahí, ni siquiera pudo avisar por
mail. Un heartbeat es un canal aparte: si el bot no llega a pingear un día
(por cualquier motivo, incluida una falla que le impida mandar su propio
mail), el servicio externo nota la ausencia del ping y avisa por su cuenta,
sin depender de la salud del bot.
"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger("tourplan_fx_bot.heartbeat")

TIMEOUT_SEG = 10


def enviar_heartbeat(url: str) -> None:
    """Nunca lanza: un fallo del ping no debe romper una corrida que salió
    bien. Si `url` está vacía, se omite en silencio (heartbeat desactivado)."""
    if not url:
        logger.debug("HEARTBEAT_URL no configurada; se omite el ping.")
        return

    try:
        requests.get(url, timeout=TIMEOUT_SEG)
        logger.info("Heartbeat enviado a %s.", url)
    except requests.exceptions.RequestException:
        logger.exception("No se pudo enviar el heartbeat (no bloquea la corrida).")
