"""Storage layer.

BUG (intentional, for RepoMedic): a past refactor renamed the record key
`username` to `user_name`, but the API layer and the tests still consume the
original contract.
"""

_USERS = {
    1: ("ada", "ada@example.com", True),
    2: ("grace", "grace@example.com", False),
}


def fetch_user(user_id=1):
    name, email, active = _USERS[user_id]
    return {
        "id": user_id,
        "user_name": name,          # was "username" before the refactor
        "email": email,
        "active": active,
    }


def list_user_ids():
    return sorted(_USERS)
