# Golden-50 evaluation

Run `runs/full-v1` vs human labels `golden/golden_50_labeled.csv` · 50 cases · 2 ambiguous · 0 missing/quarantined (counted incorrect)

| metric | topic | intent | severity | joint |
|---|---|---|---|---|
| agreement (strict) | 74% | 80% | 72% | 52% |
| agreement (accepted set) | 74% | 80% | 72% | 52% |
| macro F1 (unambiguous cases) | 0.7052 | 0.8483 | 0.5126 | |

Severity: exact 72%, MAE 0.32 (vs nearest accepted 0.32). Sentiment: MAE 0.18, within predeclared ±0.5: 0.96. Quote exact-substring rate: 100%.

needs_review as a predictor of human ambiguity flags: {'predicted': 9, 'human_flagged': 2, 'true_positive': 1, 'precision': 0.1111, 'recall': 0.5}

Entities predicted but not listed by the human: {'ads': 8, 'update': 7, 'premium': 3, 'playlist': 2, 'subscription': 2, 'duo': 1, 'crash': 1, 'recommendations': 1, 'shuffle': 1, 'lyrics': 1, 'offline': 1, 'price': 1, 'free tier': 1}; listed by human but not predicted: {}

## Topic confusion

| expected \ predicted | access | usability | playback | downloads | catalog | billing | support | other |
|---|---|---|---|---|---|---|---|---|
| **usability** | · | 5 | 1 | · | · | 2 | · | · |
| **playback** | · | · | 3 | · | · | 2 | · | · |
| **downloads** | · | · | · | 1 | · | · | · | · |
| **catalog** | · | · | · | · | 1 | · | · | · |
| **other** | · | 4 | · | · | 3 | 1 | · | 27 |

## Intent confusion

| expected \ predicted | cancellation | complaint | request | praise | unclear |
|---|---|---|---|---|---|
| **cancellation** | 1 | · | · | · | · |
| **complaint** | · | 16 | · | · | 1 |
| **request** | · | · | 1 | · | · |
| **praise** | · | 1 | · | 17 | · |
| **unclear** | · | 5 | · | 3 | 5 |

## Severity confusion

| expected \ predicted | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| **1** | 25 | 5 | 1 | · | · |
| **2** | 2 | 6 | 1 | · | · |
| **3** | · | · | 5 | 3 | 1 |
| **4** | · | · | 1 | · | · |

## Disagreements (strict, any field)

| review_id | text | expected (t/i/s) | predicted (t/i/s) | accepted? |
|---|---|---|---|---|
| `292ce26a` | Staff must be full of mentally ill kool-aid heads. Banning conservative music that speaks truth. Spotify is doing all they can to protect pedophiles, by banning | catalog/complaint/2 | catalog/complaint/3 | no |
| `991b6b3a` | Ads were okay, but limited functionality..it's just too much | usability/complaint/3 | billing/complaint/3 | no |
| `0c6ca545` | Hate this application 🤮🤮🤮🤮🤮🤮 | other/praise/1 | other/complaint/2 | no |
| `1fc8b08f` | Duo Premium .... No ads at all! | other/unclear/1 | usability/praise/1 | no |
| `947821dc` | Very good but wen I play my song it o ly plays a little bit then it stops I updated my phone and it worked but it happend again they need to fix this issue | usability/complaint/3 | playback/complaint/3 | no |
| `723f07de` | Update: I reached out to Spotify Support and with some help from Sarah I was able to get the app working again. Old Post:Normally, the app works great, and I us | playback/complaint/3 | playback/complaint/4 | no |
| `01d453b0` | I like it but god I hate it when the music Interrupts my ads | usability/unclear/1 | usability/complaint/2 | no |
| `46842184` | asem, lirik nya kdang gaada kek mana?? | other/unclear/1 | catalog/complaint/2 | no |
| `5de7f95b` | We are boycotting Swedish app in our protest against Sweden for their desrecpect & desceration of our holy book Qur'an | other/complaint/2 | other/unclear/1 | no |
| `de48c99b` | I LOVE that I was able to put in only a few songs I like and Spotify gave me recommendations for an AWESOME Playlist...!!! | other/praise/1 | catalog/praise/1 | no |
| `372d4e67` | Please ye ads ko thoda kumm Karo harr ek song ke baad ad 🙏🙄 | other/unclear/1 | usability/complaint/2 | no |
| `47f1406d` | Very poor updates as we cannot playback the songs and most bad updates are happening it is affecting the spotify quality | playback/complaint/3 | playback/complaint/4 | no |
| `3ddb3f4e` | Deleting this App. The new update suck, we can't choose our fvrt songs after some clicks, we can't CHOOSE song after a song played, and we can't turn off shuffl | usability/cancellation/4 | billing/cancellation/3 | no |
| `16d640e9` | It's my best music app but now it's not...everything basic features is premium.... | other/unclear/1 | billing/complaint/3 | no |
| `8cc4fad4` | I hate this app and sweden .I love islam | other/unclear/1 | other/complaint/2 | no |
| `215463ae` | What is happening with Spotify?? Recently I'm trying to play a song but it doesn't working. So I unstalled and thn again install it now I can't even open the ap | playback/complaint/3 | playback/complaint/4 | no |
| `46c0b49f` | Are you out of your mind? This is dangerous! Listening to music in a normal volume and suddenly an add starts and my ears feel like they explode!! Stop this BS, | usability/complaint/3 | usability/complaint/5 | no |
| `1d15724a` | Good | other/unclear/1 | other/praise/1 | no |
| `2aa566c6` | Nice app, very less advertising | other/unclear/1 | usability/praise/1 | no |
| `8c0546b0` | 1-Lyrics not getting loaded, 2-can't go back to or start song where to wanted. Pathetic update | playback/complaint/3 | billing/complaint/3 | no |
| `b58dd6f0` | The only thing Spotify lacks is the ability to organize saved  albums/artists into collections. Would definitely deserve 5 stars if it  included this feature. | usability/request/2 | usability/request/1 | no |
| `dceb14e7` | I gave one star because I can't give any less than that. the new update is sooooooo annoying like I can't even play the songs that I like and I can't rewind son | playback/complaint/3 | billing/complaint/3 | no |
| `5fba12ea` | I find Spotify very good, it finds and play whatever music you want and also adds songs that you might like, judging from your playlist if you choose to arrange | other/praise/1 | catalog/praise/1 | no |
| `99b93e31` | This update very bad for everyone.old Spotify. we all want. | other/complaint/2 | usability/complaint/2 | no |
