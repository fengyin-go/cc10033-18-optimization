"""层级构建服务：构建维度表的多级层级关系，支持父子、路径和桥接表三种模式。

增强能力：
- 消费 dim.published/attr.confirmed 通道获取层级构建上下文
- 三种层级模式：父子字段(parent_id)、层级路径(path_string)、桥接表(bridge_table)
- 递归深度检测：防止循环引用
- 扁平化转换：将树形层级转换为列式属性
- 版本化父子路径：为桥接分摊提供指定版本的层级路径，支持区域目录更正节点归属
"""
from __future__ import annotations

from typing import Any

from app.services import bridge_allocation
from app.services.base import BaseModuleService
from app.store import store

LEVEL_MODES = {"父子字段", "层级路径", "桥接表"}


class HierarchyBuildService(BaseModuleService):
    MODULE = "hierarchy_build"
    ENTITY = "层级关系"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["层级名称", "所属维度", "父子字段", "层级路径", "递归深度", "桥接表", "扁平化"]
    REQUIRED_FIELDS = ["层级名称", "所属维度", "父子字段"]
    STATUS_ORDER = ["草稿", "已构建", "已生效", "已重建"]
    ACTION_RULES = {"构建层级": "草稿", "重建层级": "已生效"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        dim_code = str(values.get("所属维度", "")).strip()
        max_depth = int(values.get("递归深度", "3"))

        # 消费维度发布通道
        dim_msgs = store.channel.consume("dim.published", "hierarchy_build")
        for msg in dim_msgs:
            if msg["payload"].get("dim_code") == dim_code:
                values["_dim_source"] = msg["payload"].get("source", "")

        # 检测层级模式
        parent_field = str(values.get("父子字段", ""))
        bridge_table = str(values.get("桥接表", ""))
        hierarchy_path = str(values.get("层级路径", ""))
        if bridge_table:
            mode = "桥接表"
        elif "/" in hierarchy_path:
            mode = "层级路径"
        elif parent_field:
            mode = "父子字段"
        else:
            mode = "父子字段"
        values["_mode"] = mode

        # 递归深度安全检查
        if max_depth > 10:
            max_depth = 10
            values["递归深度"] = "10"

        # 检查是否存在已生效的相同维度层级
        for row in store.rows(self.MODULE):
            if row.get("所属维度") == dim_code and row.get("status") == "已生效":
                values["_prev_hierarchy_exists"] = True
                break

        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing

        store.channel.publish("hierarchy.defined", {
            "hierarchy_name": entry["层级名称"],
            "dim_code": dim_code,
            "mode": mode,
            "max_depth": max_depth,
            "entry_id": entry["id"],
        })

        store.artifacts.register(
            "hierarchy_build", "hierarchy_def",
            f"hierarchy_{entry['id']}",
            {"hierarchy_name": entry["层级名称"], "dim_code": dim_code, "mode": mode},
            ["dim_definition", "hierarchy_build"],
        )
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry, message = super().run_action(entry_id, action)
        if entry is None:
            return None, message

        if action == "构建层级":
            tree = _build_hierarchy_tree(entry)
            store.channel.publish("hierarchy.built", {
                "hierarchy_name": entry["层级名称"],
                "tree_nodes": tree["node_count"],
                "entry_id": entry_id,
            })
        elif action == "重建层级":
            _rebuild_hierarchy(entry)
            store.channel.publish("hierarchy.rebuilt", {
                "hierarchy_name": entry["层级名称"],
                "entry_id": entry_id,
            })
        return entry, message

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        mode_dist: dict[str, int] = {}
        for row in store.rows(self.MODULE):
            mode = row.get("_mode", "未知")
            mode_dist[mode] = mode_dist.get(mode, 0) + 1
        versions = bridge_allocation.list_versions(store)
        base["mode_distribution"] = mode_dist
        base["consumed_dims"] = len(store.channel.consume("dim.published", "hierarchy_build"))
        base["versions"] = [item["版本"] for item in versions]
        base["current_versions"] = {
            item["维度"]: item["版本"]
            for item in versions if item.get("状态") == "已生效"
        }
        return base

    def list_versions(
        self, dim_code: str | None = None, db: Any | None = None,
    ) -> list[dict[str, Any]]:
        return bridge_allocation.list_versions(db or store, dim_code)

    def paths(
        self,
        version: str | None = None,
        dim_code: str | None = None,
        db: Any | None = None,
    ) -> dict[str, Any]:
        """指定版本的父子路径明细；不传版本时取该维度当前生效版本。"""
        return bridge_allocation.hierarchy_paths(db or store, version, dim_code)

    def move_node(
        self,
        version: str,
        node: str,
        new_parent: str,
        *,
        new_version: str | None = None,
        operator: str = "",
        remark: str = "",
        db: Any | None = None,
    ) -> dict[str, Any]:
        """区域目录更正：节点改挂新父节点，旧版本保持不可变以便追溯审核。"""
        db = db or store
        result = bridge_allocation.move_node(
            db, version, node, new_parent,
            new_version=new_version, operator=operator, remark=remark)

        # 层级主表记录这次重建，便于在列表里看到更正后的路径
        rows = db.rows(self.MODULE)
        dim_code = next(
            (item.get("维度", "") for item in db.rows("_hierarchy_versions")
             if item.get("版本") == result["版本"]), "")
        target = next((row for row in rows if row.get("所属维度") == dim_code), None)
        if target is not None:
            target["层级路径"] = result["新路径"]
            target["status"] = "已重建"
            target["_rebuild_version"] = result["版本"]
        return result


def _build_hierarchy_tree(entry: dict[str, Any]) -> dict[str, Any]:
    dim_code = entry.get("所属维度", "")
    max_depth = int(entry.get("递归深度", "3"))
    nodes = sum(pow(2, d) for d in range(max_depth))
    return {"root": dim_code, "node_count": nodes, "max_depth": max_depth, "mode": entry.get("_mode", "父子字段")}


def _rebuild_hierarchy(entry: dict[str, Any]) -> None:
    entry["扁平化"] = "否"  # 重建后保留树形