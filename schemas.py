from datetime import datetime
from ipaddress import IPv4Address

from pydantic import BaseModel, Field, model_validator


class NetworkTargetStatus(BaseModel):
    ip: IPv4Address
    online: bool


class NetworkStatusReport(BaseModel):
    targets: list[NetworkTargetStatus] = Field(max_length=64)


class EventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start_time: datetime
    end_time: datetime | None = None
    category: str = Field(default="general", max_length=40)
    description: str = Field(default="", max_length=2000)
    is_secret: bool = False

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end_time:
            start_is_aware = self.start_time.utcoffset() is not None
            end_is_aware = self.end_time.utcoffset() is not None
            if start_is_aware != end_is_aware:
                raise ValueError("start_time and end_time must use matching time zones")
            if self.end_time <= self.start_time:
                raise ValueError("end_time must be after start_time")
        return self
