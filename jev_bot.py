"""
Jev Discord Bot — thin Discord front-end over jev_core.
"""

import os
import re
import asyncio

import discord
from discord.ext import commands

from jev_core import BASE_VOCAB, MAX_HISTORY, MUTES, generate_reply, is_aside, is_stop_command, log

TOKEN = os.environ["DISCORD_TOKEN_JEV"]


async def fetch_history(channel, before_msg):
    """Grab last N messages (humans and jev) from channel — rebuilds context from Discord, no caching."""
    history = []
    try:
        async for msg in channel.history(limit=30, before=before_msg):
            is_jev = msg.author.id == bot.user.id
            if msg.author.bot and not is_jev:
                continue
            text = strip_mention(msg.content or "", bot.user.id)
            if not text:
                continue
            # Skip bot commands and asides
            if text.startswith(".") or is_aside(text):
                continue
            history.append({"role": "assistant" if is_jev else "user", "name": msg.author.display_name,
                            "content": text})
            if len(history) >= MAX_HISTORY:
                break
    except Exception:
        pass
    history.reverse()
    return history


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
gen_lock = asyncio.Lock()

def mute_key(channel):
    return f"discord:{channel.id}"

def strip_mention(c, bid):
    return re.sub(rf"<@!?{bid}>", "", c).strip()

def should_respond(m):
    if m.author.bot: return False
    if is_aside(strip_mention(m.content, bot.user.id)): return False
    if bot.user in m.mentions: return True
    if m.reference and m.reference.resolved:
        r = m.reference.resolved
        if isinstance(r, discord.Message) and r.author.id == bot.user.id: return True
    return False

@bot.event
async def on_ready():
    log.info(f"jev online as {bot.user} | vocab {len(BASE_VOCAB)}")

@bot.event
async def on_message(m):
    if not should_respond(m): return
    c = strip_mention(m.content, bot.user.id) or "hello"
    if bot.user in m.mentions:
        if is_stop_command(c):
            # "@jev stop" — mute this thread/channel; ack with a reaction, no reply.
            MUTES.add(mute_key(m.channel))
            log.info(f"[STOP] {m.author} muted {m.channel.id}")
            try: await m.add_reaction("\U0001F910")  # 🤐
            except Exception: pass
            return
        MUTES.discard(mute_key(m.channel))  # explicit mention un-mutes
    elif mute_key(m.channel) in MUTES:
        return  # reply-to-jev in a muted thread: stay quiet
    log.info(f"[IN] {m.author}: {c[:80]}")
    try:
        async with m.channel.typing():
            async with gen_lock:
                h = await fetch_history(m.channel, m)
                r = await generate_reply(c, history=h, author=m.author.display_name)
        log.info(f"[OUT] {r}")
        await m.reply(r, mention_author=False)
    except Exception as e:
        log.error(f"Error: {e}", exc_info=True)
        try: await m.reply("...", mention_author=False)
        except: pass

if __name__ == "__main__":
    bot.run(TOKEN, log_handler=None)
