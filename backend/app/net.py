"""Outbound HTTPS trust settings.

httpx ships its own CA list (certifi), which rejects certificates from anything that inspects HTTPS
(antivirus web shields such as Avast/AVG/Kaspersky, corporate proxies), even when the OS trusts them.
`truststore` verifies against the operating system's trust store instead, the same one browsers use.
Verification stays fully ON; only the source of trusted roots changes. On a Linux server this is simply
the system CA bundle.
"""
from __future__ import annotations

import ssl

try:
    import truststore

    # process-wide, so libraries we don't control (the OpenAI client inside langchain) are covered too
    truststore.inject_into_ssl()
    TLS_VERIFY: ssl.SSLContext | bool = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except ImportError:  # fall back to httpx's default (certifi)
    TLS_VERIFY = True
