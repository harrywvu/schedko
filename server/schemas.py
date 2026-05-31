from pydantic import BaseModel


class ScheduleRequest(BaseModel):
    hash: str
    classCode: str
