import asyncio

try:
    import discord
except Exception:
    discord = None

from core.brain import process_discord_chat
from tools.discord_api import CONTACTS, DISCORD_BOT_TOKEN
from tools.realtime_stream import publish_discord, publish_terminal


# Derived from Aster_Vault/discord_contacts.json (via discord_api.CONTACTS) so
# there is exactly one place that knows real friend identities — inbound
# whitelisting and outbound sending can no longer drift out of sync.
ALLOWED_FRIENDS = {discord_id: name for name, discord_id in CONTACTS.items()}


def _build_intents():
    intents = discord.Intents.default()
    for attr in ("messages", "dm_messages", "message_content"):
        if hasattr(intents, attr):
            setattr(intents, attr, True)
    return intents


def start_discord_dm_listener():
    """Blocking Discord DM listener for whitelisted friends only."""
    if discord is None:
        print("[Aster Network] Discord DM listener unavailable: install discord.py.")
        return

    token = str(DISCORD_BOT_TOKEN or "").strip()
    if not token:
        print("[Aster Network] Discord DM listener unavailable: missing bot token.")
        return

    intents = _build_intents()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"[Aster Network] Discord DM listener active as {client.user}.")
        publish_terminal(f"[SYS] Discord DM listener ready as {client.user}.")

    @client.event
    async def on_message(message):
        if message.author.bot:
            return
        if message.guild is not None:
            return

        sender_id = str(message.author.id)
        sender_name = ALLOWED_FRIENDS.get(sender_id)
        if not sender_name:
            return

        incoming_text = str(message.content or "").strip()
        if not incoming_text:
            return

        publish_discord(sender_name, incoming_text)
        publish_terminal(f"[SYS] Discord DM from {sender_name}: {incoming_text}")

        reply_text = await asyncio.to_thread(process_discord_chat, sender_name, incoming_text)
        if reply_text and reply_text.strip():
            await message.channel.send(reply_text)
            publish_discord("Aster", reply_text)

    try:
        client.run(token)
    except Exception as e:
        print(f"[Aster Network] Discord DM listener stopped: {e}")

if __name__ == "__main__":
    print("[Diagnostic] Booting Discord Listener in standalone mode...")
    start_discord_dm_listener()
