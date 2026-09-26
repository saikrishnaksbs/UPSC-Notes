"""Prompt templates and JSON schemas for every LLM call.

Kept in one place so they can be tuned (or swapped per model) without touching pipeline code.
Structured calls use JSON-schema constrained decoding with enums, so a small local model can
only pick subjects, folders and notes that actually exist in the repository.
"""

from __future__ import annotations

SYSTEM_ANALYST = (
    "You are an experienced UPSC Civil Services mentor who organises study material. "
    "You answer precisely and only with the requested JSON."
)

# ------------------------------------------------------------------ layout roles
STYLE_ROLES_PROMPT = """You are analysing the layout of a study document ("{name}", {pages} pages) so it can be split into separate articles.
Below are the distinct heading styles found (font size, bold, colour), how often each occurs, and example texts.
Body text is {body_size}pt.

{styles}

Classify EVERY style id:
- "section": broad section / category banners (e.g. "POLITY", "G.S PAPER II", "CURRENT EVENTS OF INTERNATIONAL IMPORTANCE").
- "topic": the title of an individual article or news item — each one starts a new article.
- "subheading": headings inside an article (e.g. "Why in News?", "Background", "Significance", "Key Features").
- "ignore": running headers/footers, captions, table headers, labels, noise.
Return JSON mapping each style id to its role."""


def style_roles_schema(style_ids: list[str]) -> dict:
    role = {"type": "string", "enum": ["section", "topic", "subheading", "ignore"]}
    return {"type": "object", "properties": {sid: role for sid in style_ids}, "required": style_ids}


# ------------------------------------------------------------------ unstructured splitting
SPLIT_PROMPT = """Below are numbered paragraphs from "{name}" (newspaper / magazine / notes).
Identify where each distinct article, news item or topic STARTS.
Paragraph numbers run from {first} to {last}.

{paragraphs}

Return JSON:
- "first_paragraph_continues_previous_topic": true if paragraph {first} continues a topic that began before this excerpt.
- "topics": list of {{"start": <paragraph number>, "title": <short specific title>}} in order. Only list real topic starts."""

SPLIT_SCHEMA = {
    "type": "object",
    "properties": {
        "first_paragraph_continues_previous_topic": {"type": "boolean"},
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"start": {"type": "integer"}, "title": {"type": "string"}},
                "required": ["start", "title"],
            },
        },
    },
    "required": ["first_paragraph_continues_previous_topic", "topics"],
}

# ------------------------------------------------------------------ triage
TRIAGE_PROMPT = """Classify this piece of text taken from "{tag}".

SOURCE SECTION (may be inaccurate): {section}
HEADING: {heading}
TEXT{truncated}:
<<<
{text}
>>>

SUBJECTS in the UPSC notes repository:
{subjects}

Fill in:
- "title": a short, specific note title (max 12 words). Keep official names of schemes, acts, bills, reports, places, species.
- "summary": 1–2 sentences on what the text covers.
- "keywords": 3–8 key terms (schemes, acts, constitutional articles, organisations, places, species, reports).
- "relevant": false ONLY if this is not study material: table of contents, cover, advertisement, course/admission promotion, editorial board, index, answer key, or bare practice MCQs. Otherwise true.
- "not_relevant_reason": short reason when relevant is false, else "".
- "subject": the single best subject folder for this topic.
- "secondary_subject": another plausible subject folder, or "none"."""


def triage_schema(subjects: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "relevant": {"type": "boolean"},
            "not_relevant_reason": {"type": "string"},
            "subject": {"type": "string", "enum": subjects},
            "secondary_subject": {"type": "string", "enum": subjects + ["none"]},
        },
        "required": ["title", "summary", "keywords", "relevant", "not_relevant_reason", "subject", "secondary_subject"],
    }


# ------------------------------------------------------------------ placement
LOCATE_PROMPT = """Decide where this UPSC topic belongs in an existing notes repository.

TOPIC: {title}
SUMMARY: {summary}
KEYWORDS: {keywords}
EXCERPT:
<<<
{excerpt}
>>>

FOLDERS (Subject › Module — examples of notes already inside):
{folders}

EXISTING NOTES most similar to this topic:
{notes}

Rules:
1. "folder": the single most appropriate FOLDER id for this topic.
2. "same_as": the id of an existing note ONLY if it is about the SAME specific subject (same scheme / act / institution / event / species / concept), so the new information should be added to that note. If a note is merely related, broader or narrower, answer "none".
3. "confidence": 0.0–1.0, how sure you are about the folder.
4. "reason": one short sentence."""


def locate_schema(folder_ids: list[str], note_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "reason": {"type": "string"},
            "folder": {"type": "string", "enum": folder_ids},
            "same_as": {"type": "string", "enum": note_ids + ["none"]},
            "confidence": {"type": "number"},
        },
        "required": ["reason", "folder", "same_as", "confidence"],
    }


# ------------------------------------------------------------------ note writing
SYSTEM_WRITER = """You are an expert UPSC Civil Services mentor who writes concise, exam-oriented revision notes.
Rules you must follow:
- Use ONLY information present in the SOURCE. Never add facts, numbers, dates, names or examples that are not in the SOURCE.
- Preserve every exam-relevant specific: names of schemes, acts, bills, committees, reports, indices, organisations; constitutional articles, schedules and sections; judgments and case names; numbers, percentages, rankings, dates, places, ministries.
- Remove repetition, rhetoric, filler, promotional text and anything not useful for the exam.
- Write crisp bullet points, not long paragraphs. Bold the key terms. Use Markdown tables only for comparisons that exist in the SOURCE.
- Output GitHub-flavoured Markdown only — no preamble, no closing remarks."""

NOTE_PROMPT = """Write UPSC revision notes on the topic below.

TOPIC: {title}
FILED UNDER: {location}
SOURCE ({words} words):
<<<
{source}
>>>

Use these sections, in this order, and OMIT any section for which the SOURCE has no material (never invent content to fill a section):
## Why in News
## Key Facts for Prelims
## Background
## Significance
## Challenges and Concerns
## Way Forward
## Practice Question   (only if the SOURCE contains one — copy it)
You may add other "## " sections when the SOURCE has important material that fits none of the above (e.g. "## Key Provisions", "## Comparison").

Length: about {target_words} words (never more than {max_words}). Start directly with the first "## " heading. Do not write a title."""

UPDATE_PROMPT = """An existing UPSC note already covers this topic. Add ONLY the new, exam-relevant information from the NEW SOURCE.

EXISTING NOTE (reference only — do NOT repeat anything it already says):
<<<
{existing}
>>>

NEW SOURCE — {tag}:
<<<
{source}
>>>

Write the new information as Markdown using "### " headings, for example:
### Why in News
### Key Updates
### Significance / Challenges / Way Forward (only if the NEW SOURCE has something new for them)

Only use facts from the NEW SOURCE. Keep it under {max_words} words.
If the NEW SOURCE adds nothing that is not already in the EXISTING NOTE, reply with exactly: NO_NEW_INFORMATION"""

CONDENSE_PROMPT = """This is part {part} of {parts} of a long article titled "{title}".
List every exam-relevant fact in it as concise bullet points: keep all names, numbers, dates, constitutional articles, schemes, reports, places and arguments (causes, impacts, challenges, way forward). No commentary, no introduction.

<<<
{chunk}
>>>"""
