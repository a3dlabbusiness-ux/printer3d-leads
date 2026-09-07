"""
Invio email tramite SMTP (provider email legittimo configurato dall'utente,
es. Gmail, provider aziendale, SendGrid SMTP, ecc).

Nessuna tecnica di spoofing, nessuna rotazione di account: un solo mittente
configurato in .env, esattamente come dichiarato nel campo EMAIL_FROM.
"""
from __future__ import annotations

import logging
import smtplib
import socket
from email.message import EmailMessage as MimeEmailMessage

from app.config import settings

logger = logging.getLogger(__name__)


# --- Fix per VPS senza connettività IPv6 (es. alcuni piani DigitalOcean) ---
# Senza questa correzione, smtplib prova prima l'indirizzo IPv6 del server email
# (es. Gmail) e fallisce con "Network is unreachable" se il server non ha IPv6
# attivo, anche se la connessione IPv4 funzionerebbe perfettamente.
_original_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    return _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _getaddrinfo_ipv4_only


class SmtpSendError(Exception):
    pass


def send_email(to_email: str, subject: str, body: str) -> None:
    if not settings.smtp_host or not settings.smtp_username or not settings.smtp_password:
        raise SmtpSendError(
            "Configurazione SMTP incompleta: controlla SMTP_HOST, SMTP_USERNAME, "
            "SMTP_PASSWORD nel file .env."
        )
    if not settings.email_from:
        raise SmtpSendError("EMAIL_FROM non configurato nel file .env.")

    msg = MimeEmailMessage()
    msg["Subject"] = subject
    from_header = (
        f"{settings.email_from_name} <{settings.email_from}>"
        if settings.email_from_name
        else settings.email_from
    )
    msg["From"] = from_header
    msg["To"] = to_email
    msg.set_content(body)

    try:
        if settings.smtp_use_tls:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
                server.starttls()
                server.login(settings.smtp_username, settings.smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20) as server:
                server.login(settings.smtp_username, settings.smtp_password)
                server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        # Non logghiamo mai la password; logghiamo solo l'errore SMTP/di rete.
        logger.error("Errore invio SMTP verso %s: %s", to_email, exc)
        raise SmtpSendError(str(exc)) from exc
