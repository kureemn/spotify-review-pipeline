"""Shared label definitions: the single source of truth for every role and for validation.

Primary topics, intents and severity levels are the exact common labels from
GRADING_CONTRACT.md. Subtopics are our optional refinement; the model picks one
combined "topic.subtopic" label so the two can never disagree.
"""

import re

SCHEMA_VERSION = "schema-v1"

TOPICS = ("access", "usability", "playback", "downloads", "catalog", "billing", "support", "other")
# Precedence order: the first that applies wins.
INTENTS = ("cancellation", "complaint", "request", "praise", "unclear")

TOPIC_DEFINITIONS = {
    "access": "Login, signup, password or account access",
    "usability": "Navigation, controls, layout, queue/playlist management, ad interruptions",
    "playback": "Playback failure, crashes, lag, connection failures, audio quality, resource use",
    "downloads": "Downloading, saved music, offline listening, disappearing downloads",
    "catalog": "Missing songs/artists, search/discovery, recommendations, lyrics availability",
    "billing": "Price, charges, subscriptions, paywalls, premium entitlement; explicitly premium-only controls go here",
    "support": "Contacting support and the support response",
    "other": "General praise/criticism, unrelated content, or no supported specific topic",
}

# Optional subtopics (custom). Each topic keeps a "general" bucket.
SUBTOPICS = {
    "access": {
        "login_failure": "cannot log in / logged out / Google-Facebook sign-in fails",
        "account_hacked_or_locked": "account taken over, locked, disabled, email changed",
        "signup": "cannot create an account",
        "general": "other access problems",
    },
    "usability": {
        "ads": "ad frequency, length, loudness or interruptions",
        "ui_redesign": "new layout/update/home feed changes, confusing navigation",
        "shuffle_queue_controls": "shuffle, smart shuffle, queue, repeat, autoplay behaviour",
        "library_playlists": "managing playlists, liked songs, library sorting",
        "car_device_integration": "car mode, auto-start, Android Auto, widgets, lock screen controls",
        "general": "other usability complaints",
    },
    "playback": {
        "crashes_wont_open": "app crashes, freezes, will not open",
        "stops_skips_lag": "music stops, pauses, skips by itself, lags, buffering",
        "connectivity_errors": "'no internet' or offline errors despite a connection, songs unavailable to play",
        "audio_quality": "sound quality, volume, equalizer",
        "device_connect": "Bluetooth, Spotify Connect, casting, speakers, watches",
        "performance_battery": "battery drain, heat, storage use, slowness",
        "general": "other playback problems",
    },
    "downloads": {
        "downloads_lost_failing": "downloads disappear, fail, or need re-downloading",
        "offline_mode": "offline playback of saved music does not work",
        "general": "other download problems",
    },
    "catalog": {
        "missing_content": "songs/artists/albums missing, greyed out, removed, region locked",
        "search_discovery": "search results, browsing",
        "recommendations": "recommendations, algorithm, Discover Weekly, DJ, repetitive suggestions",
        "lyrics": "lyrics availability or accuracy",
        "podcasts_audiobooks": "podcasts, audiobooks, video content",
        "general": "other catalog topics",
    },
    "billing": {
        "free_tier_restrictions": "features moved behind Premium: skip limits, choosing songs, repeat, queue for free users",
        "price": "price level or price increases",
        "charges_refunds": "unexpected charges, refunds, double billing, trial charges",
        "subscription_activation": "paid but Premium not active, plan/payment method problems",
        "general": "other billing topics",
    },
    "support": {
        "no_or_poor_response": "support unreachable, slow or unhelpful",
        "general": "other support topics",
    },
    "other": {
        "general": "general praise or criticism, unrelated content, boycott slogans, meaningless text",
    },
}

LABELS = tuple(f"{t}.{s}" for t in TOPICS for s in SUBTOPICS[t])

SEVERITY_DEFINITIONS = {
    1: "No reported problem: praise, neutral/unclear content, or a pure feature request",
    2: "Dislike, generic criticism, minor annoyance, or a cosmetic issue; no supported functional loss",
    3: "A degraded or restricted function; some use or workaround remains",
    4: "A clearly blocked core task, such as inability to log in or play music",
    5: "Explicit serious financial, privacy, or data harm; an expensive plan, a crash, or angry language alone is insufficient",
}

INTENT_DEFINITIONS = {
    "cancellation": "explicitly leaving, uninstalling, cancelling, switching away, or threatening to do so",
    "complaint": "negative experience, including mixed praise/criticism; generic 'bad app' counts",
    "request": "desired change or feature without a reported failure",
    "praise": "positive experience only",
    "unclear": "bare boycott slogans, unrelated or meaningless text, or language you cannot interpret",
}

# Sentiment is asked as a 5-point integer scale and mapped by code to [-1, 1].
SENTIMENT_SCALE = {-2: "very negative", -1: "negative", 0: "neutral or mixed", 1: "positive", 2: "very positive"}


def sentiment_value(level: int) -> float:
    return level / 2


def split_label(label: str):
    topic, subtopic = label.split(".", 1)
    return topic, subtopic


# ---- deterministic entity extraction (code, not model) --------------------------------------
# Entities are canonical feature names whose surface term appears explicitly in the text.
# Code-only extraction cannot hallucinate unsupported entities.
ENTITY_LEXICON = {
    "ads": r"\bad(s|vert\w*)?\b|\badd?s\b|\bcommercials?\b",
    "premium": r"\bpremium\b",
    "free tier": r"\bfree (version|tier|plan|users?|account)\b",
    "shuffle": r"\bshuffl\w*",
    "smart shuffle": r"\bsmart shuffle\b",
    "queue": r"\bque(ue)?\b",
    "repeat": r"\brepeat\b|\bloop\b",
    "skip": r"\bskip\w*",
    "lyrics": r"\blyric\w*",
    "download": r"\bdown?load\w*|\bdownlad\w*",
    "offline": r"\boff-?line\b",
    "playlist": r"\bplay ?lists?\b",
    "search": r"\bsearch\w*",
    "recommendations": r"\brecommend\w*|\balgorithm\w*",
    "discover weekly": r"\bdiscover weekly\b",
    "podcast": r"\bpodcasts?\b",
    "audiobook": r"\baudio ?books?\b",
    "login": r"\blog ?in\b|\blogin\w*|\bsign ?in\b|\blogged out\b",
    "password": r"\bpassword\w*",
    "account": r"\baccount\w*",
    "update": r"\bupdat\w*",
    "crash": r"\bcrash\w*",
    "bluetooth": r"\bbluetooth\b",
    "car": r"\bcar\b|\bandroid auto\b",
    "widget": r"\bwidgets?\b",
    "battery": r"\bbattery\b",
    "price": r"\bprice\w*|\bexpensive\b|\bcost\w*",
    "refund": r"\brefund\w*",
    "subscription": r"\bsubscri\w*",
    "family plan": r"\bfamily (plan|premium)\b",
    "student plan": r"\bstudent\b",
    "duo": r"\bduo\b",
    "customer support": r"\bcustomer (service|support|care)\b|\bsupport team\b",
    "dj": r"\bdj\b",
    "wrapped": r"\bwrapped\b",
    "connect": r"\bspotify connect\b|\bcast\w*\b|\bchromecast\b",
    "sleep timer": r"\bsleep timer\b",
    "equalizer": r"\bequali[sz]er\b",
    "video": r"\bvideos?\b",
    "notifications": r"\bnotifications?\b",
}
_ENTITY_RE = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in ENTITY_LEXICON.items()}
ENTITY_VERSION = "entities-lexicon-v1"


def extract_entities(text: str) -> list:
    return [name for name, rx in _ENTITY_RE.items() if rx.search(text)]


def definitions_markdown() -> str:
    """Render definitions for prompts and docs from the same data used for validation."""
    lines = ["TOPICS (topic.subtopic labels):"]
    for t in TOPICS:
        lines.append(f"- {t}: {TOPIC_DEFINITIONS[t]}")
        for s, d in SUBTOPICS[t].items():
            lines.append(f"    - {t}.{s}: {d}")
    lines.append("\nINTENT (precedence order; pick the FIRST that applies):")
    for i in INTENTS:
        lines.append(f"- {i}: {INTENT_DEFINITIONS[i]}")
    lines.append("\nSEVERITY (integer 1-5):")
    for k, v in SEVERITY_DEFINITIONS.items():
        lines.append(f"- {k}: {v}")
    lines.append("\nSENTIMENT (integer -2..2): " + ", ".join(f"{k} = {v}" for k, v in SENTIMENT_SCALE.items()))
    return "\n".join(lines)
