"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None


class MoveNodePayload(BaseModel):
    """区域目录更正：在某个层级版本上把节点改挂到新父节点，生成新版本。"""
    version: str
    node: str
    new_parent: str
    new_version: str = ""
    operator: str = ""
    remark: str = ""


class AuditPayload(BaseModel):
    """桥接分摊审核：结论只允许审核通过/审核驳回，金额由服务端按版本复算。"""
    order_key: str
    parent: str
    decision: str
    version: str = ""
    auditor: str = ""
    remark: str = ""
