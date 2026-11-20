"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, AuditPayload, EntryPayload, MoveNodePayload, PageResult
from app.services import service_for

router = APIRouter(prefix="/api", tags=["业务资源"])

def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


def require_bridge():
    service = service_for("dim_bridge")
    if service is None:
        raise HTTPException(status_code=404, detail="桥接关联模块未就绪")
    return service


def _csv_response(header: list[str], rows: list[list[Any]], filename: str) -> StreamingResponse:
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(header)
    for values in rows:
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/hierarchy_build/versions", response_model=dict)
def list_hierarchy_versions(dim_code: str | None = Query(default=None)) -> dict[str, Any]:
    """层级构建：列出可用层级版本。"""
    service = require_service("hierarchy_build")
    return {"items": service.list_versions(dim_code)}


@router.get("/hierarchy_build/paths", response_model=dict)
def get_hierarchy_paths(
    version: str | None = Query(default=None),
    dim_code: str | None = Query(default=None),
) -> dict[str, Any]:
    """层级构建：返回指定版本的父子路径，供桥接关联计算分摊。"""
    service = require_service("hierarchy_build")
    try:
        return service.paths(version, dim_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/hierarchy_build/move-node", response_model=dict)
def move_hierarchy_node(payload: MoveNodePayload) -> dict[str, Any]:
    """区域目录更正：移动区域节点并生成新的层级版本。"""
    service = require_service("hierarchy_build")
    try:
        return service.move_node(
            payload.version, payload.node, payload.new_parent,
            new_version=payload.new_version or None,
            operator=payload.operator, remark=payload.remark)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/dim_bridge/allocations", response_model=dict)
def list_bridge_allocations(
    order_key: str | None = Query(default=None),
    version: str | None = Query(default=None),
) -> dict[str, Any]:
    """桥接关联：按层级版本复算订单分摊，返回明细、金额校验和审核轨迹。"""
    service = require_bridge()
    try:
        return service.list_allocations(order_key=order_key, version=version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/dim_bridge/allocations/audit", response_model=dict)
def audit_bridge_allocation(payload: AuditPayload) -> dict[str, Any]:
    """桥接关联：审核单条分摊路径，金额按层级版本重新计算。"""
    service = require_bridge()
    try:
        return service.audit(
            payload.order_key, payload.parent, payload.decision,
            version=payload.version or None,
            auditor=payload.auditor, remark=payload.remark)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/dim_bridge/export", response_model=None)
def export_bridge_allocations(
    order_key: str | None = Query(default=None),
    version: str | None = Query(default=None),
) -> StreamingResponse:
    """桥接关联：导出分摊明细，逐笔列出事实键、父节点、分摊金额与层级版本。"""
    return bridge_export_csv(order_key, version)


def bridge_export_csv(
    order_key: str | None = None,
    version: str | None = None,
    db: Any | None = None,
) -> StreamingResponse:
    service = require_bridge()
    try:
        export = service.list_allocations(order_key=order_key, version=version, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    header = [
        "记录类型", "事实键", "订单金额", "分摊合计", "金额校验", "层级版本",
        "父节点", "层级路径", "权重合计", "分摊金额", "来源成员",
        "审核状态", "审核人", "审核时间", "前次审核版本", "前次审核节点",
        "前次审核金额", "备注",
    ]
    rows: list[list[Any]] = []
    for item in export["orders"]:
        rows.append([
            "订单校验", item["事实键"], item["订单金额"], item["分摊合计"],
            item["金额校验"], item["层级版本"], "", "", "", "", "",
            "", "", "", "", "", item.get("错误", ""),
        ])
    for item in export["rows"]:
        rows.append([
            item["记录类型"], item["事实键"], item["订单金额"], item["分摊合计"],
            item["金额校验"], item["层级版本"], item["父节点"], item["层级路径"],
            item["权重合计"], item["分摊金额"], item["来源成员"],
            item["审核状态"], item["审核人"], item["审核时间"],
            item["前次审核版本"], item["前次审核节点"], item["前次审核金额"], item["备注"],
        ])
    for item in export["audit_trail"]:
        rows.append([
            item["记录类型"], item["事实键"], item["订单金额"],
            item["分摊合计"], item["金额校验"],
            item["层级版本"], item["父节点"], item["层级路径"], item["权重合计"],
            item["分摊金额"], item["来源成员"], item["审核状态"], item["审核人"],
            item["审核时间"], "", "", "", item["备注"],
        ])
    return _csv_response(header, rows, "dim_bridge_allocations.csv")


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    return module_export_csv(module, request, keyword, status)


def module_export_csv(
    module: str,
    request: Request,
    keyword: str | None = None,
    status: str | None = None,
) -> StreamingResponse:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    extra = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key not in service.LIST_FIELDS and value.strip()
    }
    items = service.export_rows(
        filters={**filters, **extra}, keyword=keyword, status=status)
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    base_columns = ["id", *service.LIST_FIELDS, "status"]
    extra_columns: list[str] = []
    for item in items:
        for column in item:
            if column not in base_columns and column not in extra_columns:
                extra_columns.append(column)
    header = [*base_columns, *extra_columns]
    writer.writerow(header)
    for item in items:
        values = [item.get(column, "") for column in header]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/{entry_id}/actions", response_model=ActionResult)
def run_action(module: str, entry_id: int, payload: ActionPayload) -> ActionResult:
    service = require_service(module)
    entry, message = service.run_action(entry_id, payload.action.strip())
    return ActionResult(ok=entry is not None, message=message, entry=entry)
