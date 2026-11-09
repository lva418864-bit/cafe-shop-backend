"""
Bakong KHQR (EMVCo TLV + CRC16-CCITT) Generator, Open API Client, and
PostgreSQL SKIP LOCKED Verification Worker.

Complies with the National Bank of Cambodia (NBC) KHQR Specification and
Bakong Open API v1 (/v1/check_transaction_by_md5, /v1/check_transaction_by_md5_list,
/v1/renew_token, /v1/generate_deeplink_by_qr).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import httpx
from sqlalchemy.orm import Session

try:
    from bakong_khqr import KHQR
except ImportError:
    KHQR = None

from app.core.config import settings
from app.core.exception import ApplicationError
from app.models.order import Order, OrderStatus
from app.models.payment import (
    Payment,
    PaymentAttempt,
    PaymentCurrency,
    PaymentEvent,
    PaymentStatus,
    Refund,
    VerificationJob,
)
from app.services.order_service import OrderService

logger = logging.getLogger("bakong_khqr")

ISO_CURRENCY_CODES = {
    "USD": "840",
    "KHR": "116",
}


def utc_now() -> datetime:
    """Return current UTC timestamp as timezone-naive datetime matching DB storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BakongConfigurationError(ApplicationError):
    def __init__(self, message: str):
        super().__init__(message=message, error_code="err_503_bakong_config", status_code=503)


class BakongAmountValidationError(ApplicationError):
    def __init__(self, message: str):
        super().__init__(message=message, error_code="err_400_khqr_amount", status_code=400)


class BakongStateError(ApplicationError):
    def __init__(self, message: str):
        super().__init__(message=message, error_code="err_409_payment_state", status_code=409)


class BakongAuthError(ApplicationError):
    def __init__(self, message: str):
        super().__init__(message=message, error_code="err_503_bakong_auth", status_code=503)


class BakongApiTemporaryError(ApplicationError):
    def __init__(self, message: str):
        super().__init__(message=message, error_code="err_503_bakong_api", status_code=503)


def mask_secret(value: str | None) -> str:
    """Mask sensitive string (account ID, token, tx hash), showing only the last 4 characters."""
    if not value:
        return ""
    val_str = str(value).strip()
    if len(val_str) <= 4:
        return "****"
    return f"...{val_str[-4:]}"


def _tlv(tag: str, value: str) -> str:
    """Formats a single EMVCo Tag-Length-Value segment."""
    if len(tag) != 2 or not tag.isdigit():
        raise ValueError(f"Invalid EMV tag: {tag}")
    encoded_len = len(value.encode("utf-8"))
    if encoded_len > 99:
        raise ValueError(f"EMV tag {tag} value exceeds 99 bytes ({encoded_len})")
    return f"{tag}{encoded_len:02d}{value}"


def compute_crc16_ccitt(payload_without_crc_value: str) -> str:
    """
    Computes the 4-char uppercase hex CRC16-CCITT (polynomial 0x1021, initial 0xFFFF)
    over the QR payload up to and including '6304'.
    """
    crc = 0xFFFF
    for byte in payload_without_crc_value.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def verify_khqr_crc(qr_string: str) -> bool:
    """Verifies that a KHQR string ends with a valid Tag 63 CRC16-CCITT checksum."""
    if len(qr_string) < 8 or qr_string[-8:-4] != "6304":
        return False
    expected = compute_crc16_ccitt(qr_string[:-4])
    return qr_string[-4:].upper() == expected


def compute_qr_md5(qr_string: str) -> str:
    """Returns the 32-character lowercase hexadecimal MD5 digest of the KHQR string."""
    return hashlib.md5(qr_string.encode("utf-8")).hexdigest().lower()


def parse_khqr_tlv(qr_string: str) -> dict[str, str]:
    """Parses top-level EMVCo TLV tags from a KHQR string for inspection and validation."""
    idx = 0
    parsed: dict[str, str] = {}
    total_len = len(qr_string)
    while idx < total_len:
        if idx + 4 > total_len:
            raise ValueError("Truncated TLV header in KHQR string")
        tag = qr_string[idx : idx + 2]
        length = int(qr_string[idx + 2 : idx + 4])
        val_start = idx + 4
        val_end = val_start + length
        if val_end > total_len:
            raise ValueError(f"Truncated TLV value for tag {tag}")
        parsed[tag] = qr_string[val_start:val_end]
        idx = val_end
    return parsed


def mask_bakong_account(account_id: str | None) -> str | None:
    """Masks a customer Bakong account ID (e.g. 'sokha_chan@aba' -> 'so***an@aba') for PII safety."""
    if not account_id:
        return None
    if "@" in account_id:
        local, domain = account_id.split("@", 1)
        if len(local) <= 3:
            masked_local = local[0] + "***"
        else:
            masked_local = f"{local[:2]}***{local[-2:]}"
        return f"{masked_local}@{domain}"
    if len(account_id) <= 4:
        return "***"
    return f"{account_id[:2]}***{account_id[-2:]}"


def convert_usd_to_khr(usd_amount: Decimal | float | str, rate: int | None = None) -> Decimal:
    """Converts a USD amount to integer KHR using the configured exchange rate."""
    resolved_rate = Decimal(str(rate or settings.BAKONG_USD_TO_KHR_RATE))
    usd_dec = Decimal(str(usd_amount))
    raw_khr = (usd_dec * resolved_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return raw_khr


def format_khqr_amount(amount: Decimal | float | str, currency: str) -> str:
    """
    Validates and formats an amount according to NBC KHQR rules:
    - USD: positive, max 2 decimal places, formatted as '0.00' (or integer/decimal per EMVCo).
    - KHR: positive integer only (no fractional sen allowed).
    """
    curr = currency.upper()
    if curr not in ISO_CURRENCY_CODES:
        raise BakongAmountValidationError(f"Unsupported KHQR currency: {currency}")

    dec = Decimal(str(amount))
    if dec <= 0:
        raise BakongAmountValidationError("KHQR amount must be greater than zero")

    if curr == PaymentCurrency.KHR:
        if dec != dec.to_integral_value():
            raise BakongAmountValidationError("KHR amount must be a whole integer without decimals")
        return str(int(dec))

    quantized = dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quantized != dec:
        raise BakongAmountValidationError("USD amount cannot have more than 2 decimal places")
    return f"{quantized:.2f}"


def generate_khqr_string(
    *,
    bank_account: str,
    merchant_name: str,
    merchant_city: str,
    amount: Decimal | float | str,
    currency: str = "USD",
    bill_number: str,
    store_label: str = "CoffeeShop",
    terminal_label: str = "POS-01",
    phone_number: str | None = None,
    merchant_id: str | None = None,
    acquiring_bank: str | None = None,
    created_at_ms: int | None = None,
    expires_at_ms: int | None = None,
    static: bool = False,
) -> str:
    """
    Generates an NBC-compliant EMVCo KHQR string with Tag 29/30 (Account Info),
    Tag 52 (MCC), Tag 53 (Currency), Tag 54 (Amount), Tag 58 (KH), Tag 59 (Merchant Name),
    Tag 60 (City), Tag 62 (Additional Data), Tag 99 (Creation & Expiration Timestamps),
    and Tag 63 (CRC16-CCITT).
    """
    if not bank_account or "@" not in bank_account:
        raise BakongConfigurationError("Valid BAKONG_ACCOUNT_ID (e.g. merchant@bank) is required")

    iso_currency = ISO_CURRENCY_CODES.get(currency.upper())
    if not iso_currency:
        raise BakongAmountValidationError(f"Unsupported currency: {currency}")

    formatted_amount = format_khqr_amount(amount, currency)
    now_ms = created_at_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    exp_ms = expires_at_ms or (now_ms + settings.BAKONG_QR_EXPIRY_SECONDS * 1000)

    segments: list[str] = [
        _tlv("00", "01"),  # Payload Format Indicator
        _tlv("01", "11" if static else "12"),  # 12 = Dynamic QR, 11 = Static QR
    ]

    # Tag 29 (Individual) or Tag 30 (Corporate Merchant)
    if merchant_id and acquiring_bank:
        merchant_account_info = (
            _tlv("00", bank_account.strip()) + _tlv("01", merchant_id.strip()) + _tlv("02", acquiring_bank.strip()[:32])
        )
        segments.append(_tlv("30", merchant_account_info))
    else:
        individual_account_info = _tlv("00", bank_account.strip())
        segments.append(_tlv("29", individual_account_info))

    segments.extend(
        [
            _tlv("52", "5814"),  # MCC 5814 (Fast Food / Coffee Shops)
            _tlv("53", iso_currency),
        ]
    )

    if not static:
        segments.append(_tlv("54", formatted_amount))

    clean_name = (merchant_name.strip() or "Coffee Shop")[:25]
    clean_city = (merchant_city.strip() or "Phnom Penh")[:15]

    segments.extend(
        [
            _tlv("58", "KH"),
            _tlv("59", clean_name),
            _tlv("60", clean_city),
        ]
    )

    # Tag 62: Additional Data Field Template
    additional_fields = _tlv("01", bill_number[:25])
    if phone_number:
        additional_fields += _tlv("02", phone_number.strip()[:15])
    if store_label:
        additional_fields += _tlv("03", store_label.strip()[:25])
    if terminal_label:
        additional_fields += _tlv("07", terminal_label.strip()[:25])
    segments.append(_tlv("62", additional_fields))

    # Tag 99: Bakong Timestamp Extension (00 = created ms, 01 = expires ms)
    if not static:
        timestamp_template = _tlv("00", str(now_ms)) + _tlv("01", str(exp_ms))
        segments.append(_tlv("99", timestamp_template))

    payload_for_crc = "".join(segments) + "6304"
    crc = compute_crc16_ccitt(payload_for_crc)
    return payload_for_crc + crc


@dataclass
class BakongTransactionResult:
    found: bool
    response_code: int
    error_code: int | None
    message: str
    tx_hash: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    currency: str | None = None
    amount: Decimal | None = None
    created_date_ms: int | None = None
    acknowledged_date_ms: int | None = None
    raw: dict[str, Any] | None = None


class BakongClientInterface(ABC):
    """Abstract interface defining the contract for Bakong Open API interactions."""

    @abstractmethod
    def create_qr(
        self,
        amount: Decimal | float | str,
        currency: str,
        bill_number: str,
        account_id: str | None = None,
        merchant_name: str | None = None,
        merchant_city: str | None = None,
        store_label: str | None = None,
        terminal_label: str | None = None,
        phone_number: str | None = None,
        expiration_days: int = 1,
    ) -> tuple[str, str]:
        """Generates dynamic EMVCo KHQR string and returns (qr_string, qr_md5)."""
        pass

    @abstractmethod
    def renew_token(self, email: str | None = None) -> str:
        """Obtains or renews an API access token."""
        pass

    @abstractmethod
    def check_transaction_by_md5(self, qr_md5: str) -> BakongTransactionResult:
        """Queries settlement status for a single KHQR by MD5 hash."""
        pass

    @abstractmethod
    def check_transaction_by_md5_list(self, md5_list: list[str]) -> dict[str, BakongTransactionResult]:
        """Batch-queries settlement status for a list of KHQR MD5 hashes."""
        pass

    @abstractmethod
    def generate_deeplink(self, qr_string: str, callback_url: str | None = None) -> str | None:
        """Requests a Bakong mobile deeplink (best-effort)."""
        pass

    def generate_deeplink_by_qr(self, qr_string: str) -> str | None:
        """Alias for generate_deeplink."""
        return self.generate_deeplink(qr_string)


class RealBakongClient(BakongClientInterface):
    """
    Real Bakong client wrapping the `bakong-khqr` library for NBC Bakong KHQR.
    Interacts with NBC Bakong Open API or Relay API.
    """

    def __init__(
        self,
        token: str | None = None,
        account_id: str | None = None,
        merchant_name: str | None = None,
        merchant_city: str | None = None,
        store_label: str | None = None,
        terminal_label: str | None = None,
        phone_number: str | None = None,
        max_amount_usd: Decimal | float | None = None,
        registered_email: str | None = None,
        base_url: str | None = None,
        khqr_sdk: Any | None = None,
        use_httpx: bool = False,
    ):
        self._token = (token if token is not None else settings.BAKONG_TOKEN) or ""
        self.account_id = (account_id if account_id is not None else settings.BAKONG_ACCOUNT_ID) or ""
        self.merchant_name = merchant_name or settings.BAKONG_MERCHANT_NAME
        self.merchant_city = merchant_city or settings.BAKONG_MERCHANT_CITY
        self.store_label = store_label or settings.BAKONG_STORE_LABEL
        self.terminal_label = terminal_label or settings.BAKONG_TERMINAL_LABEL
        self.phone_number = phone_number or settings.BAKONG_PHONE_NUMBER
        self.max_amount_usd = Decimal(
            str(max_amount_usd if max_amount_usd is not None else settings.BAKONG_MAX_AMOUNT_USD)
        )
        self.registered_email = registered_email or settings.BAKONG_REGISTERED_EMAIL
        self.base_url = (base_url or settings.BAKONG_API_BASE_URL).rstrip("/")
        self._use_httpx = use_httpx

        if khqr_sdk is not None:
            self._khqr = khqr_sdk
        elif KHQR is not None:
            self._khqr = KHQR(bakong_token=self._token)
        else:
            raise BakongConfigurationError("bakong-khqr package is not installed in the environment.")

    @property
    def token(self) -> str:
        return self._token

    def renew_token(self, email: str | None = None) -> str:
        """Token renewal via NBC Bakong Open API endpoint POST /v1/renew_token."""
        target_email = email or self.registered_email
        if not target_email:
            raise BakongConfigurationError("BAKONG_REGISTERED_EMAIL is required to renew Bakong token")
        try:
            with httpx.Client(base_url=self.base_url, timeout=8.0) as client:
                resp = client.post("/v1/renew_token", json={"email": target_email})
                resp.raise_for_status()
                body = resp.json()
                if body.get("responseCode") == 0 and body.get("data", {}).get("token"):
                    self._token = body["data"]["token"]
                    if KHQR is not None:
                        self._khqr = KHQR(bakong_token=self._token)
                    return self._token
                raise BakongConfigurationError(body.get("responseMessage") or "Bakong token renewal failed")
        except Exception as exc:
            logger.error("Token renewal request failed: %s", exc)
            raise

    def create_qr(
        self,
        amount: Decimal | float | str,
        currency: str,
        bill_number: str,
        account_id: str | None = None,
        merchant_name: str | None = None,
        merchant_city: str | None = None,
        store_label: str | None = None,
        terminal_label: str | None = None,
        phone_number: str | None = None,
        expiration_days: int = 1,
    ) -> tuple[str, str]:
        curr = currency.upper()
        if curr not in ISO_CURRENCY_CODES:
            raise BakongAmountValidationError(f"Unsupported KHQR currency: {currency}")

        dec_amount = Decimal(str(amount))
        if dec_amount <= Decimal("0"):
            raise BakongAmountValidationError("Payment amount must be greater than zero")

        if curr == "USD":
            quantized = dec_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if quantized < Decimal("0.01"):
                raise BakongAmountValidationError("Minimum USD payment is $0.01")
            if quantized > self.max_amount_usd:
                raise BakongAmountValidationError(
                    f"Payment amount ${quantized} exceeds maximum allowed limit of ${self.max_amount_usd}"
                )
            amount_val = float(quantized)
        elif curr == "KHR":
            quantized = dec_amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            if quantized < Decimal("100"):
                raise BakongAmountValidationError("Minimum KHR payment is 100 KHR")
            max_khr = self.max_amount_usd * Decimal(str(settings.BAKONG_USD_TO_KHR_RATE))
            if quantized > max_khr:
                raise BakongAmountValidationError(
                    f"Payment amount {quantized} KHR exceeds maximum allowed limit of {max_khr} KHR"
                )
            amount_val = int(quantized)
        else:
            amount_val = float(dec_amount)

        target_account = account_id or self.account_id
        if not target_account:
            raise BakongConfigurationError("BAKONG_ACCOUNT_ID is required to generate real KHQR")

        if self._use_httpx:
            qr_string = generate_khqr_string(
                bank_account=target_account,
                merchant_name=merchant_name or self.merchant_name,
                merchant_city=merchant_city or self.merchant_city,
                amount=dec_amount,
                currency=curr,
                bill_number=bill_number,
                store_label=store_label or self.store_label,
                terminal_label=terminal_label or self.terminal_label,
                phone_number=phone_number or self.phone_number,
            )
            return qr_string, compute_qr_md5(qr_string)

        try:
            res = self._khqr.create_qr(
                amount=amount_val,
                currency=curr,
                bill_number=bill_number,
                account_id=target_account,
                merchant_name=merchant_name or self.merchant_name,
                merchant_city=merchant_city or self.merchant_city,
                store_label=store_label or self.store_label,
                terminal_label=terminal_label or self.terminal_label,
                phone_number=phone_number or self.phone_number,
                expiration=expiration_days,
            )
            qr_string = str(res)
            qr_md5 = getattr(res, "md5", None) or self._khqr.generate_md5(qr_string)
            logger.info("Generated real KHQR (MD5: %s, account: %s)", mask_secret(qr_md5), mask_secret(target_account))
            return qr_string, qr_md5
        except (ValueError, Exception) as exc:
            logger.error("Failed to generate KHQR via bakong-khqr SDK: %s", exc)
            raise BakongConfigurationError(f"Failed to generate KHQR: {exc}") from exc

    def check_transaction_by_md5(self, qr_md5: str, _retried_auth: bool = False) -> BakongTransactionResult:
        logger.info("Verifying transaction for MD5: %s", mask_secret(qr_md5))
        if self._use_httpx:
            return self._check_transaction_by_md5_httpx(qr_md5, _retried_auth=_retried_auth)

        try:
            status_result = self._khqr.check_payment(qr_md5)
            status_str = status_result[0] if isinstance(status_result, tuple) else str(status_result)

            if status_str.upper() == "PAID":
                tx_data = self._khqr.get_payment(qr_md5) or {}
                tx_hash = tx_data.get("hash")
                logger.info("Transaction %s is PAID. Hash: %s", mask_secret(qr_md5), mask_secret(tx_hash))
                return BakongTransactionResult(
                    found=True,
                    response_code=0,
                    error_code=None,
                    message="Paid",
                    tx_hash=tx_hash,
                    from_account_id=tx_data.get("fromAccountId"),
                    to_account_id=tx_data.get("toAccountId"),
                    currency=tx_data.get("currency"),
                    amount=Decimal(str(tx_data["amount"])) if tx_data.get("amount") is not None else None,
                    created_date_ms=int(tx_data["createdDateMs"]) if tx_data.get("createdDateMs") else None,
                    acknowledged_date_ms=int(tx_data["acknowledgedDateMs"])
                    if tx_data.get("acknowledgedDateMs")
                    else None,
                    raw=tx_data,
                )

            return BakongTransactionResult(
                found=False,
                response_code=1,
                error_code=None,
                message=status_str,
            )
        except ValueError as exc:
            err_msg = str(exc)
            if "Developer Token is either incorrect or expired" in err_msg or "Unauthorized" in err_msg:
                logger.critical(
                    "CRITICAL ALERT: Bakong Developer Token is invalid or expired! "
                    "Renew BAKONG_TOKEN in .env immediately. (masked account: %s)",
                    mask_secret(self.account_id),
                )
                raise BakongAuthError("Bakong Developer Token is invalid or expired. Check BAKONG_TOKEN configuration.") from exc
            logger.warning("Bakong API temporary error for MD5 %s: %s", mask_secret(qr_md5), err_msg)
            raise BakongApiTemporaryError(f"Bakong API temporary failure: {err_msg}") from exc
        except Exception as exc:
            logger.warning("Unexpected error during transaction verification for MD5 %s: %s", mask_secret(qr_md5), exc)
            raise BakongApiTemporaryError(f"Unexpected Bakong verification error: {exc}") from exc

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _check_transaction_by_md5_httpx(self, qr_md5: str, _retried_auth: bool = False) -> BakongTransactionResult:
        with httpx.Client(base_url=self.base_url, timeout=8.0) as client:
            resp = client.post(
                "/v1/check_transaction_by_md5",
                headers=self._headers(),
                json={"md5": qr_md5},
            )

            if resp.status_code == 401 and not _retried_auth and self.registered_email:
                self.renew_token()
                return self._check_transaction_by_md5_httpx(qr_md5, _retried_auth=True)

            resp.raise_for_status()
            body = resp.json()

            if (
                body.get("responseCode") == 1
                and body.get("errorCode") == 6
                and not _retried_auth
                and self.registered_email
            ):
                self.renew_token()
                return self._check_transaction_by_md5_httpx(qr_md5, _retried_auth=True)

            resp_code = int(body.get("responseCode", 1))
            err_code = body.get("errorCode")
            msg = body.get("responseMessage") or ""
            data = body.get("data")
            if resp_code == 0 and isinstance(data, dict):
                return BakongTransactionResult(
                    found=True,
                    response_code=0,
                    error_code=None,
                    message=msg,
                    tx_hash=data.get("hash"),
                    from_account_id=data.get("fromAccountId"),
                    to_account_id=data.get("toAccountId"),
                    currency=data.get("currency"),
                    amount=Decimal(str(data["amount"])) if data.get("amount") is not None else None,
                    created_date_ms=int(data["createdDateMs"]) if data.get("createdDateMs") else None,
                    acknowledged_date_ms=int(data["acknowledgedDateMs"]) if data.get("acknowledgedDateMs") else None,
                    raw=body,
                )
            return BakongTransactionResult(
                found=False,
                response_code=resp_code,
                error_code=err_code,
                message=msg,
                raw=body,
            )

    def check_transaction_by_md5_list(self, md5_list: list[str]) -> dict[str, BakongTransactionResult]:
        if not md5_list:
            return {}

        if self._use_httpx:
            return self._check_transaction_by_md5_list_httpx(md5_list)

        results: dict[str, BakongTransactionResult] = {}
        for i in range(0, len(md5_list), 50):
            chunk = md5_list[i : i + 50]
            try:
                paid_hashes = set(self._khqr.check_bulk_payments(chunk))
            except ValueError as exc:
                err_msg = str(exc)
                if "Developer Token is either incorrect or expired" in err_msg or "Unauthorized" in err_msg:
                    logger.critical(
                        "CRITICAL ALERT: Bakong Developer Token is invalid or expired during bulk check! "
                        "Renew BAKONG_TOKEN in .env immediately."
                    )
                    raise BakongAuthError("Bakong Developer Token is invalid or expired.") from exc
                logger.warning("Bulk check temporary error: %s", err_msg)
                raise BakongApiTemporaryError(f"Bakong bulk check failure: {err_msg}") from exc
            except Exception as exc:
                logger.warning("Bulk check unexpected error: %s", exc)
                raise BakongApiTemporaryError(f"Bakong bulk check failure: {exc}") from exc

            for md5 in chunk:
                if md5 in paid_hashes:
                    try:
                        tx_data = self._khqr.get_payment(md5) or {}
                        results[md5] = BakongTransactionResult(
                            found=True,
                            response_code=0,
                            error_code=None,
                            message="Paid",
                            tx_hash=tx_data.get("hash"),
                            from_account_id=tx_data.get("fromAccountId"),
                            to_account_id=tx_data.get("toAccountId"),
                            currency=tx_data.get("currency"),
                            amount=Decimal(str(tx_data["amount"])) if tx_data.get("amount") is not None else None,
                            created_date_ms=int(tx_data["createdDateMs"]) if tx_data.get("createdDateMs") else None,
                            acknowledged_date_ms=int(tx_data["acknowledgedDateMs"])
                            if tx_data.get("acknowledgedDateMs")
                            else None,
                            raw=tx_data,
                        )
                    except Exception:
                        results[md5] = BakongTransactionResult(found=True, response_code=0, error_code=None, message="Paid")
                else:
                    results[md5] = BakongTransactionResult(found=False, response_code=1, error_code=None, message="UNPAID")
        return results

    def _check_transaction_by_md5_list_httpx(self, md5_list: list[str]) -> dict[str, BakongTransactionResult]:
        with httpx.Client(base_url=self.base_url, timeout=8.0) as client:
            resp = client.post(
                "/v1/check_transaction_by_md5_list",
                headers=self._headers(),
                json={"md5List": md5_list},
            )
            resp.raise_for_status()
            body = resp.json()
            results: dict[str, BakongTransactionResult] = {}
            data_list = body.get("data") or []
            if isinstance(data_list, list):
                for entry in data_list:
                    md5_key = entry.get("md5")
                    if not md5_key:
                        continue
                    status_str = (entry.get("status") or "").upper()
                    tx_data = entry.get("data")
                    if status_str == "SUCCESS" and isinstance(tx_data, dict):
                        results[md5_key] = BakongTransactionResult(
                            found=True,
                            response_code=0,
                            error_code=None,
                            message="Success",
                            tx_hash=tx_data.get("hash"),
                            from_account_id=tx_data.get("fromAccountId"),
                            to_account_id=tx_data.get("toAccountId"),
                            currency=tx_data.get("currency"),
                            amount=Decimal(str(tx_data["amount"])) if tx_data.get("amount") is not None else None,
                            created_date_ms=int(tx_data["createdDateMs"]) if tx_data.get("createdDateMs") else None,
                            acknowledged_date_ms=int(tx_data["acknowledgedDateMs"])
                            if tx_data.get("acknowledgedDateMs")
                            else None,
                            raw=entry,
                        )
                    else:
                        results[md5_key] = BakongTransactionResult(
                            found=False,
                            response_code=1,
                            error_code=entry.get("errorCode", 1),
                            message=entry.get("message") or "Not found",
                            raw=entry,
                        )
            return results

    def generate_deeplink(self, qr_string: str, callback_url: str | None = None) -> str | None:
        if self._use_httpx:
            return self._generate_deeplink_httpx(qr_string, callback_url)

        try:
            target_callback = callback_url or settings.PUBLIC_BASE_URL
            return self._khqr.generate_deeplink(
                qr=qr_string,
                appDeepLinkCallback=target_callback,
                appName=self.merchant_name,
            )
        except Exception as exc:
            logger.warning("Bakong mobile deeplink generation failed: %s", exc)
            return None

    def _generate_deeplink_httpx(self, qr_string: str, callback_url: str | None = None) -> str | None:
        try:
            payload: dict[str, Any] = {"qr": qr_string}
            if callback_url or settings.BAKONG_CALLBACK_URL:
                payload["sourceInfo"] = {
                    "appName": settings.BAKONG_MERCHANT_NAME,
                    "appDeepLinkCallback": callback_url or settings.BAKONG_CALLBACK_URL,
                }
            with httpx.Client(base_url=self.base_url, timeout=4.0) as client:
                resp = client.post(
                    "/v1/generate_deeplink_by_qr",
                    headers=self._headers(),
                    json=payload,
                )
                if resp.status_code == 200:
                    body = resp.json()
                    if body.get("responseCode") == 0 and body.get("data", {}).get("shortLink"):
                        return body["data"]["shortLink"]
        except httpx.HTTPError:
            return None
        return None


BakongOpenAPIClient = RealBakongClient


def get_bakong_client() -> BakongClientInterface:
    return RealBakongClient()


def compute_poll_backoff_seconds(attempt_count: int) -> int:
    """Adaptive polling interval: 3s (attempts 1-10), 5s (attempts 11-20), 10s (attempts 21-30), max 15s (31+)."""
    if attempt_count <= 10:
        return 3
    if attempt_count <= 20:
        return 5
    if attempt_count <= 30:
        return 10
    return 15


class PaymentVerificationWorker:
    """
    PostgreSQL-backed background worker using SELECT ... FOR UPDATE SKIP LOCKED
    to poll pending Bakong KHQR payments and reconcile late settlements.
    """

    def __init__(
        self,
        bakong_client: BakongClientInterface | None = None,
        expected_receiver_account: str | None = None,
        worker_id: str = "worker-1",
    ):
        self._explicit_client = bakong_client
        self.expected_receiver_account = expected_receiver_account or settings.BAKONG_ACCOUNT_ID
        self.worker_id = worker_id
        self.order_service = OrderService()

    @property
    def bakong_client(self) -> BakongClientInterface:
        if self._explicit_client is not None:
            return self._explicit_client
        return get_bakong_client()

    @bakong_client.setter
    def bakong_client(self, client: BakongClientInterface | None):
        self._explicit_client = client

    def process_due_jobs(self, db: Session, batch_size: int = 25) -> int:
        """
        Claims up to `batch_size` queued verification jobs using FOR UPDATE SKIP LOCKED
        and verifies each against the Bakong Open API.
        """
        now = utc_now()
        jobs = (
            db.query(VerificationJob)
            .filter(
                VerificationJob.status == "queued",
                VerificationJob.next_run_at <= now,
            )
            .order_by(VerificationJob.next_run_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .all()
        )

        if not jobs:
            return 0

        for job in jobs:
            job.status = "processing"
            job.locked_at = now
            job.locked_by = self.worker_id
        db.flush()

        # Batch check when multiple jobs exist to respect rate limits
        if len(jobs) > 1:
            md5_list = [j.qr_md5 for j in jobs if j.qr_md5]
            try:
                bulk_results = self.bakong_client.check_transaction_by_md5_list(md5_list)
            except (BakongAuthError, BakongApiTemporaryError, httpx.HTTPError) as exc:
                for job in jobs:
                    job.attempt_count += 1
                    job.last_error = str(exc)[:250]
                    job.status = "queued"
                    job.next_run_at = now + timedelta(seconds=compute_poll_backoff_seconds(job.attempt_count))
                db.commit()
                return len(jobs)

            for job in jobs:
                res = bulk_results.get(job.qr_md5)
                self._apply_single_job_result(db, job, res, now)
            db.commit()
            return len(jobs)

        for job in jobs:
            self._verify_single_job(db, job)

        db.commit()
        return len(jobs)

    def _apply_single_job_result(
        self,
        db: Session,
        job: VerificationJob,
        result: BakongTransactionResult | None,
        now: datetime,
    ) -> None:
        payment = db.query(Payment).filter(Payment.id == job.payment_id).with_for_update().first()
        if not payment or payment.status in (
            PaymentStatus.PAID,
            PaymentStatus.SUCCESS,
            PaymentStatus.CANCELLED,
            PaymentStatus.REFUNDED,
        ):
            job.status = "completed"
            return

        if result and result.found:
            self._apply_verification_result(db, payment, result, actor=self.worker_id)
            job.status = "completed"
            return

        # Not paid yet: check expiry
        if payment.expires_at and now >= payment.expires_at:
            prev_status = payment.status
            payment.status = PaymentStatus.EXPIRED
            attempt = db.query(PaymentAttempt).filter(PaymentAttempt.qr_md5 == job.qr_md5).first()
            if attempt:
                attempt.status = PaymentStatus.EXPIRED
            db.add(
                PaymentEvent(
                    payment_id=payment.id,
                    order_id=payment.order_id,
                    event_type="payment.expired",
                    previous_status=prev_status,
                    new_status=PaymentStatus.EXPIRED,
                    qr_md5=job.qr_md5,
                    actor=self.worker_id,
                )
            )
            job.status = "completed"
            return

        job.attempt_count += 1
        if job.attempt_count >= job.max_attempts:
            job.status = "dead_letter"
        else:
            job.status = "queued"
            job.next_run_at = now + timedelta(seconds=compute_poll_backoff_seconds(job.attempt_count))

    def verify_payment_now(self, db: Session, payment: Payment) -> Payment:
        """Immediately checks a pending payment's MD5 against Bakong Open API."""
        if payment.status in (PaymentStatus.PAID, PaymentStatus.SUCCESS):
            return payment

        if not payment.qr_md5:
            return payment

        job = (
            db.query(VerificationJob)
            .filter(VerificationJob.qr_md5 == payment.qr_md5)
            .with_for_update(skip_locked=True)
            .first()
        )
        if job:
            self._verify_single_job(db, job)
            db.commit()
            db.refresh(payment)
            return payment

        # Fallback if no queued job row exists
        result = self.bakong_client.check_transaction_by_md5(payment.qr_md5)
        self._apply_verification_result(db, payment, result, actor="manual_verify")
        db.commit()
        db.refresh(payment)
        return payment

    def reconcile_expired_payments(self, db: Session, lookback_minutes: int = 30) -> int:
        """
        Reconciliation job: checks recently expired payments via Bakong API to catch
        late payments where the customer scanned before expiry and confirmed slightly after.
        """
        cutoff = utc_now() - timedelta(minutes=lookback_minutes)
        expired_payments = (
            db.query(Payment)
            .filter(
                Payment.status == PaymentStatus.EXPIRED,
                Payment.qr_md5.isnot(None),
                Payment.updated_at >= cutoff,
            )
            .with_for_update(skip_locked=True)
            .all()
        )
        recovered = 0
        for payment in expired_payments:
            if not payment.qr_md5:
                continue
            try:
                result = self.bakong_client.check_transaction_by_md5(payment.qr_md5)
            except (BakongAuthError, BakongApiTemporaryError, httpx.HTTPError):
                continue
            if result.found:
                self._apply_verification_result(db, payment, result, actor="reconciler")
                recovered += 1
        db.commit()
        return recovered

    def _verify_single_job(self, db: Session, job: VerificationJob) -> None:
        payment = db.query(Payment).filter(Payment.id == job.payment_id).with_for_update().first()
        if not payment or payment.status in (
            PaymentStatus.PAID,
            PaymentStatus.SUCCESS,
            PaymentStatus.CANCELLED,
            PaymentStatus.REFUNDED,
        ):
            job.status = "completed"
            return

        now = utc_now()
        try:
            result = self.bakong_client.check_transaction_by_md5(job.qr_md5)
        except (BakongAuthError, BakongApiTemporaryError, httpx.HTTPError) as exc:
            job.attempt_count += 1
            job.last_error = str(exc)[:250]
            job.status = "queued"
            job.next_run_at = now + timedelta(seconds=compute_poll_backoff_seconds(job.attempt_count))
            logger.warning("Job for MD5 %s retryable error (attempt %d): %s", mask_secret(job.qr_md5), job.attempt_count, exc)
            return

        if result.found:
            self._apply_verification_result(db, payment, result, actor=self.worker_id)
            job.status = "completed"
            return

        # Not paid yet: check whether QR has expired
        if payment.expires_at and now >= payment.expires_at:
            prev_status = payment.status
            payment.status = PaymentStatus.EXPIRED
            attempt = db.query(PaymentAttempt).filter(PaymentAttempt.qr_md5 == job.qr_md5).first()
            if attempt:
                attempt.status = PaymentStatus.EXPIRED
            db.add(
                PaymentEvent(
                    payment_id=payment.id,
                    order_id=payment.order_id,
                    event_type="payment.expired",
                    previous_status=prev_status,
                    new_status=PaymentStatus.EXPIRED,
                    qr_md5=job.qr_md5,
                    actor=self.worker_id,
                )
            )
            job.status = "completed"
            return

        job.attempt_count += 1
        if job.attempt_count >= job.max_attempts:
            job.status = "dead_letter"
        else:
            job.status = "queued"
            job.next_run_at = now + timedelta(seconds=compute_poll_backoff_seconds(job.attempt_count))

    def _apply_verification_result(
        self,
        db: Session,
        payment: Payment,
        result: BakongTransactionResult,
        actor: str,
    ) -> None:
        if not result.found:
            return

        # Idempotent guard if already paid with the same hash
        if payment.status in (PaymentStatus.PAID, PaymentStatus.SUCCESS):
            if result.tx_hash and payment.bakong_transaction_hash != result.tx_hash:
                # Duplicate payment on a second QR attempt for an already-paid order!
                db.add(
                    Refund(
                        payment_id=payment.id,
                        order_id=payment.order_id,
                        amount=float(result.amount or payment.amount),
                        currency=result.currency or payment.currency,
                        reason="duplicate_payment_on_paid_order",
                        status="pending_manual",
                    )
                )
                db.add(
                    PaymentEvent(
                        payment_id=payment.id,
                        order_id=payment.order_id,
                        event_type="payment.duplicate_detected",
                        previous_status=payment.status,
                        new_status=payment.status,
                        qr_md5=payment.qr_md5,
                        actor=actor,
                        metadata_json=json.dumps({"duplicate_tx_hash": result.tx_hash}),
                    )
                )
            return

        expected_receiver = (payment.receiver_account or self.expected_receiver_account or "").strip().lower()
        actual_receiver = (result.to_account_id or "").strip().lower()

        if expected_receiver and actual_receiver != expected_receiver:
            prev = payment.status
            payment.status = PaymentStatus.FAILED
            payment.failure_reason = f"Receiver account mismatch: expected {expected_receiver}"
            db.add(
                PaymentEvent(
                    payment_id=payment.id,
                    order_id=payment.order_id,
                    event_type="payment.receiver_mismatch",
                    previous_status=prev,
                    new_status=PaymentStatus.FAILED,
                    qr_md5=payment.qr_md5,
                    actor=actor,
                )
            )
            return

        if (result.currency or "").upper() != payment.currency.upper():
            prev = payment.status
            payment.status = PaymentStatus.FAILED
            payment.failure_reason = f"Currency mismatch: expected {payment.currency}, got {result.currency}"
            db.add(
                PaymentEvent(
                    payment_id=payment.id,
                    order_id=payment.order_id,
                    event_type="payment.currency_mismatch",
                    previous_status=prev,
                    new_status=PaymentStatus.FAILED,
                    qr_md5=payment.qr_md5,
                    actor=actor,
                )
            )
            return

        expected_amount = Decimal(str(payment.amount)).quantize(Decimal("0.01"))
        paid_amount = Decimal(str(result.amount or 0)).quantize(Decimal("0.01"))

        if paid_amount < expected_amount:
            prev = payment.status
            payment.status = PaymentStatus.FAILED
            payment.failure_reason = f"Underpaid: expected {expected_amount}, received {paid_amount}"
            payment.bakong_transaction_hash = result.tx_hash
            payment.payer_account_masked = mask_bakong_account(result.from_account_id)
            db.add(
                Refund(
                    payment_id=payment.id,
                    order_id=payment.order_id,
                    amount=float(paid_amount),
                    currency=payment.currency,
                    reason="underpayment_partial_refund",
                    status="pending_manual",
                )
            )
            db.add(
                PaymentEvent(
                    payment_id=payment.id,
                    order_id=payment.order_id,
                    event_type="payment.underpaid",
                    previous_status=prev,
                    new_status=PaymentStatus.FAILED,
                    qr_md5=payment.qr_md5,
                    actor=actor,
                )
            )
            return

        # Overpayment handling: accept order payment and log excess refund
        if paid_amount > expected_amount:
            excess = paid_amount - expected_amount
            db.add(
                Refund(
                    payment_id=payment.id,
                    order_id=payment.order_id,
                    amount=float(excess),
                    currency=payment.currency,
                    reason="overpayment_excess_refund",
                    status="pending_manual",
                )
            )

        order = db.query(Order).filter(Order.id == payment.order_id).with_for_update().first()
        now = utc_now()
        prev_status = payment.status

        if order and order.status == OrderStatus.CANCELLED:
            # Late payment arrived after order was already cancelled and restocked
            payment.status = PaymentStatus.REFUND_REQUIRED
            payment.paid_at = now
            payment.bakong_transaction_hash = result.tx_hash
            payment.payer_account_masked = mask_bakong_account(result.from_account_id)
            payment.failure_reason = "Late payment received after order was cancelled"
            db.add(
                Refund(
                    payment_id=payment.id,
                    order_id=payment.order_id,
                    amount=float(paid_amount),
                    currency=payment.currency,
                    reason="late_payment_on_cancelled_order",
                    status="pending_manual",
                )
            )
            db.add(
                PaymentEvent(
                    payment_id=payment.id,
                    order_id=payment.order_id,
                    event_type="payment.conflict_refund_required",
                    previous_status=prev_status,
                    new_status=PaymentStatus.REFUND_REQUIRED,
                    qr_md5=payment.qr_md5,
                    actor=actor,
                )
            )
            return

        payment.status = PaymentStatus.PAID
        payment.paid_at = now
        payment.bakong_transaction_hash = result.tx_hash
        payment.payer_account_masked = mask_bakong_account(result.from_account_id)
        payment.failure_reason = None
        if result.tx_hash:
            payment.transaction_ref = result.tx_hash[:64]

        attempt = db.query(PaymentAttempt).filter(PaymentAttempt.qr_md5 == payment.qr_md5).first()
        if attempt:
            attempt.status = PaymentStatus.PAID
            attempt.paid_at = now
            attempt.bakong_transaction_hash = result.tx_hash
            attempt.payer_account_masked = payment.payer_account_masked
            if result.raw:
                attempt.raw_bakong_response = json.dumps(result.raw)

        if order and order.status == OrderStatus.PENDING:
            order.status = OrderStatus.PAID

        db.add(
            PaymentEvent(
                payment_id=payment.id,
                order_id=payment.order_id,
                event_type="payment.paid",
                previous_status=prev_status,
                new_status=PaymentStatus.PAID,
                qr_md5=payment.qr_md5,
                actor=actor,
                metadata_json=json.dumps({"tx_hash": result.tx_hash}),
            )
        )
