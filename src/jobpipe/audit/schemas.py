from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model


class QualifyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["keep", "borderline", "drop"]
    reason: str = Field(min_length=1, max_length=600)
    confidence: float = Field(ge=0.0, le=1.0)


class ApplyPathOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apply_path: Literal["form", "email", "external", "none", "unknown"]


def completeness_schema(required_fields: list[str]) -> type[BaseModel]:
    allowed = Literal[tuple(required_fields)]  # type: ignore[valid-type]
    return create_model(
        "CompletenessOutput",
        __config__=ConfigDict(extra="forbid"),
        missing_fields=(list[allowed], ...),  # type: ignore[valid-type]
    )
