"""桥接关联服务：管理维度与事实之间的多对多桥接表定义。

增强能力：
- 消费 dim.published 和 fact.published 通道校验多对多关系
- 权重字段验证（权重值范围）
- 时效性管理：生效/失效时间与主记录标记
- 桥接表数据生成模拟
- 按层级版本分摊：成员权重经层级父子路径上卷，重复路径合并后逐笔导出供审核
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services import bridge_allocation
from app.services.base import BaseModuleService
from app.store import store

# 导出在业务字段之外追加的分摊列；common 是每种导出行都有的列，
# detail 只有「分摊」行才有，历史审核行留空以免误读成当前版本的分摊。
ALLOCATION_COMMON_FIELDS = ["记录类型", "事实键", "订单金额", "分摊合计", "金额校验"]
ALLOCATION_DETAIL_FIELDS = [
    "层级版本", "父节点", "层级路径", "权重合计", "分摊金额", "来源成员",
    "审核状态", "审核人", "审核时间", "错误",
]


class DimBridgeService(BaseModuleService):
    MODULE = "dim_bridge"
    ENTITY = "桥接表"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["桥接名称", "多对多维度", "关联事实", "权重字段", "生效时间", "失效时间", "主记录标记"]
    REQUIRED_FIELDS = ["桥接名称", "多对多维度", "关联事实"]
    STATUS_ORDER = ["草稿", "已生效", "已失效"]
    ACTION_RULES = {"启用桥接": "草稿", "停用桥接": "已生效"}
    NEGATIVE_ACTIONS = ["停用桥接"]

    def export_rows(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        path: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """导出桥接关联：桥接表清单之后追加按层级版本复算的分摊明细。

        参数里带 version / 事实键 时只导出对应版本或订单；未带时每笔订单用
        各自维度当前生效的层级版本重算，保证每行标注的版本与金额一致。
        """
        params = dict(filters or {})
        version = str(params.pop("version", "") or "")
        order_key = str(params.pop("事实键", "") or "")
        export = bridge_allocation.export_allocations(
            store, order_key=order_key or None, version=version or None)

        rows: list[dict[str, Any]] = []
        for bridge in self._bridge_rows(params, keyword, status, path):
            rows.append({
                **bridge,
                "记录类型": "桥接定义",
                "事实键": "", "订单金额": "", "分摊合计": "", "金额校验": "",
            })
        for item in export["orders"]:
            rows.append({
                "记录类型": "订单校验",
                "桥接名称": f"订单 {item['事实键']}",
                "多对多维度": "",
                "关联事实": "",
                "权重字段": "—",
                "生效时间": "",
                "失效时间": "",
                "主记录标记": "",
                "status": "已校验" if item["金额校验"] == "相等" else "校验失败",
                "事实键": item["事实键"],
                "订单金额": item["订单金额"],
                "层级版本": item["层级版本"],
                "分摊合计": item["分摊合计"],
                "金额校验": item["金额校验"],
                "错误": item.get("错误", ""),
            })
        for item in export["rows"]:
            rows.append({
                **{field: item.get(field, "") for field in ALLOCATION_COMMON_FIELDS},
                "桥接名称": f"分摊 {item['事实键']}",
                "多对多维度": "",
                "关联事实": "",
                "权重字段": "—",
                "生效时间": "",
                "失效时间": "",
                "主记录标记": "",
                "status": item.get("审核状态", ""),
                **{field: item.get(field, "") for field in ALLOCATION_DETAIL_FIELDS},
                "前次审核版本": item.get("前次审核版本", ""),
                "前次审核节点": item.get("前次审核节点", ""),
                "前次审核金额": item.get("前次审核金额", ""),
                "备注": item.get("备注", ""),
            })
        for item in export["audit_trail"]:
            rows.append({
                **{field: item.get(field, "") for field in ALLOCATION_COMMON_FIELDS},
                "桥接名称": f"历史审核 {item['事实键']}",
                "多对多维度": "",
                "关联事实": "",
                "权重字段": "—",
                "生效时间": "",
                "失效时间": "",
                "主记录标记": "",
                "status": item.get("审核状态", ""),
                **{field: item.get(field, "") for field in ALLOCATION_DETAIL_FIELDS},
                "备注": item.get("备注", ""),
            })
        return rows

    def _bridge_rows(
        self,
        filters: dict[str, str],
        keyword: str | None,
        status: str | None,
        path: list[str] | None,
    ) -> list[dict[str, Any]]:
        merged = [
            *ALLOCATION_COMMON_FIELDS, *ALLOCATION_DETAIL_FIELDS,
            "前次审核版本", "前次审核节点", "前次审核金额", "备注",
        ]
        return super().export_rows(
            filters=filters, keyword=keyword, status=status,
            path=[*(path or []), *merged])

    def list_allocations(
        self,
        order_key: str | None = None,
        version: str | None = None,
        db: Any | None = None,
    ) -> dict[str, Any]:
        return bridge_allocation.export_allocations(
            db or store, order_key=order_key, version=version)

    def audit(
        self,
        order_key: str,
        parent_node: str,
        decision: str,
        *,
        version: str | None = None,
        auditor: str = "",
        remark: str = "",
        db: Any | None = None,
    ) -> dict[str, Any]:
        db = db or store
        result = bridge_allocation.audit_allocation(
            db, order_key, parent_node, decision,
            version=version, auditor=auditor, remark=remark)
        db.channel.publish("bridge.allocation_audited", {
            "order_key": order_key,
            "version": result["audit"].get("层级版本", ""),
            "parent": parent_node,
            "decision": decision,
        })
        return result

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        many_dims = str(values.get("多对多维度", "")).strip()
        related_fact = str(values.get("关联事实", "")).strip()

        # 校验维度存在性
        _validate_bridge_dims(many_dims, related_fact)

        # 设置默认时间
        if not values.get("生效时间"):
            values["生效时间"] = "2026-01-01"
        if not values.get("失效时间"):
            values["失效时间"] = "9999-12-31"
        if not values.get("主记录标记"):
            values["主记录标记"] = "Y"

        # 消费通道数据
        _consume_bridge_context(many_dims, related_fact)
        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing

        store.channel.publish("bridge.defined", {
            "bridge_name": entry["桥接名称"],
            "dims": many_dims,
            "fact": related_fact,
            "entry_id": entry["id"],
        })

        store.artifacts.register(
            "dim_bridge", "bridge_table",
            f"bridge_{entry['id']}",
            {"bridge_name": entry["桥接名称"], "dims": many_dims, "fact": related_fact},
            ["dim_definition", "fact_design", "dim_bridge"],
        )
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry, message = super().run_action(entry_id, action)
        if entry is None:
            return None, message

        if action == "启用桥接":
            store.channel.publish("bridge.activated", {
                "bridge_name": entry["桥接名称"],
                "entry_id": entry_id,
            })
        elif action == "停用桥接":
            entry["失效时间"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            store.channel.publish("bridge.deactivated", {
                "bridge_name": entry["桥接名称"],
                "entry_id": entry_id,
            })
        return entry, message

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        active = sum(1 for r in store.rows(self.MODULE) if r.get("status") == "已生效")
        snapshot = bridge_allocation.export_allocations(store)
        base["active_bridges"] = active
        base["allocation_versions"] = [item["版本"] for item in snapshot["versions"]]
        base["allocation_checks"] = {
            "orders": len(snapshot["orders"]),
            "balanced": sum(1 for item in snapshot["orders"] if item["金额校验"] == "相等"),
            "rows": len(snapshot["rows"]),
            "audited": sum(1 for item in snapshot["rows"] if item["审核状态"] == "已审核"),
        }
        return base


def _validate_bridge_dims(dim_str: str, fact_str: str) -> None:
    dims = [d.strip() for d in dim_str.split(".") if d.strip()]
    existing = {r.get("维度编码") for r in store.rows("dim_definition")}
    valid = [d for d in dims if d in existing]
    if valid:
        store.channel.publish("bridge.dims_validated", {"valid_dims": valid, "dim_str": dim_str})


def _consume_bridge_context(dim_str: str, fact_str: str) -> None:
    store.channel.consume("dim.published", "dim_bridge")
    store.channel.consume("fact.published", "dim_bridge")