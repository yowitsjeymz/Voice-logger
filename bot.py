import asyncio
import logging
import os
from datetime import datetime, timezone

import discord
from discord.ext import commands
from flask import Flask
from threading import Thread

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vc-logger")

TOKEN = os.environ.get("DISCORD_TOKEN")
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", "0"))
GUILD_ID = int(os.environ.get("GUILD_ID", "0"))

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is missing.")
if not LOG_CHANNEL_ID:
    raise RuntimeError("LOG_CHANNEL_ID environment variable is missing.")

# A tiny web server lets Render treat this as a Web Service.
app = Flask(__name__)

@app.get("/")
def home():
    return {"status": "online", "service": "Discord VC Logger"}, 200

@app.get("/health")
def health():
    return {"status": "healthy"}, 200

def run_web_server():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

Thread(target=run_web_server, daemon=True).start()

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Stores the time each member entered VC.
join_times: dict[tuple[int, int], datetime] = {}
voice_lock = asyncio.Lock()


def discord_timestamp(dt: datetime) -> str:
    return f"<t:{int(dt.timestamp())}:F>"


def format_duration(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, secs = divmod(remainder, 60)

    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


async def get_log_channel(guild: discord.Guild):
    channel = guild.get_channel(LOG_CHANNEL_ID)
    if channel is None:
        try:
            channel = await guild.fetch_channel(LOG_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.error("Could not access log channel ID %s.", LOG_CHANNEL_ID)
            return None

    if not isinstance(channel, discord.TextChannel):
        logger.error("LOG_CHANNEL_ID must point to a normal text channel.")
        return None

    return channel


async def send_log(guild: discord.Guild, embed: discord.Embed):
    channel = await get_log_channel(guild)
    if channel:
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            logger.error("Missing Send Messages or Embed Links permission.")
        except discord.HTTPException as exc:
            logger.error("Failed to send log: %s", exc)


async def connect_or_move(channel: discord.VoiceChannel):
    """Connect to the member's channel, or move if the bot is alone elsewhere."""
    async with voice_lock:
        voice_client = channel.guild.voice_client

        try:
            if voice_client is None:
                await channel.connect(self_deaf=True)
                logger.info("Connected to %s in %s.", channel.name, channel.guild.name)
                return

            if voice_client.channel == channel:
                return

            # Do not jump away while human members are still with the bot.
            humans_in_current = [
                member for member in voice_client.channel.members if not member.bot
            ]
            if not humans_in_current:
                await voice_client.move_to(channel)
                logger.info("Moved to %s in %s.", channel.name, channel.guild.name)

        except discord.ClientException as exc:
            logger.warning("Voice client error: %s", exc)
        except discord.Forbidden:
            logger.error("Bot lacks Connect permission in %s.", channel.name)
        except discord.HTTPException as exc:
            logger.error("Voice connection failed: %s", exc)


async def disconnect_if_empty(guild: discord.Guild):
    async with voice_lock:
        voice_client = guild.voice_client
        if voice_client is None or voice_client.channel is None:
            return

        humans = [member for member in voice_client.channel.members if not member.bot]
        if not humans:
            try:
                await voice_client.disconnect(force=True)
                logger.info("Disconnected because the VC became empty.")
            except discord.HTTPException as exc:
                logger.error("Failed to disconnect: %s", exc)


@bot.event
async def on_ready():
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id)

    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name="voice channel activity"
    )
    await bot.change_presence(activity=activity)


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    if member.bot:
        return

    if GUILD_ID and member.guild.id != GUILD_ID:
        return

    now = datetime.now(timezone.utc)
    key = (member.guild.id, member.id)

    # Joined a voice channel.
    if before.channel is None and after.channel is not None:
        join_times[key] = now

        embed = discord.Embed(
            title="Voice Channel Joined",
            description=f"{member.mention} joined {after.channel.mention}.",
            timestamp=now,
            color=discord.Color.green(),
        )
        embed.set_author(
            name=str(member),
            icon_url=member.display_avatar.url,
        )
        embed.add_field(name="Member", value=f"{member} (`{member.id}`)", inline=False)
        embed.add_field(name="Channel", value=after.channel.name, inline=True)
        embed.add_field(name="Joined", value=discord_timestamp(now), inline=True)
        embed.set_footer(text="VC Activity Log")

        await send_log(member.guild, embed)
        await connect_or_move(after.channel)
        return

    # Left a voice channel.
    if before.channel is not None and after.channel is None:
        joined_at = join_times.pop(key, None)
        duration_text = "Unknown"

        if joined_at:
            duration_text = format_duration(int((now - joined_at).total_seconds()))

        embed = discord.Embed(
            title="Voice Channel Left",
            description=f"{member.mention} left {before.channel.mention}.",
            timestamp=now,
            color=discord.Color.red(),
        )
        embed.set_author(
            name=str(member),
            icon_url=member.display_avatar.url,
        )
        embed.add_field(name="Member", value=f"{member} (`{member.id}`)", inline=False)
        embed.add_field(name="Channel", value=before.channel.name, inline=True)
        embed.add_field(name="Session", value=duration_text, inline=True)
        embed.add_field(name="Left", value=discord_timestamp(now), inline=False)
        embed.set_footer(text="VC Activity Log")

        await send_log(member.guild, embed)
        await disconnect_if_empty(member.guild)
        return

    # Moved between voice channels.
    if (
        before.channel is not None
        and after.channel is not None
        and before.channel.id != after.channel.id
    ):
        joined_at = join_times.get(key, now)
        previous_duration = format_duration(int((now - joined_at).total_seconds()))
        join_times[key] = now

        embed = discord.Embed(
            title="Voice Channel Moved",
            description=(
                f"{member.mention} moved from {before.channel.mention} "
                f"to {after.channel.mention}."
            ),
            timestamp=now,
            color=discord.Color.orange(),
        )
        embed.set_author(
            name=str(member),
            icon_url=member.display_avatar.url,
        )
        embed.add_field(name="Previous Session", value=previous_duration, inline=False)
        embed.set_footer(text="VC Activity Log")

        await send_log(member.guild, embed)

        voice_client = member.guild.voice_client
        if voice_client is None:
            await connect_or_move(after.channel)
        elif voice_client.channel == before.channel:
            humans_left = [
                user for user in before.channel.members if not user.bot
            ]
            if not humans_left:
                await connect_or_move(after.channel)


bot.run(TOKEN, log_handler=None)
