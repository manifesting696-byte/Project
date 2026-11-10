"""Request and response bodies accepted by the HTTP routes."""

from pydantic import BaseModel, Field


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    session_id: str | None = None
    constraints: dict = Field(default_factory=dict)
    start: str | None = None
    end: str | None = None
    pause_after_batch: bool = False


class Credentials(BaseModel):
    username: str
    password: str


class NewUser(Credentials):
    role: str = "analyst"


class PasswordChange(BaseModel):
    old_password: str
    new_password: str


class Resume(BaseModel):
    continue_analysis: bool = True
    answer: str | None = Field(default=None, min_length=1, max_length=4000)


class Feedback(BaseModel):
    audit_id: int | None = None
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=2000)


class PluginLoad(BaseModel):
    id: str = Field(min_length=1)
    plugin: str | None = None
    config: dict = Field(default_factory=dict)


class PluginUnload(BaseModel):
    cascade: bool = False


class PluginReload(BaseModel):
    config: dict | None = None


class ProfileReload(BaseModel):
    profile: str | None = None


class TableRelation(BaseModel):
    from_field: str = Field(alias="from", min_length=1)
    to_field: str = Field(alias="to", min_length=1)


class TableEnrollment(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4000)
    column_meanings: dict[str, str] | None = None
    relations: list[TableRelation] | None = None
    expected_version: str | None = None


class CatalogRefresh(BaseModel):
    expected_version: str | None = None
