# Component task plan: Build a small Python library in the current directory: a file bank.py with an Account class supporting deposit(amount), withdraw(amount) that raises ValueError on overdraft, and a balance property; plus a transfer(src, dst, amount) function that moves money between two accounts atomically. Add focused unit tests, and an integration test that exercises a realistic multi-step transfer scenario end to end.

- [x] Define Account class structure in bank.py with initializer storing starting balance and basic validation scaffolding.
- [x] Implement deposit(amount) ensuring positive amounts are added and invalid inputs raise ValueError; add unit tests covering success and edge cases.
- [x] Implement withdraw(amount) enforcing positive amounts and raising ValueError on overdraft; add unit tests for normal withdrawals and overdraft protection.
- [x] Implement balance property returning current balance and ensure encapsulation/invariance (tests verifying read-only behavior if needed).
- [x] Implement transfer(src, dst, amount) ensuring atomic movement with validation; add unit tests for successful transfers and failure cases (e.g., insufficient funds).
- [ ] Write integration test simulating multi-step scenario with multiple deposits, withdrawals, and transfers verifying final balances end-to-end.
