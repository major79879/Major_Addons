#!/bin/sh

# المتغيرات الأساسية
BOUQUET_DIR="/etc/enigma2"
BOUQUET_MAIN_FILE="$BOUQUET_DIR/bouquets.tv"
BOUQUET_NAME="userbouquet.major_30w_abertis.tv"
RAW_URL="https://raw.githubusercontent.com/major79879/Major_Addons/refs/heads/main/userbouquet.major_30w_abertis.tv"
DISPLAY_NAME="Abertis 30W"

echo "=== بدء تثبيت باقة $DISPLAY_NAME ==="

# 1. تحميل ملف المفضلة وحفظه في مسار الإنيجما
wget -q --no-check-certificate "$RAW_URL" -O "$BOUQUET_DIR/$BOUQUET_NAME"

if [ $? -ne 0 ]; then
    echo "[-] خطأ: فشل سحب ملف المفضلة من GitHub!"
    exit 1
fi

# 2. ضبظ اسم المفضلة الداخلي ليظهر باسم Abertis 30W
sed -i "1s/.*/#NAME $DISPLAY_NAME/" "$BOUQUET_DIR/$BOUQUET_NAME"

# 3. حذف أي إشارة سابقة للمفضلة في bouquets.tv لمنع التكرار
sed -i "/$BOUQUET_NAME/d" "$BOUQUET_MAIN_FILE"

# 4. إدراج المفضلة في نهاية ملف bouquets.tv لتظهر في آخر المفضلات
echo "#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET \"$BOUQUET_NAME\" ORDER BY bouquet" >> "$BOUQUET_MAIN_FILE"

echo "[+] تم إضافة المفضلة بنجاح في آخر القائمة."

# 5. تحديث قائمة القنوات بدون إعادة تشغيل الرسيفر
wget -qO - http://127.0.0.1/web/servicelistreload?mode=0 > /dev/null 2>&1
if [ $? -eq 0 ]; then
    echo "[+] تم تحديث قائمة القنوات (Servicelist Reload) بنجاح."
else
    enigma2-client servicelistreload > /dev/null 2>&1 || killall -9 enigma2
fi

echo "=== اكمل التثبيت بنجاح! ==="
