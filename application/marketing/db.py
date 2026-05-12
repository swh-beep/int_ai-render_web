import os
from functools import lru_cache
from urllib.parse import quote_plus

from botocore.exceptions import ClientError
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from infrastructure.aws.ssm import create_ssm_client


QA_PASSWORD_PARAM = "/config/intea-api_qa/spring.datasource.password"
REAL_PASSWORD_PARAM = "/config/intea-api_real/spring.datasource.password"


class MarketingDatabaseConfigError(RuntimeError):
    """Raised when marketing DB configuration cannot be resolved safely."""


def _profile_name() -> str:
    return (os.getenv("SPRING_PROFILES_ACTIVE") or os.getenv("APP_PROFILE") or os.getenv("MARKETING_DB_PROFILE") or "qa").strip()


def _ssm_param_for_profile(profile: str) -> str:
    if profile == "real":
        return REAL_PASSWORD_PARAM
    return QA_PASSWORD_PARAM


def _host_for_profile(profile: str) -> str:
    if profile == "real":
        return "intea-database.ctgf3hglxwxb.ap-northeast-2.rds.amazonaws.com"
    return "intea-database-stage.ctgf3hglxwxb.ap-northeast-2.rds.amazonaws.com"


def _read_ssm_parameter(name: str) -> str:
    client = create_ssm_client()
    try:
        response = client.get_parameter(Name=name, WithDecryption=True)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "ClientError")
        raise MarketingDatabaseConfigError(
            "Marketing DB password could not be read from AWS SSM. "
            f"Set MARKETING_DB_PASSWORD locally or grant ssm:GetParameter for {name}. "
            f"AWS error: {code}"
        ) from exc
    value = ((response.get("Parameter") or {}).get("Value") or "").strip()
    if not value:
        raise MarketingDatabaseConfigError(f"SSM parameter is empty: {name}")
    return value


def build_marketing_database_url() -> str:
    explicit_url = (os.getenv("MARKETING_REELS_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    if explicit_url:
        return explicit_url

    profile = _profile_name()
    host = os.getenv("MARKETING_DB_HOST") or _host_for_profile(profile)
    port = os.getenv("MARKETING_DB_PORT") or "3306"
    database = os.getenv("MARKETING_DB_NAME") or "interiorteacher"
    username = os.getenv("MARKETING_DB_USER") or "intea"
    password = os.getenv("MARKETING_DB_PASSWORD") or _read_ssm_parameter(_ssm_param_for_profile(profile))

    return f"mysql+pymysql://{quote_plus(username)}:{quote_plus(password)}@{host}:{port}/{database}?charset=utf8mb4"


@lru_cache(maxsize=1)
def get_marketing_engine() -> Engine:
    return create_engine(build_marketing_database_url(), pool_pre_ping=True, future=True)


def reset_marketing_engine_cache() -> None:
    get_marketing_engine.cache_clear()
