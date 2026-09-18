import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

Tier = Literal["prime", "solid", "watch", "low", "reject"]
Source = Literal["osm", "sample"]
SourceStatus = Literal["live", "no_results", "unavailable"]


class RunCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    vertical: str = Field(min_length=2, max_length=100)
    market: str = Field(min_length=2, max_length=120)
    headcount_min: int = Field(default=5, ge=1)
    headcount_max: int = Field(default=50, ge=1)
    limit: int = 35

    @field_validator("limit")
    @classmethod
    def clamp_limit(cls, value: int) -> int:
        return min(100, max(5, value))

    @model_validator(mode="after")
    def check_headcount_band(self) -> Self:
        if self.headcount_min > self.headcount_max:
            raise PydanticCustomError(
                "headcount_band", "headcount_min cannot exceed headcount_max"
            )
        return self


class TargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    legal_name: str
    street_line: str | None
    locality: str | None
    region: str | None
    main_phone: str | None
    email: str | None
    web_url: str | None
    fit_score: float
    tier: Tier
    rationale: str
    skipped: bool
    source: Source


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vertical: str
    market: str
    headcount_min: int
    headcount_max: int
    source_status: SourceStatus
    source_detail: str | None
    created_at: datetime
    targets: list[TargetOut]
