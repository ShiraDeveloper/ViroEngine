"""The learning layer over HTTP: the curve, the seed and the approvals (PLAN §7, T24).

Everything here is counters, boxes, shapes and label texts; there is no value to leak. An approval
is an explicit human act (`POST .../approve`): until then a label synonym or a proposed institution
is stored but never used.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends

from backend.api.deps import get_container, require_api_key
from backend.api.errors import ProposalNotFound
from backend.container import Container
from backend.core.ports.learning import LearningAdminPort

router = APIRouter(tags=["learning"], dependencies=[Depends(require_api_key)])


def _admin(container: Container) -> LearningAdminPort | None:
    learning = container.learning
    return learning if isinstance(learning, LearningAdminPort) else None


@router.get("/metrics/learning")
async def metrics_learning(container: Container = Depends(get_container)) -> list[dict[str, Any]]:
    """The learning curve: one point per finished job, oldest first (`at`, `local_close_rate` and
    the counters behind it). Empty when nothing has run or the store keeps no history."""
    admin = _admin(container)
    return admin.run_metrics() if admin is not None else []


@router.get("/learning/seed")
async def learning_seed(container: Container = Depends(get_container)) -> dict[str, Any]:
    """The store exported as `learning_seed.json`: promoted boxes and shapes only, so a developer
    can commit it (PLAN §7.4)."""
    admin = _admin(container)
    return dict(admin.seed()) if admin is not None else {"layout_hints": [], "pattern_hints": []}


@router.get("/learning/proposals")
async def learning_proposals(container: Container = Depends(get_container)) -> dict[str, Any]:
    """Label synonyms and proposed institutions, with their hit counts and approval state."""
    admin = _admin(container)
    return dict(admin.proposals()) if admin is not None else {"labels": [], "senders": []}


@router.post("/learning/proposals/{kind}/{proposal_id}/approve")
async def approve_proposal(
    kind: Literal["label", "sender"],
    proposal_id: int,
    container: Container = Depends(get_container),
) -> dict[str, bool]:
    """Approve one proposal. Raises ProposalNotFound (404) when there is none."""
    admin = _admin(container)
    if admin is None or not admin.approve(kind, proposal_id):
        raise ProposalNotFound("no such proposal")
    return {"approved": True}
