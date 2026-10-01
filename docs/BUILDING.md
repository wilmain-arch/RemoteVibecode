# Сборка и выпуск

[← Главная](../README.md)

## Локально

- Android: JDK 21, Android SDK 35. В каталоге `android/` выполните `./gradlew :app:assembleRelayDebug`. `--offline` используйте только если зависимости уже скачаны.
- Windows: Python 3.13, `pip install -r agent/requirements-build.txt`, затем команда PyInstaller из `.github/workflows/windows-agent.yml`.
- Arch: `makepkg -si` в каталоге `arch/`. PKGBUILD использует тег `arch-vВЕРСИЯ`; для разработки укажите нужный commit/ref.
- Ретранслятор: `./relay/build-deb.sh ВЕРСИЯ`. Нужны bash, tar и ar.

## Версии

`release.json` — состав комплекта. Номер Android и versionCode в Gradle, pkgver в PKGBUILD должны совпадать с ним. `python3 scripts/release_metadata.py` проверяет соответствие. Тег комплекта имеет вид `bundle-vВЕРСИЯ`. Нельзя заменять файлы уже опубликованного релиза новой сборкой с тем же номером.

## GitHub Actions

Обычная проверка собирает Android и Arch из исходников и проверяет ссылки документации. Windows EXE собирается отдельным workflow. Выпуск по тегу собирает все четыре компонента, формирует таблицу версий и SHA256SUMS, затем публикует комплект в Releases. Готовые сборки в Git не хранятся.

### Подпись Android

Для выпуска нужны secrets репозитория: `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`. Ключ обязан совпадать с ключом ранее установленного приложения. Не добавляйте keystore или его содержимое в Git.

Без секретов обычная проверка создаёт только проверочный debug APK с временным ключом runner. Такой APK не является обновлением пользовательской установки. Публикация по тегу без ключа подписи останавливается, чтобы не выпустить несовместимое обновление.

После `gh auth login` можно использовать `scripts/configure_android_signing.sh ПУТЬ_К_KEYSTORE`: скрипт запросит параметры и передаст секреты GitHub без вывода ключа.

Подпись настраивается в Settings → Secrets and variables → Actions. После настройки подтвердите совпадение SHA-256 сертификата с прежним APK через `apksigner verify --print-certs`. Сертификат не секрет, приватный ключ — секрет.

## Публикация

1. Обновите версии и `RELEASE_NOTES.md`; укажите изменения, совместимость и порядок обновления.
2. Дождитесь успешных проверок main, создайте `arch-vВЕРСИЯ` и `bundle-vВЕРСИЯ`.
3. Проверьте состав опубликованного релиза и ссылки `releases/latest`.
4. Отдельно зафиксируйте результаты проверки на реальных устройствах. Сборка не подтверждает работу полного маршрута.
