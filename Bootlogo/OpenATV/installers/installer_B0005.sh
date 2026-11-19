#!/bin/sh
set -eu

ID='B0005'
URL="https://github.com/major79879/Major_Addons/raw/refs/heads/main/Bootlogo/OpenATV/${ID}/bootlogo.mvi"
DEST='/usr/share/bootlogo.mvi'
TMP_FILE="${DEST}.tmp.$$"

cleanup() {
    rm -f "$TMP_FILE"
}
trap cleanup 0 HUP INT TERM

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root." >&2
    exit 1
fi

if command -v wget >/dev/null 2>&1; then
    wget -q -O "$TMP_FILE" "$URL"
elif command -v curl >/dev/null 2>&1; then
    curl -fLsS "$URL" -o "$TMP_FILE"
else
    echo "This receiver needs wget or curl to download the bootlogo." >&2
    exit 1
fi

if [ ! -s "$TMP_FILE" ] || [ "$(wc -c < "$TMP_FILE" | tr -d '[:space:]')" -lt 50000 ]; then
    echo "The downloaded bootlogo is missing or incomplete." >&2
    exit 1
fi

# A valid MVI (MPEG-2) file starts with the sequence header 00 00 01 b3.
MAGIC="$(od -An -tx1 -N4 "$TMP_FILE" 2>/dev/null | tr -d ' \n')"
if [ -n "$MAGIC" ] && [ "$MAGIC" != "000001b3" ]; then
    echo "The downloaded file is not a valid MVI bootlogo." >&2
    exit 1
fi

if [ -f "$DEST" ]; then
    BACKUP="${DEST}.backup.$(date +%Y%m%d%H%M%S)"
    cp -p "$DEST" "$BACKUP"
    echo "Previous bootlogo backed up to $BACKUP"
fi

chmod 0644 "$TMP_FILE"
mv -f "$TMP_FILE" "$DEST"
sync
trap - 0 HUP INT TERM
echo "Installed ${ID} bootlogo at ${DEST}"
