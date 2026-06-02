from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from pydantic import BaseModel

from alarm_manager_server.api.errors import register_exception_handlers
from alarm_manager_server.config import settings
from alarm_manager_server.models.incident import GroupingResult, ProcessedIncident
from alarm_manager_server.services.processor import AlarmProcessor


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    if _processor is not None:
        _processor.persist_caches()
        await _processor.client.aclose()


app = FastAPI(
    title="Alarm Manager Server",
    description="Server-side alarm grouping and responsible-party resolution",
    version="0.1.0",
    lifespan=lifespan,
)
register_exception_handlers(app)


class GroupingResponse(BaseModel):
    children_of: dict[str, list[str]]
    parent_of: dict[str, str]
    parent_title_of: dict[str, str]


class ProcessResponse(BaseModel):
    incidents: list[ProcessedIncident]
    grouping: GroupingResponse
    total: int
    visible_count: int


_processor: AlarmProcessor | None = None


def get_processor() -> AlarmProcessor:
    global _processor
    if _processor is None:
        _processor = AlarmProcessor()
    return _processor


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config")
async def get_config() -> dict:
    return settings.model_dump(exclude={"saymon_password"})


@app.post("/process", response_model=ProcessResponse)
async def process_incidents(
    resolve_macros: bool = Query(default=True, description="Resolve responsible-party macros"),
) -> ProcessResponse:
    processor = get_processor()
    incidents = await processor.fetch_incidents()
    grouping = await processor.compute_grouping(incidents)

    class_ids = await processor.get_class_ids()
    from alarm_manager_server.services.grouping import build_synthetic_incidents, group_by_class

    _, synthetic_seeds = group_by_class(
        incidents, processor.store, class_ids, processor.cfg.group_by_depth
    )
    await processor._enrich_synthetic_seed_names(synthetic_seeds)
    synthetic = build_synthetic_incidents(synthetic_seeds, {i.id: i for i in incidents})
    all_incidents = synthetic + incidents

    if resolve_macros:
        macro_targets = [i for i in all_incidents if not i.is_synthetic]
        responsible = await processor.resolve_responsible_parties(macro_targets)
    else:
        responsible = {}

    await processor._prefetch_display_names(all_incidents)
    state_labels = await processor.get_state_labels()

    processed: list[ProcessedIncident] = []
    for inc in all_incidents:
        processed.append(
            await processor._to_processed_incident(
                inc,
                grouping,
                responsible.get(inc.id) if not inc.is_synthetic else None,
                state_labels,
            )
        )

    visible = processor.visible_rows(processed, grouping)
    processor.persist_caches()

    return ProcessResponse(
        incidents=processed,
        grouping=GroupingResponse(
            children_of=grouping.children_of,
            parent_of=grouping.parent_of,
            parent_title_of=grouping.parent_title_of,
        ),
        total=len(processed),
        visible_count=len(visible),
    )


@app.post("/grouping", response_model=GroupingResponse)
async def compute_grouping_only() -> GroupingResponse:
    processor = get_processor()
    incidents = await processor.fetch_incidents()
    grouping = await processor.compute_grouping(incidents)
    processor.persist_caches()
    return GroupingResponse(
        children_of=grouping.children_of,
        parent_of=grouping.parent_of,
        parent_title_of=grouping.parent_title_of,
    )
