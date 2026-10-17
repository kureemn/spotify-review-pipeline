"""Build golden/label.html: a self-contained page for hand-labelling the golden 50.

No model output or suggestion is shown: the human sees only the review text and the shared
definitions. Labels are saved in the browser and exported as golden_50_labeled.csv.

    python golden/build_label_page.py && open golden/label.html
"""

import csv
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.labels import (INTENT_DEFINITIONS, INTENTS, SEVERITY_DEFINITIONS, SUBTOPICS,  # noqa: E402
                             TOPIC_DEFINITIONS, TOPICS)

rows = list(csv.DictReader(open(ROOT / "data" / "golden_50_to_label.csv", encoding="utf-8-sig", newline="")))
reviews = [{k: r[k] for k in ("review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp")}
           for r in rows]   # rating is kept for the export but never displayed: label the text only
defs = {"topics": {t: {"def": TOPIC_DEFINITIONS[t], "subs": SUBTOPICS[t]} for t in TOPICS},
        "intents": {i: INTENT_DEFINITIONS[i] for i in INTENTS},
        "severity": {str(k): v for k, v in SEVERITY_DEFINITIONS.items()}}

page = (Path(__file__).parent / "label_template.html").read_text()
page = page.replace("/*__REVIEWS__*/[]", json.dumps(reviews, ensure_ascii=False).replace("</", "<\\/"))
page = page.replace("/*__DEFS__*/{}", json.dumps(defs, ensure_ascii=False))
(Path(__file__).parent / "label.html").write_text(page)
print(f"wrote golden/label.html with {len(reviews)} reviews")
