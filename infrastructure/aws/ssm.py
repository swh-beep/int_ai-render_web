import os

import boto3


def create_ssm_client():
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-northeast-2"
    access_key = (os.getenv("AWS_SSM_ACCESS_KEY_ID") or "").strip()
    secret_key = (
        os.getenv("AWS_SSM_SECRET_ACCESS_KEY")
        or os.getenv("AWS_SSM_SECRET_KEY_ID")
        or ""
    ).strip()
    session_token = (os.getenv("AWS_SSM_SESSION_TOKEN") or "").strip()

    kwargs = {"region_name": region}
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
        if session_token:
            kwargs["aws_session_token"] = session_token

    return boto3.client("ssm", **kwargs)
