#!/bin/bash

# روابط الملفات على جيثب
GITHUB_BUO_URL="https://raw.githubusercontent.com/major79879/Major_Addons/refs/heads/main/userbouquet.major_30w_abertis.tv"
BUO_FILENAME="userbouquet.major_30w_abertis.tv"
TARGET_DIR="/etc/enigma2"
BOUQUETS_FILE="$TARGET_DIR/bouquets.tv"
TMP_FILE="/tmp/$BUO_FILENAME"
BOUQUET_TITLE="#NAME Abertis 30W"

echo "=== بدء تحميل وتثبيت مفضلة Abertis 30W ==="

# 1. التحميل إلى مجلد مؤقت أولاً
wget -q --no-check-certificate -O "$TMP_FILE" "$GITHUB_BUO_URL"

if [ $? -eq 0 ] && [ -s "$TMP_FILE" ]; then
    
    # 2. إزالة أي عنوان قديم وإضافة العنوان المطلوب Abertis 30W في بداية الملف
    sed -i '/^#NAME/d' "$TMP_FILE"
    sed -i "1i $BOUQUET_TITLE" "$TMP_FILE"

    # 3. نقل الملف النهائي إلى مسار المفضلة في الجهاز
    mv -f "$TMP_FILE" "$TARGET_DIR/$BUO_FILENAME"
    chmod 644 "$TARGET_DIR/$BUO_FILENAME"
    echo "تم حفظ ملف المفضلة بنجاح."

    # 4. تسجيل الباقة في ملف bouquets.tv الرئيسي إن لم تكن مسجلة مسبقاً
    if grep -q "$BUO_FILENAME" "$BOUQUETS_FILE"; then
        echo "الباقة مسجلة مسبقاً في قائمة الباقات الرئيسية."
    else
        echo "إضافة الباقة إلى قائمة bouquets.tv..."
        sed -i -e '$a\' "$BOUQUETS_FILE"
        echo '#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "'"$BUO_FILENAME"'" ORDER BY bouquet' >> "$BOUQUETS_FILE"
    fi

    # 5. تحديث قائمة القنوات في النظام لتظهر فوراً
    echo "جاري تحديث قائمة القنوات..."
    wget -qO - "http://127.0.0.1/web/servicelistreload?mode=2" > /dev/null 2>&1

    if [ $? -ne 0 ]; then
        echo "إعادة تشغيل الواجهة الرسومية لتطبيق التغييرات..."
        init 4 && sleep 2 && init 3
    else
        echo "تم تحديث القنوات بنجاح عبر الـ API!"
    fi

    echo "=== اكتملت العملية بنجاح! ==="
else
    rm -f "$TMP_FILE"
    echo "خطأ: فشل تحميل ملف المفضلة أو أن الملف فارغ."
    exit 1
fi
