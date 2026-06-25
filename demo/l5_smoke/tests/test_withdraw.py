import pytest

from bank import Account


def test_withdraw_decreases_balance_and_returns_new_total():
    account = Account(200)
    new_balance = account.withdraw(75)
    assert new_balance == pytest.approx(125.0)
    assert account.balance == pytest.approx(125.0)


def test_withdraw_raises_value_error_on_overdraft():
    account = Account(50)
    with pytest.raises(ValueError):
        account.withdraw(75)
