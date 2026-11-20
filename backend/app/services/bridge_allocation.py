"""桥接分摊：按层级版本展开桥接关系，合并重复路径并生成可审核的导出明细。

链路：hierarchy_build（指定版本的父子路径）→ dim_bridge（成员权重）→ 分摊明细。

核心规则：
- 一笔订单可关联多个维度成员（多对多），每个成员带权重；
- 成员按指定层级版本的父子路径上卷到「分摊层级」父节点；
- 重复展开到同一路径只保留一份，权重与金额合并；
- 同一成员重复登记只取一条，避免重复展开造成金额翻倍；
- 分摊金额按分四舍五入，尾差落到权重最大的节点，保证各笔之和等于订单金额；
- 审核记录按「事实键 + 层级版本 + 父节点」保存，层级版本变更后可追溯先前审核节点。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

CENT = Decimal("0.01")
LEVEL_NAMES = {1: "大区", 2: "区", 3: "门店"}
DECISION_TO_STATUS = {"审核通过": "已审核", "审核驳回": "已驳回"}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except Exception as exc:  # noqa: BLE001 - 金额非法时给出可读错误
        raise ValueError(f"金额格式不正确：{value}") from exc


def _weight(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"权重格式不正确：{value}") from exc


def _next_version_label(base: str, existing: set[str]) -> str:
    match = re.match(r"^(.*?)(\d+)$", base)
    if match:
        prefix, number = match.group(1), int(match.group(2))
    else:
        prefix, number = base, 0
    candidate = f"{prefix}{number + 1}"
    while candidate in existing:
        number += 1
        candidate = f"{prefix}{number + 1}"
    return candidate


# ---------------------------------------------------------------------------
# 层级版本与父子路径
# ---------------------------------------------------------------------------


def list_versions(store: Any, dim_code: str | None = None) -> list[dict[str, Any]]:
    rows = [
        row for row in store.rows("_hierarchy_versions")
        if not dim_code or str(row.get("维度", "")) == dim_code
    ]
    rows.sort(key=lambda row: int(row.get("id", 0)))
    return [dict(row) for row in rows]


def get_version(store: Any, version: str) -> dict[str, Any] | None:
    for row in store.rows("_hierarchy_versions"):
        if str(row.get("版本", "")) == version:
            return row
    return None


def latest_version(store: Any, dim_code: str | None = None) -> str | None:
    candidates = [
        row for row in store.rows("_hierarchy_versions")
        if (not dim_code or str(row.get("维度", "")) == dim_code)
        and str(row.get("状态", "")) == "已生效"
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda row: int(row.get("id", 0)))
    return str(candidates[-1].get("版本", ""))


def hierarchy_nodes(store: Any, version: str) -> list[dict[str, Any]]:
    """返回指定层级版本的父子路径明细（副本，避免外部改动）。"""
    rows = [
        dict(row) for row in store.rows("_hierarchy_nodes")
        if str(row.get("版本", "")) == version
    ]
    rows.sort(key=lambda row: (int(row.get("层级", 0)), str(row.get("路径", ""))))
    return rows


def _index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("节点", "")): row for row in rows}


def _ancestor_chain(index: dict[str, dict[str, Any]], node_code: str) -> list[dict[str, Any]]:
    """从根节点到指定节点的完整路径；检测断链与循环。"""
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current = node_code
    while current:
        if current in seen:
            raise ValueError(f"层级路径存在循环引用：{node_code}")
        row = index.get(current)
        if row is None:
            raise ValueError(f"层级版本中找不到节点：{current}")
        chain.append(row)
        seen.add(current)
        current = str(row.get("父节点", "") or "")
    chain.reverse()
    return chain


def _rebuild_paths(rows: list[dict[str, Any]]) -> None:
    """按父节点关系重算层级、层级名称与路径。"""
    index = _index(rows)
    resolved: dict[str, dict[str, Any]] = {}

    def resolve(code: str, visiting: set[str]) -> dict[str, Any]:
        if code in resolved:
            return resolved[code]
        if code in visiting:
            raise ValueError(f"层级路径存在循环引用：{code}")
        row = index[code]
        parent = str(row.get("父节点", "") or "")
        if parent:
            if parent not in index:
                raise ValueError(f"父节点不存在：{parent}")
            parent_row = resolve(parent, visiting | {code})
            level = int(parent_row["层级"]) + 1
            row["层级"] = level
            row["层级名称"] = LEVEL_NAMES.get(level, f"{level}级")
            row["路径"] = f"{parent_row['路径']}/{code}"
        else:
            row["层级"] = 1
            row["层级名称"] = LEVEL_NAMES.get(1, "大区")
            row["路径"] = code
        resolved[code] = row
        return row

    for code in index:
        resolve(code, set())


def hierarchy_paths(
    store: Any,
    version: str | None = None,
    dim_code: str | None = None,
) -> dict[str, Any]:
    """层级构建对外提供的指定版本父子路径。"""
    target = version or latest_version(store, dim_code)
    if not target:
        raise ValueError("没有可用的层级版本")
    meta = get_version(store, target)
    if meta is None:
        raise ValueError(f"层级版本不存在：{target}")
    nodes = hierarchy_nodes(store, target)
    return {
        "版本": target,
        "维度": meta.get("维度", ""),
        "状态": meta.get("状态", ""),
        "基于版本": meta.get("基于版本", ""),
        "节点数": len(nodes),
        "paths": [
            {
                "节点": row.get("节点", ""),
                "父节点": row.get("父节点", ""),
                "层级": row.get("层级", 0),
                "层级名称": row.get("层级名称", ""),
                "路径": row.get("路径", ""),
            }
            for row in nodes
        ],
    }


def move_node(
    store: Any,
    version: str,
    node: str,
    new_parent: str,
    *,
    new_version: str | None = None,
    operator: str = "",
    remark: str = "",
) -> dict[str, Any]:
    """区域目录更正：把节点挂到新父节点下，生成不可变的新层级版本。

    旧版本保留原路径，已审核记录仍可按旧版本追溯；新版本按更正后的路径重算整笔订单。
    """
    meta = get_version(store, version)
    if meta is None:
        raise ValueError(f"层级版本不存在：{version}")
    if not node or not new_parent:
        raise ValueError("移动节点和新父节点不能为空")
    if node == new_parent:
        raise ValueError("节点不能挂到自身下面")

    base_rows = hierarchy_nodes(store, version)
    if not base_rows:
        raise ValueError(f"层级版本 {version} 没有节点")
    index = _index(base_rows)
    if node not in index:
        raise ValueError(f"层级版本 {version} 中找不到节点：{node}")
    if new_parent not in index:
        raise ValueError(f"层级版本 {version} 中找不到新父节点：{new_parent}")

    # 防止把节点挂到自己的后代下面形成环
    cursor = new_parent
    while cursor:
        if cursor == node:
            raise ValueError(f"移动 {node} 到 {new_parent} 会形成循环层级")
        cursor = str(index[cursor].get("父节点", "") or "")

    versions = store.rows("_hierarchy_versions")
    existing = {str(row.get("版本", "")) for row in versions}
    label = new_version or _next_version_label(version, existing)
    if label in existing:
        raise ValueError(f"层级版本已存在：{label}")

    cloned = [dict(row) for row in base_rows]
    for row in cloned:
        row.pop("id", None)
    target = next(row for row in cloned if str(row.get("节点", "")) == node)
    target["父节点"] = new_parent
    _rebuild_paths(cloned)

    next_id = max((int(row.get("id", 0)) for row in store.rows("_hierarchy_nodes")), default=0) + 1
    for row in cloned:
        row["id"] = next_id
        row["版本"] = label
        next_id += 1
    store.rows("_hierarchy_nodes").extend(cloned)

    meta["状态"] = "已归档"
    versions.append({
        "id": max((int(row.get("id", 0)) for row in versions), default=0) + 1,
        "版本": label,
        "维度": meta.get("维度", ""),
        "状态": "已生效",
        "基于版本": version,
        "生成时间": _now(),
        "说明": remark or f"{operator or '区域目录'} 将 {node} 移动至 {new_parent}",
    })

    affected = [
        _order_summary(store, order, label)
        for order in _orders(store, dim_code=str(meta.get("维度", "")))
    ]

    store.channel.publish("hierarchy.node_moved", {
        "base_version": version,
        "new_version": label,
        "node": node,
        "new_parent": new_parent,
        "operator": operator,
    })
    store.channel.publish("hierarchy.version_created", {
        "version": label,
        "base_version": version,
        "dim_code": meta.get("维度", ""),
        "node_count": len(cloned),
    })

    moved_row = next(row for row in cloned if str(row.get("节点", "")) == node)
    return {
        "版本": label,
        "基于版本": version,
        "移动节点": node,
        "新父节点": new_parent,
        "新路径": moved_row.get("路径", ""),
        "影响订单": affected,
    }


# ---------------------------------------------------------------------------
# 订单、成员与分摊计算
# ---------------------------------------------------------------------------


def _orders(
    store: Any,
    order_key: str | None = None,
    dim_code: str | None = None,
) -> list[dict[str, Any]]:
    rows = [
        row for row in store.rows("_bridge_orders")
        if (not order_key or str(row.get("事实键", "")) == order_key)
        and (not dim_code or str(row.get("维度", "")) == dim_code)
    ]
    rows.sort(key=lambda row: int(row.get("id", 0)))
    return rows


def get_order(store: Any, order_key: str) -> dict[str, Any] | None:
    orders = _orders(store, order_key=order_key)
    return orders[0] if orders else None


def _members(store: Any, order_key: str) -> list[dict[str, Any]]:
    """订单关联成员及权重；同一成员重复登记只保留首条，避免重复展开。"""
    seen: set[str] = set()
    members: list[dict[str, Any]] = []
    rows = sorted(
        (row for row in store.rows("_bridge_member_weights")
         if str(row.get("事实键", "")) == order_key),
        key=lambda row: int(row.get("id", 0)),
    )
    for row in rows:
        node = str(row.get("成员节点", ""))
        if not node or node in seen:
            continue
        seen.add(node)
        members.append({
            "成员节点": node,
            "权重": _weight(row.get("权重", "0")),
        })
    return members


def _rollup_target(chain: list[dict[str, Any]], rollup: str) -> dict[str, Any]:
    for node in chain:
        if str(node.get("层级名称", "")) == str(rollup):
            return node
    try:
        level = int(str(rollup))
    except (TypeError, ValueError):
        level = None
    if level is not None:
        for node in chain:
            if int(node.get("层级", 0)) == level:
                return node
    return chain[0]


def compute_order_allocations(
    store: Any,
    order: dict[str, Any],
    version: str,
) -> list[dict[str, Any]]:
    """按指定层级版本复算一笔订单的全部分摊行，重复路径合并为一行。"""
    nodes = hierarchy_nodes(store, version)
    if not nodes:
        raise ValueError(f"层级版本 {version} 没有父子路径")
    index = _index(nodes)

    groups: dict[str, dict[str, Any]] = {}
    for member in _members(store, str(order.get("事实键", ""))):
        chain = _ancestor_chain(index, member["成员节点"])
        target = _rollup_target(chain, str(order.get("分摊层级", "大区")))
        key = str(target.get("节点", ""))
        group = groups.get(key)
        if group is None:
            group = {
                "事实键": order.get("事实键", ""),
                "层级版本": version,
                "父节点": key,
                "层级路径": target.get("路径", key),
                "分摊层级": order.get("分摊层级", ""),
                "权重": Decimal("0"),
                "来源成员": [],
            }
            groups[key] = group
        group["权重"] += member["权重"]
        if member["成员节点"] not in group["来源成员"]:
            group["来源成员"].append(member["成员节点"])

    if not groups:
        return []

    amount = _money(order.get("订单金额", "0"))
    total_weight = sum((group["权重"] for group in groups.values()), Decimal("0"))
    if total_weight <= 0:
        raise ValueError(f"订单 {order.get('事实键', '')} 的成员权重合计必须大于 0")

    ordered = sorted(groups.values(), key=lambda group: str(group["父节点"]))
    allocated = Decimal("0")
    for group in ordered:
        share = (amount * group["权重"] / total_weight).quantize(
            CENT, rounding=ROUND_HALF_UP)
        group["分摊金额"] = share
        allocated += share

    # 分位四舍五入的尾差落到权重最大的节点，保证分摊合计等于订单金额
    residual = amount - allocated
    if residual != 0:
        # 权重相同的节点按父节点排序取第一个，保证同一订单每次导出结果稳定
        biggest = max(ordered, key=lambda group: (group["权重"], -ordered.index(group)))
        biggest["分摊金额"] += residual

    rows: list[dict[str, Any]] = []
    for group in ordered:
        weight = group["权重"].normalize()
        rows.append({
            "事实键": group["事实键"],
            "订单金额": str(amount),
            "层级版本": group["层级版本"],
            "父节点": group["父节点"],
            "层级路径": group["层级路径"],
            "分摊层级": group["分摊层级"],
            "权重合计": str(weight),
            "分摊金额": str(group["分摊金额"].quantize(CENT)),
            "来源成员": "、".join(group["来源成员"]),
        })
    return rows


def _order_summary(store: Any, order: dict[str, Any], version: str) -> dict[str, Any]:
    key = str(order.get("事实键", ""))
    amount = _money(order.get("订单金额", "0"))
    try:
        rows = compute_order_allocations(store, order, version)
    except ValueError as exc:
        return {
            "事实键": key,
            "订单金额": str(amount),
            "层级版本": version,
            "分摊合计": "0.00",
            "金额校验": "不符",
            "错误": str(exc),
            "分摊笔数": 0,
            "父节点": [],
        }
    total = sum((Decimal(row["分摊金额"]) for row in rows), Decimal("0"))
    return {
        "事实键": key,
        "订单金额": str(amount),
        "层级版本": version,
        "分摊合计": str(total.quantize(CENT)),
        "金额校验": "相等" if total == amount else "不符",
        "分摊笔数": len(rows),
        "父节点": [row["父节点"] for row in rows],
    }


# ---------------------------------------------------------------------------
# 审核
# ---------------------------------------------------------------------------


def _audit_key(order_key: str, version: str, parent: str) -> tuple[str, str, str]:
    return order_key, version, parent


def _audits(store: Any, order_key: str | None = None) -> list[dict[str, Any]]:
    rows = [
        row for row in store.rows("_bridge_audits")
        if not order_key or str(row.get("事实键", "")) == order_key
    ]
    rows.sort(key=lambda row: int(row.get("id", 0)))
    return rows


def _find_audit(
    store: Any, order_key: str, version: str, parent: str,
) -> dict[str, Any] | None:
    for row in store.rows("_bridge_audits"):
        if str(row.get("事实键", "")) == order_key \
                and str(row.get("层级版本", "")) == version \
                and str(row.get("父节点", "")) == parent:
            return row
    return None


def _prior_audit(
    store: Any, order_key: str, version: str, parent: str,
) -> dict[str, Any] | None:
    candidates = [
        row for row in store.rows("_bridge_audits")
        if str(row.get("事实键", "")) == order_key
        and str(row.get("层级版本", "")) != version
    ]
    if not candidates:
        return None
    same_parent = [row for row in candidates if str(row.get("父节点", "")) == parent]
    pool = same_parent or candidates
    pool.sort(key=lambda row: str(row.get("审核时间", "")))
    return pool[-1]


def audit_allocation(
    store: Any,
    order_key: str,
    parent_node: str,
    decision: str,
    *,
    version: str | None = None,
    auditor: str = "",
    remark: str = "",
) -> dict[str, Any]:
    """审核单条分摊路径；金额按所选层级版本重新计算，不接受调用方传入。"""
    if decision not in DECISION_TO_STATUS:
        raise ValueError(f"不支持的审核结论：{decision}")
    order = get_order(store, order_key)
    if order is None:
        raise ValueError(f"订单不存在：{order_key}")
    target_version = version or latest_version(store, str(order.get("维度", "")))
    if not target_version:
        raise ValueError("没有可用的层级版本")

    rows = compute_order_allocations(store, order, target_version)
    row = next((item for item in rows if str(item["父节点"]) == parent_node), None)
    if row is None:
        raise ValueError(f"订单 {order_key} 在版本 {target_version} 下没有父节点 {parent_node}")

    audits = store.rows("_bridge_audits")
    audit = _find_audit(store, order_key, target_version, parent_node)
    payload = {
        "事实键": order_key,
        "层级版本": target_version,
        "父节点": parent_node,
        "层级路径": row["层级路径"],
        "权重合计": row["权重合计"],
        "分摊金额": row["分摊金额"],
        "来源成员": row["来源成员"],
        "审核状态": DECISION_TO_STATUS[decision],
        "审核人": auditor or "当前审核人",
        "审核时间": _now(),
        "备注": remark,
    }
    if audit is None:
        payload["id"] = max((int(item.get("id", 0)) for item in audits), default=0) + 1
        audits.append(payload)
    else:
        audit.update(payload)
        audit["id"] = int(audit.get("id", 0))

    summary = _order_summary(store, order, target_version)
    return {
        "audit": dict(audit if audit is not None else payload),
        "row": {**row, "审核状态": payload["审核状态"],
                "审核人": payload["审核人"], "审核时间": payload["审核时间"]},
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def _audit_trail(store: Any, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = {str(order.get("事实键", "")) for order in orders}
    trail = [dict(row) for row in _audits(store) if str(row.get("事实键", "")) in keys]
    trail.sort(key=lambda row: (str(row.get("事实键", "")), str(row.get("审核时间", ""))))
    return trail


def export_allocations(
    store: Any,
    *,
    order_key: str | None = None,
    version: str | None = None,
    include_history: bool = True,
) -> dict[str, Any]:
    """导出分摊明细：逐笔列出事实键、父节点、分摊金额、层级版本。

    每一行都按该行标注的层级版本重新计算，重复路径只保留一份；
    历史审核记录单独标记为「历史审核」，其父节点与金额同样按对应版本复算。
    """
    orders = _orders(store, order_key=order_key)
    rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    for order in orders:
        target_version = version or latest_version(store, str(order.get("维度", "")))
        if not target_version:
            summaries.append({
                "事实键": order.get("事实键", ""),
                "订单金额": str(_money(order.get("订单金额", "0"))),
                "层级版本": "",
                "分摊合计": "0.00",
                "金额校验": "不符",
                "错误": "没有可用的层级版本",
                "分摊笔数": 0,
                "父节点": [],
            })
            continue
        try:
            computed = compute_order_allocations(store, order, target_version)
        except ValueError as exc:
            summaries.append({
                "事实键": order.get("事实键", ""),
                "订单金额": str(_money(order.get("订单金额", "0"))),
                "层级版本": target_version,
                "分摊合计": "0.00",
                "金额校验": "不符",
                "错误": str(exc),
                "分摊笔数": 0,
                "父节点": [],
            })
            continue

        summary = _order_summary(store, order, target_version)
        summaries.append(summary)
        amount = _money(order.get("订单金额", "0"))

        for row in computed:
            audit = _find_audit(store, row["事实键"], target_version, row["父节点"])
            prior = _prior_audit(store, row["事实键"], target_version, row["父节点"])
            rows.append({
                "记录类型": "分摊",
                **row,
                "订单金额": str(amount),
                "审核状态": audit.get("审核状态", "待审核") if audit else "待审核",
                "审核人": audit.get("审核人", "") if audit else "",
                "审核时间": audit.get("审核时间", "") if audit else "",
                "前次审核版本": prior.get("层级版本", "") if prior else "",
                "前次审核节点": prior.get("父节点", "") if prior else "",
                "前次审核金额": prior.get("分摊金额", "") if prior else "",
                "分摊合计": summary["分摊合计"],
                "金额校验": summary["金额校验"],
                "备注": "",
            })

    history: list[dict[str, Any]] = []
    if include_history:
        current_by_order = {item["事实键"]: item["层级版本"] for item in summaries}
        for audit in _audit_trail(store, orders):
            order = next((o for o in orders if str(o.get("事实键", "")) == str(audit.get("事实键", ""))), None)
            if order is None:
                continue
            audit_version = str(audit.get("层级版本", ""))
            if audit_version == current_by_order.get(str(order.get("事实键", ""))):
                # 当前版本已经体现在分摊行的审核状态里，历史区不重复
                continue
            amount = str(_money(order.get("订单金额", "0")))
            recomputed_amount = str(audit.get("分摊金额", ""))
            recomputed_path = str(audit.get("层级路径", ""))
            source_members = str(audit.get("来源成员", ""))
            weight_total = str(audit.get("权重合计", ""))
            try:
                version_rows = compute_order_allocations(store, order, audit_version)
                matched = next(
                    (row for row in version_rows if str(row["父节点"]) == str(audit.get("父节点", ""))),
                    None,
                )
            except ValueError:
                matched = None
            if matched is not None:
                recomputed_amount = matched["分摊金额"]
                recomputed_path = matched["层级路径"]
                source_members = matched["来源成员"]
                weight_total = matched["权重合计"]
            version_summary = _order_summary(store, order, audit_version)
            history.append({
                "记录类型": "历史审核",
                "事实键": audit.get("事实键", ""),
                "订单金额": amount,
                "层级版本": audit_version,
                "父节点": audit.get("父节点", ""),
                "层级路径": recomputed_path,
                "分摊层级": order.get("分摊层级", ""),
                "权重合计": weight_total,
                "分摊金额": recomputed_amount,
                "来源成员": source_members,
                "审核状态": audit.get("审核状态", ""),
                "审核人": audit.get("审核人", ""),
                "审核时间": audit.get("审核时间", ""),
                "前次审核版本": "",
                "前次审核节点": "",
                "前次审核金额": "",
                "分摊合计": version_summary["分摊合计"],
                "金额校验": version_summary["金额校验"],
                "备注": audit.get("备注", ""),
            })

    return {
        "版本": version or "",
        "versions": list_versions(store),
        "orders": summaries,
        "rows": rows,
        "audit_trail": history,
    }
