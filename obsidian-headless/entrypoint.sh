#!/bin/bash
set -euo pipefail

echo "==> Logging in to Obsidian..."
ob login --email "$OB_EMAIL" --password "$OB_PASSWORD"

IFS=',' read -ra VAULTS <<< "$OB_VAULTS"

for vault in "${VAULTS[@]}"; do
    vault=$(echo "$vault" | xargs)  # trim whitespace
    vault_dir="/vaults/$vault"
    mkdir -p "$vault_dir"

    if [ ! -f "$vault_dir/.obsidian-headless/sync.json" ]; then
        echo "==> Setting up vault: $vault"
        ob sync-setup --vault "$vault" --path "$vault_dir" ${OB_E2EE_PASSWORD:+--password "$OB_E2EE_PASSWORD"}
        ob sync-config --path "$vault_dir" --mode "${OB_SYNC_MODE:-bidirectional}"
    else
        echo "==> Vault already configured: $vault"
    fi
done

echo "==> Starting continuous sync for all vaults..."
PIDS=()
for vault in "${VAULTS[@]}"; do
    vault=$(echo "$vault" | xargs)
    vault_dir="/vaults/$vault"
    echo "    Syncing: $vault"
    ob sync --path "$vault_dir" --continuous &
    PIDS+=($!)
done

trap 'echo "==> Shutting down..."; kill "${PIDS[@]}" 2>/dev/null; wait' SIGTERM SIGINT

wait "${PIDS[@]}"
