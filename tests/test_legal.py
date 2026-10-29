from config import Settings
from services.legal import payment_and_refunds


def test_unconfigured_seller_does_not_expose_environment_variable_name() -> None:
    text = payment_and_refunds(Settings(legal_seller_details=""))
    assert "LEGAL_SELLER_DETAILS" not in text
    assert "будет указан до подключения приёма оплаты" in text
