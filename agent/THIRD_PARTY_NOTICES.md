# Компоненты интерфейса RemoteVibecode

RemoteVibecode: MIT, исходники https://github.com/wilmain-arch/RemoteVibecode.

Qt, PySide6 и Shiboken используются по LGPL v3; их библиотеки загружаются динамически. Лицензия приложения не заменяет лицензии зависимостей. Тексты LGPL v3 и дополняющей её GPL v3 находятся рядом в licenses/.

Исходники и инструкции сборки Qt/PySide6: https://code.qt.io/cgit/pyside/pyside-setup.git/ и https://doc.qt.io/qtforpython-6/building_from_source/index.html . Версия Qt/PySide6 локальной сборки: 6.11.2. Исходники Qt: https://download.qt.io/archive/qt/6.11/6.11.2/ . Для будущих сборок фактическую версию фиксирует metadata зависимостей.

В Arch библиотеки предоставляет пакет pyside6/Qt, лицензии и исходники доступны через пакеты дистрибутива. В Windows EXE библиотеки извлекаются во временную папку PyInstaller и остаются динамическими. Пользователь вправе модифицировать и заменять LGPL-компоненты и выполнять обратную разработку для отладки таких модификаций; приложение не ограничивает эти права. Для замены библиотек удобно собрать вариант --onedir из открытых исходников с выбранной совместимой версией Qt.

Pillow — HPND; qrcode — BSD-3-Clause; cryptography — Apache-2.0 OR BSD-3-Clause; Python — PSF; PyInstaller — GPL-2.0-or-later с исключением для распространения собранных приложений. Их сведения о лицензиях доступны в соответствующих исходниках/metadata зависимостей.
