# Rule: Pessimistic Verification (Slow Down)

1. **Anti-Momentum**: Do NOT proceed to the next logical step until the current one is physically verified (e.g., via tests, `lint`, or a check command).
2. **Assumption Audit**: Before starting any execution phase, explicitly list your assumptions. If an assumption can be tested, test it first.
3. **Foundation First**: When modifying shared or foundational components, perform a sanity check after every single file modification.
4. **No Premature Optimization**: Do not optimize or "clean up" unrelated code while working on a critical fix unless strictly necessary for the task's success.
