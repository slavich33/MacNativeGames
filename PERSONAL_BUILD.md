# Personal Mac / iPhone build

Основа — upstream `84eebcd0985c867da8f805e4ed1a49e5eb3a2eaa`. Это личный профиль сборки, а не обещание одинаковой производительности на всех устройствах.

Upstream уже содержит native runtime, ProMotion/display-link ceiling, renderer frame lock, общий список шейдеров и основной протокол установки. Эти изменения не являются нашей разработкой.

Наши переносимые исправления: classic physical-device Xcode target с явными SDK/architecture/output directories; legacy PNG icons вместо actool; проверка provisioning coverage; строгая обработка device listing и возобновления переноса; retry seed copy; проверка allocation/final flush при распаковке seed; ограничение числа процессов подготовки шейдеров через `TOTK_BUILD_JOBS`. Xcode пока использует два jobs. `--build-jobs` в upstream tools не добавлен.

Личные настройки отдельно от generic PR: iPhone запускается с renderer lock 60, без interpolation и переключателя 30/60; сохранённый выбор 30 сбрасывается. Настройка изображения — 540p/720p, default 720p; Full HD не выбран. Mac launcher задаёт верхний cap 60, real-time clock и interpolation off, сохраняя гостевой frame interval. Поэтому interval 2/3 может давать 30/20 FPS. `local/TOTK-720p.command` и `local/TOTK-900p.command` меняют только разрешение сцены при canvas 1080p.

Короткие локальные проверки: iPhone показал p50 около 16,67 мс при lock60; это не тест всего игрового мира. На Mac после прогрева в меню p50 экранного интервала был 16,67 мс, p95 — 50 мс, средняя частота около 38 FPS. Стабильные 60 на Mac не подтверждены. Этот замер выполнен на установленном runtime `1b60feb` с обновлённым launcher; полный Mac build этой ветки на основе `84eebcd` ещё не тестировался. Переносимые helpers из `local/` проверены только offline; их исходные локальные версии действительно использовались при сборке и установке на iPhone. Здесь проверены исходники, синтаксис и offline tests. Кэши помогают последующим запускам; новые состояния шейдеров могут требовать подготовки.

## Входные данные и настройки

Игра, ключи, прошивочные шрифты, SDK, Apple Development certificate и личная конфигурация находятся снаружи Git. Нужны собственные игровые файлы, полный совместимый Xcode с physical iOS SDK и Metal compiler, `xcodegen`, `cmake`, `ninja` и APFS с достаточным свободным местом. Helpers из `local/` работают на Python 3.11+, но выбранный build interpreter обязан быть Python 3.14+ с `compression.zstd` и `capstone`: он готовит runtime и shader seed. Для первого извлечения также нужны зависимости из `games/totk/requirements.txt`. Simulator не используется в нашем classic iOS path. Эти исправления проверялись на iPhone; legacy-icons/target изменения для Vision Pro отдельно не проверены.

Скопируй `local/config.example.json` в `${MACNATIVEGAMES_CONFIG_DIR:-$HOME/.config/macnativegames}/config.json` и заполни поля локально. Пример намеренно пустой. `device_id` — настроенный hardware UDID или свежий CoreDevice identifier. Для продления подписи сохраняй те же team/bundle/device настройки. Бесплатная Personal Team подпись требует регулярного обновления; она не становится постоянной.

Пути можно передать через `--tree`, `--out`, `--logs`, `--config-dir`; соответствующие переменные — `MACNATIVEGAMES_TREE`, `MACNATIVEGAMES_OUT`, `MACNATIVEGAMES_LOGS`, `MACNATIVEGAMES_CONFIG_DIR`. `--jobs` / `MACNATIVEGAMES_JOBS` задаёт 1–8 процессов (default 2). `MACNATIVEGAMES_PYTHON` выбирает Python executable, `DEVELOPER_DIR` — Xcode. Если явно выбран только CommandLineTools и стандартный Xcode существует, helpers подставляют его путь только в окружение дочернего процесса. Явный `DEVELOPER_DIR` и другой выбранный полный Xcode сохраняются; глобальный selector не меняется. Team/bundle/device можно также передать флагами `--team`, `--bundle-id`, `--device` или переменными `MACNATIVEGAMES_TEAM_ID`, `MACNATIVEGAMES_BUNDLE_ID`, `MACNATIVEGAMES_DEVICE_ID`. Значения и журналы не добавляй в репозиторий.

## Подготовка, сборка и установка

Для первого извлечения используй [инструкции upstream](docs/install.md). Например, с уже заданными внешними переменными:

```sh
python3 build_app.py "$GAME" -k "$KEYS" --platform ios \
  --ios-game-data documents --no-shader-reminder \
  --shared-fonts "$SHARED_FONTS" --work "$WORK_DIR" -o "$IOS_APP"
```

Это настоящая сборка из твоих данных; она может скачивать публичные build dependencies. Флаг `--no-shader-reminder` сохраняет ручной сбор запросов без установки watcher. Для повторной сборки из подготовленного дерева:

```sh
local/build-ios.command --tree "$TOTK_TREE" --out "$IOS_APP" --logs "$LOG_DIR"
local/build-ios.command --tree "$TOTK_TREE" --out "$NEW_IOS_APP" --logs "$LOG_DIR" --build
local/install-ios.command --tree "$TOTK_TREE" --out "$NEW_IOS_APP" --logs "$LOG_DIR" --check
local/install-ios.command --tree "$TOTK_TREE" --out "$NEW_IOS_APP" --logs "$LOG_DIR"
```

`build-ios` без `--build` только проверяет входы и инструменты. До запуска `make` он проверяет выбранный build Python и его импорты, upstream SHA-256 готового translator и исполняемый SPIRV-Cross; выбранный Python передаётся также в `make PY=…`. Существующая output app не перезаписывается. Подготовленный runtime, game-data и shader seed пересоздаются согласованно; повторное извлечение не требуется. Installer оставляет игровые данные и seed снаружи app, проверяет маркеры и допускает возобновление переноса; одинаковый размер сам по себе не считается доказательством совпадения содержимого. Перед обновлением закрой игру и сохрани внешнюю резервную копию профиля. Устройство должно быть сопряжено, разблокировано и иметь Developer Mode.

Для Mac сборки используй тот же upstream builder с `--platform macos` и собственными входами. Обычный запуск построенного из этой ветки app применяет личный cap; для сцены 720p/900p можно использовать `local/TOTK-720p.command` / `local/TOTK-900p.command --app "$MAC_APP"` (или `MACNATIVEGAMES_MAC_APP`). Это верхняя граница FPS, не ускорение медленных сцен.

## Ручное заполнение недостающих шейдеров

```sh
local/fill-ios-shaders.command --tree "$TOTK_TREE" --logs "$LOG_DIR" --check
local/fill-ios-shaders.command --tree "$TOTK_TREE" --logs "$LOG_DIR"
```

`--check` проверяет именно настроенное устройство и Documents, получает только новые top-level JSON-запросы, без перевода и загрузки пакета. Обычный запуск использует проверенный готовый translator, строит только запрошенные варианты, отправляет их на то же устройство и независимо читает обратно manifest. `done/` не переигрывается; ошибка доступа не означает отсутствие запросов. Игра не останавливается и не перезапускается автоматически. Helper ничего не публикует в GitHub и не устанавливает watcher.

## Проверки и границы экспорта

Offline tests используют синтетические данные; installer/device/open вызовы замоканы. Seed unpack regression компилирует небольшой Foundation harness на Mac, без Metal, игровых данных или устройства. Подпись и runtime FPS новой сборки нужно проверять отдельно после настоящей установки. `games/totk/ios/Sources/Info.plist` оставлен upstream placeholder: генерированный файл не сохраняется как наша ручная правка.

Generic PR должны содержать переносимые исправления и соответствующие тесты, отдельно от личных FPS/разрешения/языка, удаления UI controls и `local/` workflow. Не отправляй extracted/rewritten/build, ROM, ключи, сертификаты, profiles, .app/.ipa, seed/metallib/cache, save, device receipts и логи. Исходный shader list хранит идентификаторы, не игровой shader code; локальные найденные строки требуют отдельного точного merge.
