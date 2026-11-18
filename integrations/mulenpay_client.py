"""Small async client for the MulenPay REST API."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from config import Settings


class MulenPayError(RuntimeError):
    pass


@dataclass(frozen=True)
class CreatedPayment:
    id: int
    payment_url: str


class MulenPayClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        if not settings.mulenpay_configured:
            raise MulenPayError("MulenPay is not configured")
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            base_url=settings.mulenpay_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.mulenpay_api_key}"},
            timeout=20,
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def _sign(self, currency: str, amount: str, uuid: str) -> str:
        # This follows MulenPay's official Node SDK: currency + amount + shopId + uuid + secret.
        value = f"{currency}{amount}{self.settings.mulenpay_shop_id}{uuid}{self.settings.mulenpay_secret_key}"
        return hashlib.sha1(value.encode()).hexdigest()

    async def create_payment(self, *, uuid: str, amount_minor: int, description: str, email: str | None = None) -> CreatedPayment:
        amount = str((Decimal(amount_minor) / Decimal(100)).quantize(Decimal("0.01")))
        payload = {
            "currency": "rub", "amount": amount, "uuid": uuid,
            "shopId": self.settings.mulenpay_shop_id, "description": description[:255],
            "language": "ru", "client": email, "items": [{
                "description": description[:255], "quantity": 1, "price": float(amount),
                "vat_code": self.settings.mulenpay_vat_code, "payment_subject": 1,
                "payment_mode": 4, "measurement_unit": 0,
            }],
            "sign": self._sign("rub", amount, uuid),
        }
        data = await self._request("POST", "v2/payments", json=payload)
        try:
            return CreatedPayment(id=int(data["id"]), payment_url=str(data["paymentUrl"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise MulenPayError("MulenPay returned an invalid create-payment response") from exc

    async def get_payment_status(self, payment_id: int) -> int:
        data = await self._request("GET", f"v2/payments/{payment_id}")
        try:
            return int(data["payment"]["status"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MulenPayError("MulenPay returned an invalid payment-status response") from exc

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self.client.request(method, url, **kwargs)
            if response.is_error:
                # Provider validation messages identify rejected fields; never log request headers/body.
                detail = response.text[:1000]
                raise MulenPayError(f"MulenPay HTTP {response.status_code}: {detail}")
            data = response.json()
        except MulenPayError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise MulenPayError(f"MulenPay request failed: {exc}") from exc
        if not isinstance(data, dict) or data.get("success") is False:
            raise MulenPayError(f"MulenPay rejected request: {data}")
        return data
