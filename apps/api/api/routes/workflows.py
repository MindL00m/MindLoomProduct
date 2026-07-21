"""Workflow run HTTP routes: trigger and inspect OpenClaw browser runs."""

from fastapi import APIRouter, BackgroundTasks, HTTPException

from capture_service import list_skill_files
from models import SkillFileDraft, WorkflowRun
from openclaw_export import is_extension_skill
from workflow_runs import create_run, execute_run, get_run, list_runs

router = APIRouter(prefix="/workflows", tags=["workflows"])


def _load_skill(skill_id: str) -> SkillFileDraft:
    row = next((item for item in list_skill_files() if item["skill_id"] == skill_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Workflow was not found.")
    return SkillFileDraft.model_validate(row)


@router.post("/{skill_id}/runs", response_model=WorkflowRun, status_code=201)
async def start_run(skill_id: str, background_tasks: BackgroundTasks) -> WorkflowRun:
    """Create a run for an approved extension workflow and execute it in the background."""

    skill = _load_skill(skill_id)
    if not is_extension_skill(skill):
        raise HTTPException(status_code=400, detail="Only extension workflows can be run.")
    if skill.status != "approved":
        raise HTTPException(status_code=400, detail="Only approved workflows can be run.")
    run = create_run(skill)
    background_tasks.add_task(execute_run, run.run_id)
    return run


@router.get("/runs/{run_id}", response_model=WorkflowRun)
async def read_run(run_id: str) -> WorkflowRun:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run was not found.")
    return run


@router.get("/{skill_id}/runs", response_model=list[WorkflowRun])
async def read_runs_for_skill(skill_id: str) -> list[WorkflowRun]:
    return list_runs(skill_id=skill_id)
