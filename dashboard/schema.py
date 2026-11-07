"""Описание настроек бота для веб-форм.

Единый источник — core.settings.DEFAULTS. Здесь только подписи, типы и
разбиение по разделам, чтобы дашборд и бот не расходились.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from core.settings import GuildSettingsStore


@dataclass(frozen=True, slots=True)
class Field:
    key: str
    label: str
    kind: str = "text"
    options: tuple[tuple[str, str], ...] = ()
    help: str = ""
    placeholder: str = ""
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True, slots=True)
class Section:
    slug: str
    title: str
    description: str
    fields: tuple[Field, ...] = field(default_factory=tuple)


SECTIONS: tuple[Section, ...] = (
    Section(
        "logs",
        "Логи",
        "Куда бот пишет события сервера. Без канала логи выключены.",
        (
            Field("log_channel", "Общий канал логов", "channel"),
            Field("modlog_channel", "Канал модерации", "channel", help="Если пусто — используется общий лог."),
            Field("log_messages", "Сообщения", "bool"),
            Field("log_members", "Участники", "bool"),
            Field("log_roles", "Роли", "bool"),
            Field("log_channels", "Каналы", "bool"),
            Field("log_voice", "Голосовые каналы", "bool"),
            Field("log_invites", "Приглашения", "bool"),
            Field("log_moderation", "Действия модерации", "bool"),
        ),
    ),
    Section(
        "automod",
        "Автомодерация",
        "Фильтр запрещённых слов. Список слов — на отдельной странице.",
        (
            Field("automod_enabled", "Включить автомодерацию", "bool"),
            Field(
                "automod_action",
                "Действие",
                "select",
                (("delete", "Удалить сообщение"), ("warn", "Предупреждение"), ("timeout", "Тайм-аут")),
            ),
            Field("automod_timeout", "Тай-аут, секунды", "int", minimum=0),
            Field("automod_notify", "Уведомлять автора в DM", "bool"),
            Field("automod_ignored_channels", "Игнорируемые каналы", "channels"),
            Field("automod_ignored_roles", "Игнорируемые роли", "roles"),
        ),
    ),
    Section(
        "warnings",
        "Предупреждения",
        "Поведение предупреждений и пороги автонаказаний.",
        (
            Field("warnings_dm", "Отправлять предупреждение в DM", "bool"),
            Field(
                "warn_escalations",
                "Пороги наказаний (JSON)",
                "json",
                help='Список объектов: [{"count": 3, "action": "timeout", "duration": 3600}].',
            ),
        ),
    ),
    Section(
        "levels",
        "Уровни и лояльность",
        "Опыт за сообщения, награды за уровни и множители.",
        (
            Field("levels_enabled", "Включить уровни", "bool"),
            Field("levels_xp_min", "Минимум XP за сообщение", "int", minimum=0),
            Field("levels_xp_max", "Максимум XP за сообщение", "int", minimum=0),
            Field("levels_cooldown", "Кулдаун, секунды", "int", minimum=0),
            Field("levels_channel", "Канал объявлений об уровнях", "channel"),
            Field("levels_announce", "Объявлять новый уровень", "bool"),
            Field("levels_message", "Текст объявления", "textarea", placeholder="🎉 {user}, достигнут **{level}** уровень!"),
            Field("levels_ignored_channels", "Игнорируемые каналы", "channels"),
            Field("levels_xp_multiplier", "Множитель XP", "float", minimum=0, maximum=100),
            Field("levels_loyalty_multiplier", "Множитель лояльности", "float", minimum=0, maximum=100),
            Field("levels_booster_multiplier", "Бонус бустерам", "float", minimum=0, maximum=100),
            Field("levels_event_multiplier", "Множитель события", "float", minimum=0, maximum=100),
            Field("levels_event_until", "Событие до (ISO-время)", "text", placeholder="2026-12-31T23:59:00+00:00"),
            Field(
                "levels_role_boosts",
                "Бонусы ролей (JSON)",
                "json",
                help='Объект вида {"123456789": 1.5}.',
            ),
            Field("loyalty_per_message", "Лояльность за сообщение", "int", minimum=0),
            Field("loyalty_daily_base", "Дейли: база", "int", minimum=0),
            Field("loyalty_daily_step", "Дейли: шаг за серию", "int", minimum=0),
            Field("loyalty_daily_cap", "Дейли: максимум", "int", minimum=0),
        ),
    ),
    Section(
        "security",
        "Безопасность",
        "Антирейд, антиспам и капча.",
        (
            Field("antiraid_enabled", "Антирейд", "bool"),
            Field("antiraid_joins", "Входов для тревоги", "int", minimum=1),
            Field("antiraid_window", "Окно, секунды", "int", minimum=1),
            Field("antiraid_min_age_days", "Минимальный возраст аккаунта, дней", "int", minimum=0),
            Field(
                "antiraid_action",
                "Действие антирейда",
                "select",
                (("kick", "Кик"), ("ban", "Бан"), ("timeout", "Тайм-аут")),
            ),
            Field("antiraid_timeout", "Тайм-аут антирейда, секунды", "int", minimum=0),
            Field("antiraid_duration", "Длительность режима рейда, секунды", "int", minimum=0),
            Field("antispam_enabled", "Антиспам", "bool"),
            Field("antispam_count", "Сообщений за окно", "int", minimum=1),
            Field("antispam_window", "Окно антиспама, секунды", "int", minimum=1),
            Field(
                "antispam_action",
                "Действие антиспама",
                "select",
                (("delete", "Удалить"), ("timeout", "Тайм-аут")),
            ),
            Field("antispam_timeout", "Тайм-аут антиспама, секунды", "int", minimum=0),
            Field("antispam_ignored_channels", "Игнорируемые каналы", "channels"),
            Field("captcha_enabled", "Капча при входе", "bool"),
            Field("captcha_role", "Роль «проверен»", "role"),
            Field("captcha_unverified_role", "Роль до проверки", "role"),
            Field("captcha_timeout", "Время на капчу, секунды", "int", minimum=1),
            Field("captcha_kick", "Кикать за нерешённую капчу", "bool"),
        ),
    ),
    Section(
        "tickets",
        "Тикеты и апелляции",
        "Каналы логов и ограничения обращений.",
        (
            Field("ticket_log_channel", "Канал логов тикетов", "channel"),
            Field("ticket_max_open", "Максимум открытых тикетов", "int", minimum=1),
            Field("appeals_channel", "Канал апелляций", "channel"),
        ),
    ),
    Section(
        "misc",
        "Статистика и розыгрыши",
        "Каналы статистики настраиваются отдельно, здесь — общие переключатели.",
        (
            Field("stats_enabled", "Обновлять каналы статистики", "bool"),
            Field("giveaway_emoji", "Эмодзи розыгрышей", "text"),
            Field("locale", "Язык бота", "select", (("ru", "Русский"), ("en", "English"))),
        ),
    ),
)

SECTION_BY_SLUG = {section.slug: section for section in SECTIONS}


def all_keys() -> set[str]:
    return {field.key for section in SECTIONS for field in section.fields}


def _parse_bool(form: Any, key: str) -> bool:
    return key in form


def _parse_int(field: Field, raw: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field.label}: нужно целое число.") from exc
    if field.minimum is not None and value < field.minimum:
        raise ValueError(f"{field.label}: минимум {field.minimum}.")
    if field.maximum is not None and value > field.maximum:
        raise ValueError(f"{field.label}: максимум {field.maximum}.")
    return value


def _parse_float(field: Field, raw: str) -> float:
    try:
        value = float(raw.replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field.label}: нужно число.") from exc
    if field.minimum is not None and value < field.minimum:
        raise ValueError(f"{field.label}: минимум {field.minimum}.")
    if field.maximum is not None and value > field.maximum:
        raise ValueError(f"{field.label}: максимум {field.maximum}.")
    return value


def parse_field(
    field: Field,
    form: Any,
    channels: Iterable[dict[str, Any]],
    roles: Iterable[dict[str, Any]],
) -> Any:
    channel_ids = {int(item["id"]) for item in channels}
    role_ids = {int(item["id"]) for item in roles}
    raw = form.get(field.key, "")

    if field.kind == "bool":
        return _parse_bool(form, field.key)
    if field.kind == "int":
        return _parse_int(field, raw)
    if field.kind == "float":
        return _parse_float(field, raw)
    if field.kind == "select":
        values = {value for value, _ in field.options}
        if raw not in values:
            raise ValueError(f"{field.label}: недопустимое значение.")
        return raw
    if field.kind == "channel":
        if not raw:
            return None
        try:
            channel_id = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field.label}: неверный канал.") from exc
        if channel_id not in channel_ids:
            raise ValueError(f"{field.label}: канал не найден на сервере.")
        return channel_id
    if field.kind == "role":
        if not raw:
            return None
        try:
            role_id = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field.label}: неверная роль.") from exc
        if role_id not in role_ids:
            raise ValueError(f"{field.label}: роль не найдена на сервере.")
        return role_id
    if field.kind == "channels":
        result: list[int] = []
        for value in form.getlist(field.key):
            try:
                channel_id = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field.label}: неверный канал.") from exc
            if channel_id not in channel_ids:
                raise ValueError(f"{field.label}: канал не найден на сервере.")
            if channel_id not in result:
                result.append(channel_id)
        return result
    if field.kind == "roles":
        result: list[int] = []
        for value in form.getlist(field.key):
            try:
                role_id = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field.label}: неверная роль.") from exc
            if role_id not in role_ids:
                raise ValueError(f"{field.label}: роль не найдена на сервере.")
            if role_id not in result:
                result.append(role_id)
        return result
    if field.kind == "json":
        text = str(raw or "").strip()
        try:
            value = json.loads(text) if text else None
        except ValueError as exc:
            raise ValueError(f"{field.label}: некорректный JSON.") from exc
        if field.key == "warn_escalations":
            if not isinstance(value, list):
                raise ValueError(f"{field.label}: ожидается список.")
        elif field.key == "levels_role_boosts":
            if not isinstance(value, dict):
                raise ValueError(f"{field.label}: ожидается объект.")
        return value
    if field.kind == "textarea":
        return str(raw or "")
    return str(raw or "").strip()


def validate_section(
    section: Section,
    form: Any,
    channels: Iterable[dict[str, Any]],
    roles: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    values: dict[str, Any] = {}
    errors: list[str] = []
    for field_item in section.fields:
        try:
            values[field_item.key] = parse_field(field_item, form, channels, roles)
        except ValueError as exc:
            errors.append(str(exc))
    return values, errors


async def apply_section(
    settings: GuildSettingsStore,
    guild_id: int,
    section: Section,
    form: Any,
    channels: Iterable[dict[str, Any]],
    roles: Iterable[dict[str, Any]],
) -> tuple[bool, list[str]]:
    values, errors = validate_section(section, form, channels, roles)
    if errors:
        return False, errors
    for key, value in values.items():
        await settings.set(guild_id, key, value)
    return True, []
