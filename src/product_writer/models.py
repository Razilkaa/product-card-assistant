from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4.1-mini"


class Product(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    manufacturer: str = Field(default="", max_length=200)
    specs: dict[str, str] = Field(default_factory=dict, max_length=40)
    description: str = Field(default="", max_length=12000)

    @field_validator("specs")
    @classmethod
    def clean_specs(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not k.strip() or len(k) > 200 or len(v) > 500 for k, v in value.items()):
            raise ValueError("Invalid specification name or value length")
        return {k.strip(): v.strip() for k, v in value.items()}


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    seo_title: str = Field(min_length=1, max_length=70)
    seo_description: str = Field(min_length=800, max_length=1200)
    search_queries: list[str] = Field(min_length=3, max_length=5)
    commercial: str = Field(min_length=1, max_length=1000)

    @field_validator("search_queries")
    @classmethod
    def check_queries(cls, value: list[str]) -> list[str]:
        if any(not q.strip() or len(q) > 200 for q in value):
            raise ValueError("Invalid search query")
        if len(set(value)) != len(value):
            raise ValueError("Duplicate search queries")
        return value


class Card(Draft):
    specifications: dict[str, str]
    missing_fields: list[str]
    status: str = "draft_requires_review"
