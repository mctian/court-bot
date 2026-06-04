"""
Court Reservation Discord Bot
─────────────────────────────
Tracks 45-minute court slots, auto-calculates queue position,
and pings users 2 minutes before their slot begins.
State is persisted in SQLite (court_bot.db).

Commands:
  /register      — join the queue
  /adduser       — add up to 3 more players to a reservation (max 4 total)
  /status        — see all reservations grouped by court
  /myreservations — see your own reservations (private)
  /cancel        — remove an upcoming slot
  /addpassword   — add a court-system username & password to the shared pool
  /listpasswords — show all passwords and whether they are currently in use
  /usepassword   — assign a password from the pool to a reservation
"""

import os
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from logic import (
    SLOT_DURATION_MINS, NOTIFY_WINDOW_SECS, LOOP_INTERVAL_SECS,
    PACIFIC, MIDNIGHT_PT,
    MAX_COURT_NUMBER, MAX_CREDENTIAL_LEN,
    REGISTER_COOLDOWN_SECS, WRITE_COOLDOWN_SECS, MAX_USERS_PER_RESERVATION,
    DB_PATH,
    _init_db, _row_to_res,
    _get_reservation, _get_password,
    _visible_reservations, _my_reservations, _reservation_for_password,
    _safe,
    logic_register, logic_cancel, logic_adduser,
    logic_addpassword, logic_listpasswords, logic_usepassword,
)

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────
load_dotenv()
TOKEN    = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
GUILD    = discord.Object(id=int(GUILD_ID)) if GUILD_ID else None

# ─────────────────────────────────────────────────────────────
# Bot Setup
# ─────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────
db = _init_db(DB_PATH)

# ─────────────────────────────────────────────────────────────
# Status Embed Builder
# ─────────────────────────────────────────────────────────────
def build_status_embed(res_list: list[dict], title: str = "🏸  Court Reservation Board") -> discord.Embed:
    now   = datetime.now()
    embed = discord.Embed(title=title, color=0x1ABC9C)
    embed.set_footer(text=f"Updated {now.strftime('%I:%M:%S %p')}  ·  💬 DM this bot to use commands without cluttering the channel")

    if not res_list:
        embed.description = "No reservations to display."
        return embed

    courts: dict[int, list[dict]] = {}
    for r in res_list:
        courts.setdefault(r["court_number"], []).append(r)

    for court_num in sorted(courts):
        embed.add_field(name=f"🏟️  Court {court_num}", value="​", inline=False)
        for r in sorted(courts[court_num], key=lambda x: x["start_time"]):
            start, end = r["start_time"], r["end_time"]
            if now < start:
                mins   = int((start - now).total_seconds() // 60)
                timing = f"⏳  Starts in **{mins} min**  ·  {start.strftime('%I:%M %p')} – {end.strftime('%I:%M %p')}"
                icon, label = "🔵", "UPCOMING"
            elif start <= now < end:
                m, s   = divmod(int((end - now).total_seconds()), 60)
                timing = f"⏱️  **{m}m {s:02d}s remaining**  ·  Ends {end.strftime('%I:%M %p')}"
                icon, label = "🟢", "ON COURT"
            else:
                timing = f"Slot: {start.strftime('%I:%M')} – {end.strftime('%I:%M %p')}"
                icon, label = "⚫", "EXPIRED"

            players = "  ".join(f"<@{uid}>" for uid in r["users"]) or f"<@{r['user_id']}>"
            embed.add_field(
                name  = f"{icon}  **#{r['id']}**  ·  {r['user_name']}  [{label}]",
                value = f"{timing}\n👥  {players}",
                inline = False,
            )
    return embed

# ─────────────────────────────────────────────────────────────
# Global Error Handler
# ─────────────────────────────────────────────────────────────
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"⏳  Slow down! Try again in **{error.retry_after:.1f}s**.", ephemeral=True
        )
    else:
        print(f"⚠️  Unhandled command error: {error}")

# ─────────────────────────────────────────────────────────────
# Events
# ─────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅  Logged in as {bot.user}  (ID: {bot.user.id})")
    try:
        if GUILD:
            bot.tree.copy_global_to(guild=GUILD)
            synced = await bot.tree.sync(guild=GUILD)
            print(f"   Synced {len(synced)} slash command(s) to guild {GUILD.id}.")
        else:
            synced = await bot.tree.sync()
            print(f"   Synced {len(synced)} slash command(s) globally (may take up to 1 hour).")
    except Exception as exc:
        print(f"   Command sync error: {exc}")
    notification_loop.start()
    midnight_password_reset.start()

# ─────────────────────────────────────────────────────────────
# /register
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="register", description="Register your group for a court reservation")
@app_commands.checks.cooldown(1, REGISTER_COOLDOWN_SECS, key=lambda i: i.user.id)
@app_commands.describe(
    court_number    = "The court number you are reserving",
    groups_in_front = "Number of groups currently ahead of yours in the queue",
    time_remaining  = "Minutes left on the court for the current active group (0–45)",
)
async def cmd_register(
    interaction    : discord.Interaction,
    court_number   : int,
    groups_in_front: int,
    time_remaining : float,
):
    result = logic_register(
        db, interaction.user.id, interaction.user.display_name,
        interaction.channel_id, court_number, groups_in_front, time_remaining,
    )
    if result["error"]:
        await interaction.response.send_message(result["error"], ephemeral=True); return

    embed = discord.Embed(title="✅  Reservation Registered!", color=0x2ECC71)
    embed.add_field(name="Group",            value=f"<@{interaction.user.id}>",                      inline=True)
    embed.add_field(name="Reservation #",    value=f"**#{result['res_id']}**",                        inline=True)
    embed.add_field(name="Court",            value=f"**Court {court_number}**",                       inline=True)
    embed.add_field(name="Groups Ahead",     value=str(groups_in_front),                              inline=True)
    embed.add_field(name="Left on Current",  value=f"{time_remaining:.0f} min",                      inline=True)
    embed.add_field(name="Total Wait",       value=f"≈ {int(result['wait_mins'])} min",              inline=True)
    embed.add_field(
        name  = "Your Court Window",
        value = f"**{result['start_time'].strftime('%I:%M %p')}** → {result['end_time'].strftime('%I:%M %p')}",
        inline = False,
    )
    embed.set_footer(text="🔔  You'll be pinged 2 minutes before your slot starts!  ·  💬 DM this bot to avoid channel clutter")
    await interaction.response.send_message(embed=embed)

# ─────────────────────────────────────────────────────────────
# /status
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="status", description="Show all current and upcoming court reservations")
async def cmd_status(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_status_embed(_visible_reservations(db)))

# ─────────────────────────────────────────────────────────────
# /myreservations
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="myreservations", description="View only your own reservations (visible to you only)")
async def cmd_myreservations(interaction: discord.Interaction):
    embed       = build_status_embed(_my_reservations(db, interaction.user.id))
    embed.title = f"🏸  {interaction.user.display_name}'s Reservations"
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ─────────────────────────────────────────────────────────────
# /cancel
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="cancel", description="Cancel one of your upcoming reservations")
@app_commands.describe(reservation_id="The reservation number shown in /status")
async def cmd_cancel(interaction: discord.Interaction, reservation_id: int):
    result = logic_cancel(db, reservation_id, interaction.user.id)
    if result["error"]:
        await interaction.response.send_message(result["error"], ephemeral=True); return
    await interaction.response.send_message(
        f"✅  Reservation **#{reservation_id}** has been cancelled.\n"
        f"*💬 Tip: DM this bot to use commands without cluttering the channel.*",
        ephemeral=True,
    )

# ─────────────────────────────────────────────────────────────
# /adduser
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="adduser", description=f"Add up to 3 more players to a reservation (max {MAX_USERS_PER_RESERVATION} total)")
@app_commands.checks.cooldown(1, WRITE_COOLDOWN_SECS, key=lambda i: i.user.id)
@app_commands.describe(
    reservation_id = "The reservation ID to add players to (from /status)",
    user1          = "Player to add",
    user2          = "Player to add (optional)",
    user3          = "Player to add (optional)",
)
async def cmd_adduser(
    interaction   : discord.Interaction,
    reservation_id: int,
    user1         : discord.Member,
    user2         : discord.Member | None = None,
    user3         : discord.Member | None = None,
):
    users_to_add = [
        (m.id, m.display_name)
        for m in [user1, user2, user3]
        if m is not None
    ]
    result = logic_adduser(db, reservation_id, interaction.user.id, users_to_add)
    if result["error"]:
        await interaction.response.send_message(result["error"], ephemeral=True); return

    lines = []
    if result["added"]:   lines.append(f"✅  Added: {', '.join(result['added'])}")
    if result["skipped"]: lines.append(f"⚠️  Skipped: {', '.join(result['skipped'])}")
    users = result["users"]
    lines.append(f"\n👥  Current players ({len(users)}/{MAX_USERS_PER_RESERVATION}): {'  '.join(f'<@{uid}>' for uid in users)}")
    lines.append("\n*💬 Tip: DM this bot to use commands without cluttering the channel.*")
    await interaction.response.send_message("\n".join(lines))

# ─────────────────────────────────────────────────────────────
# /addpassword
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="addpassword", description="Add a court-system username and password to the shared pool")
@app_commands.checks.cooldown(1, WRITE_COOLDOWN_SECS, key=lambda i: i.user.id)
@app_commands.describe(
    username = f"Court booking system username (max {MAX_CREDENTIAL_LEN} chars)",
    password = f"Court booking system password (max {MAX_CREDENTIAL_LEN} chars)",
)
async def cmd_addpassword(interaction: discord.Interaction, username: str, password: str):
    result = logic_addpassword(db, username, password, interaction.user.display_name)
    if result["error"]:
        await interaction.response.send_message(result["error"], ephemeral=True); return
    print(f"🔑  Password #{result['pw_id']} added by {_safe(interaction.user.display_name)}")
    await interaction.response.send_message(
        f"✅  Password **#{result['pw_id']}** added to the pool.\n"
        f"> Username: `{result['username']}`  ·  Password: `{result['password']}`\n"
        f"*💬 Tip: DM this bot to use commands without cluttering the channel.*"
    )

# ─────────────────────────────────────────────────────────────
# /listpasswords
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="listpasswords", description="Show all court passwords and whether they are currently in use")
async def cmd_listpasswords(interaction: discord.Interaction):
    result = logic_listpasswords(db)
    if result["empty"]:
        await interaction.response.send_message("📋  No passwords in the pool yet. Use `/addpassword` to add one."); return

    embed = discord.Embed(title="🔑  Court Password Pool", color=0x9B59B6)
    embed.set_footer(text=f"Resets nightly at 12:00 AM PT · Next: {result['next_expire'].strftime('%b %d')}  ·  💬 DM this bot to avoid channel clutter")

    def fmt_free(p):
        return f"`{p['username']}` `{p['password']}`"

    def fmt_used(p):
        res = _reservation_for_password(db, p["id"])
        return f"`{p['username']}` `{p['password']}`\nfrees {res['end_time'].strftime('%-I:%M %p')}"

    embed.add_field(
        name   = "🟢  Free",
        value  = "\n\n".join(fmt_free(p) for p in result["free"]) or "*None*",
        inline = False,
    )
    embed.add_field(
        name   = "🔴  In Use",
        value  = "\n\n".join(fmt_used(p) for p in result["in_use"]) or "*None*",
        inline = False,
    )
    embed.add_field(
        name   = "💡  Tip",
        value  = "Mark a free password as in-use by assigning it to a court:\n`/usepassword reservation_id:<id> password_id:<id>`\nGet reservation IDs from `/status`.",
        inline = False,
    )
    await interaction.response.send_message(embed=embed)

# ─────────────────────────────────────────────────────────────
# /usepassword
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="usepassword", description="Assign a password from the pool to one of your reservations")
@app_commands.checks.cooldown(1, WRITE_COOLDOWN_SECS, key=lambda i: i.user.id)
@app_commands.describe(
    reservation_id = "The reservation ID to assign the password to (from /status)",
    password_id    = "The password ID to assign (from /listpasswords)",
)
async def cmd_usepassword(interaction: discord.Interaction, reservation_id: int, password_id: int):
    result = logic_usepassword(db, reservation_id, password_id, interaction.user.id)
    if result["error"]:
        await interaction.response.send_message(result["error"], ephemeral=True); return
    await interaction.response.send_message(
        f"✅  Password **#{password_id}** (`{result['pw_username']}`) assigned to "
        f"Reservation **#{reservation_id}** · Court {result['court_number']}.\n"
        f"*💬 Tip: DM this bot to use commands without cluttering the channel.*"
    )

# ─────────────────────────────────────────────────────────────
# Helper: Log Missing Channel Permissions
# ─────────────────────────────────────────────────────────────
REQUIRED_PERMISSIONS = [
    ("view_channel",             "View Channel"),
    ("send_messages",            "Send Messages"),
    ("send_messages_in_threads", "Send Messages in Threads"),
    ("embed_links",              "Embed Links"),
    ("read_message_history",     "Read Message History"),
    ("mention_everyone",         "Mention @everyone / @here / All Roles"),
]

def log_missing_permissions(channel: discord.TextChannel, reservation_id: int) -> list[str]:
    perms   = channel.permissions_for(channel.guild.me)
    missing = [label for attr, label in REQUIRED_PERMISSIONS if not getattr(perms, attr, True)]
    if missing:
        print(f"⚠️  Reservation #{reservation_id} — bot is missing permissions in "
              f"#{_safe(channel.name)} (ID {channel.id}):\n" + "\n".join(f"      ✗  {m}" for m in missing))
    else:
        print(f"⚠️  Reservation #{reservation_id} — all permissions present in "
              f"#{_safe(channel.name)} but send failed (rate-limit or outage?).")
    return missing

# ─────────────────────────────────────────────────────────────
# Background: Notification Loop
# ─────────────────────────────────────────────────────────────
@tasks.loop(seconds=LOOP_INTERVAL_SECS)
async def notification_loop():
    now  = datetime.now()
    rows = db.execute("SELECT * FROM reservations WHERE active=1").fetchall()

    for row in rows:
        r = _row_to_res(row)

        # Auto-expire
        if now >= r["end_time"]:
            db.execute("UPDATE reservations SET active=0 WHERE id=?", (r["id"],))
            db.commit()
            continue

        secs_until_start = (r["start_time"] - now).total_seconds()
        if r["notified_2min"] or secs_until_start > NOTIFY_WINDOW_SECS:
            continue

        # Mark notified before sending so a crash doesn't re-send
        db.execute("UPDATE reservations SET notified_2min=1 WHERE id=?", (r["id"],))
        db.commit()

        channel = bot.get_channel(r["channel_id"])
        if not channel:
            continue

        creator = f"<@{r['user_id']}>"
        if secs_until_start <= 0:
            msg = (f"⏰  **YOUR SLOT IS STARTING NOW!**  {creator}\n"
                   f"> Reservation **#{r['id']}**  ·  **Court {r['court_number']}** — get to the court! 🏸")
        else:
            mins_left = max(1, int(secs_until_start // 60))
            msg = (f"⏰  **2-MINUTE WARNING!**  {creator}\n"
                   f"> Reservation **#{r['id']}**  ·  **Court {r['court_number']}**\n"
                   f"> Starts at **{r['start_time'].strftime('%I:%M %p')}** — you're up in about "
                   f"**{mins_left} minute{'s' if mins_left > 1 else ''}**! 🏸")

        try:
            await channel.send(msg)
        except discord.errors.Forbidden:
            log_missing_permissions(channel, r["id"])
            try:
                missing     = log_missing_permissions(channel, r["id"])
                missing_str = ", ".join(missing) if missing else "unknown"
                user        = await bot.fetch_user(r["user_id"])
                await user.send(msg + f"\n\n*(Sent via DM — bot is missing **{missing_str}** "
                                      f"in <#{r['channel_id']}>. Ask an admin to fix this.)*")
            except Exception as dm_err:
                print(f"⚠️  DM fallback also failed for {_safe(r['user_name'])}: {dm_err}")
        except discord.errors.HTTPException as e:
            print(f"⚠️  Reservation #{r['id']} — HTTP error: {e}")

@notification_loop.before_loop
async def _before_loop():
    await bot.wait_until_ready()

# ─────────────────────────────────────────────────────────────
# Background: Midnight PT Password Reset
# ─────────────────────────────────────────────────────────────
@tasks.loop(time=MIDNIGHT_PT)
async def midnight_password_reset():
    count = db.execute("SELECT COUNT(*) FROM passwords").fetchone()[0]
    freed = [r[0] for r in db.execute(
        "SELECT id FROM reservations WHERE password_id IS NOT NULL AND active=1"
    ).fetchall()]
    db.execute("UPDATE reservations SET password_id=NULL WHERE password_id IS NOT NULL")
    db.execute("DELETE FROM passwords")
    db.commit()

    now_pt = datetime.now(PACIFIC)
    print(f"🕛  Midnight PT reset — {count} password(s) cleared"
          + (f", unlinked from reservations {[_safe(str(i)) for i in freed]}" if freed else "")
          + f"  [{now_pt.strftime('%Y-%m-%d %I:%M %p PT')}]")

@midnight_password_reset.before_loop
async def _before_midnight_reset():
    await bot.wait_until_ready()

# ─────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        print("❌  DISCORD_TOKEN is not set.")
        print("   Create a .env file with:  DISCORD_TOKEN=your_bot_token_here")
    else:
        bot.run(TOKEN)
