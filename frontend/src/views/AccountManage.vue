<template>
  <div>
    <el-card>
      <template #header>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span>账号管理</span>
          <el-button type="primary" @click="dialogVisible = true">添加账号</el-button>
        </div>
      </template>
      <el-table :data="pagedAccounts" stripe>
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
      <el-pagination
        style="margin-top:16px;justify-content:flex-end"
        background
        layout="total, sizes, prev, pager, next"
        :total="accounts.length"
        :page-sizes="[10, 20, 50, 100]"
        v-model:page-size="pageSize"
        v-model:current-page="currentPage"
      />
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
          <el-button size="small" style="margin-top:6px" @click="openCookieHelper">获取Cookies</el-button>
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

    <el-dialog v-model="cookieHelperVisible" title="获取Cookies" width="520px" append-to-body>
      <el-alert type="info" :closable="false" style="margin-bottom:14px">
        推荐使用「方法一」，可获取包括登录凭证在内的完整 Cookies
      </el-alert>

      <p style="margin:0 0 6px;font-weight:600">方法一：从网络请求头复制（推荐）</p>
      <ol style="margin:0 0 12px;padding-left:20px;line-height:2">
        <li>点击下方按钮打开{{ form.platform === 'xhs' ? '小红书' : 'B站' }}，确认已登录</li>
        <li>按 <b>F12</b> 打开开发者工具 → 切换到 <b>Network（网络）</b> 标签页</li>
        <li>刷新页面，点击列表中任意一条请求</li>
        <li>在右侧 <b>Headers → Request Headers</b> 找到 <b>cookie</b> 行</li>
        <li>右键该行的值 → <b>Copy value</b>，粘贴到下方</li>
      </ol>

      <p style="margin:0 0 6px;font-weight:600">方法二：控制台复制（仅限非 HttpOnly cookie）</p>
      <ol style="margin:0 0 6px;padding-left:20px;line-height:2">
        <li>按 <b>F12</b> → 切换到 <b>Console（控制台）</b></li>
        <li>输入以下命令并回车：</li>
      </ol>
      <el-input readonly model-value="copy(document.cookie)" style="font-family:monospace;margin-bottom:14px" />

      <el-button size="small" type="primary" @click="openPlatformSite" style="margin-bottom:14px">
        打开{{ form.platform === 'xhs' ? '小红书' : 'B站' }}
      </el-button>

      <p style="margin:0 0 6px;font-weight:600">粘贴 Cookies：</p>
      <el-input v-model="pastedCookies" type="textarea" :rows="4" placeholder="粘贴 Cookies 内容..." />
      <template #footer>
        <el-button @click="cookieHelperVisible = false">取消</el-button>
        <el-button type="primary" @click="usePastedCookies">使用这些Cookies</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import http from '../api/http'
import { ElMessage, ElMessageBox } from 'element-plus'

const accounts = ref<any[]>([])
const pageSize = ref(10)
const currentPage = ref(1)
const pagedAccounts = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return accounts.value.slice(start, start + pageSize.value)
})
watch(pageSize, () => { currentPage.value = 1 })

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

const cookieHelperVisible = ref(false)
const pastedCookies = ref('')

const openCookieHelper = () => {
  pastedCookies.value = ''
  cookieHelperVisible.value = true
}

const openPlatformSite = () => {
  const url = form.value.platform === 'xhs' ? 'https://www.xiaohongshu.com' : 'https://www.bilibili.com'
  window.open(url, '_blank')
}

const usePastedCookies = () => {
  const cookies = pastedCookies.value.trim()
  if (!cookies) { ElMessage.warning('请先粘贴Cookies内容'); return }
  form.value.cookies = cookies
  cookieHelperVisible.value = false
  ElMessage.success('Cookies已填入，可点击"检测Cookie"验证是否有效')
}

onMounted(loadAccounts)
</script>
