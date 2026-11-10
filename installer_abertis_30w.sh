#!/bin/bash

GITHUB_BUO_URL="https://raw.githubusercontent.com/major79879/Major_Addons/refs/heads/main/userbouquet.major_30w_abertis.tv"
BUO_FILENAME="userbouquet.major_30w_abertis.tv"
TARGET_DIR="/etc/enigma2"
BOUQUETS_FILE="$TARGET_DIR/bouquets.tv"
TMP_FILE="/tmp/$BUO_FILENAME"

echo "=== بدء تحميل وتحديث مفضلة Major Abertis 30W ==="

# 1. التحميل إلى مجلد مؤقت أولاً لضمان سلامة الملف
wget -q --no-check-certificate -O "$TMP_FILE" "$GITHUB_BUO_URL"

# التحقق من نجاح التحميل وأن الملف غير فارغ ويحتوي على صيغة مفضلة صحيحة
if [ $? -eq 0 ] && [ -s "$TMP_FILE" ] && grep -q "#NAME" "$TMP_FILE"; then
    mv -f "$TMP_FILE" "$TARGET_DIR/$BUO_FILENAME"
    chmod 644 "$TARGET_DIR/$BUO_FILENAME"
    echo "تم تحميل ملف المفضلة ونقله بنجاح."

    # 2. التحقق من وجود المفضلة في ملف bouquets.tv وإضافتها إن لم تكن موجودة
    if grep -q "$BUO_FILENAME" "$BOUQUETS_FILE"; then
        echo "المفضلة مسجلة مسبقاً في bouquets.tv."
    else
        echo "إضافة المفضلة إلى قائمة bouquets.tv..."
        # التأكد من وجود سطر جديد قبل الإضافة لتجنب تداخل السطور
        sed -i -e '$a\' "$BOUQUETS_FILE"
        echo '#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "'"$BUO_FILENAME"'" ORDER BY bouquet' >> "$BOUQUETS_FILE"
    fi

    # 3. تحديث قائمة القنوات عبر OpenWebif (mode=2 لتحديث الباقات والخدمات معاً)
    echo "جاري تحديث قائمة القنوات..."
    wget -qO - "http://127.0.0.1/web/servicelistreload?mode=2" > /dev/null 2>&1

    if [ $? -ne 0 ]; then
        echo "تعذر التحديث عبر OpenWebif، جاري إعادة تشغيل الواجهة..."
        init 4 && sleep 2 && init 3
    else
        echo "تم تحديث القنوات بنجاح!"
    fi

    echo "=== اكتملت العملية بنجاح! ==="
else
    rm -f "$TMP_FILE"
    echo "خطأ: فشل تحميل ملف المفضلة أو أن الملف تالف/فارغ."
    exit 1
fi