"""桥接分摊测试：层级版本、重复路径合并、金额守恒、审核与节点更正。"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app import store as store_module
from app.routers.resource import bridge_export_csv, module_export_csv
from app.store import Store
from app.services import bridge_allocation as alloc
from app.services.dim_bridge import DimBridgeService
from app.services.hierarchy_build import HierarchyBuildService


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    """每个测试使用独立 store 实例，并注入到模块级服务与导出接口。"""
    fresh = Store()
    monkeypatch.setattr(store_module, "store", fresh)
    monkeypatch.setattr("app.services.base.store", fresh)
    monkeypatch.setattr("app.services.dim_bridge.store", fresh)
    monkeypatch.setattr("app.services.hierarchy_build.store", fresh)
    return fresh


class _Request:
    def __init__(self, query: dict[str, str] | None = None) -> None:
        self.query_params = query or {}


def _rows(store: Store, order_key: str, version: str | None = None) -> list[dict]:
    return alloc.export_allocations(store, order_key=order_key, version=version)["rows"]


def _csv_text(response) -> str:
    async def collect() -> str:
        return "".join([chunk async for chunk in response.body_iterator])
    return asyncio.run(collect())


class TestDuplicatePaths:
    """重复展开的同一路径只保留一份，权重与金额合并。"""

    def test_same_parent_paths_merge_into_one_row(self, store: Store) -> None:
        rows = _rows(store, "ORD-2026-1001")
        assert [row["父节点"] for row in rows] == ["EAST", "SOUTH"]
        east = next(row for row in rows if row["父节点"] == "EAST")
        # SH-PD、SH-XH、HZ-XH 都上卷到华东大区，权重合并为 0.35+0.15+0.30
        assert east["权重合计"] == "0.8"
        assert east["分摊金额"] == "9600.00"
        assert east["来源成员"] == "SH-PD、SH-XH、HZ-XH"

    def test_duplicate_member_registration_is_ignored(self, store: Store) -> None:
        rows = _rows(store, "ORD-2026-1001")
        east = next(row for row in rows if row["父节点"] == "EAST")
        # HZ-XH 在成员权重表里登记了两次，只按首条 0.30 计算
        assert east["权重合计"] == "0.8"
        assert east["来源成员"].count("HZ-XH") == 1
        assert sum(Decimal(row["分摊金额"]) for row in rows) == Decimal("12000.00")

    def test_rows_are_unique_per_version_and_parent(self, store: Store) -> None:
        rows = _rows(store, "ORD-2026-1003")
        keys = [(row["事实键"], row["层级版本"], row["父节点"]) for row in rows]
        assert len(keys) == len(set(keys))


class TestAmountConservation:
    """各笔分摊之和必须等于原订单金额，含分位尾差。"""

    @pytest.mark.parametrize("order_key", ["ORD-2026-1001", "ORD-2026-1002", "ORD-2026-1003", "ORD-2026-1004"])
    def test_allocations_sum_to_order_amount(self, store: Store, order_key: str) -> None:
        export = alloc.export_allocations(store, order_key=order_key)
        summary = export["orders"][0]
        assert summary["金额校验"] == "相等"
        assert Decimal(summary["分摊合计"]) == Decimal(summary["订单金额"])
        rows = export["rows"]
        assert sum(Decimal(row["分摊金额"]) for row in rows) == Decimal(summary["订单金额"])

    def test_cent_residual_goes_to_largest_weight(self, store: Store) -> None:
        rows = _rows(store, "ORD-2026-1004")
        assert [row["父节点"] for row in rows] == ["GZ", "HZ", "SH"]
        assert [row["分摊金额"] for row in rows] == ["33.34", "33.33", "33.33"]
        assert sum(Decimal(row["分摊金额"]) for row in rows) == Decimal("100.00")

    def test_amounts_are_rounded_to_cent(self, store: Store) -> None:
        rows = _rows(store, "ORD-2026-1003")
        assert sum(Decimal(row["分摊金额"]) for row in rows) == Decimal("8888.88")
        for row in rows:
            assert Decimal(row["分摊金额"]) == Decimal(row["分摊金额"]).quantize(Decimal("0.01"))

    def test_missing_member_reports_error_instead_of_wrong_total(self, store: Store) -> None:
        store.rows("_bridge_member_weights").append({
            "id": 99, "事实键": "ORD-2026-1002", "成员节点": "NOT_EXIST", "权重": "0.1",
        })
        export = alloc.export_allocations(store, order_key="ORD-2026-1002")
        assert export["orders"][0]["金额校验"] == "不符"
        assert "NOT_EXIST" in export["orders"][0]["错误"]


class TestVersionedHierarchy:
    """层级构建提供指定版本父子路径，桥接按版本计算并标注版本与节点。"""

    def test_paths_carry_version_and_parent_chain(self, store: Store) -> None:
        service = HierarchyBuildService()
        paths = service.paths("H-2026.10.1")
        assert paths["版本"] == "H-2026.10.1"
        node = next(item for item in paths["paths"] if item["节点"] == "SH-PD")
        assert node["父节点"] == "SH"
        assert node["路径"] == "EAST/SH/SH-PD"
        assert node["层级名称"] == "门店"

    def test_unknown_version_is_rejected(self, store: Store) -> None:
        with pytest.raises(ValueError, match="层级版本不存在"):
            alloc.hierarchy_paths(store, "H-2099.1")

    def test_export_rows_record_version_and_node(self, store: Store) -> None:
        rows = _rows(store, "ORD-2026-1002")
        assert {row["层级版本"] for row in rows} == {"H-2026.10.1"}
        assert {row["父节点"] for row in rows} == {"EAST", "SOUTH"}
        assert all(row["分摊层级"] == "大区" for row in rows)

    def test_rollup_level_selects_area_nodes(self, store: Store) -> None:
        rows = _rows(store, "ORD-2026-1003")
        assert {row["父节点"] for row in rows} == {"SH", "HZ", "GZ"}
        assert all(row["分摊层级"] == "区" for row in rows)


class TestAuditAndNodeMove:
    """部分路径审核后区域目录更正父节点：新版本重算整单，旧审核节点可追溯。"""

    def test_partial_audit_keeps_other_rows_pending(self, store: Store) -> None:
        rows = _rows(store, "ORD-2026-1002")
        status = {row["父节点"]: row["审核状态"] for row in rows}
        assert status == {"EAST": "已审核", "SOUTH": "待审核"}

    def test_move_node_creates_immutable_version(self, store: Store) -> None:
        service = HierarchyBuildService()
        result = service.move_node("H-2026.10.1", "HZ", "SOUTH", operator="区域目录")
        assert result["版本"] == "H-2026.10.2"
        assert result["新路径"] == "SOUTH/HZ"

        old = service.paths("H-2026.10.1")
        old_hz = next(item for item in old["paths"] if item["节点"] == "HZ")
        assert old_hz["路径"] == "EAST/HZ"

        new = service.paths("H-2026.10.2")
        new_hz = next(item for item in new["paths"] if item["节点"] == "HZ")
        assert new_hz["父节点"] == "SOUTH"

    def test_move_node_recomputes_whole_order_under_new_version(self, store: Store) -> None:
        service = HierarchyBuildService()
        service.move_node("H-2026.10.1", "HZ", "SOUTH", operator="区域目录")

        rows = _rows(store, "ORD-2026-1002")
        # HZ-XH 从华东迁到华南，整笔订单只剩华南一个父节点，但金额仍然守恒
        assert [(row["父节点"], row["分摊金额"]) for row in rows] == [("SOUTH", "5000.00")]
        assert rows[0]["层级版本"] == "H-2026.10.2"

        export = alloc.export_allocations(store, order_key="ORD-2026-1002")
        assert export["rows"][0]["前次审核版本"] == "H-2026.10.1"
        assert export["rows"][0]["前次审核节点"] == "EAST"
        assert export["rows"][0]["前次审核金额"] == "3000.00"
        assert export["orders"][0]["金额校验"] == "相等"

    def test_old_version_still_recomputes_previous_audit_node(self, store: Store) -> None:
        HierarchyBuildService().move_node("H-2026.10.1", "HZ", "SOUTH")
        export = alloc.export_allocations(store, order_key="ORD-2026-1002", version="H-2026.10.1")
        east = next(row for row in export["rows"] if row["父节点"] == "EAST")
        assert east["分摊金额"] == "3000.00"
        assert east["审核状态"] == "已审核"
        assert export["orders"][0]["金额校验"] == "相等"

    def test_moved_order_export_lists_history_and_current(self, store: Store) -> None:
        HierarchyBuildService().move_node("H-2026.10.1", "HZ", "SOUTH")
        export = alloc.export_allocations(store, order_key="ORD-2026-1002")
        assert [row["记录类型"] for row in export["rows"]] == ["分摊"]
        history = export["audit_trail"]
        assert [(item["层级版本"], item["父节点"], item["分摊金额"]) for item in history] == [
            ("H-2026.10.1", "EAST", "3000.00"),
        ]
        # 历史行按旧版本复算，整单在旧版本下同样守恒
        assert history[0]["金额校验"] == "相等"
        assert history[0]["分摊合计"] == "5000.00"

    def test_audit_recomputes_amount_server_side(self, store: Store) -> None:
        service = DimBridgeService()
        before = _rows(store, "ORD-2026-1002")
        south = next(row for row in before if row["父节点"] == "SOUTH")
        result = service.audit("ORD-2026-1002", "SOUTH", "审核通过", auditor="李审")
        assert result["row"]["分摊金额"] == south["分摊金额"]
        assert result["audit"]["审核状态"] == "已审核"
        assert result["summary"]["金额校验"] == "相等"

    def test_audit_rejects_unknown_parent(self, store: Store) -> None:
        with pytest.raises(ValueError, match="没有父节点"):
            DimBridgeService().audit("ORD-2026-1002", "NOWHERE", "审核通过")

    def test_cycle_move_is_refused(self, store: Store) -> None:
        # EAST 移到它自己的孙节点 SH-PD 下面会形成环路
        with pytest.raises(ValueError, match="循环"):
            HierarchyBuildService().move_node("H-2026.10.1", "EAST", "SH-PD")

    def test_node_cannot_be_its_own_parent(self, store: Store) -> None:
        with pytest.raises(ValueError, match="自身"):
            HierarchyBuildService().move_node("H-2026.10.1", "SH", "SH")


class TestBridgeExport:
    """导出文件按标注版本复算，审核状态与金额都能逐笔核对。"""

    def test_export_csv_lists_key_parent_amount_and_version(self, store: Store) -> None:
        text = _csv_text(bridge_export_csv(order_key="ORD-2026-1002"))
        lines = text.strip().splitlines()
        header = lines[0].lstrip("﻿").split(",")
        for column in ("事实键", "父节点", "分摊金额", "层级版本", "金额校验"):
            assert column in header
        data = [dict(zip(header, line.split(","))) for line in lines[1:]]
        allocations = [row for row in data if row["记录类型"] == "分摊"]
        assert len(allocations) == 2
        assert {row["层级版本"] for row in allocations} == {"H-2026.10.1"}
        assert sum(Decimal(row["分摊金额"]) for row in allocations) == Decimal("5000.00")
        assert [row["审核状态"] for row in allocations] == ["已审核", "待审核"]

    def test_export_csv_reflects_moved_node(self, store: Store) -> None:
        HierarchyBuildService().move_node("H-2026.10.1", "HZ", "SOUTH")
        text = _csv_text(bridge_export_csv(order_key="ORD-2026-1002"))
        lines = text.strip().splitlines()
        header = lines[0].lstrip("﻿").split(",")
        data = [dict(zip(header, line.split(","))) for line in lines[1:]]
        allocations = [row for row in data if row["记录类型"] == "分摊"]
        assert [(row["层级版本"], row["父节点"], row["分摊金额"]) for row in allocations] == [
            ("H-2026.10.2", "SOUTH", "5000.00"),
        ]
        history = [row for row in data if row["记录类型"] == "历史审核"]
        assert [(row["层级版本"], row["父节点"], row["分摊金额"]) for row in history] == [
            ("H-2026.10.1", "EAST", "3000.00"),
        ]

    def test_generic_bridge_export_includes_allocation_rows(self, store: Store) -> None:
        text = _csv_text(module_export_csv("dim_bridge", _Request(), keyword=None, status=None))
        lines = text.strip().splitlines()
        header = lines[0].lstrip("﻿").split(",")
        assert header[:3] == ["id", "桥接名称", "多对多维度"]
        assert "分摊金额" in header and "层级版本" in header
        allocation_lines = [line for line in lines[1:] if ",分摊," in line]
        assert len(allocation_lines) == len(alloc.export_allocations(store)["rows"])
