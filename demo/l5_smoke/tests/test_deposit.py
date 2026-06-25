import pytest

from bank import Account


def test_deposit_increases_balance_and_returns_new_total():
    account = Account(100)
    new_balance = account.deposit(25)
    assert new_balance == pytest.approx(125.0)
    assert account.balance == pytest.approx(125.0)


def test_deposit_rejects_zero_amount():
    account = Account(50)
    with pytest.raises(ValueError):
        account.deposit(0)


def test_deposit_rejects_negative_amount():
    account = Account(50)
    with pytest.raises(ValueError):
        account.deposit(-10)


def test_deposit_rejects_non_numeric_amount():
    account = Account(50)
    with pytest.raises(ValueError):
        account.deposit("20")


def test_deposit_rejects_boolean_amount():
    account = Account(50)
    with pytest.raises(ValueError):
        account.deposit(True)
