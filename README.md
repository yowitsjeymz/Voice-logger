# Discord Auto VC Recorder — Separate Log and Recording Channels

This version separates ordinary logs from recording files.

## Destinations

- `LOG_CHANNEL_ID`
  - Recording started notice
  - Recording ended notice
  - Duration, channel, trigger, and mode

- `RECORDING_CHANNEL_ID`
  - Audio files only
  - Separate WAV file for each recorded participant
  - Basic recording-session information

The bot does not DM recordings and does not post recording files in the command channel.

## Commands

- `/record`
- `/stop`
- `/recording_status`

## Render environment variables

```env
DISCORD_TOKEN=your_bot_token
GUILD_ID=your_server_id
LOG_CHANNEL_ID=your_activity_logs_channel_id
RECORDING_CHANNEL_ID=your_private_voice_record_channel_id
AUTO_RECORD=true
UPLOAD_LIMIT_MB=24
```

Keep the recording channel private by allowing access only to authorized staff roles and the bot.

## Required bot permissions

For both text channels:

- View Channel
- Send Messages
- Embed Links
- Attach Files
- Read Message History

For voice channels:

- View Channel
- Connect
- Speak

Enable Server Members Intent in the Discord Developer Portal.

## Privacy

The bot posts a clear recording notice. Do not use it for secret recording. Make sure all
participants are informed and that your use follows applicable rules and laws.
