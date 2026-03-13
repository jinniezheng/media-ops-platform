<template>
  <div>
    <el-card>
      <template #header>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span>账号管理</span>
          <el-button type="primary" @click="dialogVisible = true">添加账号</el-button>
        </div>
      </template>
      <el-table :data="accounts" stripe>
        <el-table-column prop="account_name" label="账号名称" />
        <el-table-column prop="platform" label="平台" width="100" />
        <el-table-column prop="is_active" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'">
              {{ row.is_active ? '正常' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Cookie 状态" width="130">
          <template #default="{ row }">
            <el-tag v-if="row._cookieStatus === 'valid'" type="success" size="small">有效</el-tag>
            <el-tag v-else-if="row._cookieStatus === 'invalid'" type="danger" size="small">已失效</el-tag>
            <el-tag v-else type="info" size="small">未检测</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="daily_limit" label="日限额" width="100" />
        <el-table-column prop="used_today" label="今日已用" width="100" />
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button size="small" :loading="row._checking" @click="checkCookie(row)">检测Cookie</el-button>
            <el-button size="small" type="danger" @click="deleteAccount(row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" title="添加账号" width="480px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="账号名称">
          <el-input v-model="form.account_name" />
        </el-form-item>
        <el-form-item label="平台">
          <el-select v-model="form.platform">
            <el-option label="B站" value="bilibili" />
            <el-option label="小红书" value="xhs" />
            <el-option label="抖音" value="douyin" />
          </el-select>
        </el-form-item>
        <el-form-item label="Cookies">
          <el-input v-model="form.cookies" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="日限额">
          <el-input-number v-model="form.daily_limit" :min="1" :max="100" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="createAccount">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import http from '../api/http'
import { ElMessage, ElMessageBox } from 'element-plus'

const accounts = ref<any[]>([])
const dialogVisible = ref(false)
const form = ref({ account_name: '', platform: 'bilibili', cookies: '', daily_limit: 20 })

const loadAccounts = async () => {
  try {
    const { data } = await http.get('/api/accounts')
    accounts.value = data.items
  } catch { /* */ }
}

const createAccount = async () => {
  try {
    await http.post('/api/accounts', form.value)
    dialogVisible.value = false
    ElMessage.success('账号已添加')
    form.value = { account_name: '', platform: 'bilibili', cookies: '', daily_limit: 20 }
    loadAccounts()
  } catch (e: any) {
    const status = e?.response?.status
    const detail = e?.response?.data?.detail
    const msg = Array.isArray(detail)
      ? detail.map((d: any) => d.msg).join('; ')
      : (detail || e?.message || '未知错误')
    ElMessage.error(`添加失败 (${status ?? '网络错误'}): ${msg}`)
  }
}

const deleteAccount = async (id: number) => {
  await ElMessageBox.confirm('确定删除该账号？', '提示')
  await http.delete(`/api/accounts/${id}`)
  ElMessage.success('已删除')
  loadAccounts()
}

const checkCookie = async (row: any) => {
  row._checking = true
  try {
    const { data } = await http.post(`/api/accounts/${row.id}/check-cookie`)
    if (data.valid === true) {
      row._cookieStatus = 'valid'
      ElMessage.success(data.nickname ? `Cookie 有效 (${data.nickname})` : 'Cookie 有效')
    } else if (data.valid === false) {
      row._cookieStatus = 'invalid'
      ElMessage.error(data.msg || 'Cookie 已失效，请更新')
    } else {
      row._cookieStatus = 'unknown'
      ElMessage.info(data.msg || '暂不支持检测')
    }
  } catch (e: any) {
    const status = e?.response?.status
    const detail = e?.response?.data?.detail || e?.response?.data?.msg || e?.message || '未知错误'
    ElMessage.error(`检测失败 (${status ?? '网络错误'}): ${detail}`)
  } finally {
    row._checking = false
  }
}

onMounted(loadAccounts)
</script>
