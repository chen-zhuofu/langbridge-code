import pytest

from bank import Account


def test_balance_property_tracks_current_balance():
    account = Account(75)
    account.deposit(25)
    account.withdraw(10)
    assert account.balance == pytest.approx(90.0)


def test_balance_property_is_read_only():
    account = Account(50)
    with pytest.raises(AttributeError):
        account.balance = 200
