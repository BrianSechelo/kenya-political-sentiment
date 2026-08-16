from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PoliticianBase(BaseModel):
    full_name: str
    slug: str
    party: str | None = None
    current_position: str | None = None
    profile_image_url: str | None = None
    is_active: bool = True


class PoliticianCreate(PoliticianBase):
    pass

class PoliticianUpdate(BaseModel):
    full_name: str | None = None
    slug: str | None = None
    party: str | None = None
    current_position: str | None = None
    profile_image_url: str | None = None
    is_active: bool | None = None


class PoliticianResponse(PoliticianBase):
    politician_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)