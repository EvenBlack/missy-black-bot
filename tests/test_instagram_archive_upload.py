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


def _photo_handler_for_test(archive_photo, show_caption_options):
    """Compile only the Telegram photo handler; do not start the real bot."""
    import copy
    import logging

    source = Path("instagram_menu.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    matches = [item for func in tree.body
               if isinstance(func, ast.FunctionDef) and func.name == "install_instagram_menu"
               for item in ast.walk(func)
               if isinstance(item, ast.AsyncFunctionDef) and item.name == "photo_input"]
    assert len(matches) == 1
    handler = copy.deepcopy(matches[0])
    handler.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[]))
    state = {123: "photo"}
    namespace = {
        "authorized": lambda user, message: True,
        "awaiting_input": state,
        "archive_photo": archive_photo,
        "show_caption_options": show_caption_options,
        "menu_keyboard": lambda: "MENU",
        "log": logging.getLogger("archive-test"),
    }
    exec(compile(module, "instagram_menu.py", "exec"), namespace)
    return namespace["photo_input"], state


@pytest.mark.asyncio
async def test_saved_photo_caption_failure_does_not_claim_archive_failure():
    async def archive(message):
        return "a" * 24

    async def caption_menu(message, photo_id, owner):
        raise RuntimeError("transient listing failure")

    handler, state = _photo_handler_for_test(archive, caption_menu)
    replies = []

    class Message:
        from_user = SimpleNamespace(id=123)

        async def answer(self, text, **kwargs):
            replies.append(text)

    await handler(Message())
    assert state == {}
    assert len(replies) == 1
    assert "Фото збережено" in replies[0]
    assert "Повторно завантажувати" in replies[0]


@pytest.mark.asyncio
async def test_archive_failure_not_reported_as_saved():
    async def archive(message):
        raise RuntimeError("storage offline")

    async def caption_menu(*args):
        raise AssertionError("Menu must not open")

    handler, state = _photo_handler_for_test(archive, caption_menu)
    replies = []

    class Message:
        from_user = SimpleNamespace(id=123)

        async def answer(self, text, **kwargs):
            replies.append(text)

    await handler(Message())
    assert state == {}
    assert len(replies) == 1
    assert "Не вдалося підтвердити збереження" in replies[0]
    assert "Фото збережено" not in replies[0]
