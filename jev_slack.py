"""
Jev Slack Bot — thin Slack front-end over jev_core.

Uses Socket Mode, so no public URL is needed. Jev replies in-thread when:
  - it is @mentioned in a channel
  - someone posts in a thread jev has already spoken in
  - someone DMs it

"@jev stop" mutes jev in that thread; @mentioning it again there un-mutes. Mutes persist in mutes.json.

Run with --v1 for the original style, default is mix.
"""

import os
import re
import html
import asyncio

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from jev_core import BASE_VOCAB, MAX_HISTORY, MUTES, V1_MODE, generate_reply, is_stop_command, log

BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]   # xoxb-...
APP_TOKEN = os.environ["SLACK_APP_TOKEN"]   # xapp-... (Socket Mode, connections:write)

app = AsyncApp(token=BOT_TOKEN)
gen_lock = asyncio.Lock()
BOT_USER_ID = None  # filled on startup via auth.test


def mute_key(channel, thread_ts):
    return f"slack:{channel}:{thread_ts}"


MENTION_RE = re.compile(r"<@[A-Z0-9]+(\|[^>]*)?>")


def clean(text):
    """Strip user mentions, unescape Slack's HTML entities, collapse whitespace."""
    text = MENTION_RE.sub("", text or "")
    text = html.unescape(text)
    return " ".join(text.split())


def is_from_bot(msg):
    return msg.get("user") == BOT_USER_ID or msg.get("bot_id") is not None


def is_human_text(msg):
    """Plain user-authored message (no joins, edits, bot posts, etc.)."""
    return msg.get("subtype") is None and not is_from_bot(msg) and bool((msg.get("text") or "").strip())


async def fetch_history(client, channel, thread_ts, before_ts):
    """Last N user messages before `before_ts` — from the thread if in one, else the channel."""
    lines = []
    try:
        if thread_ts:
            res = await client.conversations_replies(channel=channel, ts=thread_ts, limit=100)
            msgs = res.get("messages", [])
        else:
            res = await client.conversations_history(channel=channel, latest=before_ts,
                                                     inclusive=False, limit=30)
            msgs = list(reversed(res.get("messages", [])))  # history is newest-first
        for m in msgs:
            if float(m.get("ts", 0)) >= float(before_ts):
                continue
            if not is_human_text(m):
                continue
            text = clean(m["text"])
            if text and not text.startswith("."):
                lines.append(text)
    except Exception as e:
        log.warning(f"history fetch failed: {e}")
    lines = lines[-MAX_HISTORY:]
    return [{"role": "user", "content": t} for t in lines]


async def bot_in_thread(client, channel, thread_ts):
    """True if jev has already posted in this thread."""
    try:
        res = await client.conversations_replies(channel=channel, ts=thread_ts, limit=100)
        return any(is_from_bot(m) for m in res.get("messages", []))
    except Exception as e:
        log.warning(f"thread check failed: {e}")
        return False


async def stop_thread(event, client):
    """Handle "@jev stop": mute this thread and acknowledge with a reaction instead of a reply."""
    key = mute_key(event["channel"], event.get("thread_ts") or event["ts"])
    MUTES.add(key)
    log.info(f"[STOP] {event.get('user')} muted thread {key}")
    try:
        await client.reactions_add(channel=event["channel"], timestamp=event["ts"], name="zipper_mouth_face")
    except Exception:
        pass


async def respond(event, client):
    channel = event["channel"]
    ts = event["ts"]
    thread_ts = event.get("thread_ts") or ts  # always reply in-thread
    text = clean(event.get("text")) or "hello"
    log.info(f"[IN] {event.get('user')}: {text[:80]}")

    # Slack bots can't show a typing indicator; an 👀 reaction is the usual stand-in.
    try:
        await client.reactions_add(channel=channel, timestamp=ts, name="eyes")
    except Exception:
        pass

    try:
        async with gen_lock:
            h = await fetch_history(client, channel, event.get("thread_ts"), ts)
            r = await generate_reply(text, history=h)
        log.info(f"[OUT] {r}")
    except Exception as e:
        log.error(f"Error: {e}", exc_info=True)
        r = "..."

    try:
        await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=r, mrkdwn=False)
    except Exception as e:
        log.error(f"post failed: {e}")
    try:
        await client.reactions_remove(channel=channel, timestamp=ts, name="eyes")
    except Exception:
        pass


@app.event("app_mention")
async def on_mention(event, client):
    if is_stop_command(clean(event.get("text"))):
        await stop_thread(event, client)
        return
    # An explicit mention in a stopped thread un-mutes it.
    MUTES.discard(mute_key(event["channel"], event.get("thread_ts") or event["ts"]))
    await respond(event, client)


@app.event("message")
async def on_message(event, client):
    if not is_human_text(event):
        return
    # Mentions are handled by app_mention; don't double-reply.
    if BOT_USER_ID and f"<@{BOT_USER_ID}>" in (event.get("text") or ""):
        return
    if event.get("channel_type") == "im":
        await respond(event, client)  # DMs are always on; "stop" only applies to threads
        return
    thread_ts = event.get("thread_ts")
    if not thread_ts or thread_ts == event["ts"]:
        return
    if mute_key(event["channel"], thread_ts) in MUTES:
        return
    if await bot_in_thread(client, event["channel"], thread_ts):
        await respond(event, client)


async def main():
    global BOT_USER_ID
    auth = await app.client.auth_test()
    BOT_USER_ID = auth["user_id"]
    log.info(f"jev online as @{auth['user']} ({BOT_USER_ID}) | vocab {len(BASE_VOCAB)} | {'v1' if V1_MODE else 'mix'}")
    await AsyncSocketModeHandler(app, APP_TOKEN).start_async()


if __name__ == "__main__":
    asyncio.run(main())
