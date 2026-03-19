<template>
  <div>
    <el-card>
      <template #header>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span>触达任务</span>
          <el-select v-model="selectedAccountId" placeholder="选择发送账号"
            style="width:200px" size="default">
            <el-option v-for="a in accounts" :key="a.id"
              :label="`${a.account_name} (${a.platform === 'xhs' ? '小红书' : a.platform === 'douyin' ? '抖音' : 'B站'})`" :value="a.id" />
          </el-select>
        </div>
      </template>
      <div style="display:flex;gap:10px;margin-bottom:12px;align-items:flex-start">
        <el-input v-model="promptText" type="textarea" :rows="2"
          placeholder="输入 AI 提示词（可选，留空使用默认 prompt）"
          style="flex:1" />
        <el-button type="warning" @click="batchGenerate"
          :loading="batchLoading" :disabled="!selectedRows.length">
          AI生成评论 ({{ selectedRows.length }})
        </el-button>
        <el-button type="success" @click="batchSend"
          :loading="batchSendLoading"
          :disabled="!confirmedSelectedCount || !selectedAccountId">
          批量发送 ({{ confirmedSelectedCount }})
        </el-button>
      </div>
      <el-table :data="pagedRecords" stripe @selection-change="onSelectionChange">
        <el-table-column type="selection" width="45" />
        <el-table-column label="平台" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.platform === 'xhs' ? 'danger' : row.platform === 'douyin' ? 'success' : ''">
              {{ row.platform === 'xhs' ? '小红书' : row.platform === 'douyin' ? '抖音' : 'B站' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="70">
          <template #default="{ row }">
            <el-tag size="small"
              :type="row.platform === 'douyin' ? 'success' : row.target_message ? '' : 'warning'">
              {{ row.platform === 'douyin' ? '视频' : row.target_message ? '回复' : '评论' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="标题" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <a v-if="row.platform === 'xhs' && row.target_note_id"
              :href="`https://www.xiaohongshu.com/explore/${row.target_note_id}`"
              target="_blank" rel="noopener"
              style="color:#409eff;text-decoration:none">
              {{ row.target_note_title || '-' }}
            </a>
            <a v-else-if="row.platform === 'douyin' && row.target_aweme_id"
              :href="`https://www.douyin.com/video/${row.target_aweme_id}`"
              target="_blank" rel="noopener"
              style="color:#409eff;text-decoration:none">
              {{ row.target_message || row.video_title || '-' }}
            </a>
            <a v-else-if="row.target_aid"
              :href="`https://www.bilibili.com/video/av${row.target_aid}`"
              target="_blank" rel="noopener"
              style="color:#409eff;text-decoration:none">
              {{ row.video_title || '-' }}
            </a>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="target_message" label="原评论"
          min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.platform === 'douyin' ? '(视频)' : row.target_message || '(一级评论)' }}
          </template>
        </el-table-column>
        <el-table-column prop="target_uname" label="评论者" width="90" />
        <el-table-column label="AI回复" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.ai_reply || '-' }}</template>
        </el-table-column>
        <el-table-column label="最终回复" min-width="160">
          <template #default="{ row }">
            <el-input v-if="row.status === 'ai_generated'"
              v-model="row.final_reply" type="textarea" :rows="2" />
            <span v-else>{{ row.final_reply || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="tagType(row.status)">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180">
          <template #default="{ row }">
            <el-button v-if="row.status==='ai_generated'" size="small"
              type="success" @click="confirmReply(row)">确认</el-button>
            <el-button v-if="row.status==='confirmed'" size="small"
              type="primary" @click="sendReply(row)"
              :disabled="!selectedAccountId" :loading="row._sending">
              发送</el-button>
            <el-button v-if="row.status==='needs_verify'" size="small"
              type="warning" @click="sendReply(row)"
              :disabled="!selectedAccountId" :loading="row._sending">
              重试</el-button>
            <el-button v-if="['sent','failed'].includes(row.status)" size="small"
              type="info" @click="resetToConfirmed(row)">
              重新发送</el-button>
            <el-button size="small" type="danger" @click="deleteRecord(row)">
              删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        style="margin-top:16px;justify-content:flex-end"
        background layout="total, sizes, prev, pager, next"
        :total="records.length" :page-sizes="[10, 20, 50, 100]"
        v-model:page-size="pageSize" v-model:current-page="currentPage"
      />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import http from '../api/http'
import { ElMessage, ElMessageBox } from 'element-plus'

const records = ref<any[]>([])
const pageSize = ref(10)
const currentPage = ref(1)
const pagedRecords = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return records.value.slice(start, start + pageSize.value)
})
watch(pageSize, () => { currentPage.value = 1 })
const accounts = ref<any[]>([])
const selectedAccountId = ref<number | null>(null)
const selectedRows = ref<any[]>([])
const promptText = ref('')
const batchLoading = ref(false)
const batchSendLoading = ref(false)

const confirmedSelectedCount = computed(() =>
  selectedRows.value.filter((r: any) => r.status === 'confirmed' || r.status === 'needs_verify').length
)

const statusLabel = (s: string) => {
  const m: Record<string, string> = {
    pending: '待生成', ai_generated: 'AI已生成',
    confirmed: '已确认', sent: '已发送', failed: '失败',
    needs_verify: '待验证',
  }
  return m[s] || s
}
const tagType = (s: string) => {
  if (s === 'sent') return 'success'
  if (s === 'failed') return 'danger'
  if (s === 'confirmed') return 'warning'
  if (s === 'needs_verify') return 'warning'
  if (s === 'ai_generated') return ''
  return 'info'
}

const onSelectionChange = (rows: any[]) => {
  selectedRows.value = rows
}

const loadRecords = async () => {
  try {
    const { data } = await http.get('/api/message/records')
    records.value = data.items.map((r: any) => ({
      ...r, _sending: false,
    }))
  } catch { /* */ }
}

const loadAccounts = async () => {
  try {
    const { data } = await http.get('/api/accounts')
    accounts.value = data.items || []
  } catch { /* */ }
}

const batchGenerate = async () => {
  const pendingIds = selectedRows.value
    .filter((r: any) => r.status === 'pending')
    .map((r: any) => r.id)
  if (!pendingIds.length) {
    ElMessage.warning('选中的记录中没有待生成的'); return
  }
  batchLoading.value = true
  try {
    const { data } = await http.post('/api/message/touch/batch-generate', {
      record_ids: pendingIds,
      prompt: promptText.value,
    })
    ElMessage.success(`AI生成完成: 成功 ${data.generated}, 失败 ${data.failed}`)
    await loadRecords()
  } catch { ElMessage.error('AI生成失败') }
  finally { batchLoading.value = false }
}

const batchSend = async () => {
  const confirmedIds = selectedRows.value
    .filter((r: any) => r.status === 'confirmed' || r.status === 'needs_verify')
    .map((r: any) => r.id)
  if (!confirmedIds.length) {
    ElMessage.warning('选中的记录中没有已确认或待验证的'); return
  }
  if (!selectedAccountId.value) {
    ElMessage.warning('请先选择发送账号'); return
  }
  batchSendLoading.value = true
  try {
    const { data } = await http.post('/api/message/touch/batch-send', {
      record_ids: confirmedIds,
      account_id: selectedAccountId.value,
    })
    if (data.error) {
      ElMessage.error(data.error)
    } else {
      let msg = `批量发送完成: 成功 ${data.sent}, 失败 ${data.failed}`
      if (data.skipped) msg += `, 跳过 ${data.skipped}`
      ElMessage.success(msg)
    }
    await loadRecords()
  } catch { ElMessage.error('批量发送失败') }
  finally { batchSendLoading.value = false }
}

const confirmReply = async (row: any) => {
  try {
    await http.put(`/api/message/touch/${row.id}`, {
      final_reply: row.final_reply, status: 'confirmed',
    })
    row.status = 'confirmed'
  } catch { ElMessage.error('确认失败') }
}

const resetToConfirmed = async (row: any) => {
  try {
    await http.put(`/api/message/touch/${row.id}`, { status: 'confirmed' })
    row.status = 'confirmed'
    ElMessage.success('已重置为已确认，可重新发送')
  } catch { ElMessage.error('重置失败') }
}

const sendReply = async (row: any) => {
  if (!selectedAccountId.value) {
    ElMessage.warning('请先选择发送账号'); return
  }
  row._sending = true
  try {
    const { data } = await http.post(
      `/api/message/touch/${row.id}/send`,
      { account_id: selectedAccountId.value },
    )
    if (data.error) { ElMessage.error(data.error); return }
    row.status = data.status
    if (data.status === 'sent') ElMessage.success('发送成功')
    else ElMessage.error('发送失败')
  } catch { ElMessage.error('发送请求失败') }
  finally { row._sending = false }
}

const deleteRecord = async (row: any) => {
  try {
    await ElMessageBox.confirm('确定删除该条记录？', '确认删除', { type: 'warning' })
  } catch { return }
  try {
    await http.delete(`/api/message/touch/${row.id}`)
    ElMessage.success('已删除')
    await loadRecords()
  } catch { ElMessage.error('删除失败') }
}

onMounted(() => { loadRecords(); loadAccounts() })
</script>