import asyncio
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import Optional

import discord
from discord.ext import commands
from flask import Flask

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("auto-vc-recorder")

TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_ID = int(os.environ.get("GUILD_ID", "0"))
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", "0"))
RECORDING_CHANNEL_ID = int(os.environ.get("RECORDING_CHANNEL_ID", "0"))
AUTO_RECORD = os.environ.get("AUTO_RECORD", "true").lower() in {"1", "true", "yes", "on"}
UPLOAD_LIMIT_MB = int(os.environ.get("UPLOAD_LIMIT_MB", "24"))

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing.")
if not LOG_CHANNEL_ID:
    raise RuntimeError("LOG_CHANNEL_ID is missing.")
if not RECORDING_CHANNEL_ID:
    raise RuntimeError("RECORDING_CHANNEL_ID is missing.")

app = Flask(__name__)

@app.get("/")
def home():
    return {"status": "online", "service": "Discord Auto VC Recorder"}, 200

@app.get("/health")
def health():
    return {"status": "healthy"}, 200

def run_web_server():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

Thread(target=run_web_server, daemon=True).start()

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True

bot = discord.Bot(intents=intents)

sessions: dict[int, dict] = {}
guild_locks: dict[int, asyncio.Lock] = {}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def discord_time(value: datetime) -> str:
    return f"<t:{int(value.timestamp())}:F>"


def duration_text(seconds: int) -> str:
    seconds = max(0, seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def get_lock(guild_id: int) -> asyncio.Lock:
    if guild_id not in guild_locks:
        guild_locks[guild_id] = asyncio.Lock()
    return guild_locks[guild_id]


def is_allowed_guild(guild: discord.Guild) -> bool:
    return not GUILD_ID or guild.id == GUILD_ID


async def get_text_channel(
    guild: discord.Guild,
    channel_id: int,
) -> Optional[discord.TextChannel]:
    channel = guild.get_channel(channel_id)

    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.error("Cannot access text channel %s.", channel_id)
            return None

    if not isinstance(channel, discord.TextChannel):
        logger.error("Channel ID %s must point to a text channel.", channel_id)
        return None

    return channel


async def send_log(
    guild: discord.Guild,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
):
    channel = await get_text_channel(guild, LOG_CHANNEL_ID)
    if not channel:
        return

    try:
        await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                everyone=True,
                users=True,
                roles=False,
            ),
        )
    except discord.HTTPException as exc:
        logger.error("Failed to send log: %s", exc)


async def send_recording_files(
    guild: discord.Guild,
    *,
    embed: discord.Embed,
    files: list[discord.File],
):
    channel = await get_text_channel(guild, RECORDING_CHANNEL_ID)
    if not channel:
        return

    try:
        if not files:
            await channel.send(embed=embed)
            return

        await channel.send(embed=embed, files=files[:10])

        for index in range(10, len(files), 10):
            await channel.send(
                content="Additional participant recording tracks:",
                files=files[index:index + 10],
            )
    except discord.HTTPException as exc:
        logger.error("Failed to upload recording files: %s", exc)


async def finish_recording(
    sink: discord.sinks.WaveSink,
    guild_id: int,
):
    session = sessions.pop(guild_id, None)
    guild = bot.get_guild(guild_id)

    if not session or not guild:
        return

    finished_at = utc_now()
    duration = int((finished_at - session["started_at"]).total_seconds())

    temporary_paths: list[Path] = []
    upload_files: list[discord.File] = []
    skipped: list[str] = []

    try:
        for user_id, audio in sink.audio_data.items():
            member = guild.get_member(int(user_id))
            display_name = member.display_name if member else f"user-{user_id}"
            safe_name = "".join(
                character if character.isalnum() or character in "-_"
                else "_"
                for character in display_name
            )[:50]

            audio.file.seek(0)

            with tempfile.NamedTemporaryFile(
                prefix=f"{safe_name}-",
                suffix=".wav",
                delete=False,
            ) as temporary:
                temporary.write(audio.file.read())
                temporary_path = Path(temporary.name)

            temporary_paths.append(temporary_path)
            size_mb = temporary_path.stat().st_size / (1024 * 1024)

            if size_mb <= UPLOAD_LIMIT_MB:
                upload_files.append(
                    discord.File(
                        str(temporary_path),
                        filename=f"{safe_name}-{user_id}.wav",
                    )
                )
            else:
                skipped.append(
                    f"<@{user_id}> — {size_mb:.1f} MB, above configured limit"
                )

        recording_embed = discord.Embed(
            title="🎙️ Voice Recording Files",
            description=(
                "Separate WAV tracks from the completed voice session are attached."
            ),
            color=discord.Color.blurple(),
            timestamp=finished_at,
        )
        recording_embed.add_field(
            name="Voice channel",
            value=session["voice_channel_mention"],
            inline=True,
        )
        recording_embed.add_field(
            name="Duration",
            value=duration_text(duration),
            inline=True,
        )
        recording_embed.add_field(
            name="Tracks",
            value=str(len(upload_files)),
            inline=True,
        )

        if skipped:
            recording_embed.add_field(
                name="Tracks not uploaded",
                value="\n".join(skipped)[:1024],
                inline=False,
            )

        await send_recording_files(
            guild,
            embed=recording_embed,
            files=upload_files,
        )

        end_log = discord.Embed(
            title="⏹️ Voice Recording Ended",
            description=(
                f"The completed audio files were sent only to <#{RECORDING_CHANNEL_ID}>."
            ),
            color=discord.Color.red(),
            timestamp=finished_at,
        )
        end_log.add_field(
            name="Voice channel",
            value=session["voice_channel_mention"],
            inline=True,
        )
        end_log.add_field(
            name="Started",
            value=discord_time(session["started_at"]),
            inline=True,
        )
        end_log.add_field(
            name="Duration",
            value=duration_text(duration),
            inline=True,
        )
        end_log.add_field(
            name="Started by",
            value=f"<@{session['started_by_id']}>",
            inline=True,
        )
        end_log.add_field(
            name="Mode",
            value=session["mode"],
            inline=True,
        )
        end_log.add_field(
            name="Files uploaded",
            value=str(len(upload_files)),
            inline=True,
        )

        await send_log(guild, embed=end_log)

    finally:
        for path in temporary_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not delete temporary file %s.", path)

        voice_client = guild.voice_client
        if voice_client and voice_client.is_connected():
            try:
                await voice_client.disconnect(force=True)
            except discord.HTTPException:
                pass


async def begin_recording(
    guild: discord.Guild,
    voice_channel: discord.VoiceChannel,
    started_by: discord.Member,
    mode: str,
) -> tuple[bool, str]:
    if not is_allowed_guild(guild):
        return False, "This bot is not configured for this server."

    async with get_lock(guild.id):
        if guild.id in sessions:
            return False, "A recording is already active in this server."

        voice_client = guild.voice_client

        try:
            if voice_client and voice_client.is_connected():
                if getattr(voice_client, "recording", False):
                    return False, "A recording is already active."
                await voice_client.move_to(voice_channel)
            else:
                voice_client = await voice_channel.connect()
        except discord.Forbidden:
            return False, "The bot cannot connect to that voice channel."
        except (discord.ClientException, discord.HTTPException) as exc:
            logger.exception("Voice connection failed")
            return False, f"Voice connection failed: {exc}"

        started_at = utc_now()
        sessions[guild.id] = {
            "started_at": started_at,
            "started_by_id": started_by.id,
            "voice_channel_id": voice_channel.id,
            "voice_channel_mention": voice_channel.mention,
            "mode": mode,
        }

        notice = discord.Embed(
            title="🔴 VOICE RECORDING IS ACTIVE",
            description=(
                f"{bot.user.mention} joined {voice_channel.mention} and started recording.\n\n"
                "**Everyone in the voice channel must be informed. Anyone who does "
                "not agree to be recorded should leave immediately.**\n\n"
                "A moderator can use `/stop` to end the recording."
            ),
            color=discord.Color.red(),
            timestamp=started_at,
        )
        notice.add_field(name="Voice channel", value=voice_channel.mention, inline=True)
        notice.add_field(name="Triggered by", value=started_by.mention, inline=True)
        notice.add_field(name="Mode", value=mode, inline=True)
        notice.set_footer(text="Voice recording notice")

        await send_log(
            guild,
            content=f"@here Recording notice for {voice_channel.mention}",
            embed=notice,
        )

        try:
            voice_client.start_recording(
                discord.sinks.WaveSink(),
                finish_recording,
                guild.id,
                sync_start=True,
            )
        except Exception as exc:
            sessions.pop(guild.id, None)
            logger.exception("Could not start recording")

            try:
                await voice_client.disconnect(force=True)
            except discord.HTTPException:
                pass

            return False, f"Could not start recording: {exc}"

        return True, f"Recording started in {voice_channel.mention}."


@bot.event
async def on_ready():
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id)
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="voice recording activity",
        )
    )


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    if member.bot or not is_allowed_guild(member.guild):
        return

    if (
        AUTO_RECORD
        and before.channel is None
        and isinstance(after.channel, discord.VoiceChannel)
        and member.guild.id not in sessions
    ):
        success, message = await begin_recording(
            member.guild,
            after.channel,
            member,
            "Automatic",
        )
        if not success:
            logger.warning("Auto-record did not start: %s", message)
        return

    session = sessions.get(member.guild.id)
    if not session:
        return

    voice_client = member.guild.voice_client
    if not voice_client or not voice_client.channel:
        return

    humans = [
        voice_member
        for voice_member in voice_client.channel.members
        if not voice_member.bot
    ]

    if not humans and getattr(voice_client, "recording", False):
        voice_client.stop_recording()


@bot.slash_command(
    name="record",
    description="Manually join your VC and start an announced recording.",
)
@commands.has_permissions(manage_channels=True)
async def record(ctx: discord.ApplicationContext):
    if not ctx.guild or not isinstance(ctx.author, discord.Member):
        await ctx.respond("Use this command inside a server.", ephemeral=True)
        return

    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.respond("Join a normal voice channel first.", ephemeral=True)
        return

    if not isinstance(ctx.author.voice.channel, discord.VoiceChannel):
        await ctx.respond("Stage channels are not supported.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    success, message = await begin_recording(
        ctx.guild,
        ctx.author.voice.channel,
        ctx.author,
        "Manual command",
    )
    await ctx.followup.send(message, ephemeral=True)


@bot.slash_command(
    name="stop",
    description="Stop the active recording and upload its tracks.",
)
@commands.has_permissions(manage_channels=True)
async def stop(ctx: discord.ApplicationContext):
    if not ctx.guild:
        await ctx.respond("Use this command inside a server.", ephemeral=True)
        return

    voice_client = ctx.guild.voice_client

    if (
        ctx.guild.id not in sessions
        or not voice_client
        or not getattr(voice_client, "recording", False)
    ):
        await ctx.respond("There is no active recording.", ephemeral=True)
        return

    await ctx.respond(
        f"Stopping. Audio files will be sent only to <#{RECORDING_CHANNEL_ID}>.",
        ephemeral=True,
    )
    voice_client.stop_recording()


@bot.slash_command(
    name="recording_status",
    description="Display the current voice recording status.",
)
async def recording_status(ctx: discord.ApplicationContext):
    if not ctx.guild:
        await ctx.respond("Use this command inside a server.", ephemeral=True)
        return

    session = sessions.get(ctx.guild.id)

    if not session:
        await ctx.respond(
            f"No recording is active. Automatic mode is **{'ON' if AUTO_RECORD else 'OFF'}**.",
            ephemeral=True,
        )
        return

    await ctx.respond(
        (
            f"🔴 Recording is active in <#{session['voice_channel_id']}>.\n"
            f"Audio destination: <#{RECORDING_CHANNEL_ID}>\n"
            f"Started: {discord_time(session['started_at'])}\n"
            f"Mode: **{session['mode']}**"
        ),
        ephemeral=True,
    )


@record.error
@stop.error
async def permission_error(
    ctx: discord.ApplicationContext,
    error: Exception,
):
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond(
            "You need the **Manage Channels** permission.",
            ephemeral=True,
        )
        return

    logger.exception("Slash command error", exc_info=error)

    try:
        await ctx.respond("An unexpected error occurred.", ephemeral=True)
    except discord.HTTPException:
        pass


bot.run(TOKEN)
