"""Identity: users and role assignments (RBAC)."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPkMixin, enum_values
from app.schemas.events import ROLES


class Role(enum.StrEnum):
    """Fixed role set; values mirror ``app.schemas.events.ROLES``."""

    CATALOG_OWNER = "catalog_owner"
    COMMERCE_LEAD = "commerce_lead"
    FINANCE_APPROVER = "finance_approver"
    PROCUREMENT_LEAD = "procurement_lead"
    BUDGET_OWNER = "budget_owner"
    WAREHOUSE_STAFF = "warehouse_staff"
    INVENTORY_SUPERVISOR = "inventory_supervisor"
    ACCOUNTANT = "accountant"
    CUSTOMER_SERVICE = "customer_service"
    COMPLIANCE = "compliance"
    SYSTEM_ADMIN = "system_admin"


assert {r.value for r in Role} == set(ROLES), "Role enum drifted from app.schemas.events.ROLES"


class User(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "user"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    role_assignments: Mapped[list[RoleAssignment]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class RoleAssignment(UUIDPkMixin, Base):
    __tablename__ = "role_assignment"
    __table_args__ = (
        UniqueConstraint("user_id", "role", "scope", name="uq_role_assignment_user_id_role_scope"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    role: Mapped[Role] = mapped_column(
        Enum(Role, native_enum=False, length=32, values_callable=enum_values),
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(String(64), nullable=False)

    user: Mapped[User] = relationship(back_populates="role_assignments")


__all__ = ["Role", "RoleAssignment", "User"]
