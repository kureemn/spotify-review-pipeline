# Shared labels, severity scale and applied examples

The primary `topic`, `intent` and `severity` values are the exact common labels from
`data/GRADING_CONTRACT.md`. Subtopics are our optional refinement. The model picks one combined
`topic.subtopic` label from a strict JSON-schema enum, and code splits it, so a topic and its
subtopic can never contradict each other. The source of truth is
[`pipeline/labels.py`](../pipeline/labels.py); the text below is generated from it.

```text
TOPICS (topic.subtopic labels):
- access: Login, signup, password or account access
    - access.login_failure: cannot log in / logged out / Google-Facebook sign-in fails
    - access.account_hacked_or_locked: account taken over, locked, disabled, email changed
    - access.signup: cannot create an account
    - access.general: other access problems
- usability: Navigation, controls, layout, queue/playlist management, ad interruptions
    - usability.ads: ad frequency, length, loudness or interruptions
    - usability.ui_redesign: new layout/update/home feed changes, confusing navigation
    - usability.shuffle_queue_controls: shuffle, smart shuffle, queue, repeat, autoplay behaviour
    - usability.library_playlists: managing playlists, liked songs, library sorting
    - usability.car_device_integration: car mode, auto-start, Android Auto, widgets, lock screen controls
    - usability.general: other usability complaints
- playback: Playback failure, crashes, lag, connection failures, audio quality, resource use
    - playback.crashes_wont_open: app crashes, freezes, will not open
    - playback.stops_skips_lag: music stops, pauses, skips by itself, lags, buffering
    - playback.connectivity_errors: 'no internet' or offline errors despite a connection, songs unavailable to play
    - playback.audio_quality: sound quality, volume, equalizer
    - playback.device_connect: Bluetooth, Spotify Connect, casting, speakers, watches
    - playback.performance_battery: battery drain, heat, storage use, slowness
    - playback.general: other playback problems
- downloads: Downloading, saved music, offline listening, disappearing downloads
    - downloads.downloads_lost_failing: downloads disappear, fail, or need re-downloading
    - downloads.offline_mode: offline playback of saved music does not work
    - downloads.general: other download problems
- catalog: Missing songs/artists, search/discovery, recommendations, lyrics availability
    - catalog.missing_content: songs/artists/albums missing, greyed out, removed, region locked
    - catalog.search_discovery: search results, browsing
    - catalog.recommendations: recommendations, algorithm, Discover Weekly, DJ, repetitive suggestions
    - catalog.lyrics: lyrics availability or accuracy
    - catalog.podcasts_audiobooks: podcasts, audiobooks, video content
    - catalog.general: other catalog topics
- billing: Price, charges, subscriptions, paywalls, premium entitlement; explicitly premium-only controls go here
    - billing.free_tier_restrictions: features moved behind Premium: skip limits, choosing songs, repeat, queue for free users
    - billing.price: price level or price increases
    - billing.charges_refunds: unexpected charges, refunds, double billing, trial charges
    - billing.subscription_activation: paid but Premium not active, plan/payment method problems
    - billing.general: other billing topics
- support: Contacting support and the support response
    - support.no_or_poor_response: support unreachable, slow or unhelpful
    - support.general: other support topics
- other: General praise/criticism, unrelated content, or no supported specific topic
    - other.general: general praise or criticism, unrelated content, boycott slogans, meaningless text

INTENT (precedence order; pick the FIRST that applies):
- cancellation: explicitly leaving, uninstalling, cancelling, switching away, or threatening to do so
- complaint: negative experience, including mixed praise/criticism; generic 'bad app' counts
- request: desired change or feature without a reported failure
- praise: positive experience only
- unclear: bare boycott slogans, unrelated or meaningless text, or language you cannot interpret

SEVERITY (integer 1-5):
- 1: No reported problem: praise, neutral/unclear content, or a pure feature request
- 2: Dislike, generic criticism, minor annoyance, or a cosmetic issue; no supported functional loss
- 3: A degraded or restricted function; some use or workaround remains
- 4: A clearly blocked core task, such as inability to log in or play music
- 5: Explicit serious financial, privacy, or data harm; an expensive plan, a crash, or angry language alone is insufficient

SENTIMENT (integer -2..2): -2 = very negative, -1 = negative, 0 = neutral or mixed, 1 = positive, 2 = very positive
```

## Decision rules
1. Classify the review text only. Star ratings are never shown to any model or to the human labeller.
2. Topic: the problem with the highest supported severity; on a tie, the first specific problem mentioned. For a positive review, the first specific praised feature; general praise is `other`.
3. A paid-plan mention alone is not `billing`. Explicitly premium-only controls (skip/repeat/choose songs) are `billing.free_tier_restrictions`. A crash for a paying user is `playback`.
4. Ad frequency or length is `usability.ads`, even with "unless you pay".
5. Intent precedence: cancellation > complaint > request > praise > unclear. Bare boycott slogans are `unclear`.
6. Cancellation intent, stars and angry language never raise severity by themselves.
7. Missing context sets `needs_review` with a short reason; impact is not invented.

## Applied examples (development data only: `analysis_10000.csv` rows ≥ 600, disjoint from the golden 50 and the 100/500 checkpoints)

| Review (excerpt) | label | intent | sev | Why |
|---|---|---|---|---|
| "Why does it constantly need internet to open up downloaded playlists?…" | downloads.offline_mode | complaint | 4 | offline playback of saved music is blocked |
| "…I can't even que up my songs, play on repeat or even skip… for free. They turned everything into a premium feature." | billing.free_tier_restrictions | complaint | 3 | function restricted by paywall; the app still plays |
| "The app automatically starts every time I connect to my car… so I'm uninstalling this app" | usability.car_device_integration | cancellation | 2 | annoyance; departure stated but it does not raise severity |
| "My spotify randomly stopped working one day… nothing has worked since." | playback.crashes_wont_open | complaint | 4 | core task blocked |
| "…it plays tons and tons of ads. Like, four or five ads to every one or two songs." | usability.ads | complaint | 2 | annoyance, no functional loss |
| "Give us the option to permanently turn off smart shuffle… incredibly annoying." | usability.shuffle_queue_controls | complaint | 2 | annoyance reported, so not a pure request |
| "Please allow us to save the Discover Weekly playlist in a dedicated archive playlist" | catalog.recommendations | request | 1 | pure feature request |
| "Dear spoty I go to app but I can't log in with Google pleas help" | access.login_failure | complaint | 4 | cannot log in |
| "#boycottsweden Giving a rating of 1 for the actions of sweden" | other.general | unclear | 1 | bare boycott slogan |
| "Worst Now. Best in past 👎 very bad. Just switch to you tube music" | other.general | cancellation | 2 | generic criticism + stated switch |

## Deterministic fields (code, not model)
- `evidence_quote`: reviews ≤ 150 characters use the full original text, which is always an exact substring. Longer reviews use the model's exact span, validated with `quote in review_text`; a case-only mismatch is repaired to the original characters. Otherwise the review is retried once, then saved with the full text, `needs_review=true` and note `quote_fallback_full_text`.
- `entities`: canonical feature names whose regex matches the text (40-term lexicon in `labels.py`). Code extraction cannot add unsupported entities.
- `sentiment`: the model answers on an integer scale −2..2; code maps it to −1..1 (`level / 2`).
