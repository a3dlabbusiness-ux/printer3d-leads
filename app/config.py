"""
Configurazione centrale dell'applicazione.

Tutti i valori vengono letti dal file .env (vedi .env.example per il template).
Nessuna credenziale è hardcoded qui dentro: questo file legge soltanto.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    # Telegram
    telegram_bot_token: str = ""
    telegram_admin_id: int = 0

    # Database
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'leads.db'}"

    # Lead discovery
    lead_provider: str = "csv"
    google_places_api_key: str = ""
    csv_import_path: str = str(BASE_DIR / "data" / "leads_import.csv")

    # SMTP
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    email_from: str = ""
    email_from_name: str = ""

    # IMAP
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""

    # Limiti invio
    max_emails_per_day: int = 10
    max_emails_per_hour: int = 5
    min_seconds_between_emails: int = 300
    sending_hour_start: int = 9
    sending_hour_end: int = 18
    sending_days: str = "0,1,2,3,4"  # lun-ven

    # Ricerca default
    default_city: str = "Catania"
    default_province: str = "CT"

    # AI
    ai_provider: str = "template"
    ai_api_key: str = ""
    ai_model: str = "claude-sonnet-4-6"

    # Brevo API (invio email via HTTPS invece di SMTP diretto — bypassa i blocchi
    # di porta SMTP che molti provider cloud applicano di default)
    brevo_api_key: str = ""

    # Mittente / compliance
    sender_company_name: str = "La Tua Attività"
    sender_contact_email: str = ""
    sender_phone: str = ""
    opt_out_text: str = (
        'Se non desideri ricevere ulteriori comunicazioni, rispondi con "STOP".'
    )

    # Logging
    log_level: str = "INFO"
    log_file: str = str(BASE_DIR / "logs" / "app.log")

    # Web App (PWA) - sostituisce Telegram come interfaccia di controllo
    web_admin_password: str = ""
    web_host: str = "0.0.0.0"
    web_port: int = 8000
    web_session_hours: int = 24 * 30  # sessione valida 30 giorni (comodo su un telefono personale)

    @property
    def sending_days_list(self) -> List[int]:
        return [int(d) for d in self.sending_days.split(",") if d.strip() != ""]


settings = Settings()
