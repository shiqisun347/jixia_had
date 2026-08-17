"""Server-side permission guards shared by HTTP and future realtime routes."""

from __future__ import annotations

from typing import Literal

from .session import AuthContext


class PermissionError(RuntimeError):
    """A normalized permission failure; transport maps it to an API error."""

    def __init__(self, code: Literal["password_change_required", "forbidden"]) -> None:
        super().__init__(code)
        self.code = code


def require_password_changed(context: AuthContext) -> AuthContext:
    if context.must_change_password:
        raise PermissionError("password_change_required")
    return context


def require_admin(context: AuthContext) -> AuthContext:
    changed_context = require_password_changed(context)
    if changed_context.role != "ADMIN":
        raise PermissionError("forbidden")
    return changed_context


__all__ = ["PermissionError", "require_admin", "require_password_changed"]
