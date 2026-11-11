from typing import Annotated, Literal
import re

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
BodyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=550)]
Component = Literal["hero", "benefits", "cta"]
COMPONENT_ORDER = ["hero", "benefits", "cta"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Brief(StrictModel):
    campaign: ShortText
    audience: ShortText
    goal: BodyText
    points: Annotated[list[ShortText], Field(min_length=2, max_length=4)]
    action: ShortText
    action_path: Annotated[str, StringConstraints(max_length=180)]

    @field_validator("action_path")
    @classmethod
    def internal_path(cls, value: str) -> str:
        if not re.fullmatch(r"/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]*", value):
            raise ValueError("Use an internal path such as /contact or /services/consulting")
        return value


class DesignRef(StrictModel):
    file_key: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9]{8,80}$")]
    node_id: Annotated[str, StringConstraints(pattern=r"^\d+:\d+$", max_length=30)]


class CreateRun(StrictModel):
    brief: Brief
    design: DesignRef | None = None


class Section(StrictModel):
    component: Component
    heading: ShortText
    body: BodyText
    points: Annotated[list[ShortText], Field(max_length=4)]

    @model_validator(mode="after")
    def check_component_fields(self):
        if self.component == "benefits":
            if not 2 <= len(self.points) <= 4:
                raise ValueError("Benefits requires two to four points")
        elif self.points:
            raise ValueError("Only Benefits uses points")
        return self


class PagePlan(StrictModel):
    title: ShortText
    sections: Annotated[list[Section], Field(min_length=3, max_length=3)]

    @model_validator(mode="after")
    def check_component_order(self):
        if [section.component for section in self.sections] != COMPONENT_ORDER:
            raise ValueError("Expected the Hero, Benefits and Call to action sections in design order")
        return self


class ApproveRun(StrictModel):
    revision: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class DeliverRun(StrictModel):
    simulate_lost_reply: bool = False
