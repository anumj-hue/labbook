"""Role-based navigation (spec §6.3). Add items here as later slices ship their pages."""

from dataclasses import dataclass

from flask import request, url_for
from flask_login import current_user

from .models import Role


@dataclass(frozen=True)
class NavItem:
    label: str
    endpoint: str
    icon: str
    roles: frozenset[Role] | None = None  # None = every signed-in user
    match: str | None = None  # endpoint prefix that marks the item active

    @property
    def url(self) -> str:
        return url_for(self.endpoint)

    @property
    def active(self) -> bool:
        endpoint = request.endpoint or ""
        return endpoint.startswith(self.match) if self.match else endpoint == self.endpoint


_STAFF_TECH = frozenset({Role.LAB_TECH})

MAIN_NAV = (
    NavItem("Home", "main.home", "house"),
    NavItem("Equipment", "catalogue.index", "flask-conical", match="catalogue."),
    NavItem("Calendar", "bookings.calendar", "calendar"),
    NavItem("My Bookings", "bookings.index", "calendar-check", match="bookings.index"),
    NavItem("Units", "units.index", "wrench", roles=_STAFF_TECH, match="units."),
)

ADMIN_NAV = (
    NavItem("Users", "admin.users", "users", match="admin.user"),
    NavItem("Equipment types", "admin.types", "package", match="admin.type"),
    NavItem("Labs", "admin.labs", "building-2", match="admin.lab"),
    NavItem("Academic calendar", "admin.calendar", "calendar-days", match="admin.calendar"),
    NavItem("Settings", "admin.settings", "settings", match="admin.settings"),
    NavItem("Privacy notice", "admin.privacy", "shield", match="admin.privacy"),
)


def nav_sections() -> list[tuple[str | None, list[NavItem]]]:
    if not current_user.is_authenticated:
        return []
    main = [i for i in MAIN_NAV if i.roles is None or current_user.has_role(*i.roles)]
    sections: list[tuple[str | None, list[NavItem]]] = [(None, main)]
    if current_user.role is Role.ADMIN:
        sections.append(("Admin", list(ADMIN_NAV)))
    return sections
