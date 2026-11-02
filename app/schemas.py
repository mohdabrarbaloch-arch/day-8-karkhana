"""Pydantic schemas — the API contract, and the first line of input validation."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

MAX_GPUS_PER_REQUEST = 64
MAX_PRIORITY = 10
MIN_PRIORITY = 1

Span = Literal["node", "any"]


# --------------------------------------------------------------------------- auth
class UserCreate(BaseModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=8, max_length=72)]
    full_name: Annotated[str, Field(min_length=1, max_length=120)] = ""
    team: Annotated[str, Field(min_length=1, max_length=80)] = "platform"

    @field_validator("password")
    @classmethod
    def _password_has_variety(cls, value: str) -> str:
        # Length beats complexity rules, but an 8-character all-lowercase
        # password is still guessable, so require two character classes.
        classes = sum(
            [
                any(c.islower() for c in value),
                any(c.isupper() for c in value),
                any(c.isdigit() for c in value),
                any(not c.isalnum() for c in value),
            ]
        )
        if classes < 2:
            raise ValueError("password must mix at least two of: lower, upper, digits, symbols")
        return value


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    role: str
    team: str
    created_at: datetime


class LoginRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=72)]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserRead


# --------------------------------------------------------------------------- nodes
class NodeCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")]
    gpu_model: Annotated[str, Field(min_length=1, max_length=64)]
    total_gpus: Annotated[int, Field(ge=1, le=256)]
    memory_gb: Annotated[int, Field(ge=1, le=4096)] = 80
    region: Annotated[str, Field(min_length=1, max_length=32)] = "local"
    cluster_name: Annotated[str, Field(min_length=1, max_length=64)] = "default"


class NodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    cluster_name: str
    gpu_model: str
    total_gpus: int
    allocated_gpus: int
    memory_gb: int
    region: str
    healthy: bool
    created_at: datetime


class NodeHealthUpdate(BaseModel):
    healthy: bool


# --------------------------------------------------------------------------- jobs
class JobCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    command: Annotated[str, Field(max_length=4000)] = ""
    gpus: Annotated[int, Field(ge=1, le=MAX_GPUS_PER_REQUEST)]
    gpu_model: Annotated[str | None, Field(max_length=64)] = None
    span: Span = "node"
    priority: Annotated[int, Field(ge=MIN_PRIORITY, le=MAX_PRIORITY)] = 5
    duration_minutes: Annotated[int, Field(ge=1, le=60 * 24 * 14)] = 60
    project: Annotated[str, Field(min_length=1, max_length=80)] = "default"

    @field_validator("name", "project", "command")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    project: str
    command: str
    gpus: int
    gpu_model: str | None
    span: str
    priority: int
    duration_minutes: int
    status: str
    node_id: int | None
    submitted_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    exit_code: int | None
    last_reason: str


class JobEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event: str
    message: str
    actor: str
    at: datetime


class JobDetail(JobRead):
    events: list[JobEventRead] = []


# --------------------------------------------------------------------------- scheduling
class DeferralRead(BaseModel):
    job_id: int
    reason: str


class DecisionRead(BaseModel):
    job_id: int
    node: str
    gpus: int
    reason: str


class ScheduleResult(BaseModel):
    scheduled: list[DecisionRead]
    deferred: list[DeferralRead]
    unplaceable: list[DeferralRead]
    reservation_minutes: int | None
    head_job_id: int | None
    message: str


# --------------------------------------------------------------------------- dashboard
class NodeUtilisation(BaseModel):
    name: str
    gpu_model: str
    total_gpus: int
    allocated_gpus: int
    free_gpus: int
    utilisation_percent: float
    healthy: bool
    region: str


class Dashboard(BaseModel):
    total_gpus: int
    allocated_gpus: int
    free_gpus: int
    utilisation_percent: float
    nodes: int
    healthy_nodes: int
    jobs_queued: int
    jobs_running: int
    jobs_completed: int
    jobs_failed: int
    gpu_hours_today: float
    cost_today: float
    per_node: list[NodeUtilisation]


class TeamCharge(BaseModel):
    team: str
    jobs: int
    gpu_hours: float
    cost: float
    job_ids: list[int]


class ChargebackReport(BaseModel):
    period_start: datetime
    period_end: datetime
    total_gpu_hours: float
    total_cost: float
    teams: list[TeamCharge]


class MessageResponse(BaseModel):
    message: str
