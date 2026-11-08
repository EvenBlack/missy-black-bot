"""Permission report is read-only and never echoes tokens."""
from instagram_permissions import render_permissions_report, REQUIRED
from instagram_menu import menu_keyboard


def test_meta_permissions_menu_present():
    buttons = [button.callback_data for row in menu_keyboard().inline_keyboard for button in row]
    assert "igmenu:permissions" in buttons


def test_readable_safe_permission_report():
    result = {"status": "ok", "granted": ["instagram_basic"],
              "missing": ["instagram_content_publish", "pages_read_engagement", "pages_show_list"],
              "token_type": "SYSTEM_USER"}
    text = render_permissions_report(result)
    assert "instagram_basic" in text
    assert "instagram_content_publish" in text
    assert "секрети не показуються" in text
    assert "access_token=" not in text
    assert len(REQUIRED) == 4


def test_unavailable_and_invalid_reports():
    assert "дійсний" in render_permissions_report({"status": "invalid_token"})
    assert "не вдалося" in render_permissions_report({"status": "network_error"})


def test_meta_code_200_is_actionable_and_not_token_revocation():
    msg = render_permissions_report({"status": "check_unavailable", "http": 400, "code": "200"})
    assert "200" in msg
    assert "System users" in msg
    assert "не доводить" in msg
