"""Meta errors must be diagnosable without disclosing tokens, image URLs, or user captions."""
import logging

from instagram_growth import _log_meta_rejection


def test_meta_rejection_reports_only_safe_codes(caplog):
    body = {"error": {"code": 10, "error_subcode": 2207005,
                      "type": "OAuthException",
                      "message": "image_url=https://secret.test/owner?sig=PRIVATE",
                      "fbtrace_id": "SECRETTRACE"}}
    with caplog.at_level(logging.ERROR, logger="instagram_growth"):
        _log_meta_rejection("container", 400, body)
    message = caplog.text
    assert "http=400" in message
    assert "code=10" in message
    assert "subcode=2207005" in message
    assert "type=OAuthException" in message
    for private in ("secret.test", "PRIVATE", "SECRETTRACE", "image_url="):
        assert private not in message


def test_meta_rejection_handles_malformed_response(caplog):
    with caplog.at_level(logging.ERROR, logger="instagram_growth"):
        _log_meta_rejection("publish", 503, {"error": ["not", "a", "dict"]})
    assert "http=503" in caplog.text
    assert "code=unknown" in caplog.text
