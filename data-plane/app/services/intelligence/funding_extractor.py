"""OpenAI-based metadata extractor for funding (Förderung) documents.

Extracts structured fields such as title, region, target_group, funding_type,
status, funding_amount, etc. from funding content using structured JSON output.
"""

import json
from datetime import datetime, timezone

from openai import AsyncOpenAI

from app.config import ext
from app.utils.logger import get_logger

log = get_logger(__name__)

SYSTEM_PROMPT = """\
You are a metadata extractor for Austrian/German funding documents (Förderungen).

Given the text of a funding page, extract structured metadata.
Extract the VALUES in their original language (typically German), but use the
English field names shown below.
If a field cannot be determined from the text, use an empty string for strings,
an empty list for arrays, or null for nullable fields.

Respond ONLY with valid JSON matching this exact schema:
{
  "title": "<title of the funding program>",
  "region": ["<geographic regions/municipalities this applies to>"],
  "target_group": ["<target groups, e.g. Vereine, Privatpersonen, Unternehmen>"],
  "funding_type": "<funding type, e.g. Direkte Förderungen, Zuschuss, Darlehen>",
  "status": "<active | inactive | expiring | unknown>",
  "funding_amount": "<funding amount or range, e.g. bis EUR 5.000 or empty string if unknown>",
  "thematic_focus": ["<thematic focus areas, e.g. Sport, Umwelt, Bildung>"],
  "eligibility_criteria": "<eligibility criteria and application requirements>",
  "legal_basis": "<legal basis or regulation>",
  "funding_provider": ["<funding provider organizations>"],
  "reference_number": "<reference number or ID if found, otherwise null>",
  "start_date": "<start date in DD.MM.YYYY format or empty string>",
  "end_date": "<end date in DD.MM.YYYY format, or 'unbegrenzt', or empty string>"
}"""


class FundingExtractor:
    """Extracts structured metadata from funding documents via OpenAI."""

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None
        self._model = ext.openai_model

    def is_available(self) -> bool:
        return self._client is not None

    def startup(self) -> None:
        if not ext.openai_api_key:
            log.info("funding_extractor_disabled", reason="no OPENAI_API_KEY")
            return
        self._client = AsyncOpenAI(api_key=ext.openai_api_key)
        log.info("funding_extractor_started", model=self._model)

    async def extract(self, content: str, source_url: str = "") -> dict:
        """Extract funding metadata from content. Returns a flat dict."""
        if not self._client:
            raise RuntimeError("Funding extractor not available (no OpenAI key)")

        truncated = content[:6000] if len(content) > 6000 else content

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": truncated},
            ],
            temperature=0.0,
            max_tokens=800,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)

        scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        result = {
            "title": str(data.get("title", "")),
            "region": _as_list(data.get("region", [])),
            "target_group": _as_list(data.get("target_group", [])),
            "funding_type": str(data.get("funding_type", "")),
            "status": _validated_status(data.get("status", "unknown")),
            "funding_amount": str(data.get("funding_amount", "")),
            "thematic_focus": _as_list(data.get("thematic_focus", [])),
            "eligibility_criteria": str(data.get("eligibility_criteria", "")),
            "legal_basis": str(data.get("legal_basis", "")),
            "funding_provider": _as_list(data.get("funding_provider", [])),
            "reference_number": data.get("reference_number"),
            "start_date": str(data.get("start_date", "")),
            "end_date": str(data.get("end_date", "")),
            "scraped_at": scraped_at,
        }

        log.info(
            "funding_metadata_extracted",
            title=result["title"][:80],
            status=result["status"],
            tokens_used=response.usage.total_tokens if response.usage else 0,
        )

        return result


def _as_list(val: object) -> list[str]:
    if isinstance(val, list):
        return [str(v) for v in val][:20]
    return []


def _validated_status(val: object) -> str:
    allowed = {"active", "inactive", "expiring", "unknown"}
    s = str(val).lower().strip()
    return s if s in allowed else "unknown"
