"""Tastiere inline per la revisione dei lead (Modulo 5) e menu principale."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def lead_review_keyboard(lead_id: int, website: str | None = None) -> InlineKeyboardMarkup:
    row1 = [
        InlineKeyboardButton("✅ APPROVA", callback_data=f"approve:{lead_id}"),
        InlineKeyboardButton("✏️ MODIFICA", callback_data=f"edit:{lead_id}"),
    ]
    row2 = [
        InlineKeyboardButton("⏭ SALTA", callback_data=f"skip:{lead_id}"),
        InlineKeyboardButton("🚫 BLACKLIST", callback_data=f"blacklist:{lead_id}"),
    ]
    rows = [row1, row2]
    if website:
        rows.append([InlineKeyboardButton("🌐 APRI SITO", url=website)])
    return InlineKeyboardMarkup(rows)


def confirm_edit_keyboard(lead_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ CONFERMA E APPROVA", callback_data=f"confirm_edit:{lead_id}")]]
    )


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Menu di bottoni fissi, sempre visibili sotto la tastiera di Telegram."""
    rows = [
        [KeyboardButton("🔎 Cerca lead"), KeyboardButton("📊 Statistiche")],
        [KeyboardButton("📬 Coda email"), KeyboardButton("🕓 Recenti")],
        [KeyboardButton("🚫 Blacklist"), KeyboardButton("⚙️ Impostazioni")],
        [KeyboardButton("⏸ Pausa invii"), KeyboardButton("▶️ Riprendi invii")],
        [KeyboardButton("❓ Aiuto")],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)
