#!/usr/bin/env python3
"""
Finance summary: SimpleFIN bank accounts + Splitwise group balances.
Requires env vars: SIMPLEFIN_ACCESS_URL, SPLITWISE_API_KEY
"""

import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

# ── helpers ───────────────────────────────────────────────────────────────────

def fmt(amount: float) -> str:
    return f"${amount:,.2f}"

def signed(amount: float) -> str:
    if amount > 0:
        return f"+${amount:,.2f}"
    elif amount < 0:
        return f"-${abs(amount):,.2f}"
    return "$0.00"

def bar(length: int = 60) -> str:
    return "─" * length

# ── SimpleFIN ─────────────────────────────────────────────────────────────────

def fetch_simplefin(access_url: str) -> list[dict]:
    parsed = urlparse(access_url)
    # Strip credentials from netloc so requests handles auth correctly
    clean_url = f"{parsed.scheme}://{parsed.hostname}{parsed.path}/accounts"
    resp = requests.get(clean_url, auth=(parsed.username, parsed.password), timeout=15)
    resp.raise_for_status()
    return resp.json().get("accounts", [])

# ── Splitwise ─────────────────────────────────────────────────────────────────

def sw_get(path: str, api_key: str) -> dict:
    resp = requests.get(
        f"https://secure.splitwise.com/api/v3.0/{path}",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()

def fetch_splitwise(api_key: str) -> tuple[int, list[dict]]:
    user_id = sw_get("get_current_user", api_key)["user"]["id"]
    groups  = sw_get("get_groups", api_key)["groups"]
    return user_id, groups

# ── Rendering ─────────────────────────────────────────────────────────────────

def render_accounts(accounts: list[dict]) -> tuple[str, float]:
    lines = ["## 🏦 Bank Accounts\n"]

    # Group by institution
    by_org: dict[str, list[dict]] = {}
    for acct in accounts:
        org = acct.get("org", {}).get("name", "Unknown")
        by_org.setdefault(org, []).append(acct)

    lines.append("| Institution | Account | Balance |")
    lines.append("|-------------|---------|--------:|")

    total = 0.0
    for org, accts in sorted(by_org.items()):
        for acct in accts:
            bal = float(acct.get("balance", 0))
            total += bal
            currency = acct.get("currency", "USD")
            bal_str = fmt(bal) if currency == "USD" else f"{bal:,.2f} {currency}"
            lines.append(f"| {org} | {acct['name']} | {bal_str} |")

    lines.append("")
    lines.append(f"**Total Bank Balance: {fmt(total)}**")
    return "\n".join(lines), total


def render_splitwise(user_id: int, groups: list[dict]) -> tuple[str, float, float]:
    lines = ["## 🤝 Splitwise Groups\n"]
    lines.append("| Group | Owed to You | You Owe | Net |")
    lines.append("|-------|------------:|--------:|----:|")

    total_owed_to_you = 0.0
    total_you_owe = 0.0

    for group in groups:
        name = group["name"]
        user_balance = 0.0
        for member in group.get("members", []):
            if member["id"] == user_id:
                for b in member.get("balance", []):
                    if b.get("currency_code") == "USD":
                        user_balance += float(b["amount"])

        owed_to_you = max(user_balance, 0.0)
        you_owe = max(-user_balance, 0.0)
        total_owed_to_you += owed_to_you
        total_you_owe += you_owe

        owed_str = fmt(owed_to_you) if owed_to_you > 0 else "—"
        owe_str  = fmt(you_owe)     if you_owe > 0      else "—"
        net_str  = signed(user_balance)

        lines.append(f"| {name} | {owed_str} | {owe_str} | {net_str} |")

    lines.append("")
    sw_net = total_owed_to_you - total_you_owe
    lines.append(
        f"**Owed to you: {fmt(total_owed_to_you)}** &nbsp;|&nbsp; "
        f"**You owe: {fmt(total_you_owe)}** &nbsp;|&nbsp; "
        f"**Net: {signed(sw_net)}**"
    )
    return "\n".join(lines), total_owed_to_you, total_you_owe


def render_net(bank_total: float, sw_owed_to_you: float, sw_you_owe: float) -> str:
    sw_net      = sw_owed_to_you - sw_you_owe
    grand_total = bank_total + sw_net

    lines = ["## 📊 Net Balance\n"]
    lines.append("| Component | Amount |")
    lines.append("|-----------|-------:|")
    lines.append(f"| 🏦 Bank accounts | {fmt(bank_total)} |")
    lines.append(f"| 💚 Splitwise — owed to you | {signed(sw_owed_to_you)} |")
    lines.append(f"| 🔴 Splitwise — you owe | {signed(-sw_you_owe)} |")
    lines.append(f"| **Grand total** | **{signed(grand_total)}** |")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    access_url = os.environ.get("SIMPLEFIN_ACCESS_URL")
    sw_key     = os.environ.get("SPLITWISE_API_KEY")

    if not access_url:
        print("ERROR: SIMPLEFIN_ACCESS_URL is not set.", file=sys.stderr)
        sys.exit(1)
    if not sw_key:
        print("ERROR: SPLITWISE_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    errors   = []
    accounts = []
    user_id  = 0
    groups   = []

    try:
        accounts = fetch_simplefin(access_url)
    except Exception as e:
        errors.append(f"SimpleFIN error: {e}")

    try:
        user_id, groups = fetch_splitwise(sw_key)
    except Exception as e:
        errors.append(f"Splitwise error: {e}")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = []
    out.append("# 💰 Financial Summary\n")
    out.append(f"*{now}*\n")
    out.append(bar())
    out.append("")

    accounts_md, bank_total = render_accounts(accounts)
    out.append(accounts_md)
    out.append("")
    out.append(bar())
    out.append("")

    sw_md, sw_owed_to_you, sw_you_owe = render_splitwise(user_id, groups)
    out.append(sw_md)
    out.append("")
    out.append(bar())
    out.append("")

    out.append(render_net(bank_total, sw_owed_to_you, sw_you_owe))

    if errors:
        out.append("")
        out.append("---")
        out.append("**⚠️ Errors:**")
        for e in errors:
            out.append(f"- {e}")

    print("\n".join(out))


if __name__ == "__main__":
    main()
