"""
VITAL BOT v3
Features:
1. AI chat when @mentioned
2. Moderation: ban, kick, timeout, purge, warn
3. AI auto-mod: underage + substance detection
4. Scammer/advertiser/supplier detection
5. Welcome embed in #welcome + DM on join
6. Logging to #vital-logs
7. /roleall command
"""

import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import datetime
import asyncio
import re
import aiohttp
from collections import defaultdict

# ═══════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════

BOT_TOKEN          = os.getenv("DISCORD_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")

WELCOME_CHANNEL    = "welcome"
LOG_CHANNEL        = "vital-logs"
RULES_CHANNEL      = "rules"
AUTO_ROLE_NAME     = "Member"

SPAM_LIMIT         = 5
SPAM_WINDOW        = 5
SPAM_TIMEOUT_MINS  = 10

WARNS_FILE         = "warns.json"
CUSTOM_CMDS_FILE   = "custom_commands.json"
REACTION_FILE      = "reaction_roles.json"

LOGO_URL = "https://media.discordapp.net/attachments/1473962139353092191/1482599719728582698/VITALPEPTIDES_dark.png?ex=69b78a16&is=69b63896&hm=01dd98b43e6c9dd7fb8ec115ee3119b4f4ea35d41544937946b01bb61107d2e5&=&format=webp&quality=lossless&width=2480&height=1180"
WEBSITE = "https://www.vitalpeptides.bio"

# ═══════════════════════════════════════════════════════════
#  BOT SETUP
# ═══════════════════════════════════════════════════════════

intents = discord.Intents.default()
intents.members         = True
intents.message_content = True
intents.guilds          = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

warns_db       = load_json(WARNS_FILE, {})
custom_cmds_db = load_json(CUSTOM_CMDS_FILE, {})
reaction_db    = load_json(REACTION_FILE, {})
bad_words_db   = {}
spam_tracker   = defaultdict(lambda: defaultdict(list))


def now_str():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def get_bad_words(guild_id):
    return bad_words_db.get(str(guild_id), [])

async def send_log(guild, embed):
    ch = discord.utils.get(guild.text_channels, name=LOG_CHANNEL)
    if ch:
        try:
            await ch.send(embed=embed)
        except discord.Forbidden:
            pass

def vital_embed(title, description, color=0x00b4d8):
    e = discord.Embed(title=title, description=description, color=color)
    e.set_footer(text=f"Vital Bot • {now_str()}")
    return e


# ═══════════════════════════════════════════════════════════
#  ANTHROPIC API CALL
# ═══════════════════════════════════════════════════════════

async def call_claude(system: str, user_message: str, max_tokens: int = 500) -> str:
    if not ANTHROPIC_API_KEY:
        return ""
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user_message}]
            }
            headers = {
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.json()
                return data["content"][0]["text"].strip()
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════
#  AI MODERATION
# ═══════════════════════════════════════════════════════════

MOD_SYSTEM = """You are a Discord moderation AI for Vital Peptides/Vital Chems, a research peptide community.

Flag messages that contain ANY of the following:

UNDERAGE:
- Anyone under 18 asking about or discussing peptides, HGH, steroids, SARMs, or any performance compounds
- Age mentions combined with compound discussion (e.g. "I'm 15", "my 14 year old", "im 16 should i")
- Anyone asking on behalf of a minor

GREY MARKET / VENDORS:
- References to Chinese suppliers, Chinese labs, Chinese peptides, or China-based vendors
- Mentions of known grey market vendor sites or domains
- "aliexpress", "taobao", "made in china", "chinese lab", "cn vendor", "overseas lab"
- Promoting, recommending, or linking to ANY external peptide/research chemical supplier
- "where to buy", "best source", "good source", "who sells", "any vendors", "supplier recommendations"
- Posting discount codes, referral links, or affiliate links for any supplier
- Mentioning specific vendor names that are not Vital Chems/Vital Peptides

SCAMMER ACCUSATIONS:
- Accusing anyone of being a scammer, fraudulent, fake, or sketchy supplier
- "got scammed", "scam site", "fake peptides", "bunk product" about other vendors

SPAM / ADVERTISING:
- Advertising or promoting any external product, service, or business
- Unsolicited promotions or sales pitches

SAFETY:
- Detailed injection or administration guides
- Sexual content involving minors
- Threats or calls for violence

Respond ONLY in this exact JSON format:
{"flagged": true, "reason": "brief reason", "category": "underage|advertising|scammer|sourcing|vendor|other"}
OR
{"flagged": false, "reason": "", "category": ""}

Be strict on vendors and sourcing. General peptide research discussion is allowed."""

CHAT_SYSTEM = """You are Vital Bot, the AI assistant for the Vital Peptides/Vital Chems Discord server — a research peptide community.

You help members with research questions about peptides, compounds, and related science. 
Keep answers informative but always remind users this is for RESEARCH purposes only.
Never give medical advice or dosing recommendations.
Be friendly, concise, and professional.
If asked about ordering or buying, direct them to www.vitalpeptides.bio
Always add a disclaimer that compounds are for research purposes only."""


async def ai_mod_check(content: str):
    text = await call_claude(MOD_SYSTEM, content, max_tokens=150)
    if not text:
        return False, "", ""
    try:
        text = re.sub(r"```json|```", "", text).strip()
        result = json.loads(text)
        return result.get("flagged", False), result.get("reason", ""), result.get("category", "")
    except Exception:
        return False, "", ""


async def handle_violation(message: discord.Message, reason: str, category: str = ""):
    try:
        await message.delete()
    except discord.Forbidden:
        pass

    rules_ch = discord.utils.get(message.guild.text_channels, name=RULES_CHANNEL)
    rules_mention = rules_ch.mention if rules_ch else "#rules"

    # Pick the right DM message based on category
    if category == "underage":
        detail = (
            "Your message suggested you or someone else may be under 18. "
            "Our community is strictly for adult researchers (18+). "
            "We cannot provide any information to minors regarding these compounds."
        )
    elif category == "advertising":
        detail = (
            "Your message appeared to advertise or promote an external supplier, product, or service. "
            "Advertising and promotion of other businesses is not allowed in this server."
        )
    elif category == "scammer":
        detail = (
            "Your message contained accusations or claims about scammers/fraudulent suppliers. "
            "Please contact a moderator directly if you have concerns about a vendor."
        )
    elif category == "sourcing":
        detail = (
            "Your message contained sourcing requests or links to external suppliers. "
            "Sourcing discussions are not permitted in this server."
        )
    elif category == "vendor":
        detail = (
            "Your message referenced or promoted an external peptide vendor, grey market supplier, or Chinese lab. "
            "We only support Vital Chems/Vital Peptides in this server. "
            "External vendor discussion, sourcing requests, and supplier recommendations are not permitted."
        )
    else:
        detail = f"Your message violated our server rules. Reason: {reason}"

    dm_embed = discord.Embed(
        title="Message Removed - Vital Chems",
        description=(
            f"Hey {message.author.mention},\n\n"
            f"Your message in **{message.guild.name}** was automatically removed.\n\n"
            f"**{detail}**\n\n"
            f"Please review our rules in {rules_mention} before posting again.\n"
            f"If you believe this was a mistake, contact a moderator."
        ),
        color=0xe74c3c
    )
    dm_embed.set_thumbnail(url=LOGO_URL)
    dm_embed.set_footer(text="Vital Bot • Auto-Moderation")

    try:
        await message.author.send(embed=dm_embed)
    except discord.Forbidden:
        pass

    log_embed = vital_embed(
        f"AI Auto-Mod: {category.title() if category else 'Violation'}",
        f"{message.author.mention} message removed in {message.channel.mention}",
        color=0xe74c3c
    )
    log_embed.add_field(name="Reason", value=reason, inline=False)
    log_embed.add_field(name="Message", value=f"||{message.content[:300]}||", inline=False)
    await send_log(message.guild, log_embed)


# ═══════════════════════════════════════════════════════════
#  EVENTS
# ═══════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    # Sync to each guild individually for instant registration
    total = 0
    for guild in bot.guilds:
        try:
            cmds = await bot.tree.sync(guild=guild)
            total += len(cmds)
        except Exception as e:
            print(f"Guild sync error {guild.id}: {e}")
    # Also global sync
    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"Global sync error: {e}")
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="Vital Chems Server")
    )
    print(f"Vital Bot v3 online as {bot.user} | AI: {'ON' if ANTHROPIC_API_KEY else 'OFF'} | Commands synced: {total}")


@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild

    # Auto-role
    role = discord.utils.get(guild.roles, name=AUTO_ROLE_NAME)
    if role:
        try:
            await member.add_roles(role, reason="Auto-role on join")
        except discord.Forbidden:
            pass

    # Welcome embed
    embed = discord.Embed(
        title="Welcome to Vital Peptides!",
        description=(
            f"Hey {member.mention}, welcome to the community!\n\n"
            f"We are a research-focused peptide community providing high-purity compounds "
            f"for scientific research purposes.\n\n"
            f"Read our rules, explore the channels, and feel free to ask questions!\n\n"
            f"Website: {WEBSITE}"
        ),
        color=0x00b4d8
    )
    embed.set_thumbnail(url=LOGO_URL)
    embed.set_footer(text=f"Member #{guild.member_count} - Vital Bot")

    # DM the member
    try:
        await member.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass

    # Post in welcome channel
    ch = discord.utils.get(guild.text_channels, name=WELCOME_CHANNEL)
    if ch:
        await ch.send(embed=embed)

    # Log
    log_embed = vital_embed("Member Joined", f"{member.mention} joined the server.", color=0x2ecc71)
    log_embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d"), inline=True)
    log_embed.add_field(name="Member Count", value=str(guild.member_count), inline=True)
    log_embed.set_thumbnail(url=member.display_avatar.url)
    await send_log(guild, log_embed)


@bot.event
async def on_member_remove(member: discord.Member):
    log_embed = vital_embed("Member Left", f"**{member.name}** left the server.", color=0xe74c3c)
    log_embed.add_field(name="Joined", value=member.joined_at.strftime("%Y-%m-%d") if member.joined_at else "Unknown", inline=True)
    await send_log(member.guild, log_embed)


@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    log_embed = vital_embed("Message Deleted", f"By {message.author.mention} in {message.channel.mention}", color=0xe67e22)
    log_embed.add_field(name="Content", value=message.content[:1000] or "*empty*", inline=False)
    await send_log(message.guild, log_embed)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot or not before.guild or before.content == after.content:
        return
    log_embed = vital_embed("Message Edited", f"By {before.author.mention} in {before.channel.mention}", color=0xf1c40f)
    log_embed.add_field(name="Before", value=before.content[:500] or "*empty*", inline=False)
    log_embed.add_field(name="After",  value=after.content[:500]  or "*empty*", inline=False)
    await send_log(before.guild, log_embed)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    # ── @mention AI chat (runs for everyone including admins) ────
    if bot.user in message.mentions and len(message.content.strip()) > 2:
        question = message.content.replace(f"<@{bot.user.id}>", "").strip()
        if question:
            async with message.channel.typing():
                response = await call_claude(CHAT_SYSTEM, question, max_tokens=500)
                if response:
                    if len(response) > 1900:
                        response = response[:1900] + "..."
                    await message.reply(response)
                else:
                    await message.reply("Sorry, I couldn't process that right now. Try again shortly!")
            return

    guild_id = str(message.guild.id)
    user_id  = str(message.author.id)
    content  = message.content.lower()

    # Skip moderation for admins and server owner
    member = message.author
    if (
        message.guild.owner_id == member.id or
        member.guild_permissions.administrator or
        member.guild_permissions.manage_guild
    ):
        await bot.process_commands(message)
        return

    # ── Manual bad word filter ───────────────────────────
    for word in get_bad_words(message.guild.id):
        if re.search(r'\b' + re.escape(word.lower()) + r'\b', content):
            await handle_violation(message, f"Prohibited word", "other")
            return

    # ── AI moderation ────────────────────────────────────
    if len(message.content.strip()) > 5:
        flagged, reason, category = await ai_mod_check(message.content)
        if flagged:
            await handle_violation(message, reason, category)
            return

    # ── Spam filter ──────────────────────────────────────
    now = asyncio.get_event_loop().time()
    tracker = spam_tracker[guild_id][user_id]
    tracker.append(now)
    spam_tracker[guild_id][user_id] = [t for t in tracker if now - t < SPAM_WINDOW]

    if len(spam_tracker[guild_id][user_id]) >= SPAM_LIMIT:
        spam_tracker[guild_id][user_id] = []
        try:
            until = discord.utils.utcnow() + datetime.timedelta(minutes=SPAM_TIMEOUT_MINS)
            await message.author.timeout(until, reason="Spam")
            await message.channel.send(
                embed=vital_embed("Spam Detected", f"{message.author.mention} timed out for {SPAM_TIMEOUT_MINS} min.", color=0xe74c3c)
            )
        except discord.Forbidden:
            pass
        log_embed = vital_embed("Spam Timeout", f"{message.author.mention} timed out.", color=0xe74c3c)
        await send_log(message.guild, log_embed)
        return

    # ── Custom commands ──────────────────────────────────
    trigger = message.content.strip().lower()
    if trigger in custom_cmds_db.get(guild_id, {}):
        await message.channel.send(custom_cmds_db[guild_id][trigger])
        return

    await bot.process_commands(message)


# ═══════════════════════════════════════════════════════════
#  REACTION ROLES
# ═══════════════════════════════════════════════════════════

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return
    msg_id = str(payload.message_id)
    if msg_id not in reaction_db:
        return
    emoji_str = str(payload.emoji)
    if emoji_str not in reaction_db[msg_id]:
        return
    guild  = bot.get_guild(payload.guild_id)
    role   = guild.get_role(int(reaction_db[msg_id][emoji_str]))
    member = guild.get_member(payload.user_id)
    if role and member:
        try:
            await member.add_roles(role, reason="Reaction role")
        except discord.Forbidden:
            pass


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return
    msg_id = str(payload.message_id)
    if msg_id not in reaction_db:
        return
    emoji_str = str(payload.emoji)
    if emoji_str not in reaction_db[msg_id]:
        return
    guild  = bot.get_guild(payload.guild_id)
    role   = guild.get_role(int(reaction_db[msg_id][emoji_str]))
    member = guild.get_member(payload.user_id)
    if role and member:
        try:
            await member.remove_roles(role, reason="Reaction role removed")
        except discord.Forbidden:
            pass


# ═══════════════════════════════════════════════════════════
#  SLASH COMMANDS — MODERATION
# ═══════════════════════════════════════════════════════════

@bot.tree.command(name="ban", description="Ban a member.")
@app_commands.describe(member="Member to ban", reason="Reason")
@app_commands.default_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    try:
        await member.ban(reason=f"{reason} | By {interaction.user}")
        await interaction.response.send_message(embed=vital_embed("Banned", f"{member.mention} banned.\n**Reason:** {reason}", color=0xe74c3c))
        log_embed = vital_embed("Member Banned", f"{member.mention} banned by {interaction.user.mention}", color=0xe74c3c)
        log_embed.add_field(name="Reason", value=reason)
        await send_log(interaction.guild, log_embed)
    except discord.Forbidden:
        await interaction.response.send_message("Missing permissions.", ephemeral=True)


@bot.tree.command(name="kick", description="Kick a member.")
@app_commands.describe(member="Member to kick", reason="Reason")
@app_commands.default_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    try:
        await member.kick(reason=f"{reason} | By {interaction.user}")
        await interaction.response.send_message(embed=vital_embed("Kicked", f"{member.mention} kicked.\n**Reason:** {reason}", color=0xe67e22))
        log_embed = vital_embed("Member Kicked", f"{member.mention} kicked by {interaction.user.mention}", color=0xe67e22)
        log_embed.add_field(name="Reason", value=reason)
        await send_log(interaction.guild, log_embed)
    except discord.Forbidden:
        await interaction.response.send_message("Missing permissions.", ephemeral=True)


@bot.tree.command(name="timeout", description="Timeout a member.")
@app_commands.describe(member="Member", minutes="Duration in minutes", reason="Reason")
@app_commands.default_permissions(moderate_members=True)
async def timeout_cmd(interaction: discord.Interaction, member: discord.Member, minutes: int = 10, reason: str = "No reason provided"):
    try:
        until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
        await member.timeout(until, reason=f"{reason} | By {interaction.user}")
        await interaction.response.send_message(embed=vital_embed("Timed Out", f"{member.mention} timed out for **{minutes} min**.", color=0xf39c12))
        log_embed = vital_embed("Timeout", f"{member.mention} timed out by {interaction.user.mention}", color=0xf39c12)
        log_embed.add_field(name="Duration", value=f"{minutes} min")
        log_embed.add_field(name="Reason", value=reason)
        await send_log(interaction.guild, log_embed)
    except discord.Forbidden:
        await interaction.response.send_message("Missing permissions.", ephemeral=True)


@bot.tree.command(name="untimeout", description="Remove a timeout.")
@app_commands.describe(member="Member")
@app_commands.default_permissions(moderate_members=True)
async def untimeout_cmd(interaction: discord.Interaction, member: discord.Member):
    try:
        await member.timeout(None)
        await interaction.response.send_message(embed=vital_embed("Timeout Removed", f"{member.mention}'s timeout removed.", color=0x2ecc71))
        await send_log(interaction.guild, vital_embed("Timeout Removed", f"{member.mention} by {interaction.user.mention}", color=0x2ecc71))
    except discord.Forbidden:
        await interaction.response.send_message("Missing permissions.", ephemeral=True)


@bot.tree.command(name="purge", description="Delete messages from this channel.")
@app_commands.describe(amount="Number of messages (1-100)")
@app_commands.default_permissions(manage_messages=True)
async def purge(interaction: discord.Interaction, amount: int):
    if not 1 <= amount <= 100:
        await interaction.response.send_message("Amount must be 1-100.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(embed=vital_embed("Purged", f"Deleted **{len(deleted)}** messages.", color=0x2ecc71), ephemeral=True)
    await send_log(interaction.guild, vital_embed("Purge", f"{interaction.user.mention} purged {len(deleted)} messages in {interaction.channel.mention}", color=0xe67e22))


@bot.tree.command(name="warn", description="Warn a member.")
@app_commands.describe(member="Member", reason="Reason")
@app_commands.default_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    gid, uid = str(interaction.guild.id), str(member.id)
    warns_db.setdefault(gid, {}).setdefault(uid, []).append({"reason": reason, "mod": str(interaction.user), "timestamp": now_str()})
    save_json(WARNS_FILE, warns_db)
    count = len(warns_db[gid][uid])
    await interaction.response.send_message(embed=vital_embed("Warning", f"{member.mention} warned. Total: **{count}**\n**Reason:** {reason}", color=0xf1c40f))
    log_embed = vital_embed("Warning", f"{member.mention} warned by {interaction.user.mention}", color=0xf1c40f)
    log_embed.add_field(name="Reason", value=reason)
    log_embed.add_field(name="Total", value=str(count))
    await send_log(interaction.guild, log_embed)


@bot.tree.command(name="warnings", description="View warnings for a member.")
@app_commands.describe(member="Member")
@app_commands.default_permissions(moderate_members=True)
async def warnings(interaction: discord.Interaction, member: discord.Member):
    user_warns = warns_db.get(str(interaction.guild.id), {}).get(str(member.id), [])
    if not user_warns:
        await interaction.response.send_message(embed=vital_embed("Warnings", f"{member.mention} has no warnings.", color=0x2ecc71), ephemeral=True)
        return
    embed = vital_embed("Warnings", f"{member.mention} has **{len(user_warns)}** warning(s):")
    for i, w in enumerate(user_warns[-10:], 1):
        embed.add_field(name=f"#{i} - {w['timestamp']}", value=f"**Reason:** {w['reason']}\n**By:** {w['mod']}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="clearwarnings", description="Clear warnings for a member.")
@app_commands.describe(member="Member")
@app_commands.default_permissions(administrator=True)
async def clearwarnings(interaction: discord.Interaction, member: discord.Member):
    gid, uid = str(interaction.guild.id), str(member.id)
    if gid in warns_db and uid in warns_db[gid]:
        del warns_db[gid][uid]
        save_json(WARNS_FILE, warns_db)
    await interaction.response.send_message(embed=vital_embed("Cleared", f"Warnings for {member.mention} cleared.", color=0x2ecc71))


# ═══════════════════════════════════════════════════════════
#  SLASH COMMANDS — ROLE MANAGEMENT
# ═══════════════════════════════════════════════════════════

@bot.tree.command(name="roleall", description="Give a role to every member in the server.")
@app_commands.describe(role="Role to assign to everyone")
@app_commands.default_permissions(administrator=True)
async def roleall(interaction: discord.Interaction, role: discord.Role):
    await interaction.response.defer(ephemeral=True)
    count = 0
    failed = 0
    for member in interaction.guild.members:
        if member.bot:
            continue
        if role not in member.roles:
            try:
                await member.add_roles(role, reason=f"/roleall by {interaction.user}")
                count += 1
                await asyncio.sleep(0.5)  # avoid rate limits
            except discord.Forbidden:
                failed += 1
    await interaction.followup.send(
        embed=vital_embed(
            "Role Assigned",
            f"Gave {role.mention} to **{count}** members.\nFailed: **{failed}**",
            color=0x2ecc71
        ),
        ephemeral=True
    )
    log_embed = vital_embed("Role All", f"{interaction.user.mention} gave {role.mention} to {count} members.", color=0x2ecc71)
    await send_log(interaction.guild, log_embed)


@bot.tree.command(name="reactionrole", description="Post a reaction role message.")
@app_commands.describe(channel="Channel", role="Role to assign", emoji="Emoji to react with", title="Embed title", description="Embed description")
@app_commands.default_permissions(administrator=True)
async def reactionrole(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role, emoji: str, title: str = "React to get a role!", description: str = "React below to receive your role."):
    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(title=title, description=f"{description}\n\n{emoji} - {role.mention}", color=0x00b4d8)
    embed.set_thumbnail(url=LOGO_URL)
    embed.set_footer(text="Vital Bot - React to get your role")
    try:
        msg = await channel.send(embed=embed)
        await msg.add_reaction(emoji)
        reaction_db[str(msg.id)] = {emoji: str(role.id)}
        save_json(REACTION_FILE, reaction_db)
        await interaction.followup.send(embed=vital_embed("Reaction Role Created", f"Posted in {channel.mention}\n{emoji} - {role.mention}", color=0x2ecc71), ephemeral=True)
    except discord.HTTPException:
        await interaction.followup.send("Invalid emoji or missing permissions.", ephemeral=True)


# ═══════════════════════════════════════════════════════════
#  SLASH COMMANDS — EMBED
# ═══════════════════════════════════════════════════════════

@bot.tree.command(name="embed", description="Post a custom embed in any channel.")
@app_commands.describe(
    channel="Channel to post in",
    title="Embed title",
    description="Embed body text. Use \\n for new lines, \\t for tab indent.",
    color="Hex color code e.g. e74c3c (optional, default cyan)",
    footer="Custom footer text (optional)",
    image_url="Image URL to show at the bottom (optional)",
    thumbnail_url="Thumbnail URL top right (optional)"
)
@app_commands.default_permissions(manage_messages=True)
async def embed_cmd(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    title: str,
    description: str,
    color: str = "00b4d8",
    footer: str = None,
    image_url: str = None,
    thumbnail_url: str = None
):
    try:
        hex_color = int(color.replace("#", ""), 16)
    except ValueError:
        hex_color = 0x00b4d8

    # Convert \n to real newlines and \t to tab/indent
    description = description.replace("\\n", "\n").replace("\n", "\n").replace("\\t", "\u200b  ").replace("\t", "\u200b  ")

    embed = discord.Embed(title=title, description=description, color=hex_color)

    if footer:
        embed.set_footer(text=footer)
    # No footer if not specified — clean look
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    if image_url:
        embed.set_image(url=image_url)

    try:
        await channel.send(embed=embed)
        await interaction.response.send_message(
            embed=vital_embed("Embed Posted", f"Your embed was posted in {channel.mention}.", color=0x2ecc71),
            ephemeral=True
        )
        log_embed = vital_embed("Embed Posted", f"{interaction.user.mention} posted an embed in {channel.mention}", color=0x2ecc71)
        log_embed.add_field(name="Title", value=title, inline=True)
        await send_log(interaction.guild, log_embed)
    except discord.Forbidden:
        await interaction.response.send_message("I don't have permission to post in that channel.", ephemeral=True)


# ═══════════════════════════════════════════════════════════
#  SLASH COMMANDS — AUTO-MOD MANAGEMENT
# ═══════════════════════════════════════════════════════════

@bot.tree.command(name="addbadword", description="Add a word to the filter.")
@app_commands.describe(word="Word to block")
@app_commands.default_permissions(administrator=True)
async def addbadword(interaction: discord.Interaction, word: str):
    gid = str(interaction.guild.id)
    bad_words_db.setdefault(gid, [])
    word = word.lower().strip()
    if word not in bad_words_db[gid]:
        bad_words_db[gid].append(word)
    await interaction.response.send_message(embed=vital_embed("Word Added", f"||{word}|| added to filter.", color=0x2ecc71), ephemeral=True)


@bot.tree.command(name="removebadword", description="Remove a word from the filter.")
@app_commands.describe(word="Word to remove")
@app_commands.default_permissions(administrator=True)
async def removebadword(interaction: discord.Interaction, word: str):
    gid = str(interaction.guild.id)
    word = word.lower().strip()
    if gid in bad_words_db and word in bad_words_db[gid]:
        bad_words_db[gid].remove(word)
    await interaction.response.send_message(embed=vital_embed("Word Removed", f"{word} removed.", color=0x2ecc71), ephemeral=True)


@bot.tree.command(name="listbadwords", description="List filtered words.")
@app_commands.default_permissions(administrator=True)
async def listbadwords(interaction: discord.Interaction):
    words = get_bad_words(interaction.guild.id)
    text = " ".join([f"||{w}||" for w in words]) if words else "*None*"
    await interaction.response.send_message(embed=vital_embed("Filtered Words", text, color=0x00b4d8), ephemeral=True)


# ═══════════════════════════════════════════════════════════
#  SLASH COMMANDS — CUSTOM COMMANDS
# ═══════════════════════════════════════════════════════════

@bot.tree.command(name="addcmd", description="Add a custom command.")
@app_commands.describe(trigger="Trigger phrase", response="Response")
@app_commands.default_permissions(administrator=True)
async def addcmd(interaction: discord.Interaction, trigger: str, response: str):
    gid = str(interaction.guild.id)
    custom_cmds_db.setdefault(gid, {})[trigger.lower().strip()] = response
    save_json(CUSTOM_CMDS_FILE, custom_cmds_db)
    await interaction.response.send_message(embed=vital_embed("Command Added", f"`{trigger}` - {response}", color=0x2ecc71), ephemeral=True)


@bot.tree.command(name="removecmd", description="Remove a custom command.")
@app_commands.describe(trigger="Trigger to remove")
@app_commands.default_permissions(administrator=True)
async def removecmd(interaction: discord.Interaction, trigger: str):
    gid = str(interaction.guild.id)
    trigger = trigger.lower().strip()
    if gid in custom_cmds_db and trigger in custom_cmds_db[gid]:
        del custom_cmds_db[gid][trigger]
        save_json(CUSTOM_CMDS_FILE, custom_cmds_db)
    await interaction.response.send_message(embed=vital_embed("Command Removed", f"`{trigger}` removed.", color=0x2ecc71), ephemeral=True)


# ═══════════════════════════════════════════════════════════
#  SLASH COMMANDS — UTILITY
# ═══════════════════════════════════════════════════════════

@bot.tree.command(name="userinfo", description="Get info about a member.")
@app_commands.describe(member="Member to look up")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    roles  = [r.mention for r in member.roles if r.name != "@everyone"]
    embed  = vital_embed("User Info", f"Info for {member.mention}")
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Username", value=str(member), inline=True)
    embed.add_field(name="ID",       value=str(member.id), inline=True)
    embed.add_field(name="Joined",   value=member.joined_at.strftime("%Y-%m-%d") if member.joined_at else "Unknown", inline=True)
    embed.add_field(name="Created",  value=member.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Roles",    value=", ".join(roles) if roles else "None", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="serverinfo", description="Get info about this server.")
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    embed = vital_embed("Server Info", g.description or "No description.")
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Owner",    value=str(g.owner),         inline=True)
    embed.add_field(name="Members",  value=str(g.member_count),  inline=True)
    embed.add_field(name="Channels", value=str(len(g.channels)), inline=True)
    embed.add_field(name="Created",  value=g.created_at.strftime("%Y-%m-%d"), inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="help", description="Show all Vital Bot commands.")
async def help_cmd(interaction: discord.Interaction):
    embed = vital_embed("Vital Bot v3 Commands", "Your AI-powered server admin bot.")
    embed.set_thumbnail(url=LOGO_URL)
    embed.add_field(name="AI Chat", value="@mention the bot to ask any research question", inline=False)
    embed.add_field(name="Moderation", value="`/ban` `/kick` `/timeout` `/untimeout` `/purge`\n`/warn` `/warnings` `/clearwarnings`", inline=False)
    embed.add_field(name="Roles", value="`/roleall` — give a role to everyone\n`/reactionrole` — reaction role setup", inline=False)
    embed.add_field(name="Embeds", value="`/embed` — post a custom embed in any channel\nUse `\\n` for new lines, `footer:` for custom footer", inline=False)
    embed.add_field(name="AI Auto-Mod", value="Auto-detects & removes:\n- Underage + substance talk\n- Advertisers & competitor promo\n- Scammer accusations\n- Sourcing requests\nDMs user with reason + rules link", inline=False)
    embed.add_field(name="Word Filter", value="`/addbadword` `/removebadword` `/listbadwords`", inline=False)
    embed.add_field(name="Custom Commands", value="`/addcmd` `/removecmd`", inline=False)
    embed.add_field(name="Utility", value="`/userinfo` `/serverinfo` `/help`", inline=False)
    embed.add_field(name="Logging", value=f"All actions logged to `#{LOG_CHANNEL}`", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ═══════════════════════════════════════════════════════════
#  RUN
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("Set DISCORD_BOT_TOKEN environment variable!")
    else:
        bot.run(BOT_TOKEN)
