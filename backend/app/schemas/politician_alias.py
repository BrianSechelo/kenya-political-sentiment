from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PoliticianAliasBase(BaseModel):
    alias: str
    alias_type: str | None = None


class PoliticianAliasCreate(PoliticianAliasBase):
    politician_id: int

class PoliticianAliasUpdate(BaseModel):
    alias: str | None = None
    alias_type: str | None = None


class PoliticianAliasResponse(PoliticianAliasBase):
    alias_id: int
    politician_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)