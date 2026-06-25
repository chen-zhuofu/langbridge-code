import pytest

from bank import Account, transfer


def test_multi_step_scenario_final_balances():
    """End-to-end flow covering deposits, withdrawals, and transfers."""
    alice = Account(1000)
    bob = Account(250)
    carol = Account(80)

    # Deposits
    alice.deposit(500)
    bob.deposit(200)
    carol.deposit(20)

    # Withdrawals
    alice.withdraw(300)
    bob.withdraw(50)

    # Transfers between accounts
    transfer(alice, bob, 275)
    transfer(bob, carol, 125)

    # Final adjustments before closing the books
    carol.withdraw(25)
    transfer(carol, alice, 100)

    assert alice.balance == pytest.approx(1025.0)
    assert bob.balance == pytest.approx(550.0)
    assert carol.balance == pytest.approx(100.0)

    # Verify overall conservation of funds given all deposits and withdrawals
    assert alice.balance + bob.balance + carol.balance == pytest.approx(1675.0)
