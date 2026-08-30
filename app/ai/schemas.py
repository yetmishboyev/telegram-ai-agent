"""JSON javoblar uchun sxemalar (`output_config.format`).

Ilgari model JSON'ni matn ichida qaytarardi va uni `json_parse.py` qo'lda
ajratib olardi — model JSONdan keyin izoh yozsa yoki ```json qatlamiga o'rasa
parse buzilardi. Sxema berilganda API javob shaklini kafolatlaydi.

Sxemalar Pydantic modellaridan chiqarilmadi, qo'lda yozildi: Pydantic
`$ref`/`$defs` ishlatadi va enum'larni alohida ta'rifga chiqaradi, bu esa
kerakmas murakkablik. Bu yerdagi tekis sxemalar hujjat vazifasini ham bajaradi.
"""

# ─── xabar tahlili (analysis_agent) ───────────────────────────────────────────
MESSAGE_ANALYSIS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]},
        "intent": {"type": "string"},
        "importance": {"type": "number", "minimum": 0, "maximum": 1},
        "threat_level": {"type": "string", "enum": ["none", "low", "medium", "high"]},
        "is_spam": {"type": "boolean"},
        "is_phishing": {"type": "boolean"},
        "is_manipulative": {"type": "boolean"},
        "is_toxic": {"type": "boolean"},
        "should_respond": {"type": "boolean"},
        "response_priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
        "detected_language": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": [
        "sentiment", "intent", "importance", "threat_level", "is_spam",
        "is_phishing", "is_manipulative", "is_toxic", "confidence",
    ],
    "additionalProperties": False,
}

# ─── fakt ajratish (analysis_agent) ───────────────────────────────────────────
# API ildizda obyekt kutadi, shuning uchun ro'yxat `facts` kaliti ichida.
EXTRACTED_FACTS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["personal", "work", "promise", "event", "preference", "fact"],
                    },
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                    "importance": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["category", "key", "value"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["facts"],
    "additionalProperties": False,
}

# ─── klassifikatsiya (classifier_agent) ───────────────────────────────────────
CLASSIFICATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["important", "greeting", "simple", "general"],
        },
        "language": {"type": "string", "enum": ["uz", "ru", "en", "other"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "should_notify_owner": {"type": "boolean"},
    },
    "required": ["category", "language", "confidence", "should_notify_owner"],
    "additionalProperties": False,
}

# ─── yangilik tanlash (news_fetcher.curate_top_news) ──────────────────────────
CURATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "index": {"type": "integer", "minimum": 0},
        "category": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["index", "category", "reason"],
    "additionalProperties": False,
}

# ─── quiz / so'rovnoma (news_fetcher.generate_quiz) ───────────────────────────
QUIZ_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4},
        "correct_index": {"type": "integer", "minimum": 0, "maximum": 3},
        "explanation": {"type": "string"},
    },
    "required": ["question", "options"],
    "additionalProperties": False,
}

POLL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4},
    },
    "required": ["question", "options"],
    "additionalProperties": False,
}

# ─── o'sish strategiyasi (news_fetcher.generate_growth_strategy) ──────────────
GROWTH_STRATEGY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "holat": {"type": "string"},
        "tavsiyalar": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "kontent_goyalar": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "keyingi_qadam": {"type": "string"},
    },
    "required": ["holat", "tavsiyalar", "kontent_goyalar", "keyingi_qadam"],
    "additionalProperties": False,
}
