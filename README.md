# jevbot

Discord + Slack bot that makes [TypeSafe's Jev](https://docs.typesafe.ai) talk — a decision model that "cannot generate text," loomed word-by-word into broken sentences.

Jev is a non-autoregressive decision model. It answers questions with calibrated probabilities, not text. This bot gives it a 20K word vocabulary and asks "next word?" repeatedly via tournament sampling until it forms a reply.

## How it works

1. **Tournament sampling**: 20K vocab shuffled into 255-word buckets, all scored in parallel
2. **Runoff**: Top-2 from each bucket compete in a final round  
3. **Completeness judge**: A separate `noul` question asks "is the reply complete?" — jev stops when it thinks it's done
4. **Penalty system**: Content words penalized 2.5x per reuse, stopwords 1.6x — prevents "is are I is are" loops
5. **User-only history**: Last 3 user messages included as context; jev's own broken output is excluded (it poisons follow-ups)

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

The generation core lives in `jev_core.py`; `jev_bot.py` (Discord) and `jev_slack.py` (Slack) are thin front-ends over it. Both take `--v1` for the original broken-grammar style.

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

Uses Socket Mode, so no public URL or Request URL is needed. Jev replies in-thread when @mentioned, when someone posts in a thread jev is already in, or in DMs. It adds 👀 to your message while it thinks (Slack bots can't show a typing indicator). Say `@jev stop` in a thread to mute it there (it reacts 🤐); @mentioning it again in that thread un-mutes it. Mutes (for both platforms) are saved to `mutes.json` next to the code (override with `JEV_MUTES_FILE`) and survive restarts.

## Cost

Calls `POST https://api.typesafe.ai/v1/systemone` with `jev-latest` (set `TYPESAFE_BASE_URL` to point elsewhere). Tournament sampling does ~9 API calls per word, roughly 80 buckets × 255 words each; at $0.042 per million input tokens a reply is a fraction of a cent.

## Vocab

`vocab.txt` is a 20K word list (from [bewinxed/jevgpt](https://github.com/bewinxed/jevgpt)) with slurs removed. Words can be added or removed freely — the vocab IS the content filter.

## Credits

- [TypeSafe AI](https://typesafe.ai) for Jev
- [bewinxed/jevgpt](https://github.com/bewinxed/jevgpt) for the tournament sampling architecture and vocab
- Built by [lyra](https://twitter.com/_lyraaaa_) + clod
