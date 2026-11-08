"""Regression: new Telegram archive upload returns its photo ID to draft flow.

Avoid importing app.py because it starts a live Telegram bot at import time.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_instagram_archive_upload_forwards_saved_photo_id():
    source = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    funcs = [node for node in tree.body
             if isinstance(node, ast.AsyncFunctionDef)
             and node.name == "archive_instagram_menu_photo"]
    assert len(funcs) == 1
    module = ast.fix_missing_locations(ast.Module(body=funcs, type_ignores=[]))
    seen = []

    async def fake_save(message, photo, original):
        seen.append((message, photo, original))
        return "a" * 24

    namespace = {"receive_archived_image": fake_save}
    exec(compile(module, "app.py", "exec"), namespace)
    photo = object()
    message = SimpleNamespace(photo=[photo])
    result = await namespace["archive_instagram_menu_photo"](message)
    assert result == "a" * 24
    assert seen == [(message, photo, False)]


@pytest.mark.asyncio
async def test_failed_archive_does_not_claim_photo_saved():
    source = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    funcs = [node for node in tree.body
             if isinstance(node, ast.AsyncFunctionDef)
             and node.name == "archive_instagram_menu_photo"]
    assert len(funcs) == 1
    module = ast.fix_missing_locations(ast.Module(body=funcs, type_ignores=[]))

    async def fake_save(message, photo, original):
        return None

    namespace = {"receive_archived_image": fake_save}
    exec(compile(module, "app.py", "exec"), namespace)
    assert await namespace["archive_instagram_menu_photo"](
        SimpleNamespace(photo=[object()])
    ) is None
