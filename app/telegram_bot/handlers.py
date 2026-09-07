"""
Handler dei comandi Telegram (Modulo 10) e dei bottoni inline (Modulo 5).
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.database.db import get_session
from app.database.models import (
    ApprovalDecision,
    Blacklist,
    EmailMessage,
    EventLog,
    Lead,
    LeadStatus,
)
from app.services import dedup, lead_service, queue_service, stats_service
from app.telegram_bot.auth import is_authorized
from app.telegram_bot.keyboards import confirm_edit_keyboard, lead_review_keyboard, main_menu_keyboard

logger = logging.getLogger(__name__)


def _unauthorized_reply():
    return "⛔ Non sei autorizzato a usare questo bot."


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(_unauthorized_reply())
        return
    await update.message.reply_text(
        "👋 *Ciao!*\n\n"
        "Sono il tuo assistente per trovare nuovi clienti per la stampa 3D.\n\n"
        "Usa i bottoni qui sotto per muoverti — niente comandi da ricordare a memoria.\n\n"
        "Per una ricerca, scrivi:\n"
        "`/cerca Catania bar 10`\n"
        "(città, categoria, quanti risultati)",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(_unauthorized_reply())
        return
    text = (
        "📋 *Comandi disponibili*\n\n"
        "I bottoni qui sotto coprono quasi tutto. Solo la ricerca richiede scriverla:\n\n"
        "`/cerca Catania bar 10`\n"
        "→ città, categoria, numero massimo di risultati\n\n"
        "*Bottoni menu:*\n"
        "📊 Statistiche · 📬 Coda email · 🕓 Recenti\n"
        "🚫 Blacklist · ⚙️ Impostazioni\n"
        "⏸ Pausa invii · ▶️ Riprendi invii"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())


async def cmd_cerca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(_unauthorized_reply())
        return

    from app.config import settings as app_settings
    from app.leads.discovery import run_discovery

    args = context.args or []
    city = args[0] if len(args) > 0 else app_settings.default_city
    category = args[1] if len(args) > 1 else "bar"
    try:
        max_results = int(args[2]) if len(args) > 2 else 20
    except ValueError:
        max_results = 20

    await update.message.reply_text(
        f"🔎 Avvio ricerca: categoria='{category}', città='{city}', max={max_results}...\n"
        f"(provider: {app_settings.lead_provider})"
    )

    try:
        raw_leads = run_discovery(city=city, category=category, max_results=max_results)
    except Exception as exc:
        logger.exception("Errore durante la ricerca")
        await update.message.reply_text(f"❌ Errore durante la ricerca: {exc}")
        return

    with get_session() as session:
        new_leads = lead_service.ingest_raw_leads(
            session, raw_leads, city=city, category=category, max_results=max_results
        )
        session.flush()
        lead_ids = [l.id for l in new_leads]

    if not lead_ids:
        await update.message.reply_text(
            "Nessun nuovo lead da proporre (già presenti, in blacklist, o nessun risultato)."
        )
        return

    await update.message.reply_text(f"✅ Trovati {len(lead_ids)} nuovi lead. Te li mostro uno alla volta:")

    with get_session() as session:
        for lead_id in lead_ids:
            lead = session.get(Lead, lead_id)
            await send_lead_card(context, update.effective_chat.id, lead, session)


async def send_lead_card(context: ContextTypes.DEFAULT_TYPE, chat_id: int, lead: Lead, session) -> None:
    draft = lead_service.get_active_draft(session, lead)

    lines = [
        "🆕 *NUOVO POTENZIALE CLIENTE*",
        "",
        f"*Nome:* {lead.name}",
        f"*Categoria:* {lead.category or '-'}",
        f"*Città:* {lead.city or '-'}",
        f"*Sito:* {lead.website or '-'}",
        f"*Email:* {lead.email or '⚠️ non trovata'}",
        f"*Telefono:* {lead.phone or '-'}",
        f"*Fonte:* {lead.source.name if lead.source else '-'}",
        "",
        f"*Motivo interesse:* {lead.site_analysis or 'analisi non disponibile'}",
    ]

    if draft:
        lines += ["", "*PROPOSTA EMAIL*", "", f"*Oggetto:* {draft.subject}", "", draft.body]
    else:
        lines += ["", "⚠️ Nessuna bozza email disponibile (email non trovata)."]

    text = "\n".join(lines)
    keyboard = lead_review_keyboard(lead.id, website=lead.website)

    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=keyboard)


async def on_lead_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_authorized(update):
        await query.answer("Non autorizzato.", show_alert=True)
        return

    action, lead_id_str = query.data.split(":", 1)
    lead_id = int(lead_id_str)

    with get_session() as session:
        lead = session.get(Lead, lead_id)
        if not lead:
            await query.answer("Lead non trovato.", show_alert=True)
            return

        if action == "approve":
            from app.database.models import Approval

            session.add(
                Approval(
                    lead_id=lead.id,
                    decision=ApprovalDecision.APPROVE,
                    telegram_user_id=update.effective_user.id,
                )
            )
            lead.status = LeadStatus.APPROVED
            try:
                queue_service.enqueue_approved_lead(session, lead)
                await query.answer("✅ Approvato e messo in coda.")
                await query.edit_message_reply_markup(reply_markup=None)
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"✅ *{lead.name}* approvato e in coda di invio.",
                    parse_mode="Markdown",
                )
            except ValueError as exc:
                await query.answer(str(exc), show_alert=True)

        elif action == "skip":
            from app.database.models import Approval

            session.add(
                Approval(lead_id=lead.id, decision=ApprovalDecision.SKIP, telegram_user_id=update.effective_user.id)
            )
            lead.status = LeadStatus.SKIPPED
            await query.answer("⏭ Saltato.")
            await query.edit_message_reply_markup(reply_markup=None)

        elif action == "blacklist":
            from app.database.models import Approval

            session.add(
                Approval(
                    lead_id=lead.id, decision=ApprovalDecision.BLACKLIST, telegram_user_id=update.effective_user.id
                )
            )
            lead.status = LeadStatus.BLACKLISTED
            dedup.add_to_blacklist(
                session,
                reason="Bloccato manualmente da Telegram",
                domain=lead.domain,
                email=lead.email,
                phone=lead.phone,
                name=lead.name,
                address=lead.address,
            )
            await query.answer("🚫 Aggiunto in blacklist.")
            await query.edit_message_reply_markup(reply_markup=None)

        elif action == "edit":
            context.user_data["editing_lead_id"] = lead.id
            await query.answer()
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    f"✏️ Invia ora il nuovo testo dell'email per *{lead.name}*.\n"
                    "Formato: prima riga = oggetto, righe successive = corpo del messaggio."
                ),
                parse_mode="Markdown",
            )

        session.flush()


MENU_BUTTON_ACTIONS = {
    "📊 Statistiche": "cmd_stats",
    "📬 Coda email": "cmd_queue",
    "🕓 Recenti": "cmd_recent",
    "🚫 Blacklist": "cmd_blacklist",
    "⚙️ Impostazioni": "cmd_settings",
    "⏸ Pausa invii": "cmd_pause",
    "▶ Riprendi invii": "cmd_resume",
    "❓ Aiuto": "cmd_help",
}


async def on_edit_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cattura sia i tap sui bottoni del menu fisso, sia il testo inviato dopo MODIFICA."""
    if not is_authorized(update):
        return

    text = (update.message.text or "").strip()

    # Bottone "🔎 Cerca lead": spiega come usare /cerca (serve città+categoria)
    if text == "🔎 Cerca lead":
        await update.message.reply_text(
            "Scrivi il comando così:\n`/cerca Catania bar 10`\n\n"
            "(prima la città, poi la categoria, poi quanti risultati)",
            parse_mode="Markdown",
        )
        return

    # Altri bottoni del menu fisso: richiamano direttamente il comando corrispondente
    action_name = MENU_BUTTON_ACTIONS.get(text)
    if action_name:
        await globals()[action_name](update, context)
        return

    # Altrimenti, è testo inviato dopo aver premuto MODIFICA su un lead
    lead_id = context.user_data.get("editing_lead_id")
    if not lead_id:
        return  # non stiamo aspettando una modifica: ignora il messaggio

    text = update.message.text or ""
    lines = text.split("\n", 1)
    subject = lines[0].strip()
    body = lines[1].strip() if len(lines) > 1 else ""

    if not subject or not body:
        await update.message.reply_text(
            "⚠ Formato non valido. Prima riga = oggetto, resto = corpo. Riprova."
        )
        return

    with get_session() as session:
        lead = session.get(Lead, lead_id)
        if not lead:
            await update.message.reply_text("Lead non trovato.")
            context.user_data.pop("editing_lead_id", None)
            return

        lead_service.update_draft(session, lead, subject, body)
        session.flush()

    context.user_data.pop("editing_lead_id", None)

    await update.message.reply_text(
        "✅ Bozza aggiornata. Conferma per approvarla e metterla in coda:",
        reply_markup=confirm_edit_keyboard(lead_id),
    )


async def on_confirm_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_authorized(update):
        await query.answer("Non autorizzato.", show_alert=True)
        return

    _, lead_id_str = query.data.split(":", 1)
    lead_id = int(lead_id_str)

    with get_session() as session:
        lead = session.get(Lead, lead_id)
        if not lead:
            await query.answer("Lead non trovato.", show_alert=True)
            return
        from app.database.models import Approval

        session.add(
            Approval(lead_id=lead.id, decision=ApprovalDecision.EDIT, telegram_user_id=update.effective_user.id)
        )
        lead.status = LeadStatus.APPROVED
        try:
            queue_service.enqueue_approved_lead(session, lead)
            await query.answer("✅ Approvato e in coda.")
            await query.edit_message_reply_markup(reply_markup=None)
        except ValueError as exc:
            await query.answer(str(exc), show_alert=True)
        session.flush()


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(_unauthorized_reply())
        return

    with get_session() as session:
        g = stats_service.get_global_stats(session)
        by_cat = stats_service.get_stats_by_category(session)

    lines = [
        "📊 *Statistiche generali*",
        f"Lead trovati: {g.leads_found}",
        f"Lead approvati: {g.leads_approved}",
        f"Email inviate: {g.emails_sent}",
        f"Email fallite: {g.emails_failed}",
        f"Risposte: {g.replies}",
        f"Interessati: {g.interested}",
        f"Clienti ottenuti: {g.clients}",
        "",
        f"Tasso approvazione: {g.approval_rate}%",
        f"Tasso risposta: {g.reply_rate}%",
        f"Tasso conversione: {g.conversion_rate}%",
    ]

    if by_cat:
        lines += ["", "*Per categoria:*"]
        for cat, cs in by_cat.items():
            lines.append(f"{cat}: {cs.contacted} contatti, {cs.replies} risposte, {cs.clients} clienti")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(_unauthorized_reply())
        return
    with get_session() as session:
        queued = queue_service.list_queue(session)
    if not queued:
        await update.message.reply_text("La coda email è vuota.")
        return
    lines = [f"- {m.to_email} ({m.lead.name if m.lead else '?'})" for m in queued]
    await update.message.reply_text("📬 *Coda email:*\n" + "\n".join(lines), parse_mode="Markdown")


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(_unauthorized_reply())
        return
    from datetime import datetime

    with get_session() as session:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        sent_today = (
            session.query(EmailMessage)
            .filter(EmailMessage.status == "SENT", EmailMessage.sent_at >= today_start)
            .all()
        )
    if not sent_today:
        await update.message.reply_text("Nessuna email inviata oggi.")
        return
    lines = [f"- {m.to_email} ({m.lead.name if m.lead else '?'}) alle {m.sent_at.strftime('%H:%M')}" for m in sent_today]
    await update.message.reply_text("📧 *Inviate oggi:*\n" + "\n".join(lines), parse_mode="Markdown")


async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(_unauthorized_reply())
        return
    with get_session() as session:
        recent = session.query(Lead).order_by(Lead.discovered_at.desc()).limit(10).all()
    if not recent:
        await update.message.reply_text("Nessun lead trovato finora.")
        return
    lines = [f"- {l.name} ({l.city}) — stato: {l.status.value}" for l in recent]
    await update.message.reply_text("🕓 *Ultimi lead:*\n" + "\n".join(lines), parse_mode="Markdown")


async def cmd_blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(_unauthorized_reply())
        return
    with get_session() as session:
        rows = session.query(Blacklist).order_by(Blacklist.created_at.desc()).limit(30).all()
    if not rows:
        await update.message.reply_text("La blacklist è vuota.")
        return
    lines = [f"- [{b.identifier_type}] {b.identifier_value}" for b in rows]
    await update.message.reply_text("🚫 *Blacklist:*\n" + "\n".join(lines), parse_mode="Markdown")


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(_unauthorized_reply())
        return
    from app.config import settings as app_settings

    with get_session() as session:
        paused = queue_service.is_paused(session)

    lines = [
        "⚙ *Impostazioni correnti*",
        f"Provider ricerca: {app_settings.lead_provider}",
        f"Provider AI: {app_settings.ai_provider}",
        f"Città/provincia default: {app_settings.default_city}/{app_settings.default_province}",
        f"Max email/giorno: {app_settings.max_emails_per_day}",
        f"Max email/ora: {app_settings.max_emails_per_hour}",
        f"Intervallo minimo tra invii: {app_settings.min_seconds_between_emails}s",
        f"Fascia oraria invio: {app_settings.sending_hour_start}:00-{app_settings.sending_hour_end}:00",
        f"Invii in pausa: {'sì' if paused else 'no'}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(_unauthorized_reply())
        return
    with get_session() as session:
        queue_service.set_paused(session, True)
    await update.message.reply_text("⏸ Invii messi in pausa. La coda resta invariata. Usa /resume per riattivare.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(_unauthorized_reply())
        return
    with get_session() as session:
        queue_service.set_paused(session, False)
    await update.message.reply_text("▶️ Invii riattivati.")
