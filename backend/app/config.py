from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://review:review@localhost:5432/review"
    jwt_secret: str = "dev-secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12
    admin_username: str = "admin"
    admin_password: str = "admin123"
    reviewer_password: str = "reviewer123"
    # business limits
    max_manuscripts_per_round: int = 20
    max_reviewers_per_round: int = 10
    max_assignments_per_reviewer: int = 3

    model_config = {"env_prefix": "", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
