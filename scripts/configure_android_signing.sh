#!/usr/bin/env bash
set -euo pipefail
# Run only after gh auth login. Never prints the private key.
repo='wilmain-arch/RemoteVibecode'
keystore_path="${1:-$HOME/.android/debug.keystore}"
test -f "$keystore_path"
gh auth status
read -rsp 'Пароль хранилища: ' store_password
printf '\n'
read -rp 'Псевдоним ключа (для прежнего debug: androiddebugkey): ' key_alias
read -rsp 'Пароль ключа: ' key_password
printf '\n'
base64 -w0 "$keystore_path" | gh secret set ANDROID_KEYSTORE_BASE64 --repo "$repo"
printf '%s' "$store_password" | gh secret set ANDROID_KEYSTORE_PASSWORD --repo "$repo"
printf '%s' "$key_alias" | gh secret set ANDROID_KEY_ALIAS --repo "$repo"
printf '%s' "$key_password" | gh secret set ANDROID_KEY_PASSWORD --repo "$repo"
printf 'Секреты подписи добавлены. Проверьте совпадение сертификата с установленной APK.\n'
