from pydantic import BaseModel
from typing import Optional, Dict, Any


class Document(BaseModel):
    id: Optional[int] = None
    data: Dict[str, Any]


class DeleteResult(BaseModel):
    success: bool
    message: str


class RabbitMQMessage(BaseModel):
    id: int
    changeType: str
    name: Optional[str] = None
    source: Optional[str] = None

    class Config:
        populate_by_name = True
