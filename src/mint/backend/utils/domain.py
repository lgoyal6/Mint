from typing import Any
from urllib.parse import urlparse


PERSONAL_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "icloud.com",
    "qq.com",
    "163.com",
    "126.com",
    "foxmail.com",
    "aol.com",
    "protonmail.com",
    "live.com",
    "msn.com",
    "yandex.com",
    "mail.com",
    "me.com",
    "naver.com",
    "sina.com",
    "sohu.com",
    "ymail.com",
}


def email_domain(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    if "@" not in email:
        return None
    return clean_domain(email.rsplit("@", 1)[-1])


def is_personal_email_domain(value: Any) -> bool:
    domain = email_domain(value) if isinstance(value, str) and "@" in value else clean_domain(value)
    return bool(domain and domain in PERSONAL_EMAIL_DOMAINS)


def clean_domain(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().lower()
    if "@" in raw and "://" not in raw:
        raw = raw.rsplit("@", 1)[-1]
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    domain = parsed.hostname or parsed.path.split("/", 1)[0]
    domain = domain.strip().strip(".").lower()
    if domain.startswith("www."):
        domain = domain.removeprefix("www.")
    return domain or None
