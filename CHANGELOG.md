# CHANGELOG

В этом файле фиксируются релизы, которые были выложены в production.

## Формат

Каждый релиз оформляется отдельным блоком:

```md
## YYYY-MM-DD

- Версия или commit: <commit_sha>
- Что вошло в релиз
- Важные изменения в данных, миграциях или инфраструктуре
```

## Unreleased

- Изменения в работе, которые еще не были выложены в production.

## 2026-05-07

- Версия или commit: `e707b56`
- Удалены ручные Telegram-команды `/water`, `/meal`, `/weight`, `/sleep`, `/activity`, `/goals`.
- Обновлены help-текст и документация бота под photo-first и import-first сценарии.

## 2026-05-07

- Версия или commit: `be9ddfe`
- Добавлен multi-user режим с invite-only onboarding для Telegram-бота.
- Добавлены пользователи, инвайты, private-chat only режим и admin-команды для управления инвайтами.
- Данные health, food drafts, decisions и finance переведены на изоляцию по `user_id`.
- Добавлена автомиграция MySQL-схемы с привязкой legacy-данных к owner-аккаунту `96445950`.
