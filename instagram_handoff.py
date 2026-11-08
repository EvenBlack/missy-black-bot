"""Durable, owner-controlled Instagram human handoff.

Only metadata (Instagram-scoped sender IDs and pause flags) is stored here;
no Direct message content, access tokens, or owner Telegram identities.
"""
from datetime import datetime, timezone

MAX_DIALOGS = 500


def get_dialogs(data):
    records = data.get("instagram_dialogs", {})
    return records if isinstance(records, dict) else {}


async def register_dialog(change_memory, read_memory, sender_id, instagram_account_id=None):
    sender_id = str(sender_id)
    data = await read_memory()
    if sender_id in get_dialogs(data) and (not instagram_account_id or data.get("instagram_account_id")):
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def update(payload):
        records = payload.setdefault("instagram_dialogs", {})
        if not isinstance(records, dict):
            raise ValueError("Invalid Instagram dialog state")
        records.setdefault(sender_id, {"mode": "auto", "first_seen": now})
        if instagram_account_id and str(instagram_account_id).isdigit():
            existing_account = payload.get("instagram_account_id")
            if existing_account is None:
                payload["instagram_account_id"] = str(instagram_account_id)
            elif str(existing_account) != str(instagram_account_id):
                raise ValueError("Unexpected Instagram business account ID")
        if len(records) > MAX_DIALOGS:
            removable = sorted(
                ((key, item.get("first_seen", "")) for key, item in records.items()
                 if isinstance(item, dict) and item.get("mode") != "human"),
                key=lambda row: row[1],
            )
            for key, _ in removable[:max(0, len(records) - MAX_DIALOGS)]:
                records.pop(key, None)

    await change_memory(update)


async def allow_automatic_reply(read_memory, sender_id):
    data = await read_memory()
    record = get_dialogs(data).get(str(sender_id), {})
    return not isinstance(record, dict) or record.get("mode") != "human"


async def set_dialog_mode(change_memory, read_memory, sender_id, mode):
    if mode not in {"human", "auto"}:
        raise ValueError("Invalid dialog mode")
    sender_id = str(sender_id)
    data = await read_memory()
    if sender_id not in get_dialogs(data):
        return False

    def update(payload):
        entry = payload.setdefault("instagram_dialogs", {}).get(sender_id)
        if not isinstance(entry, dict):
            raise ValueError("Unknown Instagram dialog")
        entry["mode"] = mode

    await change_memory(update)
    return True


async def list_dialogs(read_memory):
    data = await read_memory()
    return sorted(get_dialogs(data).items(), key=lambda pair: pair[1].get("first_seen", ""), reverse=True)
