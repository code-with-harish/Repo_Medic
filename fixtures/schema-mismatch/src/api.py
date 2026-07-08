"""API layer: exposes user profiles built from the storage layer."""

from src.repository import fetch_user, list_user_ids


def profile(user_id=1):
    record = fetch_user(user_id)
    return {
        "username": record["username"],
        "contact": record["email"],
        "active": record["active"],
    }


def directory():
    return [profile(uid)["username"] for uid in list_user_ids()]
