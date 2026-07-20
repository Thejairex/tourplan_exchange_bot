"""
alertas.py
----------
Envío de email de alerta cuando falla algún paso del proceso.
Usa SMTP simple (funciona con Gmail, Office365, o cualquier SMTP corporativo).
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger("tourplan_fx_bot.alertas")


def enviar_alerta(
    asunto: str,
    cuerpo: str,
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    destinatario: str,
    usar_tls: bool = True,
) -> None:
    if not smtp_host or not destinatario:
        logger.warning(
            "No hay configuración de email de alerta (SMTP_HOST/ALERTA_EMAIL_TO); "
            "se omite el envío. Revisar el log para ver el detalle del error."
        )
        return

    msg = MIMEText(cuerpo, "plain", "utf-8")
    msg["Subject"] = asunto
    msg["From"] = smtp_user
    msg["To"] = destinatario

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            if usar_tls:
                server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, [destinatario], msg.as_string())
        logger.info("Email de alerta enviado a %s.", destinatario)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo enviar el email de alerta.")
