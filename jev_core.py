"""
Jev core — tournament-sampled loomed text from TypeSafe's decision model.

Shared by the Discord (jev_bot.py) and Slack (jev_slack.py) front-ends.

v1: empty descriptions, "Next word?" — the original broken-grammar jev
v2: mode D descriptions ("...lastword candidate"), better instructions — more coherent

Run with --v1 for the original style, default is mix (random v1/v2 per word).
"""

import io
import os
import re
import sys
import asyncio
import logging
import random
from pathlib import Path
from collections import defaultdict

import json
from dotenv import load_dotenv
from PIL import Image as PILImage, ImageOps
from typesafe_sdk import AsyncTypeSafeClient, Image, TypeSafeError

load_dotenv()

TYPESAFE_KEY = os.environ["TYPESAFE_API_KEY"]
MODEL = "jev-preview"
END = "<END>"

# Version flag
V1_MODE = "--v1" in sys.argv

MAX_CHOICES = 255
QUESTIONS_PER_CALL = 20 if V1_MODE else 10  # mix mode averages out; pure v1 can pack more
TOP_PER_BUCKET = 2
MAX_WORDS = 30
MIN_WORDS = 2
MAX_HISTORY = 5
STOP_THRESHOLD = 0.5
REPEAT_PENALTY = 1.5
REPEAT_WINDOW = 8
CONTENT_PENALTY = 2.5
CONTENT_PENALTY_CAP = 4
STOP_PENALTY = 1.6
STOP_PENALTY_CAP = 6

STOPWORDS = set(
    "a an the and or but if of to in on at by for with from as is are was were be been "
    "being it its this that these those i you he she they we me him her them us my your "
    "his their our not no so then than there here when where which who what how all any "
    "some each into over under about above below up down out off again more most very "
    "can will just do does did have has had would could should may might must".split()
)
NO_SPACE_BEFORE = set(".,!?;:)\"'")

# Images (dreamer-ready in the API, opt-in per request via this header)
IMAGES_HEADER = {"TypeSafe-Danger-Zone-Images": "true"}
IMAGE_MIN_SIDE = 280    # API rejects anything smaller
IMAGE_MAX_SIDE = 1024   # API allows 1568; smaller is cheaper since the image rides along on every call

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)
log = logging.getLogger("jev")

# "@jev stop" — mutes jev in the current thread. Matches "stop" / "stop!" / "Stop." only.
STOP_CMD_RE = re.compile(r"^\s*stop[\s.!]*$", re.I)


def is_stop_command(text):
    return bool(STOP_CMD_RE.match(text or ""))


class MuteStore:
    """Set of muted thread keys, persisted to a JSON file so mutes survive restarts.

    Keys are strings; namespace them per platform (e.g. "slack:C123:1700000000.000100").
    """

    def __init__(self, path):
        self.path = Path(path)
        self.keys = set()
        try:
            self.keys = set(json.loads(self.path.read_text()))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning(f"could not read mutes from {self.path}: {e}")

    def _save(self):
        try:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(sorted(self.keys), indent=0))
            tmp.replace(self.path)  # atomic on POSIX
        except Exception as e:
            log.warning(f"could not write mutes to {self.path}: {e}")

    def __contains__(self, key):
        return key in self.keys

    def add(self, key):
        if key not in self.keys:
            self.keys.add(key)
            self._save()

    def discard(self, key):
        if key in self.keys:
            self.keys.discard(key)
            self._save()


MUTES = MuteStore(os.environ.get("JEV_MUTES_FILE", Path(__file__).parent / "mutes.json"))

# Vocab
VOCAB_PATH = Path(__file__).parent / "vocab.txt"
BANNED = {"unanswered", "\\n"}
BASE_VOCAB = [w for w in VOCAB_PATH.read_text().split("\n") if w and w.lower() not in BANNED]
log.info(f"Loaded {len(BASE_VOCAB)} vocab words | {'v1' if V1_MODE else 'mix'} mode")


def render(tokens):
    out = ""
    for t in tokens:
        if t == "\\n" or t == "\n":
            continue
        if not out or out.endswith(("\n", " ")) or t in NO_SPACE_BEFORE:
            out += t
        else:
            out += " " + t
    return re.sub(r"(^|[.!?]\s+|\n)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out.strip())


def vocabulary(message):
    seen = set(BASE_VOCAB)
    extra = [w for w in re.findall(r"[A-Za-z']+", message.lower())
             if w not in seen and not seen.add(w)]
    return BASE_VOCAB + extra + [END]


def penalty(reply, word):
    local = reply[-REPEAT_WINDOW:].count(word) + 2 * (reply[-1:] == [word])
    p = REPEAT_PENALTY ** local
    seen = reply.count(word)
    if word.isalpha() and word.lower() not in STOPWORDS:
        p *= CONTENT_PENALTY ** min(seen, CONTENT_PENALTY_CAP)
    elif word.isalpha():
        p *= STOP_PENALTY ** min(seen, STOP_PENALTY_CAP)
    return p


def prepare_image(data):
    """Raw image file bytes -> an upright RGB JPEG sized within the API's limits, or None if unreadable."""
    try:
        with PILImage.open(io.BytesIO(data)) as im:
            im.seek(0)  # first frame of GIFs/animated images
            im = ImageOps.exif_transpose(im).convert("RGB")  # API strips EXIF, so rotate the pixels
    except Exception as e:
        log.warning(f"could not read image: {e}")
        return None
    im.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
    w, h = im.size
    if min(w, h) < IMAGE_MIN_SIDE:
        # Scale up toward the minimum without blowing past the max, then pad what's left.
        scale = min(IMAGE_MIN_SIDE / min(w, h), IMAGE_MAX_SIDE / max(w, h))
        im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), PILImage.LANCZOS)
        canvas = PILImage.new("RGB", (max(im.width, IMAGE_MIN_SIDE), max(im.height, IMAGE_MIN_SIDE)), "white")
        canvas.paste(im, ((canvas.width - im.width) // 2, (canvas.height - im.height) // 2))
        im = canvas
    return Image.from_pil(im, format="jpeg")


async def post(client, state, questions):
    try:
        return await client.system_one(state=state, questions=questions)
    except TypeSafeError as e:  # SDK already retried
        log.warning(f"API err: {str(e)[:200]}")
        return None


def choice_q(words, reply_so_far="", force_mode=None):
    mode = force_mode or ("v1" if V1_MODE else "mix")
    if mode == "v1":
        return {"type": "choice", "instructions": "Next word?",
                "criteria": {w: "" for w in words}}
    elif mode == "v2":
        last = reply_so_far.split()[-1] if reply_so_far.split() else ""
        criteria = {w: f"...{last} {w}" if last else w for w in words}
        return {"type": "choice", "instructions": "Next word?",
                "criteria": criteria}
    else:
        # mix: randomly v1 or v2 each call
        if random.random() < 0.5:
            return choice_q(words, reply_so_far, force_mode="v1")
        else:
            return choice_q(words, reply_so_far, force_mode="v2")


async def next_word(client, state, vocab, rng, reply_so_far=""):
    shuffled = list(vocab)
    rng.shuffle(shuffled)
    buckets = [shuffled[i:i + MAX_CHOICES] for i in range(0, len(shuffled), MAX_CHOICES)]
    groups = [buckets[i:i + QUESTIONS_PER_CALL] for i in range(0, len(buckets), QUESTIONS_PER_CALL)]

    results = await asyncio.gather(
        *(post(client, state, {f"b{gi * QUESTIONS_PER_CALL + i}": choice_q(b, reply_so_far)
                                for i, b in enumerate(g)})
          for gi, g in enumerate(groups)),
        post(client, state, {"complete": {"type": "noul", "instructions": "Is the reply complete?"}}),
    )

    complete_noul = results[-1].nouls["complete"].noul if results[-1] else 0

    finalists = []
    for res in results[:-1]:
        if not res:
            continue
        for ans in res.choices.values():
            ranked = sorted(ans.probabilities.items(), key=lambda kv: -kv[1])
            finalists += [w for w, p in ranked[:TOP_PER_BUCKET] if p > 0]

    if END not in finalists:
        finalists.append(END)

    runoff = await post(client, state, {"final": choice_q(finalists[:MAX_CHOICES], reply_so_far)})
    probs = runoff.choices["final"].probabilities if runoff else {}

    return probs, complete_noul


async def generate_reply(message, history=None, images=None):
    """`images` (and each history item's optional "images") are raw image file bytes (PNG/JPEG/GIF/WebP...)."""
    assets = []

    def turn(name, text, imgs):
        prepared = [a for a in map(prepare_image, imgs or []) if a]
        assets.extend(prepared)
        return " ".join([f"{name}:"] + [text] * bool(text) + [str(a) for a in prepared])  # str(asset) is its reference

    turns = [turn("Jev" if h["role"] == "assistant" else "User", h["content"], h.get("images"))
             for h in history or []]
    turns.append(turn("User", message, images))
    if assets:
        log.info(f"  with {len(assets)} image(s)")

    client = AsyncTypeSafeClient(api_key=TYPESAFE_KEY, model=MODEL, timeout=30,
                                 headers=IMAGES_HEADER if assets else None)
    async with client:
        with client.attach_assets(assets):
            return await loom(client, "\n".join(turns), vocabulary(message))


async def loom(client, context, vocab):
    rng = random.Random()
    words = []
    for step in range(MAX_WORDS):
        state = f"{context}\nJev: {render(words)}"

        probs, complete = await next_word(client, state, vocab, rng, reply_so_far=render(words))
        if not probs:
            break

        stoppable = sum(1 for w in words if w.isalnum()) >= MIN_WORDS
        if stoppable and complete >= STOP_THRESHOLD:
            log.info(f"  noul={complete:.2f} stop")
            break

        scored = {}
        for w, p in probs.items():
            if p <= 0: continue
            if w in NO_SPACE_BEFORE and words[-1:] == [w]: continue
            if w == END and not stoppable: continue
            scored[w] = p / penalty(words, w)

        if not scored: break

        ranked = sorted(scored.items(), key=lambda kv: -kv[1])
        word = ranked[0][0]

        top3 = [(w, probs.get(w, 0)) for w, _ in ranked[:3]]
        log.info(f"  [{step+1:2d}] {word:12s}  {' '.join(f'{w}:{p:.0%}' for w,p in top3)}  done={complete:.2f}")

        if word == END: break
        words.append(word)

    return render(words) if words else "..."
