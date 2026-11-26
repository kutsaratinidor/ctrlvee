import requests


def describe_request_error(service: str, exc: Exception) -> str:
    """Short user-facing message for a failed HTTP call; log the raw exception separately."""
    # Order matters: SSLError and ConnectTimeout are both ConnectionError subclasses.
    if isinstance(exc, requests.exceptions.SSLError):
        return f"{service} SSL/TLS handshake failed (check the http/https setting)"
    if isinstance(exc, requests.exceptions.Timeout):
        return f"{service} didn't respond in time"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return f"{service} is unreachable (is it running?)"
    if isinstance(exc, requests.exceptions.RequestException):
        return f"Couldn't talk to {service}"
    return f"Unexpected error talking to {service}"


def describe_http_status(service: str, status: int) -> str:
    if status in (401, 403):
        return f"{service} rejected the API key (HTTP {status})"
    if status == 404:
        return f"{service} endpoint not found (HTTP 404, check the URL)"
    if status >= 500:
        return f"{service} had an internal error (HTTP {status})"
    return f"{service} returned HTTP {status}"


if __name__ == "__main__":
    e = requests.exceptions
    assert "unreachable" in describe_request_error("Seerr", e.ConnectionError("Max retries..."))
    assert "in time" in describe_request_error("Seerr", e.ConnectTimeout())
    assert "SSL" in describe_request_error("Seerr", e.SSLError())
    assert "Unexpected" in describe_request_error("Seerr", ValueError("bad json"))
    assert "API key" in describe_http_status("Radarr", 401)
    assert "internal" in describe_http_status("Radarr", 502)
    print("ok")
