from unittest.mock import patch

from infrastructure.aws.ssm import create_ssm_client


def test_create_ssm_client_uses_dedicated_ssm_credentials(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "ap-northeast-2")
    monkeypatch.setenv("AWS_SSM_ACCESS_KEY_ID", "ssm-ak")
    monkeypatch.setenv("AWS_SSM_SECRET_KEY_ID", "ssm-sk")

    with patch("infrastructure.aws.ssm.boto3.client") as mock_client:
        create_ssm_client()

    mock_client.assert_called_once_with(
        "ssm",
        region_name="ap-northeast-2",
        aws_access_key_id="ssm-ak",
        aws_secret_access_key="ssm-sk",
    )


def test_create_ssm_client_falls_back_to_default_aws_chain(monkeypatch):
    monkeypatch.delenv("AWS_SSM_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SSM_SECRET_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SSM_SECRET_ACCESS_KEY", raising=False)

    with patch("infrastructure.aws.ssm.boto3.client") as mock_client:
        create_ssm_client()

    kwargs = mock_client.call_args.kwargs
    assert "aws_access_key_id" not in kwargs
    assert "aws_secret_access_key" not in kwargs
