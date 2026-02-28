"""
🤖 DuelBot V4 — Canaux indépendants + déclaration de victoire complète
- Chaque joueur enregistre SON canal personnel avec /mychannel
- Le bot surveille les deux canaux séparément pendant un duel
- Toutes les annonces (duel, victoire, classement) se font dans le GROUPE MÈRE
- Nécessite: pip install python-telegram-bot pytz
"""

import asyncio
import json
import logging
import os
import re
import time
import threading
from datetime import datetime, timedelta
from typing import Optional
from http.server import HTTPServer, BaseHTTPRequestHandler

import pytz
from telegram import Update, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN     = os.environ.get("BOT_TOKEN", "").strip()

# Parser MAIN_GROUP_ID de façon robuste
_gid_raw = os.environ.get("MAIN_GROUP_ID", "0").strip().strip('"').strip("'")
try:
    MAIN_GROUP_ID = int(_gid_raw)
except ValueError:
    MAIN_GROUP_ID = 0

DATA_FILE      = "duel_data.json"
DUEL_TIMEOUT   = 300
ACCEPT_TIMEOUT = 300
VIDEO_MIN_SIZE = 70 * 1024 * 1024

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Log immédiat pour voir si le bot démarre
logger.info("=" * 50)
logger.info("🤖 DuelBot — Démarrage en cours...")
logger.info(f"BOT_TOKEN présent: {bool(BOT_TOKEN)}")
logger.info(f"MAIN_GROUP_ID raw: '{_gid_raw}' → parsed: {MAIN_GROUP_ID}")
logger.info("=" * 50)


def esc(text: str) -> str:
    """Échappe les caractères spéciaux pour MarkdownV2."""
    special = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(special)}])', r'\\\1', str(text))


def h(text: str) -> str:
    """Échappe pour HTML Telegram."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
TZ_STR_TO_LABEL = {v: k for k, v in COMMON_TIMEZONES.items()}


def get_offset_str(tz_string: str) -> str:
    try:
        tz  = pytz.timezone(tz_string)
        now = datetime.now(tz)
        total_seconds = int(now.utcoffset().total_seconds())
        sign = "+" if total_seconds >= 0 else "-"
        total_seconds = abs(total_seconds)
        h, rem = divmod(total_seconds, 3600)
        return f"UTC{sign}{h:02d}:{rem//60:02d}"
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
    return {
        "players": {},
        "duels": {},
        "history": [],
        "registered_channels": {}   # chat_id → owner_user_id
    }


def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
#  HELPERS JOUEURS
# ─────────────────────────────────────────────

def get_player(data: dict, user_id: int, username: str = None) -> dict:
    uid = str(user_id)
    if uid not in data["players"]:
        data["players"][uid] = {
            "username": username or str(user_id),
            "points": 0, "wins": 0, "losses": 0,
            "duels_played": 0, "timezone": None,
            "channel_id": None,      # canal personnel du joueur
            "channel_name": None,
            "joined": datetime.now().isoformat()
        }
    elif username:
        data["players"][uid]["username"] = username
    return data["players"][uid]


def get_player_by_username(data: dict, username: str):
    """Retourne (uid_str, player_dict) ou (None, None)."""
    uname = username.lower().lstrip("@")
    for uid, p in data["players"].items():
        if p.get("username", "").lower() == uname:
            return uid, p
    return None, None


def format_leaderboard(data: dict) -> str:
    players = [(uid, p) for uid, p in data["players"].items() if p.get("duels_played", 0) > 0]
    if not players:
        return "📊 Aucun joueur au classement pour l'instant\\."
    players.sort(key=lambda x: x[1]["points"], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines  = ["🏆 *CLASSEMENT DES DUELS*\n"]
    for i, (uid, p) in enumerate(players[:10]):
        medal = medals[i] if i < 3 else f"{i+1}\\."
        name  = esc(p.get("username", uid))
        pts   = esc(p["points"])
        w     = p.get("wins", 0)
        l     = p.get("losses", 0)
        ch    = f" 📺" if p.get("channel_name") else ""
        lines.append(f"{medal} @{name}{ch} — *{pts} pts* \\({w}W/{l}L\\)")
    return "\n".join(lines)


# ─────────────────────────────────────────────
#  /start & /help
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 *Bienvenue sur DuelBot V4 \\!*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📋 *INSCRIPTION*\n"
        "`/join` — S'inscrire au classement\n"
        "`/mychannel` — Enregistrer ton canal de duel\n"
        "`/settimezone` — Définir ton fuseau horaire\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚔️ *DUELS*\n"
        "`/duel @pseudo` — Duel immédiat\n"
        "`/duel @pseudo 18:30` — Duel planifié \\(ton heure\\)\n"
        "`/duel @pseudo 18:30 25/07` — Date précise\n"
        "`/accept` — Accepter un duel\n"
        "`/decline` — Refuser un duel\n"
        "`/cancel` — Annuler son duel en cours\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *STATS*\n"
        "`/top` — Classement général\n"
        "`/stats` — Ses statistiques\n"
        "`/mystats` — Ses stats détaillées\n"
        "`/regles` — Règles du jeu\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🔧 *ADMIN*\n"
        "`/addchannel` — Ajouter un canal au bot\n"
        "`/channels` — Voir les canaux enregistrés\n"
        "`/resetpoints @pseudo` — Remettre à zéro\n"
    )
    await update.message.reply_text(msg, parse_mode="MarkdownV2")


# ─────────────────────────────────────────────
#  /join
# ─────────────────────────────────────────────

async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    uid  = str(user.id)
    name = user.username or user.first_name

    if uid in data["players"]:
        p = data["players"][uid]
        ch_info = f"\n📺 Canal enregistré : *{esc(p.get('channel_name', 'Aucun'))}*" if p.get("channel_name") else "\n📺 Pas encore de canal \\— utilise `/mychannel`"
        await update.message.reply_text(
            f"✅ @{esc(name)}, tu es déjà inscrit \\!{ch_info}",
            parse_mode="MarkdownV2"
        )
    else:
        get_player(data, user.id, name)
        save_data(data)
        await update.message.reply_text(
            f"🎉 *Bienvenue @{esc(name)} \\!* Tu es maintenant inscrit\\.\n\n"
            f"Prochaines étapes :\n"
            f"1️⃣ `/mychannel` — Enregistre ton canal de duel\n"
            f"2️⃣ `/settimezone` — Définis ton fuseau horaire\n"
            f"3️⃣ `/duel @pseudo` — Lance ton premier duel \\!",
            parse_mode="MarkdownV2"
        )


# ─────────────────────────────────────────────
#  /mychannel — Enregistrer son canal personnel
# ─────────────────────────────────────────────

async def cmd_mychannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Utilisé DEPUIS le canal : le bot enregistre ce canal comme canal du joueur.
    Ou depuis le groupe avec un argument : /mychannel @channelusername
    """
    user = update.effective_user
    chat = update.effective_chat
    data = load_data()

    # Si utilisé depuis un canal directement
    if chat.type in ["channel", "supergroup"] and chat.id != MAIN_GROUP_ID:
        # Vérifier que le bot est admin dans ce canal
        try:
            bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
            if bot_member.status not in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                await update.message.reply_text(
                    "❌ Je dois être admin dans ce canal pour l'enregistrer\\.",
                    parse_mode="MarkdownV2"
                )
                return
        except Exception:
            pass

        # Enregistrer
        p = get_player(data, user.id, user.username or user.first_name)
        p["channel_id"]   = chat.id
        p["channel_name"] = chat.title or chat.username or str(chat.id)

        if "registered_channels" not in data:
            data["registered_channels"] = {}
        data["registered_channels"][str(chat.id)] = user.id

        save_data(data)

        ch_name = chat.title or chat.username or str(chat.id)
        await update.message.reply_text(
            f"✅ Canal *{esc(ch_name)}* enregistré comme ton canal de duel \\!\n"
            f"Les duels te concernant seront surveillés ici\\.",
            parse_mode="MarkdownV2"
        )
        return

    # Si utilisé depuis le groupe principal avec un argument (ID ou @username)
    if context.args:
        channel_ref = context.args[0]
        try:
            # Essayer par ID ou @username
            if channel_ref.lstrip("-").isdigit():
                channel_id = int(channel_ref)
            else:
                channel_ref_clean = channel_ref if channel_ref.startswith("@") else f"@{channel_ref}"
                chat_obj   = await context.bot.get_chat(channel_ref_clean)
                channel_id = chat_obj.id

            ch_obj  = await context.bot.get_chat(channel_id)
            ch_name = ch_obj.title or ch_obj.username or str(channel_id)

            # Vérifier que le bot est admin
            try:
                bot_member = await context.bot.get_chat_member(channel_id, context.bot.id)
                if bot_member.status not in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                    await update.message.reply_text(
                        f"❌ Je ne suis pas admin dans *{esc(ch_name)}*\\. Ajoute\\-moi comme admin d'abord \\!",
                        parse_mode="MarkdownV2"
                    )
                    return
            except Exception:
                await update.message.reply_text("❌ Impossible d'accéder à ce canal\\. Vérifie que je suis admin dedans\\.", parse_mode="MarkdownV2")
                return

            p = get_player(data, user.id, user.username or user.first_name)
            p["channel_id"]   = channel_id
            p["channel_name"] = ch_name

            if "registered_channels" not in data:
                data["registered_channels"] = {}
            data["registered_channels"][str(channel_id)] = user.id

            save_data(data)
            await update.message.reply_text(
                f"✅ *{esc(ch_name)}* enregistré comme ton canal de duel \\!\n"
                f"Les vidéos postées là\\-dedans compteront pour tes duels\\.",
                parse_mode="MarkdownV2"
            )

        except Exception as e:
            await update.message.reply_text(
                f"❌ Canal introuvable ou inaccessible\\.\n"
                f"Assure\\-toi que je suis admin dans le canal et réessaie\\.\n\n"
                f"Usage : `/mychannel @nomdcanal` ou `/mychannel -1001234567890`",
                parse_mode="MarkdownV2"
            )
        return

    # Instructions si aucun argument
    await update.message.reply_text(
        "📺 *Enregistrer ton canal de duel :*\n\n"
        "*Méthode 1* — Depuis ton canal :\n"
        "1\\. Ajoute le bot dans ton canal comme admin\n"
        "2\\. Tape `/mychannel` directement dans le canal\n\n"
        "*Méthode 2* — Depuis ce groupe :\n"
        "`/mychannel @nomdcanal`\n"
        "ou\n"
        "`/mychannel -1001234567890` \\(l'ID du canal\\)\n\n"
        "💡 Le bot doit être *admin* dans ton canal pour détecter les vidéos\\.",
        parse_mode="MarkdownV2"
    )


# ─────────────────────────────────────────────
#  /addchannel — Admin : ajouter n'importe quel canal
# ─────────────────────────────────────────────

async def cmd_addchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin seulement : ajouter un canal à la liste surveillée sans l'associer à un joueur."""
    user = update.effective_user
    data = load_data()

    try:
        member = await context.bot.get_chat_member(MAIN_GROUP_ID, user.id)
        if member.status not in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
            await update.message.reply_text("❌ Commande réservée aux admins\\.", parse_mode="MarkdownV2")
            return
    except Exception:
        pass

    if not context.args:
        await update.message.reply_text(
            "Usage : `/addchannel @canal` ou `/addchannel -1001234567890`",
            parse_mode="MarkdownV2"
        )
        return

    channel_ref = context.args[0]
    try:
        if channel_ref.lstrip("-").isdigit():
            channel_id = int(channel_ref)
        else:
            channel_ref_clean = channel_ref if channel_ref.startswith("@") else f"@{channel_ref}"
            ch_obj     = await context.bot.get_chat(channel_ref_clean)
            channel_id = ch_obj.id

        ch_obj  = await context.bot.get_chat(channel_id)
        ch_name = ch_obj.title or ch_obj.username or str(channel_id)

        if "registered_channels" not in data:
            data["registered_channels"] = {}

        if str(channel_id) not in data["registered_channels"]:
            data["registered_channels"][str(channel_id)] = None  # pas de propriétaire défini
            save_data(data)
            await update.message.reply_text(
                f"✅ Canal *{esc(ch_name)}* ajouté à la surveillance\\.",
                parse_mode="MarkdownV2"
            )
        else:
            await update.message.reply_text(f"ℹ️ Ce canal est déjà enregistré\\.", parse_mode="MarkdownV2")

    except Exception:
        await update.message.reply_text("❌ Canal introuvable ou inaccessible\\.", parse_mode="MarkdownV2")


# ─────────────────────────────────────────────
#  /channels — Lister les canaux enregistrés
# ─────────────────────────────────────────────

async def cmd_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data     = load_data()
    channels = data.get("registered_channels", {})

    if not channels:
        await update.message.reply_text(
            "ℹ️ Aucun canal enregistré\\.\nUtilise `/mychannel` pour enregistrer le tien\\.",
            parse_mode="MarkdownV2"
        )
        return

    lines = [f"📺 *Canaux enregistrés \\({len(channels)}\\) :*\n"]
    for cid, owner_id in channels.items():
        try:
            ch     = await context.bot.get_chat(int(cid))
            ch_name = esc(ch.title or ch.username or cid)
        except Exception:
            ch_name = esc(str(cid))

        if owner_id:
            owner_p = data["players"].get(str(owner_id), {})
            owner_name = esc(owner_p.get("username", str(owner_id)))
            lines.append(f"• *{ch_name}* → @{owner_name}")
        else:
            lines.append(f"• *{ch_name}* → \\(sans propriétaire\\)")

    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


# ─────────────────────────────────────────────
#  /settimezone
# ─────────────────────────────────────────────

def tz_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for label, tz_str in COMMON_TIMEZONES.items():
        offset = get_offset_str(tz_str)
        buttons.append([InlineKeyboardButton(f"{label} ({offset})", callback_data=f"settz:{user_id}:{tz_str}")])
    return InlineKeyboardMarkup(buttons)


async def cmd_settimezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        "🌍 *Choisis ton fuseau horaire :*",
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
    data  = load_data()
    p     = get_player(data, int(uid_str), query.from_user.username or query.from_user.first_name)
    p["timezone"] = tz_str
    save_data(data)
    label  = TZ_STR_TO_LABEL.get(tz_str, tz_str)
    offset = get_offset_str(tz_str)
    await query.edit_message_text(
        f"✅ Fuseau enregistré : *{esc(label)}* \\({esc(offset)}\\)",
        parse_mode="MarkdownV2"
    )


# ─────────────────────────────────────────────
#  /duel — Lancer un duel
# ─────────────────────────────────────────────

async def cmd_duel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Seulement depuis le groupe mère
    if update.effective_chat.id != MAIN_GROUP_ID:
        await update.message.reply_text(
            f"❌ Les duels doivent être lancés depuis le groupe principal\\.",
            parse_mode="MarkdownV2"
        )
        return

    challenger = update.effective_user
    data       = load_data()

    if not context.args:
        await update.message.reply_text(
            "❌ Usage :\n`/duel @pseudo` — duel immédiat\n`/duel @pseudo 18:30` — planifié",
            parse_mode="MarkdownV2"
        )
        return

    target_username = context.args[0].lstrip("@").lower()
    target_uid_str, target_p = get_player_by_username(data, target_username)

    if not target_uid_str:
        await update.message.reply_text(
            f"❌ @{esc(target_username)} n'est pas inscrit\\. Il/elle doit faire `/join` d'abord \\!",
            parse_mode="MarkdownV2"
        )
        return

    target_uid = int(target_uid_str)

    if target_uid == challenger.id:
        await update.message.reply_text("😂 Tu ne peux pas te défier toi\\-même \\!", parse_mode="MarkdownV2")
        return

    # Vérifier que les deux ont un canal enregistré
    challenger_p = get_player(data, challenger.id, challenger.username or challenger.first_name)
    if not challenger_p.get("channel_id"):
        await update.message.reply_text(
            "❌ Tu n'as pas encore enregistré ton canal de duel\\.\nUtilise `/mychannel` d'abord \\!",
            parse_mode="MarkdownV2"
        )
        return

    if not target_p.get("channel_id"):
        await update.message.reply_text(
            f"❌ @{esc(target_username)} n'a pas encore enregistré son canal de duel\\.\n"
            f"Il/elle doit utiliser `/mychannel` d'abord \\!",
            parse_mode="MarkdownV2"
        )
        return

    duel_key = f"{min(challenger.id, target_uid)}_{max(challenger.id, target_uid)}"
    if duel_key in data.get("duels", {}):
        await update.message.reply_text("⚠️ Un duel est déjà en cours entre vous deux \\!", parse_mode="MarkdownV2")
        return

    # Gestion du temps planifié
    scheduled_ts = None
    display_info = ""

    if len(context.args) >= 2:
        time_str = " ".join(context.args[1:])
        naive_dt = parse_time_input(time_str)
        if naive_dt is None:
            await update.message.reply_text(
                "❌ Format d'heure invalide\\.\nExemples : `18:30` · `18:30 25/07`",
                parse_mode="MarkdownV2"
            )
            return

        tz_str_c  = challenger_p.get("timezone") or "UTC"
        tz_c      = pytz.timezone(tz_str_c)
        aware_dt  = tz_c.localize(naive_dt)
        now_utc   = datetime.now(pytz.utc)

        if aware_dt < now_utc + timedelta(minutes=2):
            await update.message.reply_text(
                "❌ L'heure planifiée doit être dans au moins 2 minutes dans le futur\\.",
                parse_mode="MarkdownV2"
            )
            return

        scheduled_ts  = aware_dt.timestamp()
        tz_str_t      = target_p.get("timezone") or "UTC"
        tz_t          = pytz.timezone(tz_str_t)
        dt_challenger = aware_dt.astimezone(tz_c)
        dt_challenged = aware_dt.astimezone(tz_t)
        lbl_c  = TZ_STR_TO_LABEL.get(tz_str_c, tz_str_c)
        lbl_t  = TZ_STR_TO_LABEL.get(tz_str_t, tz_str_t)
        off_c  = get_offset_str(tz_str_c)
        off_t  = get_offset_str(tz_str_t)

        cname  = esc(challenger.username or challenger.first_name)
        tname  = esc(target_username)
        display_info = (
            f"\n\n🗓️ *Heure du duel :*\n"
            f"  📍 @{cname} : `{esc(dt_challenger.strftime('%d/%m/%Y %H:%M'))}` _{esc(lbl_c)} \\({esc(off_c)}\\)_\n"
            f"  📍 @{tname} : `{esc(dt_challenged.strftime('%d/%m/%Y %H:%M'))}` _{esc(lbl_t)} \\({esc(off_t)}\\)_\n"
        )
        if not target_p.get("timezone"):
            display_info += f"\n⚠️ @{tname} n'a pas défini son fuseau \\(`/settimezone`\\)\\."

    # Créer le duel
    if "duels" not in data:
        data["duels"] = {}

    data["duels"][duel_key] = {
        "challenger_id":      challenger.id,
        "challenger_name":    challenger.username or challenger.first_name,
        "challenger_channel": challenger_p["channel_id"],
        "challenged_id":      target_uid,
        "challenged_name":    target_p["username"],
        "challenged_channel": target_p["channel_id"],
        "status":             "pending",
        "created_at":         time.time(),
        "scheduled_ts":       scheduled_ts,
        "penalty_flag":       {},
        "videos_posted":      {}   # user_id → {"size": x, "ts": t}
    }
    save_data(data)

    cname = esc(challenger.username or challenger.first_name)
    tname = esc(target_p["username"])
    ch_c  = esc(challenger_p.get("channel_name", "son canal"))
    ch_t  = esc(target_p.get("channel_name", "son canal"))

    if scheduled_ts:
        msg = (
            f"⚔️ *DÉFI PLANIFIÉ \\!*\n\n"
            f"@{cname} 🆚 @{tname}\n\n"
            f"📺 Canal de @{cname} : *{ch_c}*\n"
            f"📺 Canal de @{tname} : *{ch_t}*"
            f"{display_info}\n\n"
            f"@{tname}, réponds avec `/accept` ou `/decline`\\.\n"
            f"⏱️ 5 minutes pour répondre\\."
        )
    else:
        msg = (
            f"⚔️ *DÉFI LANCÉ \\!*\n\n"
            f"@{cname} 🆚 @{tname}\n\n"
            f"📺 Canal de @{cname} : *{ch_c}*\n"
            f"📺 Canal de @{tname} : *{ch_t}*\n\n"
            f"@{tname}, réponds avec `/accept` pour accepter "
            f"ou `/decline` pour refuser\\.\n"
            f"⏱️ 5 minutes pour répondre\\."
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
        await update.message.reply_text("❌ Tu n'as aucun duel en attente\\.", parse_mode="MarkdownV2")
        return

    cname  = esc(active_duel["challenger_name"])
    chname = esc(active_duel["challenged_name"])
    ch_c   = esc(data["players"].get(str(active_duel["challenger_id"]), {}).get("channel_name", "canal inconnu"))
    ch_t   = esc(data["players"].get(str(active_duel["challenged_id"]), {}).get("channel_name", "canal inconnu"))

    scheduled_ts = active_duel.get("scheduled_ts")

    if scheduled_ts:
        active_duel["status"] = "scheduled"
        save_data(data)

        now_utc      = datetime.now(pytz.utc)
        start_dt_utc = datetime.fromtimestamp(scheduled_ts, tz=pytz.utc)
        delta        = start_dt_utc - now_utc
        min_until    = int(delta.total_seconds() // 60)
        sec_until    = int(delta.total_seconds() % 60)

        p1    = data["players"].get(str(active_duel["challenger_id"]), {})
        p2    = data["players"].get(str(active_duel["challenged_id"]), {})
        tz1   = pytz.timezone(p1.get("timezone") or "UTC")
        tz2   = pytz.timezone(p2.get("timezone") or "UTC")
        dt1   = start_dt_utc.astimezone(tz1)
        dt2   = start_dt_utc.astimezone(tz2)
        lbl1  = TZ_STR_TO_LABEL.get(p1.get("timezone") or "UTC", "UTC")
        lbl2  = TZ_STR_TO_LABEL.get(p2.get("timezone") or "UTC", "UTC")
        off1  = get_offset_str(p1.get("timezone") or "UTC")
        off2  = get_offset_str(p2.get("timezone") or "UTC")

        msg = (
            f"✅ *DUEL PLANIFIÉ CONFIRMÉ \\!*\n\n"
            f"⚔️ @{cname} 🆚 @{chname}\n\n"
            f"📺 *Canaux de duel :*\n"
            f"  • @{cname} poste dans *{ch_c}*\n"
            f"  • @{chname} poste dans *{ch_t}*\n\n"
            f"🕐 *Début du duel :*\n"
            f"  • @{cname} : `{esc(dt1.strftime('%d/%m/%Y %H:%M'))}` _{esc(lbl1)} \\({esc(off1)}\\)_\n"
            f"  • @{chname} : `{esc(dt2.strftime('%d/%m/%Y %H:%M'))}` _{esc(lbl2)} \\({esc(off2)}\\)_\n\n"
            f"⏳ Début dans *{esc(min_until)}min {sec_until:02d}s*\n"
            f"📢 Rappel 5 minutes avant \\!"
        )
        await update.message.reply_text(msg, parse_mode="MarkdownV2")
        try:
            if update.effective_chat.id != MAIN_GROUP_ID:
                await context.bot.send_message(MAIN_GROUP_ID, msg, parse_mode="MarkdownV2")
        except Exception:
            pass
        asyncio.create_task(scheduled_duel_start(context.bot, active_key, scheduled_ts))

    else:
        active_duel["status"]     = "active"
        active_duel["started_at"] = time.time()
        save_data(data)

        msg = (
            f"🔥 *DUEL COMMENCÉ \\!*\n\n"
            f"⚔️ @{cname} 🆚 @{chname}\n\n"
            f"📺 *Canaux surveillés :*\n"
            f"  • @{cname} poste dans *{ch_c}*\n"
            f"  • @{chname} poste dans *{ch_t}*\n\n"
            f"⏱️ *5 minutes* pour poster une vidéo \\!\n"
            f"🎬 Vidéo ≥ 70 Mo en premier \\= *victoire \\+3 pts*\n"
            f"⚠️ Vidéo \\< 70 Mo \\= *\\-3 pts* \\(rattrapable \\+6 pts\\)\n\n"
            f"🏁 Le bot annoncera le vainqueur ici dès qu'une vidéo valide est postée \\!"
        )
        await update.message.reply_text(msg, parse_mode="MarkdownV2")
        try:
            if update.effective_chat.id != MAIN_GROUP_ID:
                await context.bot.send_message(MAIN_GROUP_ID, msg, parse_mode="MarkdownV2")
        except Exception:
            pass
        asyncio.create_task(duel_video_timeout(context.bot, active_key))


# ─────────────────────────────────────────────
#  /decline & /cancel
# ─────────────────────────────────────────────

async def cmd_decline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    for key, duel in list(data.get("duels", {}).items()):
        if duel["challenged_id"] == user.id and duel["status"] == "pending":
            cname = esc(duel["challenger_name"])
            uname = esc(user.username or user.first_name)
            del data["duels"][key]
            save_data(data)
            msg = f"❌ @{uname} a refusé le duel de @{cname}\\."
            await update.message.reply_text(msg, parse_mode="MarkdownV2")
            try:
                await context.bot.send_message(MAIN_GROUP_ID, msg, parse_mode="MarkdownV2")
            except Exception:
                pass
            return
    await update.message.reply_text("❌ Tu n'as aucun duel en attente à refuser\\.", parse_mode="MarkdownV2")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    for key, duel in list(data.get("duels", {}).items()):
        if user.id in [duel["challenger_id"], duel["challenged_id"]]:
            cname = esc(duel["challenger_name"])
            tname = esc(duel["challenged_name"])
            uname = esc(user.username or user.first_name)
            del data["duels"][key]
            save_data(data)
            msg = f"🚫 Duel @{cname} 🆚 @{tname} annulé par @{uname}\\."
            await update.message.reply_text(msg, parse_mode="MarkdownV2")
            try:
                await context.bot.send_message(MAIN_GROUP_ID, msg, parse_mode="MarkdownV2")
            except Exception:
                pass
            return
    await update.message.reply_text("❌ Tu n'as aucun duel actif à annuler\\.", parse_mode="MarkdownV2")


# ─────────────────────────────────────────────
#  DUEL PLANIFIÉ — démarrage automatique
# ─────────────────────────────────────────────

async def scheduled_duel_start(bot, duel_key: str, scheduled_ts: float):
    now         = time.time()
    reminder_ts = scheduled_ts - 300

    if reminder_ts > now:
        await asyncio.sleep(reminder_ts - now)
        data = load_data()
        if duel_key not in data.get("duels", {}) or data["duels"][duel_key]["status"] != "scheduled":
            return
        duel = data["duels"][duel_key]
        try:
            await bot.send_message(
                MAIN_GROUP_ID,
                f"⏰ *RAPPEL — 5 minutes \\!*\n\n"
                f"⚔️ @{esc(duel['challenger_name'])} 🆚 @{esc(duel['challenged_name'])}\n"
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

    duel = data["duels"][duel_key]
    duel["status"]     = "active"
    duel["started_at"] = time.time()
    save_data(data)

    p1    = data["players"].get(str(duel["challenger_id"]), {})
    p2    = data["players"].get(str(duel["challenged_id"]), {})
    ch_c  = esc(p1.get("channel_name", "son canal"))
    ch_t  = esc(p2.get("channel_name", "son canal"))
    cname = esc(duel["challenger_name"])
    tname = esc(duel["challenged_name"])

    msg = (
        f"🔥 *LE DUEL COMMENCE \\!*\n\n"
        f"⚔️ @{cname} 🆚 @{tname}\n\n"
        f"📺 *Canaux surveillés :*\n"
        f"  • @{cname} poste dans *{ch_c}*\n"
        f"  • @{tname} poste dans *{ch_t}*\n\n"
        f"⏱️ *5 minutes* pour poster une vidéo \\!\n"
        f"🎬 Vidéo ≥ 70 Mo en premier \\= *victoire \\+3 pts*\n"
        f"⚠️ Vidéo \\< 70 Mo \\= *\\-3 pts* \\(rattrapable \\+6 pts\\)\n\n"
        f"🏁 Le bot annoncera le vainqueur ici \\!"
    )
    try:
        await bot.send_message(MAIN_GROUP_ID, msg, parse_mode="MarkdownV2")
    except Exception:
        pass
    asyncio.create_task(duel_video_timeout(bot, duel_key))


# ─────────────────────────────────────────────
#  GESTION DES VIDÉOS
#  Dans un CANAL Telegram, effective_user est None
#  car c'est le canal lui-même qui est l'auteur.
#  On identifie le joueur par l'ID du canal.
# ─────────────────────────────────────────────

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Accepter les posts de canaux ET les messages normaux
    msg = update.channel_post or update.message
    if not msg:
        return

    chat_id    = msg.chat_id
    video_size = 0

    if msg.video:
        video_size = msg.video.file_size or 0
    elif msg.document and msg.document.mime_type and "video" in msg.document.mime_type:
        video_size = msg.document.file_size or 0

    if video_size == 0:
        return

    logger.info(f"📹 Vidéo reçue — chat_id={chat_id}, size={video_size}, update_type={'channel_post' if update.channel_post else 'message'}")

    data = load_data()

    # Log tous les duels actifs pour comparaison
    active_duels = [(k, d) for k, d in data.get("duels", {}).items() if d["status"] == "active"]
    logger.info(f"⚔️ Duels actifs : {len(active_duels)}")
    for k, d in active_duels:
        logger.info(f"   Duel {k}: canal_A={d.get('challenger_channel')} canal_B={d.get('challenged_channel')}")
        logger.info(f"   Ce chat ({chat_id}) correspond ? A={chat_id == d.get('challenger_channel')} B={chat_id == d.get('challenged_channel')}")

    for duel_key, duel in list(data.get("duels", {}).items()):
        if duel["status"] != "active":
            continue

        challenger_channel = duel.get("challenger_channel")
        challenged_channel = duel.get("challenged_channel")

        if chat_id not in [challenger_channel, challenged_channel]:
            continue

        # Identifier le joueur par son canal (pas par l'user)
        if chat_id == challenger_channel:
            poster_id     = duel["challenger_id"]
            poster_name   = duel["challenger_name"]
            opponent_id   = duel["challenged_id"]
            opponent_name = duel["challenged_name"]
        else:
            poster_id     = duel["challenged_id"]
            poster_name   = duel["challenged_name"]
            opponent_id   = duel["challenger_id"]
            opponent_name = duel["challenger_name"]

        is_big     = video_size >= VIDEO_MIN_SIZE
        size_mb    = video_size / (1024 * 1024)
        chat_title = msg.chat.title or str(chat_id)

        # Heure exacte de publication (à la seconde)
        now_ts       = time.time()
        now_dt       = datetime.now()
        post_time_str = now_dt.strftime("%d/%m/%Y à %H:%M:%S")

        # Enregistrer le timestamp de cette vidéo dans le duel
        if "video_timestamps" not in duel:
            duel["video_timestamps"] = {}
        duel["video_timestamps"][str(poster_id)] = {
            "ts":      now_ts,
            "size_mb": round(size_mb, 2),
            "big":     is_big,
            "channel": chat_title
        }

        if not is_big:
            # ── Petite vidéo → pénalité ──
            if "penalty_flag" not in duel:
                duel["penalty_flag"] = {}
            duel["penalty_flag"][str(poster_id)] = True
            get_player(data, poster_id, poster_name)
            data["players"][str(poster_id)]["points"] -= 3
            save_data(data)

            try:
                await context.bot.send_message(
                    MAIN_GROUP_ID,
                    f"⚠️ <b>Petite vidéo détectée !</b>\n\n"
                    f"👤 @{h(poster_name)}\n"
                    f"📺 Canal : <b>{h(chat_title)}</b>\n"
                    f"📦 Taille : <b>{size_mb:.2f} Mo</b> (minimum : 70 Mo)\n"
                    f"🕐 Heure : <code>{h(post_time_str)}</code>\n\n"
                    f"💸 <b>-3 points</b> pour @{h(poster_name)}\n"
                    f"⚡ Il peut encore poster une vidéo ≥ 70 Mo avant @{h(opponent_name)} pour gagner <b>+6 pts</b> !",
                    parse_mode="HTML"
                )
                logger.info(f"✅ Message pénalité envoyé dans {MAIN_GROUP_ID}")
            except Exception as e:
                logger.error(f"Erreur pénalité HTML: {e}")
                try:
                    await context.bot.send_message(
                        MAIN_GROUP_ID,
                        f"⚠️ Petite vidéo de @{poster_name} : {size_mb:.2f} Mo (< 70 Mo)\n-3 points !"
                    )
                except Exception as e2:
                    logger.error(f"Erreur pénalité texte: {e2} — MAIN_GROUP_ID={MAIN_GROUP_ID}")

        else:
            # ── Grande vidéo ≥ 70 Mo → VICTOIRE ──
            had_penalty = duel.get("penalty_flag", {}).get(str(poster_id), False)
            points_won  = 6 if had_penalty else 3
            points_lost = -1

            # Chrono depuis le début du duel
            duel_start   = duel.get("started_at", now_ts)
            elapsed      = int(now_ts - duel_start)
            elapsed_min  = elapsed // 60
            elapsed_sec  = elapsed % 60

            # Infos sur la vidéo du perdant si elle existe
            loser_video = duel.get("video_timestamps", {}).get(str(opponent_id))
            loser_info  = ""
            if loser_video:
                loser_dt       = datetime.fromtimestamp(loser_video["ts"])
                loser_str      = loser_dt.strftime("%d/%m/%Y à %H:%M:%S")
                loser_size_str = f"{loser_video['size_mb']:.2f}"
                gap            = int(now_ts - loser_video["ts"])
                gap_min        = gap // 60
                gap_sec        = gap % 60
                loser_info = (
                    f"\n\n📋 <b>Vidéo de @{h(opponent_name)} :</b>\n"
                    f"  🕐 Heure : <code>{h(loser_str)}</code>\n"
                    f"  📦 Taille : <b>{loser_size_str} Mo</b>\n"
                    f"  ⏳ Retard : <b>{gap_min}min {gap_sec:02d}s</b> après le vainqueur"
                )

            get_player(data, poster_id, poster_name)
            get_player(data, opponent_id, opponent_name)

            data["players"][str(poster_id)]["points"]          += points_won
            data["players"][str(poster_id)]["wins"]             = data["players"][str(poster_id)].get("wins", 0) + 1
            data["players"][str(poster_id)]["duels_played"]     = data["players"][str(poster_id)].get("duels_played", 0) + 1
            data["players"][str(opponent_id)]["points"]        += points_lost
            data["players"][str(opponent_id)]["losses"]         = data["players"][str(opponent_id)].get("losses", 0) + 1
            data["players"][str(opponent_id)]["duels_played"]   = data["players"][str(opponent_id)].get("duels_played", 0) + 1

            total_winner   = data["players"][str(poster_id)]["points"]
            total_opponent = data["players"][str(opponent_id)]["points"]

            data["history"].append({
                "winner":        poster_name,
                "loser":         opponent_name,
                "points_won":    points_won,
                "date":          now_dt.isoformat(),
                "video_size_mb": round(size_mb, 2),
                "elapsed_sec":   elapsed
            })
            del data["duels"][duel_key]
            save_data(data)

            bonus_txt = "\n🔥 <b>Bonus rattrapage !</b> (pénalité petite vidéo compensée)" if had_penalty else ""

            victory_msg = (
                f"🏆 <b>DUEL TERMINÉ — VICTOIRE !</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"⚔️ @{h(duel['challenger_name'])} 🆚 @{h(duel['challenged_name'])}\n\n"
                f"🥇 <b>VAINQUEUR : @{h(poster_name)}</b>{bonus_txt}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📋 <b>Preuve de victoire :</b>\n\n"
                f"  👤 Vainqueur : @{h(poster_name)}\n"
                f"  📺 Canal : <b>{h(chat_title)}</b>\n"
                f"  📦 Taille vidéo : <b>{size_mb:.2f} Mo</b>\n"
                f"  🕐 Heure de publication : <code>{h(post_time_str)}</code>\n"
                f"  ⏱️ Temps depuis le début : <b>{elapsed_min}min {elapsed_sec:02d}s</b>"
                f"{loser_info}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Mise à jour des scores :</b>\n\n"
                f"  ✅ @{h(poster_name)} : <b>+{points_won} pts</b> → Total : <b>{total_winner} pts</b>\n"
                f"  ❌ @{h(opponent_name)} : <b>{points_lost} pt</b> → Total : <b>{total_opponent} pts</b>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏅 Tape /top pour voir le classement !"
            )

            try:
                await context.bot.send_message(MAIN_GROUP_ID, victory_msg, parse_mode="HTML")
                logger.info(f"✅ Message victoire envoyé dans {MAIN_GROUP_ID}")
            except Exception as e:
                logger.error(f"Erreur victoire HTML: {e}")
                # Fallback: texte brut sans formatage
                try:
                    plain = (
                        f"🏆 DUEL TERMINÉ — VICTOIRE !\n\n"
                        f"⚔️ {duel['challenger_name']} vs {duel['challenged_name']}\n\n"
                        f"🥇 VAINQUEUR : @{poster_name}\n"
                        f"📦 Taille vidéo : {size_mb:.2f} Mo\n"
                        f"🕐 Heure : {post_time_str}\n"
                        f"⏱️ Durée : {elapsed_min}min {elapsed_sec:02d}s\n\n"
                        f"✅ @{poster_name} : +{points_won} pts (Total: {total_winner} pts)\n"
                        f"❌ @{opponent_name} : {points_lost} pt (Total: {total_opponent} pts)"
                    )
                    await context.bot.send_message(MAIN_GROUP_ID, plain)
                    logger.info("✅ Message victoire envoyé en texte brut")
                except Exception as e2:
                    logger.error(f"Erreur victoire texte brut: {e2} — MAIN_GROUP_ID={MAIN_GROUP_ID}")

        break


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
    del data["duels"][duel_key]
    save_data(data)
    try:
        await bot.send_message(
            MAIN_GROUP_ID,
            f"⏰ @{esc(duel['challenged_name'])} n'a pas répondu au défi de @{esc(duel['challenger_name'])}\\.\n"
            f"Duel annulé automatiquement \\(5 min écoulées\\)\\.",
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
    del data["duels"][duel_key]
    save_data(data)
    try:
        await bot.send_message(
            MAIN_GROUP_ID,
            f"⏰ *Timeout \\!* Le duel est terminé sans vainqueur\\.\n\n"
            f"⚔️ @{esc(duel['challenger_name'])} 🆚 @{esc(duel['challenged_name'])}\n\n"
            f"Aucun des deux n'a posté de vidéo ≥ 70 Mo dans les temps\\.\n"
            f"*Match nul — aucun point attribué\\.*",
            parse_mode="MarkdownV2"
        )
    except Exception:
        pass


# ─────────────────────────────────────────────
#  STATS & CLASSEMENT
# ─────────────────────────────────────────────

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
    channel    = p.get("channel_name", "Non enregistré")

    # Calculer le ratio
    wins   = p.get("wins", 0)
    losses = p.get("losses", 0)
    played = p.get("duels_played", 0)
    ratio  = f"{round(wins/played*100)}%" if played > 0 else "N/A"

    msg = (
        f"📊 *Stats de @{esc(name)}*\n"
        f"{'━' * 20}\n\n"
        f"🏅 Points : *{esc(p.get('points', 0))}*\n"
        f"⚔️ Duels joués : *{esc(played)}*\n"
        f"✅ Victoires : *{esc(wins)}*\n"
        f"❌ Défaites : *{esc(losses)}*\n"
        f"📈 Taux de victoire : *{esc(ratio)}*\n\n"
        f"📺 Canal : *{esc(channel)}*\n"
        f"🌍 Fuseau : *{esc(tz_display)}* \\({esc(offset)}\\)\n"
    )
    await update.message.reply_text(msg, parse_mode="MarkdownV2")


async def cmd_regles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📜 *RÈGLES DES DUELS*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣ Chaque joueur enregistre *son propre canal* avec `/mychannel`\n"
        "2️⃣ Lance un duel avec `/duel @pseudo` depuis le groupe principal\n"
        "3️⃣ L'adversaire accepte avec `/accept`\n"
        "4️⃣ Chacun poste une vidéo dans *son propre canal*\n"
        "5️⃣ Le bot détecte et annonce le vainqueur dans ce groupe \\!\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🎬 *Système de points :*\n\n"
        "• 1ère vidéo ≥ 70 Mo → *\\+3 pts* \\(victoire\\) / adversaire *\\-1 pt*\n"
        "• Vidéo \\< 70 Mo → *\\-3 pts* \\(pénalité immédiate\\)\n"
        "  ↳ Si tu postes ensuite une ≥ 70 Mo avant l'adversaire → *\\+6 pts* \\!\n"
        "• Timeout sans vidéo valide → *match nul, 0 pt*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🗓️ *Duels planifiés :*\n\n"
        "• `/duel @pseudo 20:00` — l'heure est dans *ton fuseau*\n"
        "• L'adversaire voit l'heure dans *son fuseau*\n"
        "• Rappel automatique 5 min avant\n"
        "• Configure ton fuseau avec `/settimezone`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⏱️ Délai pour poster après le début : *5 minutes*\n"
        "⏱️ Délai pour accepter un défi : *5 minutes*\n"
    )
    await update.message.reply_text(msg, parse_mode="MarkdownV2")


async def cmd_resetpoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        member = await context.bot.get_chat_member(MAIN_GROUP_ID, user.id)
        if member.status not in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
            await update.message.reply_text("❌ Commande réservée aux admins\\.", parse_mode="MarkdownV2")
            return
    except Exception:
        await update.message.reply_text("❌ Impossible de vérifier tes droits\\.", parse_mode="MarkdownV2")
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

class HealthHandler(BaseHTTPRequestHandler):
    """Serveur HTTP minimal pour garder le service actif sur Render/Koyeb."""
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"DuelBot is running!")
    def log_message(self, format, *args):
        pass  # Silence les logs HTTP


def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f"🌐 Health server démarré sur port {port}")
    server.serve_forever()


def main():
    # Démarrer le serveur HTTP EN PREMIER pour passer le health check Render
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    import time as _time
    _time.sleep(1)  # Laisser le temps au serveur de démarrer

    # Vérifications au démarrage
    if not BOT_TOKEN:
        logger.critical("❌ BOT_TOKEN manquant ! Ajoute la variable d'environnement BOT_TOKEN sur Koyeb.")
        exit(1)
    if MAIN_GROUP_ID == 0:
        logger.critical("❌ MAIN_GROUP_ID manquant ! Ajoute la variable d'environnement MAIN_GROUP_ID sur Koyeb.")
        exit(1)

    logger.info(f"✅ BOT_TOKEN détecté")
    logger.info(f"✅ MAIN_GROUP_ID = {MAIN_GROUP_ID}")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("help",         cmd_start))
    app.add_handler(CommandHandler("join",         cmd_join))
    app.add_handler(CommandHandler("mychannel",    cmd_mychannel))
    app.add_handler(CommandHandler("addchannel",   cmd_addchannel))
    app.add_handler(CommandHandler("channels",     cmd_channels))
    app.add_handler(CommandHandler("settimezone",  cmd_settimezone))
    app.add_handler(CommandHandler("duel",         cmd_duel))
    app.add_handler(CommandHandler("accept",       cmd_accept))
    app.add_handler(CommandHandler("decline",      cmd_decline))
    app.add_handler(CommandHandler("cancel",       cmd_cancel))
    app.add_handler(CommandHandler("top",          cmd_top))
    app.add_handler(CommandHandler("classement",   cmd_top))
    app.add_handler(CommandHandler("stats",        cmd_stats))
    app.add_handler(CommandHandler("mystats",      cmd_stats))
    app.add_handler(CommandHandler("regles",       cmd_regles))
    app.add_handler(CommandHandler("resetpoints",  cmd_resetpoints))

    app.add_handler(CallbackQueryHandler(callback_settz, pattern=r"^settz:"))

    # Intercepte les vidéos dans les CANAUX (channel_post) ET les groupes (message)
    app.add_handler(MessageHandler(
        filters.VIDEO | filters.Document.MimeType("video/mp4"),
        handle_video
    ))
    # Handler spécifique pour les posts de canaux
    app.add_handler(MessageHandler(
        filters.UpdateType.CHANNEL_POSTS & (filters.VIDEO | filters.Document.MimeType("video/mp4")),
        handle_video
    ))

async def cmd_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche l'ID du chat actuel et les duels actifs — pour déboguer."""
    chat   = update.effective_chat
    data   = load_data()

    # Infos du chat
    lines = [
        f"🔍 *DEBUG INFO*\n",
        f"📍 Ce chat : `{esc(str(chat.id))}`",
        f"📝 Nom : {esc(chat.title or chat.username or 'N/A')}",
        f"📋 Type : {esc(chat.type)}\n",
    ]

    # Canaux enregistrés
    channels = data.get("registered_channels", {})
    lines.append(f"📺 *Canaux enregistrés \\({len(channels)}\\) :*")
    for cid, owner_id in channels.items():
        owner = data["players"].get(str(owner_id), {}).get("username", "?") if owner_id else "?"
        lines.append(f"  • `{esc(cid)}` → @{esc(owner)}")

    # Duels actifs
    duels = data.get("duels", {})
    active = [(k, d) for k, d in duels.items() if d["status"] in ["active", "pending", "scheduled"]]
    lines.append(f"\n⚔️ *Duels en cours \\({len(active)}\\) :*")
    for k, d in active:
        lines.append(
            f"  • @{esc(d['challenger_name'])} vs @{esc(d['challenged_name'])}\n"
            f"    Status: `{esc(d['status'])}`\n"
            f"    Canal A: `{esc(str(d.get('challenger_channel', 'N/A')))}`\n"
            f"    Canal B: `{esc(str(d.get('challenged_channel', 'N/A')))}`"
        )

    if not active:
        lines.append("  Aucun duel actif")

    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Répond avec l'ID du chat — à utiliser depuis n'importe quel canal."""
    msg  = update.message or update.channel_post
    if not msg:
        return
    chat = msg.chat
    await context.bot.send_message(
        MAIN_GROUP_ID,
        f"📍 ID du canal *{esc(chat.title or chat.username or 'N/A')}* : `{esc(str(chat.id))}`",
        parse_mode="MarkdownV2"
    )


# ─────────────────────────────────────────────
#  LANCEMENT
# ─────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    """Serveur HTTP minimal pour garder le service actif sur Render/Koyeb."""
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"DuelBot is running!")
    def log_message(self, format, *args):
        pass  # Silence les logs HTTP


def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f"🌐 Health server démarré sur port {port}")
    server.serve_forever()


def main():
    # Démarrer le serveur HTTP EN PREMIER pour passer le health check Render
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    import time as _time
    _time.sleep(1)  # Laisser le temps au serveur de démarrer

    # Vérifications au démarrage
    if not BOT_TOKEN:
        logger.critical("❌ BOT_TOKEN manquant ! Ajoute la variable d'environnement BOT_TOKEN sur Koyeb.")
        exit(1)
    if MAIN_GROUP_ID == 0:
        logger.critical("❌ MAIN_GROUP_ID manquant ! Ajoute la variable d'environnement MAIN_GROUP_ID sur Koyeb.")
        exit(1)

    logger.info(f"✅ BOT_TOKEN détecté")
    logger.info(f"✅ MAIN_GROUP_ID = {MAIN_GROUP_ID}")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("help",         cmd_start))
    app.add_handler(CommandHandler("join",         cmd_join))
    app.add_handler(CommandHandler("mychannel",    cmd_mychannel))
    app.add_handler(CommandHandler("addchannel",   cmd_addchannel))
    app.add_handler(CommandHandler("channels",     cmd_channels))
    app.add_handler(CommandHandler("settimezone",  cmd_settimezone))
    app.add_handler(CommandHandler("duel",         cmd_duel))
    app.add_handler(CommandHandler("accept",       cmd_accept))
    app.add_handler(CommandHandler("decline",      cmd_decline))
    app.add_handler(CommandHandler("cancel",       cmd_cancel))
    app.add_handler(CommandHandler("top",          cmd_top))
    app.add_handler(CommandHandler("classement",   cmd_top))
    app.add_handler(CommandHandler("stats",        cmd_stats))
    app.add_handler(CommandHandler("mystats",      cmd_stats))
    app.add_handler(CommandHandler("regles",       cmd_regles))
    app.add_handler(CommandHandler("resetpoints",  cmd_resetpoints))
    app.add_handler(CommandHandler("debug",        cmd_debug))
    app.add_handler(CommandHandler("chatid",       cmd_chatid))

    app.add_handler(CallbackQueryHandler(callback_settz, pattern=r"^settz:"))

    # Handler vidéo pour messages normaux (groupes)
    app.add_handler(MessageHandler(
        filters.VIDEO | filters.Document.MimeType("video/mp4"),
        handle_video
    ))
    # Handler vidéo spécifique pour les posts de CANAUX
    app.add_handler(MessageHandler(
        filters.UpdateType.CHANNEL_POSTS & (filters.VIDEO | filters.Document.MimeType("video/mp4")),
        handle_video
    ))

    logger.info("🤖 DuelBot V4 démarré !")
    logger.info(f"📢 Groupe main configuré : {MAIN_GROUP_ID}")

    # Vérifier que le bot peut envoyer dans le groupe main au démarrage
    async def post_start_message(app):
        try:
            await app.bot.send_message(
                MAIN_GROUP_ID,
                "🤖 DuelBot démarré et opérationnel ! Tapez /start pour commencer."
            )
            logger.info("✅ Message de démarrage envoyé dans le groupe main")
        except Exception as e:
            logger.error(f"❌ Impossible d'envoyer dans le groupe main ({MAIN_GROUP_ID}): {e}")
            logger.error("Vérifiez que le bot est admin dans le groupe main !")

    app.post_init = post_start_message

    app.run_polling(
        allowed_updates=["message", "channel_post", "callback_query", "edited_channel_post"]
    )


if __name__ == "__main__":
    main()
