#!/bin/sh

# Download and install the OSCam IPK package.

MY_FILE="enigma2-plugin-softcams-oscam_11989-emu-r802_all.ipk"
MY_URL="https://github.com/major79879/Major_Addons/raw/refs/heads/main/$MY_FILE"
MY_TMP_FILE="/tmp/$MY_FILE"
MY_SEP='============================================================='

echo ''
echo "$MY_SEP"
echo "Downloading $MY_FILE ..."
echo "$MY_SEP"
echo ''

rm -f "$MY_TMP_FILE"
if ! wget -T 30 -O "$MY_TMP_FILE" "$MY_URL" || [ ! -s "$MY_TMP_FILE" ]; then
	echo "Download failed !"
	rm -f "$MY_TMP_FILE"
	exit 1
fi

if ! command -v opkg > /dev/null 2>&1; then
	echo "opkg is not available; this IPK installer requires an Enigma2 receiver with opkg."
	rm -f "$MY_TMP_FILE"
	exit 1
fi

echo ''
echo "$MY_SEP"
echo 'Installation started'
echo "$MY_SEP"
echo ''

opkg install --force-reinstall "$MY_TMP_FILE"
MY_RESULT=$?

echo ''
if [ "$MY_RESULT" -eq 0 ]; then
	echo "   >>>>   SUCCESSFULLY INSTALLED   <<<<"
	echo ''
	echo "   >>>>         RESTARTING        <<<<"
	if command -v systemctl > /dev/null 2>&1; then
		sleep 2
		systemctl restart enigma2
	else
		init 4
		sleep 4
		init 3
	fi
else
	echo "   >>>>   INSTALLATION FAILED !   <<<<"
fi

echo ''
echo '**************************************************'
echo '**                   FINISHED                   **'
echo '**************************************************'
echo ''
exit "$MY_RESULT"
