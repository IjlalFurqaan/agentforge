from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ItemStatus = Literal["todo", "in_progress", "done"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    created_at: datetime


class ItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ItemUpdate(BaseModel):
    status: ItemStatus


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    title: str
    status: ItemStatus
    created_at: datetime
