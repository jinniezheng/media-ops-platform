<template>
  <div>
    <el-card>
      <template #header>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span>用户库</span>
          <div style="display:flex;gap:10px;align-items:center">
            <el-select v-model="platformFilter" placeholder="平台" style="width:100px" clearable>
              <el-option label="全部" value="" />
              <el-option label="小红书" value="xhs" />
              <el-option label="B站" value="bilibili" />
            </el-select>
            <el-input v-model="keyword" placeholder="搜索昵称" style="width:160px" @keyup.enter="loadUsers" clearable />
            <el-select v-model="selectedAccountId" placeholder="选择账号" style="width:160px">
              <el-option v-for="a in xhsAccounts" :key="a.id" :label="a.account_name" :value="a.id" />
            </el-select>
            <el-button type="warning" :disabled="!selectedRows.length || !selectedAccountId"
              :loading="fetchLoading" @click="fetchUserInfo">
              获取详情 ({{ selectedRows.length }})
            </el-button>
          </div>
        </div>
      </template>
      <el-table :data="users" stripe @selection-change="onSelectionChange">
        <el-table-column type="selection" width="45" />
        <el-table-column label="头像" width="60">
          <template #default="{ row }">
            <el-avatar :src="row.avatar_url" :size="36" v-if="row.avatar_url" />
            <el-avatar :size="36" v-else>{{ row.nickname?.charAt(0) || '?' }}</el-avatar>
          </template>
        </el-table-column>
        <el-table-column prop="nickname" label="昵称" width="120" />
        <el-table-column label="小红书号/UID" width="180">
          <template #default="{ row }">
            <el-link v-if="row.platform === 'xhs'"
              :href="`https://www.xiaohongshu.com/user/profile/${row.platform_uid}`"
              target="_blank" type="primary" :underline="false">
              {{ row.platform_uid }}
            </el-link>
            <el-link v-else
              :href="`https://space.bilibili.com/${row.platform_uid}`"
              target="_blank" type="primary" :underline="false">
              {{ row.platform_uid }}
            </el-link>
          </template>
        </el-table-column>
        <el-table-column label="平台" width="80">
          <template #default="{ row }">
            <el-tag :type="row.platform === 'xhs' ? 'danger' : 'primary'" size="small">
              {{ row.platform === 'xhs' ? '小红书' : 'B站' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="follower_count" label="粉丝" width="90" sortable />
        <el-table-column prop="following_count" label="关注" width="80" />
        <el-table-column prop="liked_count" label="获赞与收藏" width="110" />
        <el-table-column prop="signature" label="简介" show-overflow-tooltip />
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'contacted' ? 'warning' : row.status === 'converted' ? 'success' : 'info'" size="small">
              {{ statusMap[row.status] || row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button size="small" type="primary" link
              @click="openUserPage(row)">
              查看主页
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        style="margin-top:16px;justify-content:flex-end"
        background layout="total, sizes, prev, pager, next"
        :total="total" :page-sizes="[10, 20, 50, 100]"
        v-model:page-size="pageSize"
        @size-change="() => { page = 1; loadUsers() }"
        @current-change="(p: number) => { page = p; loadUsers() }"
      />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import http from '../api/http'
import { ElMessage } from 'element-plus'

const statusMap: Record<string, string> = {
  new: '新用户',
  contacted: '已联系',
  converted: '已转化',
}

const users = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const keyword = ref('')
const platformFilter = ref('')
const selectedRows = ref<any[]>([])
const selectedAccountId = ref<number | null>(null)
const xhsAccounts = ref<any[]>([])
const fetchLoading = ref(false)

const loadUsers = async () => {
  try {
    const params: any = { page: page.value, size: pageSize.value }
    if (keyword.value) params.keyword = keyword.value
    if (platformFilter.value) params.platform = platformFilter.value
    const { data } = await http.get('/api/users', { params })
    users.value = data.items
    total.value = data.total
  } catch { /* */ }
}

const loadAccounts = async () => {
  try {
    const { data } = await http.get('/api/accounts')
    xhsAccounts.value = data.filter((a: any) => a.platform === 'xhs')
    if (xhsAccounts.value.length && !selectedAccountId.value) {
      selectedAccountId.value = xhsAccounts.value[0].id
    }
  } catch { /* */ }
}

const onSelectionChange = (rows: any[]) => {
  selectedRows.value = rows
}

const fetchUserInfo = async () => {
  if (!selectedAccountId.value) {
    ElMessage.warning('请先选择小红书账号')
    return
  }
  const xhsUsers = selectedRows.value.filter((u: any) => u.platform === 'xhs')
  if (!xhsUsers.length) {
    ElMessage.warning('请选择小红书用户')
    return
  }
  fetchLoading.value = true
  try {
    const { data } = await http.post('/api/collect/xhs-fetch-user-info', {
      user_ids: xhsUsers.map((u: any) => u.id),
      account_id: selectedAccountId.value,
    })
    if (data.error) {
      ElMessage.error(data.error)
    } else {
      ElMessage.success(`获取成功 ${data.updated} 个，失败 ${data.failed} 个`)
      loadUsers()
    }
  } catch {
    ElMessage.error('获取用户信息失败')
  } finally {
    fetchLoading.value = false
  }
}

const openUserPage = (row: any) => {
  if (row.platform === 'xhs') {
    window.open(`https://www.xiaohongshu.com/user/profile/${row.platform_uid}`, '_blank')
  } else {
    window.open(`https://space.bilibili.com/${row.platform_uid}`, '_blank')
  }
}

watch(platformFilter, () => {
  page.value = 1
  loadUsers()
})

onMounted(() => {
  loadUsers()
  loadAccounts()
})
</script>
