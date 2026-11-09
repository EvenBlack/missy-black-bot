"""Explicit opt-in one-shot Meta read-only diagnostic after deploy.

Uses configured token only for profile/media/permission reads. Does not publish,
rotate tokens, expose IDs, or modify persistent state.
"""
import logging
import os

from instagram_account_diagnostic import check_account_read_path
from instagram_permissions import check_instagram_permissions

log = logging.getLogger("instagram_runtime_probe")


async def run_meta_readonly_probe(read_memory, account_probe=check_account_read_path,
                                  permission_probe=check_instagram_permissions):
    if os.getenv("INSTAGRAM_READONLY_PROBE_ON_START", "").lower() != "true":
        return {"status": "disabled"}
    try:
        account = await account_probe(read_memory)
        if not isinstance(account, dict):
            account = {"status": "invalid"}
        log.warning(
            "META_READONLY account_status=%s profile=%s basic=%s media=%s instagram_host=%s",
            str(account.get("status", "unknown"))[:30],
            str(account.get("profile_full", "unknown"))[:30],
            str(account.get("profile_basic", "unknown"))[:30],
            str(account.get("media_list", "unknown"))[:30],
            str(account.get("instagram_host", "unknown"))[:30],
        )
        permission = await permission_probe()
        if not isinstance(permission, dict):
            permission = {"status": "invalid"}
        log.warning("META_READONLY token_check=%s http=%s code=%s",
                    str(permission.get("status", "unknown"))[:30],
                    str(permission.get("http", "none"))[:8],
                    str(permission.get("code", "none"))[:12])
        return {"status": "complete", "account": account, "token_check": permission}
    except Exception as exc:
        log.warning("META_READONLY failed reason=%s", type(exc).__name__)
        return {"status": "failed"}
