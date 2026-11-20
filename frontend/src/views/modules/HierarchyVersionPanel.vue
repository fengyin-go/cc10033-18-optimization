<template>
  <section class="alloc-panel" data-testid="hierarchy-version-panel">
    <header class="alloc-head">
      <div>
        <h3>层级版本与区域更正</h3>
        <p class="page-desc">
          层级构建按版本提供父子路径，桥接关联按这些路径计算分摊；区域目录更正会生成新版本，旧版本保持不可变以便追溯已审核节点。
        </p>
      </div>
      <div class="page-actions">
        <label class="alloc-versions">
          <span>层级版本</span>
          <select v-model="selectedVersion" data-testid="hierarchy-version" @change="loadPaths">
            <option v-for="item in versions" :key="item['版本']" :value="item['版本']">
              {{ item['版本'] }}（{{ item['状态'] }}）
            </option>
          </select>
        </label>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-if="notice" class="notice-text">{{ notice }}</p>

    <form class="analysis-form" @submit.prevent="moveNode">
      <label>
        <span>移动节点</span>
        <select v-model="moveForm.node" data-testid="move-node">
          <option v-for="node in nodes" :key="node['节点']" :value="node['节点']">
            {{ node['节点'] }}（{{ node['路径'] }}）
          </option>
        </select>
      </label>
      <label>
        <span>新父节点</span>
        <select v-model="moveForm.parent" data-testid="move-parent">
          <option v-for="node in nodes" :key="node['节点']" :value="node['节点']">
            {{ node['节点'] }}（{{ node['层级名称'] }}）
          </option>
        </select>
      </label>
      <label>
        <span>操作人</span>
        <input v-model="moveForm.operator" data-testid="move-operator" placeholder="区域目录" />
      </label>
      <button class="btn primary" data-testid="submit-move" type="submit" :disabled="moving">
        {{ moving ? '更正中…' : '生成新版本' }}
      </button>
    </form>

    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value" :class="{ 'value-bad': card.bad }">{{ card.value }}</strong>
      </article>
    </div>

    <table class="data-table">
      <thead>
        <tr><th>版本</th><th>状态</th><th>基于版本</th><th>生成时间</th><th>说明</th></tr>
      </thead>
      <tbody>
        <tr v-for="item in versions" :key="item['版本']" :data-testid="`version-${item['版本']}`">
          <td>{{ item['版本'] }}</td>
          <td>{{ item['状态'] }}</td>
          <td>{{ item['基于版本'] || '—' }}</td>
          <td>{{ item['生成时间'] }}</td>
          <td>{{ item['说明'] }}</td>
        </tr>
      </tbody>
    </table>

    <h4 class="alloc-subtitle">父子路径明细</h4>
    <table class="data-table">
      <thead>
        <tr><th>节点</th><th>父节点</th><th>层级</th><th>层级名称</th><th>路径</th></tr>
      </thead>
      <tbody>
        <tr v-for="node in nodes" :key="node['节点']" :data-testid="`path-${node['节点']}`">
          <td>{{ node['节点'] }}</td>
          <td>{{ node['父节点'] || '—' }}</td>
          <td>{{ node['层级'] }}</td>
          <td>{{ node['层级名称'] }}</td>
          <td>{{ node['路径'] }}</td>
        </tr>
        <tr v-if="!nodes.length">
          <td colspan="5" class="empty-state">该版本没有层级节点</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { listHierarchyVersions, loadHierarchyPaths, moveHierarchyNode } from '@/api/backend-service'
import type { HierarchyPath, HierarchyVersion } from '@/data/types'

const versions = ref<HierarchyVersion[]>([])
const selectedVersion = ref('')
const nodes = ref<HierarchyPath[]>([])
const errorMessage = ref('')
const notice = ref('')
const moving = ref(false)
const moveForm = reactive({ node: '', parent: '', operator: '区域目录' })

const cards = computed(() => [
  { label: '层级版本数', value: versions.value.length, bad: false },
  { label: '当前版本节点', value: nodes.value.length, bad: false },
  { label: '生效版本', value: versions.value.filter((item) => item['状态'] === '已生效').length, bad: false },
])

async function loadVersions() {
  versions.value = await listHierarchyVersions('DIM_STORE')
  const active = versions.value.find((item) => item['状态'] === '已生效')
  if (!selectedVersion.value || !versions.value.some((item) => item['版本'] === selectedVersion.value)) {
    selectedVersion.value = active?.['版本'] ?? versions.value[0]?.['版本'] ?? ''
  }
}

async function loadPaths() {
  errorMessage.value = ''
  if (!selectedVersion.value) {
    nodes.value = []
    return
  }
  try {
    const payload = await loadHierarchyPaths(selectedVersion.value)
    nodes.value = payload.paths
    moveForm.node = nodes.value[0]?.['节点'] ?? ''
    moveForm.parent = nodes.value.find((item) => item['层级'] === 1)?.['节点'] ?? ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '层级路径读取失败'
  }
}

async function moveNode() {
  errorMessage.value = ''
  notice.value = ''
  moving.value = true
  try {
    const result = await moveHierarchyNode({
      version: selectedVersion.value,
      node: moveForm.node,
      new_parent: moveForm.parent,
      operator: moveForm.operator || '区域目录',
    })
    const unbalanced = result['影响订单'].filter((item) => item['金额校验'] !== '相等').length
    notice.value = `已生成 ${result['版本']}：${result['移动节点']} → ${result['新路径']}，`
      + `影响 ${result['影响订单'].length} 笔订单，金额不符 ${unbalanced} 笔`
    await loadVersions()
    selectedVersion.value = result['版本']
    await loadPaths()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '节点移动失败'
  } finally {
    moving.value = false
  }
}

onMounted(async () => {
  try {
    await loadVersions()
    await loadPaths()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '层级版本读取失败'
  }
})
</script>
