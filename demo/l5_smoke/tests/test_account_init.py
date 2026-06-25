import pytest

from bank import Account


def test_account_stores_starting_balance():
    account = Account(150)
    assert account.balance == 150.0


def test_account_defaults_to_zero_balance():
    account = Account()
    assert account.balance == 0.0


def test_account_rejects_negative_starting_balance():
    with pytest.raises(ValueError):
        Account(-1)


def test_account_rejects_non_numeric_starting_balance():
    with pytest.raises(TypeError):
        Account("100")


def test_account_rejects_boolean_starting_balance():
    with pytest.raises(TypeError):
        Account(True)
