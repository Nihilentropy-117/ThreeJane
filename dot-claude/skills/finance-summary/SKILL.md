---
name: finance-summary
description: >
  Print a live financial dashboard showing all bank accounts (via SimpleFIN),
  all Splitwise group balances, and a net worth summary. Use this skill whenever
  the user asks about their finances, bank balance, money owed, Splitwise debts,
  net worth, or says anything like "how much money do I have", "what do people
  owe me", "show me my finances", "financial summary", or "check my accounts".
---

# Finance Summary Skill

Fetches live data from SimpleFIN (bank accounts) and Splitwise (shared expenses),
then prints a pretty markdown summary with three sections:
1. Bank accounts grouped by institution
2. Splitwise groups with per-group balances
3. Net balance combining both

## How to run

Execute the bundled script. No arguments needed — credentials come from env vars.

```bash
python3 /root/.claude/skills/finance-summary/scripts/finance_summary.py
```

## Required environment variables

| Variable | Description |
|----------|-------------|
| `SIMPLEFIN_ACCESS_URL` | Full SimpleFIN Bridge access URL (includes credentials) |
| `SPLITWISE_API_KEY` | Splitwise personal API key |

If either is missing the script exits with a clear error message.

## Output

The script prints markdown to stdout with three sections:

- **🏦 Bank Accounts** — table of all accounts grouped by institution, with balances. Negative balances (e.g. credit cards) are shown as-is.
- **🤝 Splitwise Groups** — one row per group showing owed-to-you, you-owe, and net. Groups with zero balances are included for completeness.
- **📊 Net Balance** — summary table: bank total + Splitwise owed to you − Splitwise you owe = grand total.

## Notes

- All amounts are USD; non-USD balances show the currency code.
- The Splitwise "Non-group expenses" entry (group id 0) is included — it represents individual (non-group) splits.
- If either API fails, the script still prints what it could and appends an errors section at the bottom.
