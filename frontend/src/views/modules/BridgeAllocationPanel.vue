<template>
  <section class="alloc-panel" data-testid="allocation-panel">
    <header class="alloc-head">
      <div>
        <h3>分摊明细审核</h3>
        <p class="page-desc">
          按层级版本复算每笔订单：重复展开到同一父节点的路径只保留一份，各笔分摊之和必须等于订单金额。
        </p>
      </div>
      <div class="page-actions">
        <label class="alloc-versions">
          <span>层级版本</span>
          <select v-model="selectedVersion" data-testid="allocation-version" @change="reload">
            <option value="">当前生效版本</option>
            <option v-for="item in versions" :key="item['版本']" :value="item['版本']">
              {{ item['版本'] }}（{{ item['状态'] }}）
            </option>
          </select>
        </label>
        <button class="btn" data-testid="refresh-allocations" type="button" @click="reload">
          刷新分摊
        </button>
        <button class="btn primary" data-testid="export-allocations" type="button" @click="exportAllocations">
          导出分摊明细
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value" :class="{ 'value-bad': card.bad }">{{ card.value }}</strong>
      </article>
    </div>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-if="notice" class="notice-text">{{ notice }}</p>

    <table class="data-table alloc-orders">
      <thead>
        <tr>
          <th>事实键</th><th>订单金额</th><th>层级版本</th><th>分摊合计</th>
          <th>金额校验</th><th>分摊笔数</th><th>父节点</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="item in orders"
          :key="item['事实键']"
          :data-testid="`order-${item['事实键']}`"
          :data-balanced="item['金额校验']"
          :class="{ selected: item['事实键'] === activeOrder }"
          @click="selectOrder(item['事实键'])"
        >
          <td>{{ item['事实键'] }}</td>
          <td>{{ item['订单金额'] }}</td>
          <td>{{ item['层级版本'] }}</td>
          <td>{{ item['分摊合计'] }}</td>
          <td :class="item['金额校验'] === '相等' ? 'value-ok' : 'value-bad'">
            {{ item['金额校验'] }}<span v-if="item['错误']" class="error-text">（{{ item['错误'] }}）</span>
          </td>
          <td>{{ item['分摊笔数'] }}</td>
          <td>{{ item['父节点'].join('、') }}</td>
        </tr>
        <tr v-if="!orders.length">
          <td colspan="7" class="empty-state">所选层级版本下没有可分摊的订单</td>
        </tr>
      </tbody>
    </table>

    <h4 class="alloc-subtitle">
      分摊路径
      <span v-if="activeOrder" class="subtitle-note">当前订单 {{ activeOrder }}</span>
    </h4>
    <table class="data-table alloc-rows">
      <thead>
        <tr>
          <th>事实键</th><th>层级版本</th><th>父节点</th><th>层级路径</th>
          <th>权重合计</th><th>分摊金额</th><th>来源成员</th>
          <th>审核状态</th><th>前次审核</th><th>审核操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in allocationRows"
          :key="`${row['事实键']}-${row['层级版本']}-${row['父节点']}`"
          :data-testid="`allocation-${row['事实键']}-${row['父节点']}`"
        >
          <td>{{ row['事实键'] }}</td>
          <td>{{ row['层级版本'] }}</td>
          <td>{{ row['父节点'] }}</td>
          <td>{{ row['层级路径'] }}</td>
          <td>{{ row['权重合计'] }}</td>
          <td>{{ row['分摊金额'] }}</td>
          <td>{{ row['来源成员'] }}</td>
          <td :class="row['审核状态'] === '已审核' ? 'value-ok' : 'value-warn'">
            {{ row['审核状态'] }}<span v-if="row['审核人']">（{{ row['审核人'] }}）</span>
          </td>
          <td>
            <span v-if="row['前次审核版本']">
              {{ row['前次审核版本'] }} / {{ row['前次审核节点'] }} / {{ row['前次审核金额'] }}
            </span>
            <span v-else>—</span>
          </td>
          <td class="row-actions">
            <button
              class="link"
              type="button"
              :data-testid="`audit-${row['事实键']}-${row['父节点']}`"
              :disabled="row['审核状态'] === '已审核'"
              @click="approve(row)"
            >
              审核通过
            </button>
          </td>
        </tr>
        <tr v-if="!allocationRows.length">
          <td colspan="10" class="empty-state">暂无分摊路径</td>
        </tr>
      </tbody>
    </table>

    <template v-if="historyRows.length">
      <h4 class="alloc-subtitle">历史审核（按当时的层级版本复算，便于追溯先前节点）</h4>
      <table class="data-table alloc-history">
        <thead>
          <tr>
            <th>事实键</th><th>层级版本</th><th>父节点</th><th>层级路径</th>
            <th>分摊金额</th><th>订单分摊合计</th><th>审核人</th><th>审核时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in historyRows" :key="`${row['事实键']}-${row['层级版本']}-${row['父节点']}`">
            <td>{{ row['事实键'] }}</td>
            <td>{{ row['层级版本'] }}</td>
            <td>{{ row['父节点'] }}</td>
            <td>{{ row['层级路径'] }}</td>
            <td>{{ row['分摊金额'] }}</td>
            <td>{{ row['分摊合计'] }}</td>
            <td>{{ row['审核人'] }}</td>
            <td>{{ row['审核时间'] }}</td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  auditBridgeAllocation,
  downloadBridgeAllocations,
  listHierarchyVersions,
  loadBridgeAllocations,
} from '@/api/backend-service'
import type { AllocationExport, AllocationRow, HierarchyVersion, OrderCheck } from '@/data/types'

const versions = ref<HierarchyVersion[]>([])
const selectedVersion = ref('')
const orders = ref<OrderCheck[]>([])
const allocationRows = ref<AllocationRow[]>([])
const historyRows = ref<AllocationRow[]>([])
const activeOrder = ref('')
const errorMessage = ref('')
const notice = ref('')

const cards = computed(() => {
  const balanced = orders.value.filter((item) => item['金额校验'] === '相等').length
  const approved = allocationRows.value.filter((row) => row['审核状态'] === '已审核').length
  return [
    { label: '待审核订单', value: orders.value.length, bad: false },
    { label: '金额校验相等', value: `${balanced}/${orders.value.length}`, bad: balanced !== orders.value.length },
    { label: '分摊路径数', value: allocationRows.value.length, bad: false },
    { label: '已审核路径', value: approved, bad: false },
  ]
})

function applyExport(payload: AllocationExport) {
  orders.value = payload.orders
  allocationRows.value = payload.rows
  historyRows.value = payload.audit_trail
  if (!orders.value.some((item) => item['事实键'] === activeOrder.value)) {
    activeOrder.value = orders.value[0]?.['事实键'] ?? ''
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    versions.value = await listHierarchyVersions('DIM_STORE')
    const payload = await loadBridgeAllocations(activeOrder.value, selectedVersion.value)
    applyExport(payload)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分摊明细读取失败'
  }
}

function selectOrder(orderKey: string) {
  activeOrder.value = orderKey
  notice.value = ''
  void reload()
}

async function approve(row: AllocationRow) {
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await auditBridgeAllocation({
      order_key: row['事实键'],
      parent: row['父节点'],
      decision: '审核通过',
      version: row['层级版本'],
      auditor: '当前审核人',
    })
    notice.value = `${row['事实键']} / ${row['父节点']}：${result.message}`
    const payload = await loadBridgeAllocations(activeOrder.value, selectedVersion.value)
    applyExport(payload)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '审核失败'
  }
}

async function exportAllocations() {
  errorMessage.value = ''
  try {
    await downloadBridgeAllocations(activeOrder.value, selectedVersion.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

onMounted(reload)
</script>
