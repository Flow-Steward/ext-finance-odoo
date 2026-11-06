"""Project-scoped Odoo connection: one Odoo database, one user's API key.

A connection is built from what the host hydrates for one invocation and is
discarded when that invocation ends. The API key lives in a private field of a
frozen dataclass whose ``repr`` never shows it, and it is never placed in a
result, a log line or an exception message.

The server address is the customer's own Odoo (Odoo Online, Odoo.sh or on
premise). It must be https, a host name (no IP literal, no localhost), without
a path, credentials, query or fragment; the SDK's pinned opener additionally
refuses any host that resolves to a private or reserved address.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from . import errors
from .errors import ExtensionError

CONNECTION_TYPE_ID = "odoo"
CONFIG_FIELDS: frozenset[str] = frozenset(
    {"label", "base_url", "database", "login", "api", "odoo_version", "language"}
)
SECRET_FIELDS: frozenset[str] = frozenset({"api_key"})
#: JSON-2 (/json/2, bearer key) exists from Odoo 19; XML-RPC (/xmlrpc/2) on 16-20.
APIS: frozenset[str] = frozenset({"json2", "xmlrpc"})
SUPPORTED_VERSIONS: tuple[int, ...] = (16, 17, 18, 19, 20)

MIN_KEY_LENGTH = 8
MAX_KEY_LENGTH = 512
_KEY_RE = re.compile(r"^[\x21-\x7e]+$")
_HOST_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_DB_RE = re.compile(r"^[A-Za-z0-9_.-]{1,63}$")
_LANG_RE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?(@[a-z]+)?$")


@dataclass(frozen=True)
class Connection:
    """One validated Odoo database connection."""

    connection_id: str
    base_url: str
    database: str
    login: str
    api: str
    odoo_version: int | None
    language: str
    _api_key: str = field(repr=False)

    def secret_fragments(self) -> tuple[str, ...]:
        """The key, for redacting text Odoo might echo back."""
        return (self._api_key,)

    @property
    def api_key(self) -> str:
        """The key, for building one request; never logged or returned."""
        return self._api_key

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname or ""

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"Connection(connection_id={self.connection_id!r})"

    __str__ = __repr__


def _invalid(message: str) -> ExtensionError:
    return ExtensionError(
        errors.INVALID_CONNECTION,
        message,
        definitely_no_external_effect=True,
        external_effect_status="failed",
    )


def base_url_of(value: Any) -> str:
    """https://host[:port], validated; anything else is refused before any request."""
    text = value.strip().rstrip("/") if isinstance(value, str) else ""
    if not text:
        raise _invalid("Add the Odoo address (for example https://mycompany.odoo.com)")
    parts = urlsplit(text)
    if parts.scheme != "https":
        raise _invalid("The Odoo address must start with https://")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise _invalid("The Odoo address may not contain credentials, a query or a fragment")
    if parts.path not in ("", "/"):
        raise _invalid("The Odoo address is the server only, without a path")
    host = (parts.hostname or "").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise _invalid("The Odoo address must be a host name, not an IP address")
    if host in {"localhost"} or host.endswith((".localhost", ".local")) or not _HOST_RE.match(host):
        raise _invalid("The Odoo address is not a public host name")
    port = parts.port
    return f"https://{host}" + (f":{port}" if port and port != 443 else "")


def _api(value: Any) -> str:
    if value in (None, ""):
        return "json2"
    if isinstance(value, str) and value.strip().lower() in APIS:
        return value.strip().lower()
    raise _invalid("api must be json2 (Odoo 19 and later) or xmlrpc (Odoo 16 to 20)")


def _version(value: Any) -> int | None:
    if value in (None, "", "auto"):
        return None
    text = str(value).strip()
    if text.isdigit() and int(text) in SUPPORTED_VERSIONS:
        return int(text)
    raise _invalid("odoo_version must be auto or one of 16, 17, 18, 19, 20")


def _key(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise _invalid("Add the Odoo API key to this connection")
    if len(text) < MIN_KEY_LENGTH or len(text) > MAX_KEY_LENGTH or not _KEY_RE.fullmatch(text):
        raise _invalid("The Odoo API key is not usable")
    return text


def connection_from_payload(payload: Mapping[str, Any], *, connection_ref: str) -> Connection:
    """Validate the selected project connection before any outbound request."""
    action = payload.get("action")
    target = action.get("target") if isinstance(action, Mapping) else None
    connection = target.get("connection") if isinstance(target, Mapping) else None
    if not isinstance(connection, Mapping):
        raise _invalid("The selected connection is unavailable")
    connection_id = str(connection.get("connection_id") or "")
    if not connection_id or (connection_ref and connection_id != connection_ref):
        raise _invalid("The selected connection does not match connection_ref")
    connection_type = str(
        connection.get("connection_type_id") or connection.get("connection_type") or ""
    )
    if connection_type and connection_type != CONNECTION_TYPE_ID:
        raise _invalid("The selected connection is not supported")
    config = connection.get("config")
    config = config if isinstance(config, Mapping) else {}
    secrets = connection.get("secrets")
    secrets = secrets if isinstance(secrets, Mapping) else {}
    if set(secrets) - SECRET_FIELDS:
        raise _invalid("The connection carries unsupported secrets")
    api = _api(config.get("api"))
    database = str(config.get("database") or "").strip()
    if database and not _DB_RE.fullmatch(database):
        raise _invalid("The database name may contain letters, digits and _ . - only")
    login = str(config.get("login") or "").strip()
    if api == "xmlrpc" and (not database or not login):
        raise _invalid("XML-RPC needs the database name and the login of the API key's user")
    if len(login) > 200 or not login.isprintable():
        raise _invalid("The login is not usable")
    language = str(config.get("language") or "").strip()
    if language and not _LANG_RE.fullmatch(language):
        raise _invalid("language must be an Odoo language code such as en_US or lt_LT")
    return Connection(
        connection_id=connection_id,
        base_url=base_url_of(config.get("base_url")),
        database=database,
        login=login,
        api=api,
        odoo_version=_version(config.get("odoo_version")),
        language=language,
        _api_key=_key(secrets.get("api_key")),
    )


__all__ = [
    "APIS",
    "CONFIG_FIELDS",
    "CONNECTION_TYPE_ID",
    "SECRET_FIELDS",
    "SUPPORTED_VERSIONS",
    "Connection",
    "base_url_of",
    "connection_from_payload",
]
