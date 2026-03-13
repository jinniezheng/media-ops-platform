<template>
  <div>
    <el-card>
      <template #header>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span>采集任务</span>
          <el-button type="primary" @click="dialogVisible = true">新建任务</el-button>
        </div>
      </template>
      <el-table :data="pagedTasks" stripe>
        <el-table-column prop="name" label="任务名称" />
        <el-table-column label="平台" width="100">
          <template #default="{ row }">
            {{ platformMap[row.platform] || row.platform }}
          </template>
        </el-table-column>
        <el-table-column prop="task_type" label="类型" width="120">
          <template #default="{ row }">
            {{ typeMap[row.task_type] || row.task_type }}
          </template>
        </el-table-column>
        <el-table-column label="关键词/目标" min-width="140">
          <template #default="{ row }">
            {{ row.keyword || row.target_url || '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="collected_count" label="已采集" width="100" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)">
              {{ statusMap[row.status] || row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="280">
          <template #default="{ row }">
            <el-button size="small" type="primary"
              @click="runTask(row)"
              :disabled="row.status === 'running' || row.status === 'done'">
              执行
            </el-button>
            <el-button v-if="canViewResult(row)"
              size="small" @click="viewResult(row)">
              查看结果
            </el-button>
            <el-button v-if="row.status === 'done' || row.status === 'failed'"
              size="small" type="danger" @click="deleteTask(row.id)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        style="margin-top:16px;justify-content:flex-end"
        background layout="total, sizes, prev, pager, next"
        :total="tasks.length" :page-sizes="[10, 20, 50, 100]"
        v-model:page-size="taskPageSize" v-model:current-page="taskCurrentPage"
      />
    </el-card>

    <!-- 新建任务对话框 -->
    <el-dialog v-model="dialogVisible" title="新建采集任务" width="480px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="任务名称">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="平台">
          <el-select v-model="form.platform" @change="onPlatformChange">
            <el-option label="小红书" value="xhs" />
            <el-option label="Bilibili" value="bilibili" />
            <el-option label="抖音" value="douyin" />
          </el-select>
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.task_type">
            <el-option v-for="opt in taskTypeOptions" :key="opt.value"
              :label="opt.label" :value="opt.value" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.task_type !== 'follower'" label="关键词">
          <el-input v-model="form.keyword" placeholder="输入搜索关键词" />
        </el-form-item>
        <el-form-item v-if="form.task_type === 'follower'" label="目标用户">
          <el-input v-model="form.target_url" placeholder="B站用户主页链接或UID" />
        </el-form-item>
        <el-form-item label="最大数量">
          <el-input-number v-model="form.max_count" :min="1" :max="1000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="createTask">创建</el-button>
      </template>
    </el-dialog>

    <!-- 视频列表对话框 (Bilibili) -->
    <el-dialog v-model="videoDialogVisible" title="采集到的视频" width="900px">
      <el-table :data="pagedVideoList" stripe
        @selection-change="onVideoSelectionChange">
        <el-table-column type="selection" width="45" />
        <el-table-column label="标题" min-width="240">
          <template #default="{ row }">
            <a :href="`https://www.bilibili.com/video/${row.bvid}`"
              target="_blank" rel="noopener" style="color:#409eff;text-decoration:none">
              {{ stripHtml(row.title) }}
            </a>
          </template>
        </el-table-column>
        <el-table-column prop="author" label="作者" width="120" />
        <el-table-column prop="play_count" label="播放" width="80" />
        <el-table-column prop="like_count" label="点赞" width="80" />
        <el-table-column prop="reply_count" label="评论数" width="80" />
        <el-table-column label="发布时间" width="160">
          <template #default="{ row }">
            {{ formatTime(row.pubdate) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button size="small" @click="viewComments(row.id, row.title)">评论</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination style="margin-top:12px;justify-content:flex-end" background
        layout="total, sizes, prev, pager, next" :total="videoList.length"
        :page-sizes="[10, 20, 50]" v-model:page-size="videoPageSize"
        v-model:current-page="videoCurrentPage" />
      <template #footer>
        <span style="float:left;line-height:32px;color:#909399">
          已选 {{ selectedVideos.length }} 个视频
        </span>
        <el-button @click="videoDialogVisible = false">关闭</el-button>
        <el-button type="primary" :disabled="!selectedVideos.length"
          @click="addVideosToTouch">
          加入触达
        </el-button>
      </template>
    </el-dialog>

    <!-- 评论列表对话框 (Bilibili) -->
    <el-dialog v-model="commentDialogVisible" :title="`评论详情 — ${currentVideoTitle}`" width="750px">
      <el-table :data="pagedCommentList" stripe
        @selection-change="onCommentSelectionChange">
        <el-table-column type="selection" width="45" />
        <el-table-column prop="uname" label="用户" width="120" />
        <el-table-column prop="message" label="评论内容" min-width="260" show-overflow-tooltip />
        <el-table-column prop="like_count" label="点赞" width="70" />
        <el-table-column label="时间" width="160">
          <template #default="{ row }">
            {{ formatTime(row.ctime) }}
          </template>
        </el-table-column>
      </el-table>
      <el-pagination style="margin-top:12px;justify-content:flex-end" background
        layout="total, sizes, prev, pager, next" :total="commentList.length"
        :page-sizes="[10, 20, 50]" v-model:page-size="commentPageSize"
        v-model:current-page="commentCurrentPage" />
      <template #footer>
        <span style="float:left;line-height:32px;color:#909399">
          已选 {{ selectedComments.length }} 条
        </span>
        <el-button @click="commentDialogVisible = false">关闭</el-button>
        <el-button type="primary" :disabled="!selectedComments.length"
          @click="addToTouch">
          加入触达
        </el-button>
      </template>
    </el-dialog>

    <!-- XHS 笔记列表对话框 -->
    <el-dialog v-model="xhsNoteDialogVisible" title="采集到的笔记" width="900px">
      <el-table :data="pagedXhsNoteList" stripe
        @selection-change="onXhsNoteSelectionChange">
        <el-table-column type="selection" width="45" />
        <el-table-column label="标题" min-width="240">
          <template #default="{ row }">
            <a :href="row.note_url" target="_blank" rel="noopener"
              style="color:#409eff;text-decoration:none">
              {{ row.title || '(无标题)' }}
            </a>
          </template>
        </el-table-column>
        <el-table-column prop="nickname" label="作者" width="120" />
        <el-table-column prop="liked_count" label="点赞" width="80" />
        <el-table-column prop="collected_count" label="收藏" width="80" />
        <el-table-column prop="comment_count" label="评论" width="80" />
        <el-table-column prop="type" label="类型" width="70" />
        <el-table-column label="发布时间" width="160">
          <template #default="{ row }">
            {{ row.time ? formatTime(row.time / 1000) : '-' }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button size="small"
              @click="viewXhsComments(row.note_id, row.title)">
              评论
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination style="margin-top:12px;justify-content:flex-end" background
        layout="total, sizes, prev, pager, next" :total="xhsNoteList.length"
        :page-sizes="[10, 20, 50]" v-model:page-size="xhsNotePageSize"
        v-model:current-page="xhsNoteCurrentPage" />
      <template #footer>
        <div style="display:flex;justify-content:space-between;align-items:center;width:100%">
          <span style="color:#909399">
            已选 {{ selectedXhsNotes.length }} 篇笔记
          </span>
          <div style="display:flex;gap:10px;align-items:center">
            <el-select v-model="selectedXhsAccountId" placeholder="选择账号" style="width:160px" size="small">
              <el-option v-for="a in xhsAccounts" :key="a.id" :label="a.account_name" :value="a.id" />
            </el-select>
            <el-button @click="xhsNoteDialogVisible = false">关闭</el-button>
            <el-button type="success" :disabled="!selectedXhsNotes.length || !selectedXhsAccountId"
              :loading="parseMediaLoading" @click="parseNoteMedia">
              解析媒体
            </el-button>
            <el-button type="warning" :disabled="!selectedXhsNotes.length"
              @click="extractUsersFromNotes">
              提取作者
            </el-button>
            <el-button type="primary" :disabled="!selectedXhsNotes.length"
              @click="addXhsNotesToTouch">
              加入触达
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- XHS 评论列表对话框 -->
    <el-dialog v-model="xhsCommentDialogVisible"
      :title="`评论详情 — ${currentXhsNoteTitle}`" width="750px">
      <el-table :data="pagedXhsCommentList" stripe
        @selection-change="onXhsCommentSelectionChange">
        <el-table-column type="selection" width="45" />
        <el-table-column prop="nickname" label="用户" width="120" />
        <el-table-column prop="content" label="评论内容"
          min-width="260" show-overflow-tooltip />
        <el-table-column prop="like_count" label="点赞" width="70" />
        <el-table-column prop="ip_location" label="IP" width="80" />
        <el-table-column label="时间" width="160">
          <template #default="{ row }">
            {{ formatTime(row.create_time / 1000) }}
          </template>
        </el-table-column>
      </el-table>
      <el-pagination style="margin-top:12px;justify-content:flex-end" background
        layout="total, sizes, prev, pager, next" :total="xhsCommentList.length"
        :page-sizes="[10, 20, 50]" v-model:page-size="xhsCommentPageSize"
        v-model:current-page="xhsCommentCurrentPage" />
      <template #footer>
        <div style="display:flex;justify-content:space-between;align-items:center;width:100%">
          <span style="color:#909399">
            已选 {{ selectedXhsComments.length }} 条
          </span>
          <div style="display:flex;gap:10px;align-items:center">
            <el-select v-model="selectedXhsAccountId" placeholder="选择账号获取详情" style="width:180px" size="small">
              <el-option v-for="a in xhsAccounts" :key="a.id" :label="a.account_name" :value="a.id" />
            </el-select>
            <el-button @click="xhsCommentDialogVisible = false">关闭</el-button>
            <el-button type="warning" :disabled="!selectedXhsComments.length"
              @click="extractUsersFromComments">
              提取用户
            </el-button>
            <el-button type="primary" :disabled="!selectedXhsComments.length"
              @click="addXhsCommentsToTouch">
              加入触达
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import http from '../api/http'
import { ElMessage, ElMessageBox } from 'element-plus'

const platformMap: Record<string, string> = {
  bilibili: 'Bilibili',
  xhs: '小红书',
  douyin: '抖音',
}
const typeMap: Record<string, string> = {
  keyword: '关键词搜索',
  video_comment: '视频评论',
  follower: '粉丝列表',
}
const statusMap: Record<string, string> = {
  pending: '待执行',
  running: '执行中',
  done: '已完成',
  failed: '失败',
}
const statusType = (s: string) => {
  if (s === 'done') return 'success'
  if (s === 'running') return ''
  if (s === 'failed') return 'danger'
  return 'info'
}
const formatTime = (ts: number) => {
  if (!ts) return '-'
  return new Date(ts * 1000).toLocaleString('zh-CN')
}

const tasks = ref<any[]>([])
const taskPageSize = ref(10)
const taskCurrentPage = ref(1)
const pagedTasks = computed(() => {
  const start = (taskCurrentPage.value - 1) * taskPageSize.value
  return tasks.value.slice(start, start + taskPageSize.value)
})
watch(taskPageSize, () => { taskCurrentPage.value = 1 })

const dialogVisible = ref(false)
const form = ref({
  name: '', platform: 'xhs', task_type: 'keyword',
  keyword: '', target_url: '', max_count: 20,
})

const taskTypeOptions = computed(() => {
  if (form.value.platform === 'xhs' || form.value.platform === 'douyin') {
    return [{ label: '关键词搜索', value: 'keyword' }]
  }
  return [
    { label: '关键词搜索', value: 'keyword' },
    { label: '视频评论', value: 'video_comment' },
    { label: '粉丝列表', value: 'follower' },
  ]
})

const onPlatformChange = () => {
  form.value.task_type = 'keyword'
}

// ── XHS 账号（用于提取用户时获取详情）───────────────────────
const xhsAccounts = ref<any[]>([])
const selectedXhsAccountId = ref<number | null>(null)
const parseMediaLoading = ref(false)

const loadXhsAccounts = async () => {
  try {
    const { data } = await http.get('/api/accounts')
    const accounts = data.items || data || []
    xhsAccounts.value = accounts.filter((a: any) => a.platform === 'xhs')
    if (xhsAccounts.value.length && !selectedXhsAccountId.value) {
      selectedXhsAccountId.value = xhsAccounts.value[0].id
    }
  } catch { /* */ }
}

const canViewResult = (row: any) => {
  if (row.status !== 'done') return false
  return row.task_type === 'video_comment' || row.platform === 'xhs'
}

const viewResult = (row: any) => {
  if (row.platform === 'xhs') {
    viewXhsNotes(row.id)
  } else {
    viewVideos(row.id)
  }
}

// ── Bilibili state ──────────────────────────────────────────
const videoDialogVisible = ref(false)
const videoList = ref<any[]>([])
const videoPageSize = ref(10)
const videoCurrentPage = ref(1)
const pagedVideoList = computed(() => {
  const start = (videoCurrentPage.value - 1) * videoPageSize.value
  return videoList.value.slice(start, start + videoPageSize.value)
})
watch(videoPageSize, () => { videoCurrentPage.value = 1 })

const selectedVideos = ref<any[]>([])
const commentDialogVisible = ref(false)
const commentList = ref<any[]>([])
const commentPageSize = ref(10)
const commentCurrentPage = ref(1)
const pagedCommentList = computed(() => {
  const start = (commentCurrentPage.value - 1) * commentPageSize.value
  return commentList.value.slice(start, start + commentPageSize.value)
})
watch(commentPageSize, () => { commentCurrentPage.value = 1 })

const currentVideoTitle = ref('')
const currentVideoAid = ref(0)
const selectedComments = ref<any[]>([])

// ── XHS state ───────────────────────────────────────────────
const xhsNoteDialogVisible = ref(false)
const xhsNoteList = ref<any[]>([])
const xhsNotePageSize = ref(10)
const xhsNoteCurrentPage = ref(1)
const pagedXhsNoteList = computed(() => {
  const start = (xhsNoteCurrentPage.value - 1) * xhsNotePageSize.value
  return xhsNoteList.value.slice(start, start + xhsNotePageSize.value)
})
watch(xhsNotePageSize, () => { xhsNoteCurrentPage.value = 1 })

const selectedXhsNotes = ref<any[]>([])
const xhsCommentDialogVisible = ref(false)
const xhsCommentList = ref<any[]>([])
const xhsCommentPageSize = ref(10)
const xhsCommentCurrentPage = ref(1)
const pagedXhsCommentList = computed(() => {
  const start = (xhsCommentCurrentPage.value - 1) * xhsCommentPageSize.value
  return xhsCommentList.value.slice(start, start + xhsCommentPageSize.value)
})
watch(xhsCommentPageSize, () => { xhsCommentCurrentPage.value = 1 })

const selectedXhsComments = ref<any[]>([])
const currentXhsNoteTitle = ref('')
const currentXhsNoteId = ref('')

const stripHtml = (html: string) => html?.replace(/<[^>]+>/g, '') || ''

const loadTasks = async () => {
  try {
    const { data } = await http.get('/api/collect/tasks')
    tasks.value = data.items
  } catch { /* */ }
}

const createTask = async () => {
  await http.post('/api/collect/tasks', form.value)
  dialogVisible.value = false
  ElMessage.success('任务已创建')
  loadTasks()
}

const runTask = async (row: any) => {
  row.status = 'running'
  try {
    const { data } = await http.post(`/api/collect/tasks/${row.id}/run`)
    if (data.error) {
      row.status = 'failed'
      ElMessage.error(`采集失败: ${data.error}`)
    } else if (row.platform === 'xhs') {
      ElMessage.success(
        `采集完成: ${data.collected_notes} 篇笔记, ${data.collected_comments} 条评论`
      )
    } else if (row.task_type === 'video_comment') {
      ElMessage.success(
        `采集完成: ${data.collected_videos} 个视频, ${data.collected_comments} 条评论`
      )
    } else {
      ElMessage.success(
        `采集完成: 新增 ${data.collected} 人, 跳过重复 ${data.duplicates_skipped} 人`
      )
    }
  } catch {
    row.status = 'failed'
    ElMessage.error('请求失败，请稍后重试')
  }
  loadTasks()
}

// ── Bilibili: videos & comments ─────────────────────────────

const viewVideos = async (taskId: number) => {
  selectedVideos.value = []
  videoCurrentPage.value = 1
  try {
    const { data } = await http.get('/api/collect/videos', { params: { task_id: taskId } })
    videoList.value = data.items
    videoDialogVisible.value = true
  } catch {
    ElMessage.error('加载视频列表失败')
  }
}

const onVideoSelectionChange = (rows: any[]) => {
  selectedVideos.value = rows
}

const addVideosToTouch = async () => {
  const videos = selectedVideos.value.map((v: any) => ({
    aid: v.aid,
    title: stripHtml(v.title),
  }))
  try {
    const { data } = await http.post('/api/message/touch', { videos })
    ElMessage.success(`已加入触达 ${data.created} 条`)
    videoDialogVisible.value = false
  } catch {
    ElMessage.error('加入触达失败')
  }
}

const viewComments = async (postId: number, title: string = '') => {
  currentVideoTitle.value = stripHtml(title)
  selectedComments.value = []
  commentCurrentPage.value = 1
  try {
    const { data } = await http.get('/api/collect/comments', { params: { post_id: postId } })
    commentList.value = data.items
    currentVideoAid.value = data.video_aid || 0
    if (data.video_title) currentVideoTitle.value = stripHtml(data.video_title)
    commentDialogVisible.value = true
  } catch {
    ElMessage.error('加载评论列表失败')
  }
}

const onCommentSelectionChange = (rows: any[]) => {
  selectedComments.value = rows
}

const addToTouch = async () => {
  const comments = selectedComments.value.map((c: any) => ({
    rpid: c.rpid,
    aid: currentVideoAid.value,
    uname: c.uname,
    message: c.message,
    video_title: currentVideoTitle.value,
  }))
  try {
    const { data } = await http.post('/api/message/touch', { comments })
    ElMessage.success(`已加入触达 ${data.created} 条`)
    commentDialogVisible.value = false
  } catch {
    ElMessage.error('加入触达失败')
  }
}

// ── XHS: notes & comments ───────────────────────────────────

const viewXhsNotes = async (taskId: number) => {
  selectedXhsNotes.value = []
  xhsNoteCurrentPage.value = 1
  try {
    const { data } = await http.get('/api/collect/xhs-notes', { params: { task_id: taskId } })
    xhsNoteList.value = data.items
    xhsNoteDialogVisible.value = true
  } catch {
    ElMessage.error('加载笔记列表失败')
  }
}

const onXhsNoteSelectionChange = (rows: any[]) => {
  selectedXhsNotes.value = rows
}

const addXhsNotesToTouch = async () => {
  const xhs_notes = selectedXhsNotes.value.map((n: any) => ({
    note_id: n.note_id,
    title: n.title || '(无标题)',
  }))
  try {
    const { data } = await http.post('/api/message/touch', { xhs_notes })
    ElMessage.success(`已加入触达 ${data.created} 条`)
    xhsNoteDialogVisible.value = false
  } catch {
    ElMessage.error('加入触达失败')
  }
}

const parseNoteMedia = async () => {
  if (!selectedXhsAccountId.value) {
    ElMessage.warning('请先选择小红书账号')
    return
  }
  const note_ids = selectedXhsNotes.value.map((n: any) => n.note_id)
  parseMediaLoading.value = true
  try {
    const { data } = await http.post('/api/collect/xhs-parse-media', {
      note_ids,
      account_id: selectedXhsAccountId.value,
      save_to_db: true,
    })
    if (data.error) {
      ElMessage.error(data.error)
    } else {
      const results = data.results || []
      const failed = results.filter((r: any) => !r.success)
      const succeeded = results.filter((r: any) => r.success)

      if (succeeded.length === 0 && results.length > 0) {
        // 全部失败
        const errors = failed.map((r: any) => r.error || '未知错误')
        ElMessage.error(`解析失败：${errors[0]}`)
        console.warn('解析失败详情:', failed)
      } else {
        let msg = `解析完成（成功 ${succeeded.length}/${results.length}）`
        if (data.videos_added) msg += `，新增视频 ${data.videos_added} 个`
        if (data.images_added) msg += `，新增图片 ${data.images_added} 张`
        ElMessage.success(msg)
        if (failed.length) {
          console.warn('部分解析失败:', failed)
        }
      }
    }
  } catch {
    ElMessage.error('解析媒体失败')
  } finally {
    parseMediaLoading.value = false
  }
}

const extractUsersFromNotes = async () => {
  const note_ids = selectedXhsNotes.value.map((n: any) => n.note_id)
  try {
    const { data } = await http.post('/api/collect/xhs-extract-authors', {
      note_ids,
      account_id: selectedXhsAccountId.value,
    })
    let msg = `已提取 ${data.added} 个作者，跳过 ${data.skipped} 个重复`
    if (data.fetched) msg += `，获取详情 ${data.fetched} 个`
    ElMessage.success(msg)
  } catch {
    ElMessage.error('提取作者失败')
  }
}

const viewXhsComments = async (noteId: string, title: string = '') => {
  currentXhsNoteTitle.value = title || '(无标题)'
  currentXhsNoteId.value = noteId
  selectedXhsComments.value = []
  xhsCommentCurrentPage.value = 1
  try {
    const { data } = await http.get('/api/collect/xhs-comments', { params: { note_id: noteId } })
    xhsCommentList.value = data.items
    xhsCommentDialogVisible.value = true
  } catch {
    ElMessage.error('加载评论列表失败')
  }
}

const onXhsCommentSelectionChange = (rows: any[]) => {
  selectedXhsComments.value = rows
}

const addXhsCommentsToTouch = async () => {
  const xhs_comments = selectedXhsComments.value.map((c: any) => ({
    comment_id: c.comment_id,
    note_id: currentXhsNoteId.value,
    note_title: currentXhsNoteTitle.value,
    nickname: c.nickname,
    content: c.content,
  }))
  try {
    const { data } = await http.post('/api/message/touch', { xhs_comments })
    ElMessage.success(`已加入触达 ${data.created} 条`)
    xhsCommentDialogVisible.value = false
  } catch {
    ElMessage.error('加入触达失败')
  }
}

const extractUsersFromComments = async () => {
  const comment_ids = selectedXhsComments.value.map((c: any) => c.comment_id)
  try {
    const { data } = await http.post('/api/collect/xhs-extract-users', {
      note_id: currentXhsNoteId.value,
      comment_ids,
      account_id: selectedXhsAccountId.value,
    })
    let msg = `已提取 ${data.added} 个用户，跳过 ${data.skipped} 个重复`
    if (data.fetched) msg += `，获取详情 ${data.fetched} 个`
    ElMessage.success(msg)
  } catch {
    ElMessage.error('提取用户失败')
  }
}

// ── Delete ──────────────────────────────────────────────────

const deleteTask = async (id: number) => {
  try {
    await ElMessageBox.confirm('确定删除该任务及其采集数据？', '确认删除', { type: 'warning' })
  } catch { return }
  try {
    await http.delete(`/api/collect/tasks/${id}`)
    ElMessage.success('任务已删除')
    loadTasks()
  } catch {
    ElMessage.error('删除失败')
  }
}

onMounted(() => {
  loadTasks()
  loadXhsAccounts()
})
</script>
