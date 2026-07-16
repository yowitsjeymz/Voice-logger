# Discord VC Logger for Render

This bot:

- Logs voice-channel joins, leaves, and moves.
- Records attendance time and session duration.
- Automatically joins the first active member's voice channel.
- Moves to another channel only when no human remains in its current channel.
- Disconnects when its current channel becomes empty.
- Does **not** capture or save anyone's audio.

## 1. Create the Discord bot

1. Open the Discord Developer Portal and create an application.
2. Open **Bot**, create the bot, and copy its token.
3. Under **Privileged Gateway Intents**, enable **Server Members Intent**.
4. Never place the real token in GitHub.

## 2. Invite permissions

Give the bot these permissions:

- View Channels
- Send Messages
- Embed Links
- Connect
- Speak

The bot does not transmit audio, but Discord voice connections may require normal voice permissions.

## 3. Get IDs

Enable Discord Developer Mode:

**User Settings → Advanced → Developer Mode**

Then right-click and copy:

- The server ID → `GUILD_ID`
- The log channel ID → `LOG_CHANNEL_ID`

`GUILD_ID` is optional. Leaving it blank allows every server containing the bot, but setting it is safer.

## 4. Upload to GitHub

Upload these files:

- `bot.py`
- `requirements.txt`
- `render.yaml`

Do not upload `.env`.

## 5. Deploy on Render

### Blueprint method

1. In Render, choose **New → Blueprint**.
2. Select the GitHub repository.
3. Add the requested secret environment variables:
   - `DISCORD_TOKEN`
   - `LOG_CHANNEL_ID`
   - `GUILD_ID`
4. Deploy.

### Manual method

Create a **Web Service** with:

- Runtime: Python
- Build command: `pip install -r requirements.txt`
- Start command: `python bot.py`
- Health check path: `/health`

Add the three environment variables under the service's Environment page.

## Important Render note

Render's free Web Service may spin down after a period without incoming HTTP requests.
The bot includes `/` and `/health` endpoints, but keeping a free service continuously awake may
require periodic legitimate HTTP traffic. A paid always-on service or worker is more reliable.

## Privacy note

This project records attendance events, not conversations. Recording people's audio without clear
notice and consent may violate server rules, Discord policies, or applicable law.
