from pydantic import BaseModel, Field
from typing import Literal


class AppVersionCreate(BaseModel):
    version: str = Field(min_length=1, max_length=40)
    platform: Literal["android", "ios", "web"] = "android"
    expected_fields: list[str] = Field(default_factory=list)
    active_users: int = Field(default=0, ge=0, description="Optional observed count; 0 means unknown/not supplied")


class ChangeCheckRequest(BaseModel):
    endpoint: str = Field(default="/api/profile", min_length=1, max_length=200)
    current_fields: list[str] = Field(default_factory=list)
    proposed_fields: list[str] = Field(default_factory=list)
    description: str = Field(default="Proposed API schema change", max_length=500)
