# 🤖 DuelBot V2 — Fuseaux horaires & Duels planifiés

## Installation

```bash
pip install python-telegram-bot pytz
```

## Configuration

Ouvre `duel_bot.py` et modifie ces 2 lignes :

```python
BOT_TOKEN     = "VOTRE_BOT_TOKEN_ICI"
MAIN_GROUP_ID = -1001234567890
```

## Lancement

```bash
python duel_bot.py
```

---

## 🌍 Nouveau — Fuseaux horaires

Chaque joueur peut enregistrer son fuseau avec `/settimezone`.
Un menu inline apparaît avec les fuseaux les plus courants (Paris, Kinshasa, Abidjan, New York, Dubai, etc.)

Quand un joueur lance `/duel @pseudo 18:30` :
- L'heure `18:30` est interprétée dans **son fuseau à lui**
- L'adversaire voit l'heure **convertie dans son propre fuseau**

**Exemple :**
> @Alpha (Paris UTC+1) lance `/duel @Beta 20:00`
> @Beta (New York UTC-5) voit : `14:00 New York`

---

## 🗓️ Formats de duel acceptés

```
/duel @pseudo                → duel immédiat
/duel @pseudo 18:30          → aujourd'hui à 18h30 (ton fuseau)
/duel @pseudo 18:30 25/07    → le 25 juillet à 18h30
/duel @pseudo 25/07/2025 18:30  → avec l'année
```

**Déroulement d'un duel planifié :**
1. Invitation envoyée avec les horaires traduits pour chaque joueur
2. L'adversaire accepte avec `/accept`
3. Rappel automatique 5 minutes avant le début
4. Le bot annonce le début à l'heure exacte
5. 5 minutes pour poster une vidéo

---

## Toutes les commandes

| Commande | Description |
|----------|-------------|
| `/join` | S'inscrire |
| `/settimezone` | Choisir son fuseau horaire |
| `/duel @pseudo [heure]` | Lancer un duel (immédiat ou planifié) |
| `/accept` | Accepter un duel |
| `/decline` | Refuser un duel |
| `/cancel` | Annuler son duel actif |
| `/top` ou `/classement` | Top 10 |
| `/stats` | Stats + fuseau enregistré |
| `/regles` | Règles du jeu |
| `/addchat` | (Admin) Surveiller ce canal |
| `/removechat` | (Admin) Retirer ce canal |
| `/listchats` | Canaux surveillés |
| `/resetpoints @pseudo` | (Admin) Remettre à 0 |

---

## Règles des points

| Situation | Points |
|-----------|--------|
| Vidéo ≥ 70 Mo postée en premier | +3 pts |
| Vidéo < 70 Mo | -3 pts (pénalité) |
| Vidéo ≥ 70 Mo après pénalité, avant l'adversaire | +6 pts (rattrapage) |
| Perdre le duel | -1 pt |
| Timeout sans vidéo | Match nul, 0 pt |
