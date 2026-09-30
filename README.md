# RemoteVibecode

Работайте с Codex на своём ПК через Android: чаты, проекты, файлы и ход выполнения задач доступны с телефона. Для связи вне дома используется **ваш сервер** в роли ретранслятора.

## Скачать

| Устройство | Файл |
| --- | --- |
| Android | [RemoteVibecode Relay.apk](https://github.com/wilmain-arch/RemoteVibecode/releases/download/bundle-v0.2.0/RemoteVibecode-0.9.7-relay-debug.apk) |
| ПК с Windows | [RemoteVibecodeAgent.exe](https://github.com/wilmain-arch/RemoteVibecode/releases/download/bundle-v0.2.0/RemoteVibecodeAgent.exe) |
| ПК с Arch Linux | [remotevibecode-agent.pkg.tar.zst](https://github.com/wilmain-arch/RemoteVibecode/raw/main/releases/remotevibecode-agent-0.1.1-1-any.pkg.tar.zst) |
| Сервер Debian 13 / Ubuntu 26.04 | [remotevibecode-relay.deb](https://github.com/wilmain-arch/RemoteVibecode/releases/download/bundle-v0.2.0/remotevibecode-relay_0.1.0_all.deb) |

## Подключить

1. Установите ретранслятор на своём сервере и откройте TCP-порты **8765–8767**. [Инструкция для сервера](relay/README.md).
2. Установите Codex CLI на ПК и войдите в свой аккаунт. Запустите Windows-агент и укажите адрес сервера, его секрет и отпечаток сертификата. [Инструкция для ПК](agent/README.md).
3. Установите APK на телефон. В агенте откройте QR-код, в приложении нажмите **«Сканировать QR-код»**.

На Arch Linux агент устанавливается через `sudo pacman -U ./remotevibecode-agent-0.1.1-1-any.pkg.tar.zst`. [Инструкция для Arch Linux](arch/README.md).

После привязки приложение открывает чаты без повторного ввода кода. Телефон подключается через ваш сервер; на домашнем роутере ПК проброс портов не нужен. [Подробнее о подключении Android](android/RELAY_APK.md).

**Статус:** предварительная версия. APK подписан отладочным ключом; полный маршрут через реальный сервер и Windows-ПК ещё не проверен.
