import hashlib

from config import Settings
from integrations.mulenpay_client import MulenPayClient


def test_mulenpay_signature_uses_order_uuid() -> None:
    settings = Settings(
        payments_enabled=True, mulenpay_api_key="api", mulenpay_secret_key="secret", mulenpay_shop_id=42,
    )
    # The signature is pure; do not open an HTTP connection in this unit test.
    client = object.__new__(MulenPayClient)
    client.settings = settings
    expected = hashlib.sha1(b"rub100.5042order-refsecret").hexdigest()
    assert client._sign("rub", "100.50", "order-ref") == expected


def test_mulenpay_requires_all_credentials() -> None:
    assert not Settings(payments_enabled=True, mulenpay_api_key="api", mulenpay_shop_id=42).mulenpay_configured
