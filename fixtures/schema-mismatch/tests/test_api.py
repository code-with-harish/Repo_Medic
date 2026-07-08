from src.api import directory, profile


def test_profile_username():
    assert profile(1)["username"] == "ada"


def test_profile_contact():
    assert profile(2)["contact"] == "grace@example.com"


def test_directory_lists_all_usernames():
    assert directory() == ["ada", "grace"]
