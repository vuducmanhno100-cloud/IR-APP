import ssl

from elasticsearch import Elasticsearch


def make_client(
    url: str,
    api_key: str,
    ca_pem: str | None = None,
    ca_fingerprint: str | None = None,
    request_timeout: float = 30.0,
    max_retries: int = 2,
) -> Elasticsearch:
    options: dict = {
        "api_key": api_key,
        "request_timeout": request_timeout,
        "max_retries": max_retries,
        "retry_on_status": (502, 503, 504),
        "retry_on_timeout": True,
    }
    if url.startswith("https://"):
        if ca_pem:
            options["ssl_context"] = ssl.create_default_context(cadata=ca_pem)
        elif ca_fingerprint:
            options["ssl_assert_fingerprint"] = ca_fingerprint.replace(":", "").lower()
        else:
            raise ValueError("ca_pem or ca_fingerprint is required for https")
    return Elasticsearch(url, **options)
