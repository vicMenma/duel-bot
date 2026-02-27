"""
🤖 DuelBot Telegram V3 — MarkdownV2 safe + fuseaux horaires
Nécessite: pip install python-telegram-bot pytz
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Optional

import pytz
from telegram import Update, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN")
MAIN_GROUP_ID  = int(os.environ.get("MAIN_GROUP_ID", "0"))
DATA_FILE      = "duel_data.json"
DUEL_TIMEOUT   = 300
ACCEPT_TIMEOUT = 300
VIDEO_SIZE_LIMIT = 70 * 1024 * 1024

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def esc(text: str) -> str:
    """Échappe les caractères spéciaux pour MarkdownV2 Telegram."""
    special = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(special)}])', r'\\\1', str(text))


# ─────────────────────────────────────────────
#  FUSEAUX HORAIRES
# ─────────────────────────────────────────────

COMMON_TIMEZONES = {
    "🌍 Paris / Afrique francophone": "Europe/Paris",
    "🌍 Kinshasa / Brazzaville":      "Africa/Kinshasa",
    "🌍 Abidjan / Dakar":             "Africa/Abidjan",
    "🌍 Lagos / Douala":              "Africa/Lagos",
    "🌍 Nairobi":                     "Africa/Nairobi",
    "🌍 Johannesburg":                "Africa/Johannesburg",
    "🌍 Le Caire":                    "Africa/Cairo",
    "🌍 Londres":                     "Europe/London",
    "🌍 Moscou":                      "Europe/Moscow",
    "🌎 New York":                    "America/New_York",
    "🌎 Los Angeles":                 "America/Los_Angeles",
    "🌎 Montréal":                    "America/Montreal",
    "🌏 Dubai":                       "Asia/Dubai",
    "🌏 Tokyo":                       "Asia/Tokyo",
    "🌏 Pékin":                       "Asia/Shanghai",
}

TZ_STR_TO_LABEL = {tz: label for label, tz in COMMON_TIMEZONES.items()}


def get_offset_str(tz_string: str) -> str:
    try:
        tz  = pytz.timezone(tz_string)
        now = datetime.now(tz)
        offset = now.utcoffset()
        total_seconds = int(offset.total_seconds())
        sign = "+" if total_seconds >= 0 else "-"
        total_seconds = abs(total_seconds)
        hours, remainder = divmod(total_seconds, 3600)
        minutes = remainder // 60
        return f"UTC{sign}{hours:02d}:{minutes:02d}"
    except Exception:
        return "UTC?"


def parse_time_input(text: str) -> Optional[datetime]:
    text = text.strip()
    now  = datetime.now()
    patterns = [
        (r"^(\d{1,2}):(\d{2})$",
         lambda m: now.replace(hour=int(m[0]), minute=int(m[1]), second=0, microsecond=0)),
        (r"^(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})$",
         lambda m: now.replace(day=int(m[0]), month=int(m[1]), hour=int(m[2]), minute=int(m[3]), second=0, microsecond=0)),
        (r"^(\d{1,2}):(\d{2})\s+(\d{1,2})/(\d{1,2})$",
         lambda m: now.replace(hour=int(m[0]), minute=int(m[1]), day=int(m[2]), month=int(m[3]), second=0, microsecond=0)),
        (r"^(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})$",
         lambda m: datetime(int(m[2]), int(m[1]), int(m[0]), int(m[3]), int(m[4]))),
    ]
    for pattern, builder in patterns:
        match = re.match(pattern, text)
        if match:
            try:
                result = builder(match.groups())
                if result < now and len(match.groups()) <= 2:
                    result += timedelta(days=1)
                return result
            except ValueError:
                continue
    return None


# ─────────────────────────────────────────────
#  PERSISTANCE
# ─────────────────────────────────────────────

def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"players": {}, "duels": {}, "history": [], "monitored_chats": []}


def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def get_player(data: dict, user_id: int, username: str = None) -> dict:
    uid = str(user_id)
    if uid not in data["players"]:
        data["players"][uid] = {
            "username": username or str(user_id),
            "points": 0, "wins": 0, "losses": 0,
            "duels_played": 0, "timezone": None,
            "joined": datetime.now().isoformat()
        }
    elif username:
        data["players"][uid]["username"] = username
    return data["players"][uid]


def format_leaderboard(data: dict) -> str:
    players = [(uid, p) for uid, p in data["players"].items() if p.get("duels_played", 0) > 0]
    if not players:
        return "📊 Aucun joueur au classement pour l'instant\\."
    players.sort(key=lambda x: x[1]["points"], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines  = ["🏆 *CLASSEMENT DES DUELS*\n"]
    for i, (uid, p) in enumerate(players[:10]):
        medal  = medals[i] if i < 3 else f"{i+1}\\."
        name   = esc(p.get("username", uid))
        pts    = esc(p["points"])
        wins   = p.get("wins", 0)
        losses = p.get("losses", 0)
        lines.append(f"{medal} @{name} — *{pts} pts* \\({wins}W/{losses}L\\)")
    return "\n".join(lines)


async def get_member_in_chat(bot, chat_id: int, user_id: int) -> Optional[ChatMember]:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status in [ChatMember.LEFT, ChatMember.BANNED]:
            return None
        return member
    except Exception:
        return None


async def find_common_chat(bot, data: dict, user1_id: int, user2_id: int) -> Optional[int]:
    for chat_id in data.get("monitored_chats", []):
        try:
            bot_member = await bot.get_chat_member(chat_id, bot.id)
            if bot_member.status not in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                continue
            m1 = await get_member_in_chat(bot, chat_id, user1_id)
            m2 = await get_member_in_chat(bot, chat_id, user2_id)
            if m1 and m2:
                return chat_id
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────
#  FUSEAU — /settimezone
# ─────────────────────────────────────────────

def tz_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for label, tz_str in COMMON_TIMEZONES.items():
        offset   = get_offset_str(tz_str)
        btn_text = f"{label} ({offset})"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"settz:{user_id}:{tz_str}")])
    return InlineKeyboardMarkup(buttons)


async def cmd_settimezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        "🌍 *Choisis ton fuseau horaire :*\nIl sera utilisé pour les duels planifiés\\.",
        reply_markup=tz_keyboard(user.id),
        parse_mode="MarkdownV2"
    )


async def callback_settz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    parts  = query.data.split(":", 2)
    if len(parts) < 3:
        return
    _, uid_str, tz_str = parts
    if query.from_user.id != int(uid_str):
        await query.answer("❌ Ce menu n'est pas pour toi.", show_alert=True)
        return
    data = load_data()
    p    = get_player(data, int(uid_str), query.from_user.username or query.from_user.first_name)
    p["timezone"] = tz_str
    save_data(data)
    label  = TZ_STR_TO_LABEL.get(tz_str, tz_str)
    offset = get_offset_str(tz_str)
    await query.edit_message_text(
        f"✅ Fuseau enregistré : *{esc(label)}* \\({esc(offset)}\\)\n"
        f"Les heures de duel te seront affichées dans ce fuseau\\.",
        parse_mode="MarkdownV2"
    )


# ─────────────────────────────────────────────
#  /duel
# ─────────────────────────────────────────────

async def cmd_duel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    challenger = update.effective_user
    data       = load_data()

    if not context.args:
        await update.message.reply_text(
            "❌ Usage :\n"
            "`/duel @pseudo` — duel immédiat\n"
            "`/duel @pseudo 18:30` — aujourd'hui à 18h30\n"
            "`/duel @pseudo 18:30 25/07` — date précise",
            parse_mode="MarkdownV2"
        )
        return

    target_username = context.args[0].lstrip("@").lower()
    target_uid, target_data = None, None
    for uid, p in data.get("players", {}).items():
        if p.get("username", "").lower() == target_username:
            target_uid  = int(uid)
            target_data = p
            break

    if not target_uid:
        await update.message.reply_text(
            f"❌ @{esc(target_username)} n'est pas inscrit\\. Il/elle doit faire `/join` d'abord \\!",
            parse_mode="MarkdownV2"
        )
        return

    if target_uid == challenger.id:
        await update.message.reply_text("😂 Tu ne peux pas te défier toi\\-même \\!", parse_mode="MarkdownV2")
        return

    duel_key = f"{min(challenger.id, target_uid)}_{max(challenger.id, target_uid)}"
    if duel_key in data.get("duels", {}):
        await update.message.reply_text("⚠️ Un duel est déjà en cours entre vous deux \\!")
        return

    scheduled_ts = None
    display_info = ""

    if len(context.args) >= 2:
        time_str = " ".join(context.args[1:])
        naive_dt = parse_time_input(time_str)
        if naive_dt is None:
            await update.message.reply_text(
                "❌ Format d'heure invalide\\.\nExemples : `18:30` · `18:30 25/07` · `25/07/2025 18:30`",
                parse_mode="MarkdownV2"
            )
            return

        challenger_p      = get_player(data, challenger.id, challenger.username or challenger.first_name)
        challenger_tz_str = challenger_p.get("timezone") or "UTC"
        challenger_tz     = pytz.timezone(challenger_tz_str)
        aware_dt          = challenger_tz.localize(naive_dt)
        now_utc           = datetime.now(pytz.utc)

        if aware_dt < now_utc + timedelta(minutes=2):
            await update.message.reply_text(
                "❌ L'heure planifiée doit être dans au moins 2 minutes dans le futur\\.",
                parse_mode="MarkdownV2"
            )
            return

        scheduled_ts      = aware_dt.timestamp()
        challenged_tz_str = target_data.get("timezone") or "UTC"
        challenged_tz     = pytz.timezone(challenged_tz_str)
        dt_for_challenged = aware_dt.astimezone(challenged_tz)
        dt_for_challenger = aware_dt.astimezone(challenger_tz)
        off_challenger    = get_offset_str(challenger_tz_str)
        off_challenged    = get_offset_str(challenged_tz_str)
        lbl_challenger    = TZ_STR_TO_LABEL.get(challenger_tz_str, challenger_tz_str)
        lbl_challenged    = TZ_STR_TO_LABEL.get(challenged_tz_str, challenged_tz_str)

        chal_name = esc(challenger.username or challenger.first_name)
        tgt_name  = esc(target_username)

        display_info = (
            f"\n\n🗓️ *Heure du duel proposée :*\n"
            f"  • Pour toi \\(@{chal_name}\\) : "
            f"`{esc(dt_for_challenger.strftime('%d/%m/%Y %H:%M'))}` _{esc(lbl_challenger)} \\({esc(off_challenger)}\\)_\n"
            f"  • Pour @{tgt_name} : "
            f"`{esc(dt_for_challenged.strftime('%d/%m/%Y %H:%M'))}` _{esc(lbl_challenged)} \\({esc(off_challenged)}\\)_\n"
        )
        if not target_data.get("timezone"):
            display_info += (
                f"\n⚠️ @{tgt_name} n'a pas encore défini son fuseau \\(`/settimezone`\\)\\. "
                f"L'heure affichée est en UTC\\."
            )

    if "duels" not in data:
        data["duels"] = {}

    data["duels"][duel_key] = {
        "challenger_id":   challenger.id,
        "challenger_name": challenger.username or challenger.first_name,
        "challenged_id":   target_uid,
        "challenged_name": target_data["username"],
        "chat_id":         None,
        "status":          "pending",
        "created_at":      time.time(),
        "scheduled_ts":    scheduled_ts,
        "chat_id_origin":  update.effective_chat.id,
        "penalty_flag":    {}
    }
    save_data(data)

    chal_name = esc(challenger.username or challenger.first_name)
    tgt_name  = esc(target_data["username"])

    if scheduled_ts:
        msg = (
            f"⚔️ *DÉFI PLANIFIÉ \\!*\n\n"
            f"@{chal_name} défie @{tgt_name} \\!"
            f"{display_info}\n"
            f"@{tgt_name}, réponds avec `/accept` ou `/decline`\\.\n"
            f"⏱️ Tu as 5 minutes pour répondre\\."
        )
    else:
        msg = (
            f"⚔️ *DÉFI LANCÉ \\!*\n\n"
            f"@{chal_name} défie @{tgt_name} en duel immédiat \\!\\!\n\n"
            f"@{tgt_name}, réponds avec `/accept` pour accepter "
            f"ou `/decline` pour refuser\\.\n"
            f"⏱️ Tu as 5 minutes pour répondre\\."
        )

    await update.message.reply_text(msg, parse_mode="MarkdownV2")
    asyncio.create_task(duel_accept_timeout(context.bot, duel_key))


# ─────────────────────────────────────────────
#  /accept
# ─────────────────────────────────────────────

async def cmd_accept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()

    active_key, active_duel = None, None
    for key, duel in data.get("duels", {}).items():
        if duel["challenged_id"] == user.id and duel["status"] == "pending":
            active_key  = key
            active_duel = duel
            break

    if not active_duel:
        await update.message.reply_text("❌ Tu n'as aucun duel en attente.")
        return

    common_chat = await find_common_chat(
        context.bot, data, active_duel["challenger_id"], active_duel["challenged_id"]
    )
    if not common_chat:
        await update.message.reply_text(
            "❌ Aucun canal commun trouvé\\. Assurez\\-vous d'être dans un canal surveillé par le bot\\.",
            parse_mode="MarkdownV2"
        )
        return

    active_duel["chat_id"] = common_chat
    origin_chat = active_duel.get("chat_id_origin", MAIN_GROUP_ID)

    try:
        chat      = await context.bot.get_chat(common_chat)
        chat_name = chat.title or chat.username or str(common_chat)
    except Exception:
        chat_name = str(common_chat)

    scheduled_ts = active_duel.get("scheduled_ts")
    chal_name    = esc(active_duel["challenger_name"])
    chld_name    = esc(active_duel["challenged_name"])

    if scheduled_ts:
        active_duel["status"] = "scheduled"
        save_data(data)

        now_utc      = datetime.now(pytz.utc)
        start_dt_utc = datetime.fromtimestamp(scheduled_ts, tz=pytz.utc)
        delta        = start_dt_utc - now_utc
        minutes_until = int(delta.total_seconds() // 60)
        seconds_until = int(delta.total_seconds() % 60)

        p1   = data["players"].get(str(active_duel["challenger_id"]), {})
        p2   = data["players"].get(str(active_duel["challenged_id"]), {})
        tz1  = pytz.timezone(p1.get("timezone") or "UTC")
        tz2  = pytz.timezone(p2.get("timezone") or "UTC")
        dt1  = start_dt_utc.astimezone(tz1)
        dt2  = start_dt_utc.astimezone(tz2)
        off1 = get_offset_str(p1.get("timezone") or "UTC")
        off2 = get_offset_str(p2.get("timezone") or "UTC")
        lbl1 = TZ_STR_TO_LABEL.get(p1.get("timezone") or "UTC", p1.get("timezone") or "UTC")
        lbl2 = TZ_STR_TO_LABEL.get(p2.get("timezone") or "UTC", p2.get("timezone") or "UTC")

        msg = (
            f"✅ *DUEL PLANIFIÉ CONFIRMÉ \\!*\n\n"
            f"⚔️ @{chal_name} VS @{chld_name}\n"
            f"📍 Canal : *{esc(chat_name)}*\n\n"
            f"🕐 *Début du duel :*\n"
            f"  • @{chal_name} : `{esc(dt1.strftime('%d/%m/%Y %H:%M'))}` _{esc(lbl1)} \\({esc(off1)}\\)_\n"
            f"  • @{chld_name} : `{esc(dt2.strftime('%d/%m/%Y %H:%M'))}` _{esc(lbl2)} \\({esc(off2)}\\)_\n\n"
            f"⏳ Début dans *{esc(minutes_until)}min {seconds_until:02d}s*\n\n"
            f"📢 Rappel 5 minutes avant le début \\!"
        )

        await update.message.reply_text(msg, parse_mode="MarkdownV2")
        if update.effective_chat.id != MAIN_GROUP_ID:
            try:
                await context.bot.send_message(MAIN_GROUP_ID, msg, parse_mode="MarkdownV2")
            except Exception:
                pass
        asyncio.create_task(scheduled_duel_start(context.bot, active_key, scheduled_ts))

    else:
        active_duel["status"]     = "active"
        active_duel["started_at"] = time.time()
        save_data(data)

        msg = (
            f"🔥 *DUEL LANCÉ IMMÉDIATEMENT \\!*\n\n"
            f"⚔️ @{chal_name} VS @{chld_name}\n"
            f"📍 Canal : *{esc(chat_name)}*\n\n"
            f"⏱️ Vous avez *5 minutes* pour poster une vidéo \\!\n"
            f"🎬 Vidéo ≥ 70 Mo en premier \\= victoire \\(\\+3 pts\\)\n"
            f"⚠️ Vidéo \\< 70 Mo \\= \\-3 pts \\(mais rattrapable avec \\+6 pts \\!\\)"
        )
        await update.message.reply_text(msg, parse_mode="MarkdownV2")
        if update.effective_chat.id != MAIN_GROUP_ID:
            try:
                await context.bot.send_message(MAIN_GROUP_ID, msg, parse_mode="MarkdownV2")
            except Exception:
                pass
        asyncio.create_task(duel_video_timeout(context.bot, active_key))


# ─────────────────────────────────────────────
#  DUEL PLANIFIÉ — démarrage auto
# ─────────────────────────────────────────────

async def scheduled_duel_start(bot, duel_key: str, scheduled_ts: float):
    now        = time.time()
    reminder_ts = scheduled_ts - 300

    if reminder_ts > now:
        await asyncio.sleep(reminder_ts - now)
        data = load_data()
        if duel_key not in data.get("duels", {}) or data["duels"][duel_key]["status"] != "scheduled":
            return
        duel        = data["duels"][duel_key]
        origin_chat = duel.get("chat_id_origin", MAIN_GROUP_ID)
        try:
            await bot.send_message(
                origin_chat,
                f"⏰ *RAPPEL — 5 minutes \\!*\n\n"
                f"⚔️ @{esc(duel['challenger_name'])} VS @{esc(duel['challenged_name'])}\n"
                f"Le duel commence dans *5 minutes* \\! Préparez vos vidéos 🎬",
                parse_mode="MarkdownV2"
            )
        except Exception:
            pass

    remaining = scheduled_ts - time.time()
    if remaining > 0:
        await asyncio.sleep(remaining)

    data = load_data()
    if duel_key not in data.get("duels", {}) or data["duels"][duel_key]["status"] != "scheduled":
        return

    duel        = data["duels"][duel_key]
    origin_chat = duel.get("chat_id_origin", MAIN_GROUP_ID)
    common_chat = duel.get("chat_id")

    try:
        chat      = await bot.get_chat(common_chat)
        chat_name = chat.title or chat.username or str(common_chat)
    except Exception:
        chat_name = str(common_chat)

    duel["status"]     = "active"
    duel["started_at"] = time.time()
    save_data(data)

    msg = (
        f"🔥 *LE DUEL COMMENCE \\!*\n\n"
        f"⚔️ @{esc(duel['challenger_name'])} VS @{esc(duel['challenged_name'])}\n"
        f"📍 Canal : *{esc(chat_name)}*\n\n"
        f"⏱️ Vous avez *5 minutes* pour poster une vidéo \\!\n"
        f"🎬 Vidéo ≥ 70 Mo en premier \\= victoire \\(\\+3 pts\\)\n"
        f"⚠️ Vidéo \\< 70 Mo \\= \\-3 pts \\(rattrapable \\+6 pts \\!\\)"
    )
    try:
        await bot.send_message(origin_chat, msg, parse_mode="MarkdownV2")
    except Exception:
        pass
    asyncio.create_task(duel_video_timeout(bot, duel_key))


# ─────────────────────────────────────────────
#  TIMEOUTS
# ─────────────────────────────────────────────

async def duel_accept_timeout(bot, duel_key: str):
    await asyncio.sleep(ACCEPT_TIMEOUT)
    data = load_data()
    if duel_key not in data.get("duels", {}):
        return
    duel = data["duels"][duel_key]
    if duel["status"] != "pending":
        return
    origin_chat = duel.get("chat_id_origin", MAIN_GROUP_ID)
    del data["duels"][duel_key]
    save_data(data)
    try:
        await bot.send_message(
            origin_chat,
            f"⏰ @{esc(duel['challenged_name'])} n'a pas répondu au défi de @{esc(duel['challenger_name'])}\\.\n"
            f"Duel annulé par timeout \\(5 min écoulées\\)\\.",
            parse_mode="MarkdownV2"
        )
    except Exception:
        pass


async def duel_video_timeout(bot, duel_key: str):
    await asyncio.sleep(DUEL_TIMEOUT)
    data = load_data()
    if duel_key not in data.get("duels", {}):
        return
    duel = data["duels"][duel_key]
    if duel["status"] != "active":
        return
    origin_chat = duel.get("chat_id_origin", MAIN_GROUP_ID)
    del data["duels"][duel_key]
    save_data(data)
    try:
        await bot.send_message(
            origin_chat,
            f"⏰ *Timeout \\!* @{esc(duel['challenger_name'])} VS @{esc(duel['challenged_name'])}\n"
            f"Aucun des joueurs n'a posté de vidéo valide à temps\\. Match nul \\!",
            parse_mode="MarkdownV2"
        )
    except Exception:
        pass


# ─────────────────────────────────────────────
#  GESTION DES VIDÉOS
# ─────────────────────────────────────────────

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    msg        = update.message
    user       = update.effective_user
    chat_id    = update.effective_chat.id
    video_size = 0

    if msg.video:
        video_size = msg.video.file_size or 0
    elif msg.document and msg.document.mime_type and "video" in msg.document.mime_type:
        video_size = msg.document.file_size or 0

    if video_size == 0:
        return

    data = load_data()

    for duel_key, duel in list(data.get("duels", {}).items()):
        if duel["status"] != "active":
            continue
        if duel.get("chat_id") != chat_id:
            continue
        if user.id not in [duel["challenger_id"], duel["challenged_id"]]:
            continue

        poster_name   = user.username or user.first_name
        opponent_id   = duel["challenged_id"] if user.id == duel["challenger_id"] else duel["challenger_id"]
        opponent_name = duel["challenged_name"] if user.id == duel["challenger_id"] else duel["challenger_name"]
        is_big        = video_size >= VIDEO_SIZE_LIMIT
        size_mb       = video_size / (1024 * 1024)
        origin_chat   = duel.get("chat_id_origin", MAIN_GROUP_ID)

        if not is_big:
            if "penalty_flag" not in duel:
                duel["penalty_flag"] = {}
            duel["penalty_flag"][str(user.id)] = True
            get_player(data, user.id, poster_name)
            data["players"][str(user.id)]["points"] -= 3
            save_data(data)

            await context.bot.send_message(
                origin_chat,
                f"⚠️ @{esc(poster_name)} a posté une vidéo de *{esc(f'{size_mb:.1f}')} Mo* \\(\\< 70 Mo\\) \\!\n"
                f"💸 *\\-3 points* pour @{esc(poster_name)}\n"
                f"⚡ Poste une vidéo ≥ 70 Mo avant @{esc(opponent_name)} pour gagner *\\+6 pts* \\!",
                parse_mode="MarkdownV2"
            )
        else:
            had_penalty = duel.get("penalty_flag", {}).get(str(user.id), False)
            points_won  = 6 if had_penalty else 3
            points_lost = -1

            get_player(data, user.id, poster_name)
            get_player(data, opponent_id, opponent_name)

            data["players"][str(user.id)]["points"]      += points_won
            data["players"][str(user.id)]["wins"]         = data["players"][str(user.id)].get("wins", 0) + 1
            data["players"][str(user.id)]["duels_played"] = data["players"][str(user.id)].get("duels_played", 0) + 1
            data["players"][str(opponent_id)]["points"]  += points_lost
            data["players"][str(opponent_id)]["losses"]   = data["players"][str(opponent_id)].get("losses", 0) + 1
            data["players"][str(opponent_id)]["duels_played"] = data["players"][str(opponent_id)].get("duels_played", 0) + 1

            data["history"].append({
                "winner": poster_name, "loser": opponent_name,
                "points": points_won, "date": datetime.now().isoformat()
            })
            del data["duels"][duel_key]
            save_data(data)

            bonus_msg = " *\\(Bonus rattrapage \\!\\)*" if had_penalty else ""
            total_pts = data["players"][str(user.id)]["points"]

            await context.bot.send_message(
                origin_chat,
                f"🏆 *DUEL TERMINÉ \\!*\n\n"
                f"⚔️ @{esc(duel['challenger_name'])} VS @{esc(duel['challenged_name'])}\n\n"
                f"🎉 *@{esc(poster_name)} GAGNE \\!*{bonus_msg}\n"
                f"📹 Vidéo de *{esc(f'{size_mb:.1f}')} Mo* postée en premier \\!\n\n"
                f"✅ @{esc(poster_name)} : *\\+{points_won} pts*\n"
                f"❌ @{esc(opponent_name)} : *{points_lost} pt*\n\n"
                f"📊 @{esc(poster_name)} totalise maintenant *{esc(total_pts)} pts*",
                parse_mode="MarkdownV2"
            )
        break


# ─────────────────────────────────────────────
#  AUTRES COMMANDES
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 *Bienvenue sur DuelBot \\!*\n\n"
        "⚔️ `/duel @pseudo` — Duel immédiat\n"
        "⚔️ `/duel @pseudo 18:30` — Duel planifié à 18h30\n"
        "⚔️ `/duel @pseudo 18:30 25/07` — Date précise\n"
        "✅ `/accept` — Accepter un duel\n"
        "❌ `/decline` — Refuser un duel\n"
        "🚫 `/cancel` — Annuler son duel\n\n"
        "📝 `/join` — S'inscrire au classement\n"
        "🌍 `/settimezone` — Définir son fuseau horaire\n"
        "🏆 `/top` — Classement\n"
        "📊 `/stats` — Ses statistiques\n"
        "📜 `/regles` — Règles du jeu\n\n"
        "🔧 `/addchat` — \\(Admin\\) Surveiller ce canal\n"
    )
    await update.message.reply_text(msg, parse_mode="MarkdownV2")


async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    uid  = str(user.id)
    name = user.username or user.first_name

    if uid in data["players"] and data["players"][uid].get("duels_played", 0) > 0:
        await update.message.reply_text(f"✅ @{esc(name)}, tu es déjà inscrit \\!", parse_mode="MarkdownV2")
    else:
        get_player(data, user.id, name)
        save_data(data)
        await update.message.reply_text(
            f"🎉 Bienvenue @{esc(name)} \\! Tu es inscrit\\.\n"
            f"💡 Définis ton fuseau avec `/settimezone` pour les duels planifiés \\!",
            parse_mode="MarkdownV2"
        )


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    await update.message.reply_text(format_leaderboard(data), parse_mode="MarkdownV2")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    uid  = str(user.id)

    if uid not in data["players"]:
        await update.message.reply_text("❌ Inscris\\-toi d'abord avec `/join` \\!", parse_mode="MarkdownV2")
        return

    p    = data["players"][uid]
    name = p.get("username", user.first_name)
    tz   = p.get("timezone")
    tz_display = TZ_STR_TO_LABEL.get(tz, tz or "Non défini")
    offset     = get_offset_str(tz) if tz else "–"

    msg = (
        f"📊 *Stats de @{esc(name)}*\n\n"
        f"🏅 Points : *{esc(p.get('points', 0))}*\n"
        f"⚔️ Duels joués : *{esc(p.get('duels_played', 0))}*\n"
        f"✅ Victoires : *{esc(p.get('wins', 0))}*\n"
        f"❌ Défaites : *{esc(p.get('losses', 0))}*\n"
        f"🌍 Fuseau : *{esc(tz_display)}* \\({esc(offset)}\\)\n"
    )
    await update.message.reply_text(msg, parse_mode="MarkdownV2")


async def cmd_regles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📜 *RÈGLES DES DUELS*\n\n"
        "1️⃣ Lance un duel avec `/duel @pseudo \\[heure optionnelle\\]`\n"
        "2️⃣ L'adversaire accepte avec `/accept`\n"
        "3️⃣ Les deux joueurs doivent être dans un canal commun surveillé\n\n"
        "🎬 *Système de points :*\n"
        "• Vidéo ≥ 70 Mo postée en premier → *\\+3 pts* \\(victoire\\)\n"
        "• Vidéo \\< 70 Mo → *\\-3 pts* \\(pénalité\\)\n"
        "  → Si tu postes ensuite une ≥ 70 Mo avant l'adversaire → *\\+6 pts* \\!\n"
        "• Perdre un duel → *\\-1 pt*\n\n"
        "🗓️ *Duels planifiés :*\n"
        "• `/duel @pseudo 18:30` — l'heure est dans *ton fuseau horaire*\n"
        "• L'adversaire voit l'heure convertie dans *son propre fuseau*\n"
        "• Rappel envoyé 5 min avant le début\n"
        "• Configure ton fuseau avec `/settimezone` \\!\n\n"
        "⏱️ Délai pour poster après le début : *5 minutes*\n"
    )
    await update.message.reply_text(msg, parse_mode="MarkdownV2")


async def cmd_decline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    for key, duel in list(data.get("duels", {}).items()):
        if duel["challenged_id"] == user.id and duel["status"] == "pending":
            name = duel["challenger_name"]
            del data["duels"][key]
            save_data(data)
            await update.message.reply_text(
                f"❌ @{esc(user.username or user.first_name)} a refusé le duel de @{esc(name)}\\.",
                parse_mode="MarkdownV2"
            )
            return
    await update.message.reply_text("❌ Tu n'as aucun duel en attente à refuser.")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    for key, duel in list(data.get("duels", {}).items()):
        if user.id in [duel["challenger_id"], duel["challenged_id"]]:
            del data["duels"][key]
            save_data(data)
            await update.message.reply_text(
                f"🚫 Duel annulé par @{esc(user.username or user.first_name)}\\.",
                parse_mode="MarkdownV2"
            )
            return
    await update.message.reply_text("❌ Tu n'as aucun duel actif à annuler.")


async def cmd_addchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    data = load_data()
    if "monitored_chats" not in data:
        data["monitored_chats"] = []
    if chat.id not in data["monitored_chats"]:
        data["monitored_chats"].append(chat.id)
        save_data(data)
        chat_name = chat.title or chat.username or str(chat.id)
        await update.message.reply_text(
            f"✅ *{esc(chat_name)}* ajouté à la surveillance des duels \\!",
            parse_mode="MarkdownV2"
        )
    else:
        await update.message.reply_text("ℹ️ Ce chat est déjà surveillé.")


async def cmd_removechat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    data = load_data()
    if "monitored_chats" in data and chat.id in data["monitored_chats"]:
        data["monitored_chats"].remove(chat.id)
        save_data(data)
        await update.message.reply_text("✅ Chat retiré de la surveillance.")
    else:
        await update.message.reply_text("ℹ️ Ce chat n'était pas surveillé.")


async def cmd_listchats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data  = load_data()
    chats = data.get("monitored_chats", [])
    if not chats:
        await update.message.reply_text("ℹ️ Aucun chat surveillé\\. Utilise `/addchat`\\.", parse_mode="MarkdownV2")
        return
    lines = [f"🔍 *Chats surveillés \\({len(chats)}\\) :*"]
    for cid in chats:
        try:
            c = await context.bot.get_chat(cid)
            lines.append(f"• {esc(c.title or c.username)} \\(`{cid}`\\)")
        except Exception:
            lines.append(f"• `{cid}` \\(introuvable\\)")
    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def cmd_resetpoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        member = await context.bot.get_chat_member(MAIN_GROUP_ID, user.id)
        if member.status not in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
            await update.message.reply_text("❌ Commande réservée aux admins.")
            return
    except Exception:
        await update.message.reply_text("❌ Impossible de vérifier tes droits.")
        return
    if not context.args:
        await update.message.reply_text("Usage: `/resetpoints @pseudo`", parse_mode="MarkdownV2")
        return
    target = context.args[0].lstrip("@").lower()
    data   = load_data()
    for uid, p in data["players"].items():
        if p.get("username", "").lower() == target:
            p["points"] = 0
            save_data(data)
            await update.message.reply_text(f"✅ Points de @{esc(target)} remis à 0\\.", parse_mode="MarkdownV2")
            return
    await update.message.reply_text(f"❌ Joueur @{esc(target)} introuvable\\.", parse_mode="MarkdownV2")


# ─────────────────────────────────────────────
#  LANCEMENT
# ─────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_start))
    app.add_handler(CommandHandler("join",        cmd_join))
    app.add_handler(CommandHandler("top",         cmd_top))
    app.add_handler(CommandHandler("classement",  cmd_top))
    app.add_handler(CommandHandler("stats",       cmd_stats))
    app.add_handler(CommandHandler("regles",      cmd_regles))
    app.add_handler(CommandHandler("duel",        cmd_duel))
    app.add_handler(CommandHandler("accept",      cmd_accept))
    app.add_handler(CommandHandler("decline",     cmd_decline))
    app.add_handler(CommandHandler("cancel",      cmd_cancel))
    app.add_handler(CommandHandler("settimezone", cmd_settimezone))
    app.add_handler(CommandHandler("addchat",     cmd_addchat))
    app.add_handler(CommandHandler("removechat",  cmd_removechat))
    app.add_handler(CommandHandler("listchats",   cmd_listchats))
    app.add_handler(CommandHandler("resetpoints", cmd_resetpoints))

    app.add_handler(CallbackQueryHandler(callback_settz, pattern=r"^settz:"))

    app.add_handler(MessageHandler(
        filters.VIDEO | filters.Document.MimeType("video/mp4"),
        handle_video
    ))

    logger.info("🤖 DuelBot V3 démarré !")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
