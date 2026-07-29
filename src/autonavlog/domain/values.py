from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .enums import AdoptedSource, ValueState

T = TypeVar("T")


class AdoptedValue(BaseModel, Generic[T]):
    """Stores automatic and manual values without destroying either provenance."""

    model_config = ConfigDict(extra="forbid")

    automatic_value: T | None = None
    automatic_status: ValueState = ValueState.UNAVAILABLE
    automatic_metadata: dict[str, Any] = Field(default_factory=dict)
    manual_override: T | None = None
    adopted_source: AdoptedSource | None = None
    warnings: tuple[str, ...] = ()

    def adopted(self) -> T | None:
        if self.adopted_source == AdoptedSource.MANUAL:
            return self.manual_override
        if self.adopted_source == AdoptedSource.AUTOMATIC:
            return self.automatic_value
        if self.manual_override is not None:
            return self.manual_override
        return self.automatic_value

    @property
    def state(self) -> ValueState:
        if self.adopted_source == AdoptedSource.MANUAL or (
            self.adopted_source is None and self.manual_override is not None
        ):
            return ValueState.MANUAL_OVERRIDE
        return self.automatic_status
