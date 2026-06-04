# 🏸 Court Reservation Discord Bot

A Discord bot that manages 45-minute court reservation slots, tracks queue position,
and automatically pings users **2 minutes before their slot begins**.
Reservations are persisted in a local SQLite database (`court_bot.db`).

---

## Features

| Feature | Detail |
|---|---|
| `/register` | Join the queue by entering groups ahead + time left on current slot |
| `/adduser` | Add up to 3 teammates to your reservation (max 4 players total) |
| `/status` | Live board showing every active and upcoming reservation |
| `/myreservations` | See only your own slots (private, visible to you only) |
| `/cancel` | Remove an upcoming slot before it starts |
| `/addpassword` | Add a court-system login to the shared password pool |
| `/listpasswords` | View all passwords and which reservations are using them |
| `/usepassword` | Assign a pooled password to one of your reservations |
| `/subscribe` | Get a one-time DM 2 minutes before the next slot starts |
| `/unsubscribe` | Cancel your court notification subscription |
| Auto-notify | Bot pings you in the channel 2 minutes before your turn |
| Auto-expire | Slots are marked inactive once their 45-minute window ends |

---

## Queue Logic

When you register, the bot calculates your start time as:

```
start_time = now + time_remaining + (groups_in_front × 45 min)
```

**Example:** 2 groups ahead, 15 minutes left on current group
→ your slot starts in **105 minutes** (15 + 45 + 45)

---

## Setup

### 1 — Create a Discord Application & Bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application** → give it a name (e.g. *Court Bot*) → **Create**
3. In the left sidebar go to **Bot**
   - Click **Add Bot** (if shown) → confirm
   - Under **Privileged Gateway Intents**, enable **Message Content Intent**
   - Click **Save Changes**
4. Still on the Bot page, click **Reset Token** → **Yes, do it** → copy the token (you'll need it in step 5)

### 2 — Invite the Bot to Your Server

> You must be the server owner or have the **Manage Server** permission to invite a bot.

1. In the left sidebar go to **OAuth2 → URL Generator**
2. Under **Scopes**, check: `bot` and `applications.commands`
3. Under **Bot Permissions**, check:
   - `Send Messages`
   - `Send Messages in Threads`
   - `Embed Links`
   - `Read Message History`
   - `Mention Everyone`
4. Copy the generated URL at the bottom, open it in a browser, and invite the bot to your server

### 3 — Get Your Server (Guild) ID

Setting a Guild ID makes slash commands sync instantly instead of taking up to an hour.

1. In Discord, open **User Settings → Advanced** and enable **Developer Mode**
2. Right-click your server name in the left sidebar → **Copy Server ID**

### 4 — Download the Code

```bash
git clone https://github.com/your-username/court-bot.git
cd court-bot
```

Or download and unzip the repository from GitHub if you don't have git installed.

### 5 — Install Dependencies

Requires **Python 3.10+**

```bash
pip install -r requirements.txt
```

### 6 — Create a `.env` File

In the same folder as `bot.py`, create a file named `.env`:

```
DISCORD_TOKEN=paste_your_bot_token_here
GUILD_ID=paste_your_server_id_here
```

> `GUILD_ID` is optional but recommended for instant command syncing during development.
> Remove it (or leave it empty) for a global deployment.

### 7 — Run the Bot

```bash
python bot.py
```

You should see:

```
✅  Logged in as Court Bot#1234  (ID: ...)
   Synced 10 slash command(s) to guild 123456789.
```

Slash commands will now appear in your server when you type `/`.

> The bot must keep running to send notifications. For long-term hosting, run it inside
> `screen` or `tmux`, or set it up as a system service.

---

## Commands Reference

### `/register court_number groups_in_front time_remaining`
Register your group in the reservation queue.

| Parameter | Type | Description |
|---|---|---|
| `court_number` | integer | The court you are reserving (1–99) |
| `groups_in_front` | integer | How many groups are ahead of yours right now |
| `time_remaining` | number | Minutes left on the court for the **current** active group (0–45) |

**Example:** `/register court_number:2 groups_in_front:1 time_remaining:12`

---

### `/adduser reservation_id user1 [user2] [user3]`
Add teammates to your reservation. Only the person who registered can add players.
Maximum **4 players** per reservation (including the registrant).

---

### `/status`
Displays a live board of all reservations grouped by court — on-court, upcoming, and recently expired.

---

### `/myreservations`
Shows only your reservations. The response is ephemeral (only you can see it).

---

### `/cancel reservation_id`
Cancels an upcoming reservation by its ID (shown in `/status`).
You can only cancel your own reservations, and only before they start.

---

### `/addpassword username password`
Adds a court-booking-system login to the shared pool. The pool resets nightly at **12:00 AM PT**.

---

### `/listpasswords`
Shows all passwords in the pool, split into **Free** and **In Use** sections.
Each entry displays the username, password, and (if in use) when it frees up.

---

### `/usepassword reservation_id password_id`
Assigns a free password from the pool to one of your reservations.
Password IDs are shown in `/listpasswords`; reservation IDs are shown in `/status`.

---

### `/subscribe [court_number]`
Subscribe to receive a **one-time DM** 2 minutes before the next court slot starts.

| Parameter | Type | Description |
|---|---|---|
| `court_number` | integer (optional) | Only notify when this specific court is about to start. Omit to be notified for any court. |

The subscription fires once and then cancels itself automatically.
Calling `/subscribe` again replaces any existing subscription.

**Examples:**
- `/subscribe` — notify me when any court slot is about to start
- `/subscribe court_number:3` — notify me only for Court 3

---

### `/unsubscribe`
Cancels your active subscription. Has no effect if you have no subscription.

---

## Notification Timing

The bot runs a background check every **20 seconds**.
It fires the 2-minute warning when your slot is ≤ 2 min 30 sec away — this buffer
ensures the notification always goes out even if the loop is mid-cycle.

The notification appears as a **channel ping** in the same channel where you used `/register`.
If the bot lacks permission to send there, it will fall back to a **direct message**.

---

## Customization

Edit the constants at the top of `logic.py` to change behavior:

```python
SLOT_DURATION_MINS  = 45   # minutes per court slot
NOTIFY_WINDOW_SECS  = 150  # fire when within 2 min 30 sec of start
LOOP_INTERVAL_SECS  = 20   # background check frequency (seconds)
```

---

## Running the Tests

```bash
python -m unittest test_bot -v
```

57 unit tests cover every slash command's logic — validation, error branches, DB writes, and edge cases — using an in-memory SQLite database so no real data is touched.
