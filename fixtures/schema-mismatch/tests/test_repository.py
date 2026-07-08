from src.repository import fetch_user, list_user_ids


def test_fetch_user_ids():
    assert list_user_ids() == [1, 2]


def test_fetch_user_email():
    assert fetch_user(2)["email"] == "grace@example.com"


def test_fetch_user_active_flag():
    assert fetch_user(1)["active"] is True
