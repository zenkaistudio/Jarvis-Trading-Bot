# Telegram Bot Setup

This guide covers creating a Telegram bot for Jarvis and finding the two values you need: the bot token and your chat ID.

---

## Step 1 — Create a bot with BotFather

1. Open Telegram and search for `@BotFather`
2. Start a conversation and send `/newbot`
3. Choose a name for your bot — this is the display name (e.g. "Jarvis Trading")
4. Choose a username — must end in `bot` (e.g. `jarvis_my_trading_bot`)
5. BotFather will respond with your bot token. It looks like this:

   ```
   8992102387:AAFxAwbaslJLVZEbdrgMRH9S9XUpdjUxoNM
   ```

6. Copy this token. You will paste it into `jarvis_config.json` as `telegram_token`.

---

## Step 2 — Get your chat ID

Your chat ID is the numeric ID Telegram uses to identify your personal conversation with the bot. Jarvis uses this to make sure it only responds to messages from you.

**Method:**

1. Open Telegram and search for your new bot by its username
2. Send it any message (e.g. "hello")
3. In your browser, open this URL — replace `YOUR_BOT_TOKEN` with the token from Step 1:

   ```
   https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
   ```

4. You will see a JSON response. Look for `"chat"` inside `"message"`:

   ```json
   {
     "update_id": 123456789,
     "message": {
       "chat": {
         "id": 7573659558,
         "first_name": "Your Name",
         "type": "private"
       },
       "text": "hello"
     }
   }
   ```

5. The number at `"id"` is your chat ID (e.g. `7573659558`). Copy it.

If the response shows an empty `result` array, send another message to the bot first, then refresh the URL.

---

## Step 3 — Fill in jarvis_config.json

Open `jarvis_config.json` and update these two fields:

```json
{
  "telegram_token": "8992102387:AAFxAwbaslJLVZEbdrgMRH9S9XUpdjUxoNM",
  "telegram_chat_id": "7573659558"
}
```

Both values should be strings (wrapped in quotes).

---

## Step 4 — Test the connection

Start Jarvis monitoring from Claude Code (ask "Start Jarvis monitoring") or run a quick test from the terminal:

```python
import requests

token = "YOUR_BOT_TOKEN"
chat_id = "YOUR_CHAT_ID"

requests.post(
    f"https://api.telegram.org/bot{token}/sendMessage",
    json={"chat_id": chat_id, "text": "Jarvis connection test"}
)
```

You should receive "Jarvis connection test" in Telegram within a few seconds.

---

## Troubleshooting

**No message received:**
- Confirm you sent a message to the bot before calling `getUpdates`
- Double-check the token is copied exactly with no extra spaces
- Make sure the bot username does not have a `/` prefix in the URL

**"Unauthorized" error from the API:**
- The token is invalid or was revoked
- Go back to BotFather, send `/mybots`, select your bot, then `API Token` to regenerate

**Chat ID shows as negative number:**
- A negative ID means you added the bot to a group. Jarvis is designed for private chats. Remove the bot from the group, message it directly, and use that positive ID.

**Messages from others are ignored:**
- This is by design. Jarvis only processes messages from the chat ID in `telegram_chat_id`. Anyone else who messages your bot will get no response.
