"""
POST /api/v1/classify — Classify content and extract entities.
"""

from fastapi import APIRouter, Request

from app.models.classify import ClassifyData, ClassifyRequest
from app.models.classify import ExtractedEntities as ClassifyEntities
from app.models.common import ErrorCode, ResponseEnvelope
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Content Intelligence"])


@router.post(
    "/classify",
    summary="Classify content and extract entities",
    description="Classifies municipality content into one of 9 categories and extracts structured entities.\n\n**Categories:** `funding`, `event`, `policy`, `contact`, `form`, `announcement`, `minutes`, `report`, `general`\n\n**Extracted entities:**\n- **Dates**: All dates in German/ISO formats\n- **Deadlines**: Dates near deadline-indicating words\n- **Amounts**: Monetary values (EUR, €)\n- **Contacts**: Email addresses\n- **Departments**: Municipal department names\n\nAlso returns sub-categories (e.g. `renewable_energy`, `housing`) and an auto-generated summary.\n\n**Error codes:** `VALIDATION_EMPTY_CONTENT`, `CLASSIFY_FAILED`",
    response_description="Classification result with confidence score, entities, and summary",
)
async def classify(body: ClassifyRequest, request: Request) -> ResponseEnvelope[ClassifyData]:
    request_id = request.state.request_id
    classifier = request.app.state.classifier

    if not body.content.strip():
        return ResponseEnvelope(
            success=False,
            error=ErrorCode.VALIDATION_EMPTY_CONTENT,
            detail="Content must not be empty",
            request_id=request_id,
        )

    try:
        result = await classifier.classify(body.content, language=body.language)
    except Exception as e:
        log.error("classify_endpoint_error", error=str(e), request_id=request_id)
        return ResponseEnvelope(
            success=False,
            error=ErrorCode.CLASSIFY_FAILED,
            detail=str(e),
            request_id=request_id,
        )

    return ResponseEnvelope(
        success=True,
        data=ClassifyData(
            classification=result.category.value,
            confidence=result.confidence,
            sub_categories=result.sub_categories,
            entities=ClassifyEntities(
                dates=result.entities.dates,
                deadlines=result.entities.deadlines,
                amounts=result.entities.amounts,
                contacts=result.entities.contacts,
                departments=result.entities.departments,
            ),
            summary=result.summary,
        ),
        request_id=request_id,
    )
