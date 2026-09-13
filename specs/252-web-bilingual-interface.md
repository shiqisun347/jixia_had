# 252 Web bilingual interface

> Version: v2.1
> Status: implementing
> Approved: 2026-09-04

## Scope

- Add `zh-CN` and `en` interface locales to the Web application with `next-intl`.
- Keep all URLs, Core APIs, MatchActor behaviour, user-generated content, rule resources,
  prompts, transcripts, names and diagnostics unchanged.
- Persist a browser-local language preference; use browser language on first visit and Chinese
  when it is unavailable.

## Invariants

- Language changes never reconnect, restart or advance a match; Core remains the source of
  truth for all runtime state.
- API error codes remain stable. The Web maps them to localized actionable copy.
- The locale is reflected in `html[lang]` and metadata without a database schema or migration.
- No credentials, `api.md`, `.env`, provider responses or user speech are read into messages.

## Acceptance

- A keyboard-accessible `中文 / EN` switch appears on public, compact match and admin headers.
- Localized navigation, authentication, global confirmation/toast, key match states and admin
  controls are rendered through typed message keys.
- Tests cover defaulting, local persistence, browser-language fallback, HTML language updates,
  and rendering in both locales; lint, typecheck, Web tests and browser tests pass.

## Rollback

- Remove the Web-only locale provider, messages and switcher. No persisted server data or
  migration requires rollback.
