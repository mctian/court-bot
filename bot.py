"""
Court Reservation Discord Bot
─────────────────────────────
Tracks 45-minute court slots, auto-calculates queue position,
and pings users 2 minutes before their slot begins.
State is persisted in SQLite (court_bot.db).

Commands:
  /register       — join the queue (requires last reservation to have ≥2 passwords)
  /adduser        — add up to 3 more players to a reservation (max 4 total)
  /status         — see all reservations grouped by court
  /myreservations — see your own reservations (private)
  /cancel         — remove an upcoming slot
  /addpassword    — add a court-system username & password to the shared pool
  /listpasswords  — show all passwords and whether they are currently in use
  /usepassword    — assign a password from the pool to a reservation
  /subscribe      — get a one-time DM 2 min before the next slot starts
  /unsubscribe    — cancel your court notification subscription
  /delete         — permanently delete one of your reservations

Pet System:
  /pet            — show your pet publicly; recovery code sent privately
  /food           — check how much food you have
  /feed [amount]  — feed your pet to gain XP (default: all food)
  /rename <name>  — give your pet a new name
  /whistle <code> — recover your pet using the code shown in /pet

Food Economy:
  +20 food  — making a reservation
  +5 food   — assigning a password with /usepassword
  +1 food   — any other command (once per minute)
"""

import os
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from logic import (
    NOTIFY_WINDOW_SECS, LOOP_INTERVAL_SECS,
    PACIFIC, MIDNIGHT_PT,
    MAX_CREDENTIAL_LEN, MAX_PET_NAME_LEN,
    REGISTER_COOLDOWN_SECS, WRITE_COOLDOWN_SECS, MAX_USERS_PER_RESERVATION,
    MIN_PASSWORDS_FOR_NEW_RES,
    DB_PATH,
    _init_db, _row_to_res,
    _visible_reservations, _my_reservations, _reservation_for_password,
    _pop_subscribers_for_court,
    _safe, _award_cmd_food, _pet_tier, _xp_for_tier, PET_TIERS,
    logic_register, logic_cancel, logic_adduser,
    logic_addpassword, logic_listpasswords, logic_usepassword,
    logic_subscribe, logic_unsubscribe, logic_delete,
    logic_pet, logic_food, logic_feed, logic_whistle, logic_rename,
    delete_expired_passwords,
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
    expired = delete_expired_passwords(db)
    if expired:
        print(f"🕛  Startup cleanup — deleted {expired} expired password(s) missed while bot was offline.")
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
    if result["error"] == "REMIND":
        await interaction.response.send_message(
            f"💡  **Almost there!** Before making a new reservation, assign at least "
            f"**{MIN_PASSWORDS_FOR_NEW_RES} passwords** to your last one with `/usepassword`.\n"
            f"Each password you assign earns you **5 🍖 food** for your pet — don't miss out!",
            ephemeral=True,
        )
        return
    if result["error"]:
        await interaction.response.send_message(result["error"], ephemeral=True)
        return

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
    embed.add_field(name="🍖  Pet Food",  value=f"+{result['food_awarded']} food earned!", inline=False)
    embed.set_footer(text="🔔  You'll be pinged 2 minutes before your slot starts!  ·  💬 DM this bot to use commands without cluttering the channel")
    await interaction.response.send_message(embed=embed)

# ─────────────────────────────────────────────────────────────
# /status
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="status", description="Show all current and upcoming court reservations")
async def cmd_status(interaction: discord.Interaction):
    got_food = _award_cmd_food(db, interaction.user.id, interaction.user.display_name)
    await interaction.response.send_message(embed=build_status_embed(_visible_reservations(db)))
    if got_food:
        await interaction.followup.send("🍖  +1 food earned!", ephemeral=True)

# ─────────────────────────────────────────────────────────────
# /myreservations
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="myreservations", description="View only your own reservations (visible to you only)")
async def cmd_myreservations(interaction: discord.Interaction):
    got_food    = _award_cmd_food(db, interaction.user.id, interaction.user.display_name)
    embed       = build_status_embed(_my_reservations(db, interaction.user.id))
    embed.title = f"🏸  {interaction.user.display_name}'s Reservations"
    if got_food:
        embed.add_field(name="🍖  Food", value="+1 food earned!", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ─────────────────────────────────────────────────────────────
# /cancel
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="cancel", description="Cancel one of your upcoming reservations")
@app_commands.describe(reservation_id="The reservation number shown in /status")
async def cmd_cancel(interaction: discord.Interaction, reservation_id: int):
    result = logic_cancel(db, reservation_id, interaction.user.id)
    if result["error"]:
        await interaction.response.send_message(result["error"], ephemeral=True)
        return
    got_food = _award_cmd_food(db, interaction.user.id, interaction.user.display_name)
    food_line = "\n🍖  +1 food earned!" if got_food else ""
    await interaction.response.send_message(
        f"✅  Reservation **#{reservation_id}** has been cancelled.{food_line}\n"
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
        await interaction.response.send_message(result["error"], ephemeral=True)
        return

    got_food = _award_cmd_food(db, interaction.user.id, interaction.user.display_name)
    lines = []
    if result["added"]:
        lines.append(f"✅  Added: {', '.join(result['added'])}")
    if result["skipped"]:
        lines.append(f"⚠️  Skipped: {', '.join(result['skipped'])}")
    users = result["users"]
    lines.append(f"\n👥  Current players ({len(users)}/{MAX_USERS_PER_RESERVATION}): {'  '.join(f'<@{uid}>' for uid in users)}")
    if got_food:
        lines.append("🍖  +1 food earned!")
    lines.append("\n*💬 Tip: DM this bot to use commands without cluttering the channel.*")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)

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
        await interaction.response.send_message(result["error"], ephemeral=True)
        return
    got_food = _award_cmd_food(db, interaction.user.id, interaction.user.display_name)
    food_line = "\n🍖  +1 food earned!" if got_food else ""
    print(f"🔑  Password #{result['pw_id']} added by {_safe(interaction.user.display_name)}")
    await interaction.response.send_message(
        f"✅  Password **#{result['pw_id']}** added to the pool.\n"
        f"> Username: `{result['username']}`  ·  Password: `{result['password']}`{food_line}\n"
        f"*💬 Tip: DM this bot to use commands without cluttering the channel.*",
        ephemeral=True,
    )

# ─────────────────────────────────────────────────────────────
# /listpasswords
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="listpasswords", description="Show all court passwords and whether they are currently in use")
async def cmd_listpasswords(interaction: discord.Interaction):
    got_food = _award_cmd_food(db, interaction.user.id, interaction.user.display_name)
    result = logic_listpasswords(db)
    if result["empty"]:
        food_line = "\n🍖  +1 food earned!" if got_food else ""
        await interaction.response.send_message(
            f"📋  No passwords in the pool yet. Use `/addpassword` to add one.{food_line}\n"
            "*💬 Tip: DM this bot to use commands without cluttering the channel.*"
        )
        return

    embed = discord.Embed(title="🔑  Court Password Pool", color=0x9B59B6)
    embed.set_footer(text=f"Resets nightly at 12:00 AM PT · Next: {result['next_expire'].strftime('%b %d')}  ·  💬 DM this bot to use commands without cluttering the channel")

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
        value  = "Mark a free password as in-use by assigning it to a court:\n`/usepassword reservation_id:<id> username:<username>`\nGet reservation IDs from `/status`.",
        inline = False,
    )
    if got_food:
        embed.add_field(name="🍖  Food", value="+1 food earned!", inline=False)
    await interaction.response.send_message(embed=embed)

# ─────────────────────────────────────────────────────────────
# /usepassword
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="usepassword", description="Assign a password from the pool to one of your reservations")
@app_commands.checks.cooldown(1, WRITE_COOLDOWN_SECS, key=lambda i: i.user.id)
@app_commands.describe(
    reservation_id = "The reservation ID to assign the password to (from /status)",
    username       = "The username of the password to assign (from /listpasswords)",
)
async def cmd_usepassword(interaction: discord.Interaction, reservation_id: int, username: str):
    result = logic_usepassword(db, reservation_id, username, interaction.user.id)
    if result["error"]:
        await interaction.response.send_message(result["error"], ephemeral=True)
        return
    await interaction.response.send_message(
        f"✅  `{result['pw_username']}` assigned to "
        f"Reservation **#{reservation_id}** · Court {result['court_number']}.\n"
        f"🍖  +{result['food_awarded']} food earned for your pet!\n"
        f"*💬 Tip: DM this bot to use commands without cluttering the channel.*",
        ephemeral=True,
    )

# ─────────────────────────────────────────────────────────────
# /subscribe
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="subscribe", description="Get a one-time DM 2 minutes before the next court slot starts")
@app_commands.describe(court_number="Only notify for this specific court (omit for any court)")
async def cmd_subscribe(interaction: discord.Interaction, court_number: int | None = None):
    result = logic_subscribe(db, interaction.user.id, interaction.channel_id, court_number)
    if result["error"]:
        await interaction.response.send_message(result["error"], ephemeral=True)
        return

    got_food  = _award_cmd_food(db, interaction.user.id, interaction.user.display_name)
    food_line = "\n🍖  +1 food earned!" if got_food else ""
    scope = f"Court {court_number}" if court_number else "any court"
    verb  = "updated" if result["replaced"] else "set"
    await interaction.response.send_message(
        f"🔔  Subscription {verb}! You'll get a DM 2 minutes before the next slot on **{scope}**.\n"
        f"Use `/unsubscribe` to cancel.{food_line}\n"
        f"*💬 Tip: DM this bot to use commands without cluttering the channel.*",
        ephemeral=True,
    )

# ─────────────────────────────────────────────────────────────
# /unsubscribe
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="unsubscribe", description="Cancel your court notification subscription")
async def cmd_unsubscribe(interaction: discord.Interaction):
    result = logic_unsubscribe(db, interaction.user.id)
    if result["error"]:
        await interaction.response.send_message(result["error"], ephemeral=True)
        return
    got_food  = _award_cmd_food(db, interaction.user.id, interaction.user.display_name)
    food_line = "\n🍖  +1 food earned!" if got_food else ""
    await interaction.response.send_message(
        f"🔕  Subscription cancelled. You won't receive any more court notifications.{food_line}\n"
        "*💬 Tip: DM this bot to use commands without cluttering the channel.*",
        ephemeral=True,
    )

# ─────────────────────────────────────────────────────────────
# /delete
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="delete", description="Permanently delete any reservation")
@app_commands.describe(reservation_id="The reservation number shown in /status")
async def cmd_delete(interaction: discord.Interaction, reservation_id: int):
    result = logic_delete(db, reservation_id)
    if result["error"]:
        await interaction.response.send_message(result["error"], ephemeral=True)
        return
    got_food  = _award_cmd_food(db, interaction.user.id, interaction.user.display_name)
    food_line = "\n🍖  +1 food earned!" if got_food else ""
    await interaction.response.send_message(
        f"🗑️  Reservation **#{reservation_id}** has been permanently deleted.{food_line}\n"
        f"*💬 Tip: DM this bot to use commands without cluttering the channel.*",
        ephemeral=True,
    )

# ─────────────────────────────────────────────────────────────
# Pet XP progress bar helper
# ─────────────────────────────────────────────────────────────
def _xp_bar(xp: int, width: int = 8) -> str:
    tier = _pet_tier(xp)
    if tier >= len(PET_TIERS) - 1:
        return "█" * width + "  MAX LEVEL"
    low  = _xp_for_tier(tier)
    high = _xp_for_tier(tier + 1)
    span = high - low
    done = xp - low
    filled = int(done / span * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}]  {done}/{span} XP to next tier"

# ─────────────────────────────────────────────────────────────
# /pet
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="pet", description="Show your pet to everyone (recovery code sent privately)")
async def cmd_pet(interaction: discord.Interaction):
    result = logic_pet(db, interaction.user.id, interaction.user.display_name)
    r   = result
    bar = _xp_bar(r["xp"])
    embed = discord.Embed(
        title = f"{r['emoji']}  {r['pet_name']}",
        color = 0xF1C40F,
    )
    embed.add_field(name="Owner",    value=f"<@{interaction.user.id}>",               inline=True)
    embed.add_field(name="Tier",     value=f"**{r['tier']}** / {len(PET_TIERS) - 1}", inline=True)
    embed.add_field(name="Total XP", value=str(r["xp"]),                               inline=True)
    embed.add_field(name="Progress", value=bar,                                         inline=False)
    await interaction.response.send_message(embed=embed)
    feed_hint = f"Use `/feed` to give it to **{r['pet_name']}**!" if r["food"] > 0 else "Earn food by making reservations, assigning passwords, or using commands!"
    await interaction.followup.send(
        f"🔑  Recovery code: `{r['hash']}`\n"
        f"🍖  Food on hand: **{r['food']}**  —  {feed_hint}\n"
        f"To recover on another host: `/whistle {r['hash']} {r['emoji']}`\n"
        f"*(Code updates each time your pet evolves to a new tier. Food and partial XP are not recovered.)*",
        ephemeral=True,
    )

# ─────────────────────────────────────────────────────────────
# /food
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="food", description="Check how much food you have for your pet")
async def cmd_food(interaction: discord.Interaction):
    got_food = _award_cmd_food(db, interaction.user.id, interaction.user.display_name)
    result   = logic_food(db, interaction.user.id, interaction.user.display_name)
    food_line = "\n🍖  +1 food earned!" if got_food else ""
    await interaction.response.send_message(
        f"🍽️  You have **{result['food']}** food.{food_line}\n"
        f"*Earn more by making reservations (+20), assigning passwords (+5), or using any command (+1/min).*",
        ephemeral=True,
    )

# ─────────────────────────────────────────────────────────────
# /feed
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="feed", description="Feed your pet to gain experience")
@app_commands.describe(amount="How much food to feed (default: all your food)")
async def cmd_feed(interaction: discord.Interaction, amount: int | None = None):
    result = logic_feed(db, interaction.user.id, interaction.user.display_name, amount)
    if result["error"] == "no_food":
        await interaction.response.send_message(
            "🍽️  You have no food! Earn food by:\n"
            "• Making a reservation — **+20 🍖**\n"
            "• Assigning a password — **+5 🍖**\n"
            "• Using any other command — **+1 🍖** (once per minute)",
            ephemeral=True,
        )
        return
    if result["error"]:
        await interaction.response.send_message(result["error"], ephemeral=True)
        return

    r   = result
    bar = _xp_bar(r["new_xp"])
    lines = [f"You fed **{r['pet_name']}** {r['old_emoji']} **{r['fed']}×** {r['food_emoji']}!"]
    if r["grew"]:
        lines.append(f"✨  **{r['pet_name']}** {r['new_emoji']} evolved!  (Tier {r['old_tier']} → {r['new_tier']})")
    lines.append(f"📊  {bar}")
    lines.append(f"🍖  Food remaining: **{r['food_left']}**")
    if r["at_max"]:
        lines.append("🏆  **Maximum level reached!**")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)

# ─────────────────────────────────────────────────────────────
# /whistle
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="whistle", description="Recover your pet on this host using your recovery code from /pet")
@app_commands.describe(
    code     = "The 8-character recovery code shown in /pet",
    pet_type = "Your pet's emoji exactly as shown in /pet (e.g. 🐱)",
)
async def cmd_whistle(interaction: discord.Interaction, code: str, pet_type: str):
    result = logic_whistle(
        db, interaction.user.id, interaction.user.display_name,
        code.strip(), pet_type.strip(),
    )
    if result["error"] == "not_found":
        await interaction.response.send_message(
            "🎵  No pet responded to your whistle.\n"
            "*(Check that the code and pet emoji both match what `/pet` shows. "
            "The code updates each time your pet evolves.)*",
            ephemeral=True,
        )
        return
    if result["error"]:
        await interaction.response.send_message(result["error"], ephemeral=True)
        return

    await interaction.response.send_message(
        f"🎵  **{result['pet_name']}** {result['emoji']} has been recovered!\n"
        f"Tier **{result['tier']}** restored · Food and partial XP were not carried over.\n"
        f"Use `/feed` to keep growing!",
        ephemeral=True,
    )

# ─────────────────────────────────────────────────────────────
# /rename
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="rename", description="Give your pet a new name")
@app_commands.describe(name=f"New name for your pet (max {MAX_PET_NAME_LEN} characters)")
async def cmd_rename(interaction: discord.Interaction, name: str):
    result = logic_rename(db, interaction.user.id, interaction.user.display_name, name)
    if result["error"]:
        await interaction.response.send_message(result["error"], ephemeral=True)
        return
    got_food  = _award_cmd_food(db, interaction.user.id, interaction.user.display_name)
    food_line = "  🍖 +1 food earned!" if got_food else ""
    await interaction.response.send_message(
        f"✅  Your pet is now named **{result['pet_name']}** {result['emoji']}!{food_line}",
        ephemeral=True,
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

        for sub in _pop_subscribers_for_court(db, r["court_number"]):
            try:
                sub_user = await bot.fetch_user(sub["user_id"])
                await sub_user.send(
                    f"🔔  **Court {r['court_number']}** is opening up soon!\n"
                    f"> Reservation **#{r['id']}** starts at "
                    f"**{r['start_time'].strftime('%I:%M %p')}** "
                    f"· ends {r['end_time'].strftime('%I:%M %p')}"
                )
            except Exception as sub_err:
                print(f"⚠️  Subscriber DM failed for user {sub['user_id']}: {sub_err}")

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
