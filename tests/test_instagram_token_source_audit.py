"""Read-only OAuth token source audit tests: never log, return or publish tokens."""
import pytest
from cryptography.fernet import Fernet
import instagram_token_source_audit as audit
from instagram_menu import menu_keyboard


@pytest.mark.asyncio
async def test_two_sources_compared_without_exposing_tokens(monkeypatch):
    key=Fernet.generate_key().decode()
    secret="long-secret-value-that-must-not-appear"
    encrypted=Fernet(key.encode()).encrypt(secret.encode()).decode()
    state={"instagram_account_id":"123", "instagram_business_oauth":{"encrypted_token":encrypted}}
    monkeypatch.setenv("META_TOKEN_ENCRYPTION_KEY",key)
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN",secret)
    monkeypatch.setenv("META_GRAPH_VERSION","v27.0")
    seen=[]
    class Reply:
        status=200
        async def __aenter__(self):return self
        async def __aexit__(self,*args):return False
        async def json(self,**kwargs):return {"username":"_tsarapka__"}
    class Session:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):return False
        def get(self,url,**kwargs):
            assert "/123" in url
            assert kwargs["params"]=={"fields":"username"}
            assert kwargs["headers"]=={"Authorization":"Bearer "+secret}
            seen.append(url)
            return Reply()
        def post(self,*args,**kwargs):raise AssertionError("Must never POST to Meta")
    monkeypatch.setattr(audit.aiohttp,"ClientSession",Session)
    async def read():return state
    result=await audit.audit_instagram_token_sources(read)
    assert result["status"]=="ok"
    assert result["render"]=="readable"
    assert result["stored"]=="readable"
    assert result["same_token"] is True
    report=audit.render_token_source_audit(result)
    assert secret not in report and encrypted not in report and "123" not in report
    assert len(seen)==2


@pytest.mark.asyncio
async def test_stored_token_absent_is_not_claimed_valid(monkeypatch):
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN",raising=False)
    monkeypatch.setenv("META_GRAPH_VERSION","v27.0")
    async def read():return {"instagram_account_id":"123"}
    result=await audit.audit_instagram_token_sources(read)
    assert result["status"]=="ok" and result["render"]=="absent"
    assert result["stored"]=="absent" and result["same_token"] is None


def test_bad_encryption_and_owner_menu(monkeypatch):
    assert audit._stored_oauth_token({}, "xxx")==("absent",None)
    assert audit._stored_oauth_token({"instagram_business_oauth":{"encrypted_token":"garbage"}},"garbage")==("decryption_failed",None)
    actions=[b.callback_data for row in menu_keyboard().inline_keyboard for b in row]
    assert "igmenu:tokensources" in actions


def test_return_is_safe_for_malformed_payload():
    assert audit._classify(400,{"error":{"code":200,"message":"sensitive"}})=="permission_denied"
    assert audit._classify(400,{"error":{"code":190,"message":"sensitive"}})=="token_rejected"
