# jevbot

Discord + Slack bot that makes [TypeSafe's Jev](https://docs.typesafe.ai) talk — a decision model that "cannot generate text," loomed word-by-word into broken sentences.

Jev is a non-autoregressive decision model. It answers questions with calibrated probabilities, not text. This bot gives it a 20K word vocabulary and asks "next word?" repeatedly via tournament sampling until it forms a reply.

## How it works

1. **Tournament sampling**: 20K vocab shuffled into 255-word buckets, all scored in parallel
2. **Runoff**: Top-2 from each bucket compete in a final round  
3. **2-word lookahead**: The top 6 words each get their 2 best follow-ups, and jev picks the best phrase — so an answer split over several words ("cats", "dogs") can beat a single hedge ("depends")
4. **Completeness judge**: A separate `noul` question asks whether Jev's message is finished (not whether it's good) — jev stops when it's done, even after one word
5. **Penalty system**: Content words penalized 2.5x per reuse, stopwords 1.6x — prevents "is are I is are" loops
6. **History**: The last 8 messages, jev's own replies included, with each person labeled by display name. Every call starts with a short header saying the last line is Jev's unfinished reply

## Output examples

- "I love jazz because its improvised and freedom."
- "Rock.? Yeah"  
- "No because overkill. Overkill!.!.!"
- "I depends on on situation of circumstances."
- "Yuck no ugh spit! Gag gagging ing"
- "Band is from california in san los angeles. Las angels."

## Setup

```bash
pip install -r requirements.txt
```

The generation core lives in `jev_core.py`; `jev_bot.py` (Discord) and `jev_slack.py` (Slack) are thin front-ends over it.

### Discord

Create `.env`:
```
DISCORD_TOKEN_JEV=your_discord_bot_token
TYPESAFE_API_KEY=your_typesafe_api_key
```

Enable **Message Content Intent** in Discord developer portal.

```bash
python jev_bot.py
```

Mention jev or reply to jev's messages. Replies only — it won't respond to messages that don't involve it. Say `@jev stop` to mute it in that channel/thread (it reacts 🤐 and ignores replies to it there until it's @mentioned again).

### Slack

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest**, paste `slack_manifest.yaml`.
2. **Basic Information → App-Level Tokens**: generate a token with `connections:write` (this is the `xapp-` token).
3. **Install App** to your workspace and copy the Bot User OAuth Token (`xoxb-`).
4. Invite jev to a channel: `/invite @jev`.

Create `.env`:
```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
TYPESAFE_API_KEY=your_typesafe_api_key
```

```bash
python jev_slack.py
```

Uses Socket Mode, so no public URL or Request URL is needed. Jev replies in-thread when @mentioned, when someone posts in a thread jev is already in, or in DMs. Context is per thread: inside a thread jev reads the last few messages of that thread, while a top-level @mention or DM only sees that one message. It adds 👀 to your message while it thinks (Slack bots can't show a typing indicator). Jev also looks at images (PNG/JPEG/GIF/WebP, resized to fit the API's limits) attached to the message and to the earlier messages it reads as context — this needs the `files:read` scope. Jev labels people in a thread by display name, which needs the `users:read` scope. If your app was created from an older manifest, add these scopes and reinstall the app. Say `@jev stop` in a thread to mute it there (it reacts 🤐); @mentioning it again in that thread un-mutes it. Mutes (for both platforms) are saved to `mutes.json` next to the code (override with `JEV_MUTES_FILE`) and survive restarts.

## Cost

Calls `POST https://api.typesafe.ai/v1/systemone` with `jev-preview` via the [TypeSafe Python SDK](https://docs.typesafe.ai/sdk/python.md) (set `TYPESAFE_BASE_URL` to point elsewhere). Image support is [dreamer-ready](https://docs.typesafe.ai/images.md), so requests with images send the `TypeSafe-Danger-Zone-Images: true` header. Images are billed as input tokens and ride along on every call, so a reply with an image costs noticeably more than a text-only one. Each step does ~11 API calls for up to 2 words: the tournament (roughly 80 buckets × 255 words), the completeness check, the runoff and 2 lookahead calls; at $0.042 per million input tokens a reply is a fraction of a cent.

## Vocab

`vocab.txt` is a 20K word list (from [bewinxed/jevgpt](https://github.com/bewinxed/jevgpt)) with slurs removed. `jev_core.py` adds "jev" and the numbers 10–1000, plus any words and numbers from the conversation. Words can be added or removed freely — the vocab IS the content filter.

## Credits

- [TypeSafe AI](https://typesafe.ai) for Jev
- [bewinxed/jevgpt](https://github.com/bewinxed/jevgpt) for the tournament sampling architecture and vocab
- Built by [lyra](https://twitter.com/_lyraaaa_) + clod
