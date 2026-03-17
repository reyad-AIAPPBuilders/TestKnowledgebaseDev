"""Content classifier for municipality documents.

Uses OpenAI as primary classifier with rule-based keyword scoring as fallback.
Extracts structured entities (dates, deadlines, amounts, contacts, departments)
via regex patterns regardless of which classifier is used.
"""

import re

from app.services.intelligence.llm_classifier import LLMClassifier
from app.services.intelligence.models import (
    ClassifyResult,
    ContentCategory,
    ExtractedEntities,
)
from app.utils.logger import get_logger

log = get_logger(__name__)

# ── Keyword sets per category (German + English) ─────────────────────

CATEGORY_KEYWORDS: dict[ContentCategory, list[str]] = {
    ContentCategory.FUNDING: [
        "förderung", "förderprogramm", "förderantrag", "zuschuss", "subvention",
        "beihilfe", "fördermittel", "förderbar", "antragsfrist", "förderhöhe",
        "funding", "grant", "subsidy", "financial aid", "application deadline",
    ],
    ContentCategory.EVENT: [
        "veranstaltung", "termin", "einladung", "fest", "feier", "konzert",
        "ausstellung", "workshop", "vortrag", "seminar", "markt", "flohmarkt",
        "event", "ceremony", "exhibition", "festival", "celebration",
    ],
    ContentCategory.POLICY: [
        "verordnung", "erlass", "beschluss", "richtlinie", "satzung",
        "gesetz", "paragraph", "gemeinderatsbeschluss", "vorschrift",
        "policy", "regulation", "ordinance", "decree", "statute", "bylaw",
    ],
    ContentCategory.CONTACT: [
        "kontakt", "ansprechpartner", "telefon", "e-mail", "öffnungszeiten",
        "sprechstunde", "erreichbar", "zuständig", "servicezeiten", "bürgerservice",
        "contact", "phone", "email", "office hours", "reach us",
    ],
    ContentCategory.FORM: [
        "formular", "antrag", "antragsformular", "ausfüllen", "einreichen",
        "beiblatt", "vordruck", "meldezettel", "anmeldung",
        "form", "application form", "submit", "fill out", "download form",
    ],
    ContentCategory.ANNOUNCEMENT: [
        "bekanntmachung", "mitteilung", "information", "hinweis", "ankündigung",
        "pressemitteilung", "kundmachung", "verlautbarung", "amtliche",
        "announcement", "notice", "press release", "public notice",
    ],
    ContentCategory.MINUTES: [
        "protokoll", "sitzungsprotokoll", "niederschrift", "tagesordnung",
        "gemeinderatssitzung", "ausschusssitzung", "abstimmung", "beschlussfassung",
        "minutes", "meeting minutes", "agenda", "proceedings", "resolution",
    ],
    ContentCategory.REPORT: [
        "bericht", "jahresbericht", "tätigkeitsbericht", "rechenschaftsbericht",
        "statistik", "auswertung", "analyse", "bilanz", "evaluation",
        "report", "annual report", "statistics", "analysis", "assessment",
    ],
}

# ── Sub-category keywords ────────────────────────────────────────────

SUB_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "renewable_energy": ["erneuerbare energie", "solar", "photovoltaik", "windkraft", "renewable"],
    "subsidy": ["zuschuss", "subvention", "beihilfe", "subsidy", "grant"],
    "housing": ["wohnung", "wohnbau", "miete", "housing", "rent", "apartment"],
    "education": ["schule", "bildung", "kindergarten", "education", "school"],
    "environment": ["umwelt", "klima", "nachhaltigkeit", "environment", "climate", "sustainability"],
    "infrastructure": ["straße", "kanal", "infrastruktur", "verkehr", "infrastructure", "road"],
    "social": ["sozial", "pflege", "betreuung", "jugend", "senioren", "social", "care"],
    "culture": ["kultur", "museum", "bibliothek", "theater", "culture", "library"],
    "sports": ["sport", "sportplatz", "verein", "turnhalle", "schwimmbad"],
    "digitalization": ["digital", "e-government", "online-service", "digitalisierung"],
}

# ── Entity extraction patterns ───────────────────────────────────────

DATE_PATTERNS = [
    r"\b(\d{1,2}\.\s?\d{1,2}\.\s?\d{4})\b",                # 01.04.2025
    r"\b(\d{1,2}\.\s?(?:Jänner|Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s+\d{4})\b",
    r"\b(\d{4}-\d{2}-\d{2})\b",                              # 2025-04-01
]

DEADLINE_INDICATORS = [
    "frist", "deadline", "bis zum", "bis spätestens", "einreichfrist",
    "antragsfrist", "abgabefrist", "stichtag", "ablauf",
]

AMOUNT_PATTERNS = [
    r"(EUR\s?[\d.,]+)",
    r"(€\s?[\d.,]+)",
    r"([\d.,]+\s?(?:Euro|EUR|€))",
]

EMAIL_PATTERN = r"[\w.+-]+@[\w-]+\.[\w.-]+"

DEPARTMENT_KEYWORDS = [
    "amt", "abteilung", "referat", "dienststelle", "magistrat",
    "bezirksamt", "stadtamt", "gemeindeamt", "bürgerservice",
    "department", "office", "division",
]


class Classifier:
    """Content classifier — OpenAI primary, rule-based fallback."""

    def __init__(self) -> None:
        self._llm = LLMClassifier()
        self._llm.startup()

    async def classify(self, content: str, language: str = "de") -> ClassifyResult:
        # Try OpenAI first
        if self._llm.is_available():
            try:
                result = await self._llm.classify(content, language=language)
                log.info("classify_via_llm", category=result.category.value)
                return result
            except Exception as exc:
                log.warning("llm_classify_failed_falling_back", error=str(exc))

        # Fallback to rule-based
        return await self._classify_rule_based(content)

    async def _classify_rule_based(self, content: str) -> ClassifyResult:
        content_lower = content.lower()

        category, confidence = self._score_categories(content_lower)
        sub_categories = self._detect_sub_categories(content_lower)
        entities = self._extract_entities(content, content_lower)
        summary = self._generate_summary(content, category)

        log.info(
            "classify_via_rules",
            category=category.value,
            confidence=round(confidence, 2),
            sub_categories=sub_categories,
            entity_count=sum([
                len(entities.dates), len(entities.deadlines),
                len(entities.amounts), len(entities.contacts),
                len(entities.departments),
            ]),
        )

        return ClassifyResult(
            category=category,
            confidence=round(confidence, 3),
            sub_categories=sub_categories,
            entities=entities,
            summary=summary,
        )

    def _score_categories(self, text: str) -> tuple[ContentCategory, float]:
        scores: dict[ContentCategory, int] = {}

        for category, keywords in CATEGORY_KEYWORDS.items():
            score = 0
            for kw in keywords:
                count = text.count(kw)
                score += count
            scores[category] = score

        total = sum(scores.values())
        if total == 0:
            return ContentCategory.GENERAL, 0.3

        best_category = max(scores, key=scores.get)  # type: ignore[arg-type]
        best_score = scores[best_category]

        confidence = min(best_score / max(total, 1) + 0.3, 0.99)

        # Boost confidence if many keyword hits
        if best_score >= 5:
            confidence = min(confidence + 0.1, 0.99)

        return best_category, confidence

    def _detect_sub_categories(self, text: str) -> list[str]:
        found = []
        for sub_cat, keywords in SUB_CATEGORY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                found.append(sub_cat)
        return found[:5]  # Cap at 5

    def _extract_entities(self, text: str, text_lower: str) -> ExtractedEntities:
        dates = self._extract_dates(text)
        deadlines = self._extract_deadlines(text, text_lower, dates)
        amounts = self._extract_amounts(text)
        contacts = self._extract_contacts(text)
        departments = self._extract_departments(text_lower)

        return ExtractedEntities(
            dates=dates[:10],
            deadlines=deadlines[:5],
            amounts=amounts[:10],
            contacts=contacts[:10],
            departments=departments[:5],
        )

    def _extract_dates(self, text: str) -> list[str]:
        dates = []
        for pattern in DATE_PATTERNS:
            dates.extend(re.findall(pattern, text, re.IGNORECASE))
        return list(dict.fromkeys(dates))  # Deduplicate, preserve order

    def _extract_deadlines(self, text: str, text_lower: str, dates: list[str]) -> list[str]:
        deadlines = []
        for indicator in DEADLINE_INDICATORS:
            if indicator in text_lower:
                # Find dates near the deadline indicator
                for date in dates:
                    idx_indicator = text_lower.find(indicator)
                    idx_date = text.find(date)
                    if abs(idx_indicator - idx_date) < 200:
                        deadlines.append(date)
        return list(dict.fromkeys(deadlines))

    def _extract_amounts(self, text: str) -> list[str]:
        amounts = []
        for pattern in AMOUNT_PATTERNS:
            amounts.extend(re.findall(pattern, text))
        return list(dict.fromkeys(amounts))

    def _extract_contacts(self, text: str) -> list[str]:
        return list(dict.fromkeys(re.findall(EMAIL_PATTERN, text)))

    def _extract_departments(self, text_lower: str) -> list[str]:
        departments = []
        for keyword in DEPARTMENT_KEYWORDS:
            # Find capitalized department names near the keyword
            pattern = rf"([A-ZÄÖÜ][\wäöüß]*{keyword}[\wäöüß]*)"
            matches = re.findall(pattern, text_lower, re.IGNORECASE)
            departments.extend(matches)

        # Also look for standalone department names with common patterns
        pattern = r"((?:Amt|Abteilung|Referat|Magistrat|Dienststelle)\s+(?:für\s+)?[\wäöüß\s]+?)(?:[,.\n])"
        departments.extend(re.findall(pattern, text_lower, re.IGNORECASE))

        # Deduplicate and clean
        cleaned = []
        seen = set()
        for d in departments:
            d = d.strip()
            if d and d.lower() not in seen and len(d) > 3:
                seen.add(d.lower())
                cleaned.append(d)
        return cleaned

    def _generate_summary(self, text: str, category: ContentCategory) -> str:
        # Take the first meaningful sentence(s) up to ~200 chars
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if not lines:
            return ""

        summary_parts = []
        total_len = 0
        for line in lines:
            # Skip very short lines (headers, etc.)
            if len(line) < 10:
                continue
            summary_parts.append(line)
            total_len += len(line)
            if total_len >= 200:
                break

        summary = " ".join(summary_parts)
        if len(summary) > 300:
            summary = summary[:297] + "..."
        return summary
