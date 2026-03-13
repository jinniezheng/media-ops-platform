<template>
  <div class="creative-write">
    <!-- AI 创作区 -->
    <el-card header="AI 创作" class="section-card">
      <el-form :inline="true">
        <el-form-item label="主题">
          <el-input v-model="form.topic" placeholder="输入创作主题" style="width:240px" />
        </el-form-item>
        <el-form-item label="风格">
          <el-select v-model="form.style" style="width:140px">
            <el-option v-for="s in styles" :key="s" :label="s" :value="s" />
          </el-select>
        </el-form-item>
        <el-form-item label="参考数量">
          <el-input-number v-model="form.refCount" :min="0" :max="10" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="generating" @click="handleGenerate">
            AI 生成
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 编辑区 -->
    <el-card header="编辑笔记" class="section-card" style="margin-top:16px">
      <el-form label-width="60px">
        <el-form-item label="标题">
          <el-input v-model="draft.title" placeholder="笔记标题" />
        </el-form-item>
        <el-form-item label="正文">
          <el-input v-model="draft.content" type="textarea" :rows="8" placeholder="笔记正文" />
        </el-form-item>
        <el-form-item label="标签">
          <div class="tag-area">
            <el-tag v-for="(t, i) in draft.tags" :key="i" closable @close="draft.tags.splice(i,1)"
              style="margin-right:6px;margin-bottom:4px">{{ t }}</el-tag>
            <el-input v-if="tagInputVisible" ref="tagInputRef" v-model="tagInputVal"
              size="small" style="width:120px" @keyup.enter="addTag" @blur="addTag" />
            <el-button v-else size="small" @click="tagInputVisible=true">+ 标签</el-button>
          </div>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="saveDraft('draft')">保存草稿</el-button>
          <el-button type="success" @click="saveDraft('published')">发布</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 草稿箱 -->
    <el-card header="草稿箱" class="section-card" style="margin-top:16px">
      <el-table :data="posts" v-loading="loadingPosts" stripe>
        <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status==='published'?'success':'info'" size="small">
              {{ row.status==='published'?'已发布':'草稿' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="style" label="风格" width="90" />
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="editPost(row)">编辑</el-button>
            <el-button size="small" type="warning" :loading="row._regen"
              @click="regeneratePost(row)">重新生成</el-button>
            <el-button size="small" type="danger" @click="deletePost(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination style="margin-top:12px;justify-content:flex-end" background
        layout="total, sizes, prev, pager, next" :total="postTotal"
        :page-sizes="[10, 20, 50, 100]"
        v-model:page-size="pageSize" v-model:current-page="currentPage" @current-change="fetchPosts" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api/http'

const API = '/api/creative'
const styles = ['种草', '测评', '教程', '日常分享', '好物推荐', '经验分享']

const form = reactive({ topic: '', style: '种草', refCount: 3 })
const generating = ref(false)

const draft = reactive({ id: null as number|null, title: '', content: '', tags: [] as string[], style: '', topic: '' })
const tagInputVisible = ref(false)
const tagInputVal = ref('')

const posts = ref<any[]>([])
const postTotal = ref(0)
const currentPage = ref(1)
const pageSize = ref(10)
const loadingPosts = ref(false)
watch(pageSize, () => { currentPage.value = 1; fetchPosts() })
const editingId = ref<number|null>(null)

function addTag() {
  const v = tagInputVal.value.trim()
  if (v && !draft.tags.includes(v)) draft.tags.push(v)
  tagInputVisible.value = false
  tagInputVal.value = ''
}

async function handleGenerate() {
  if (!form.topic) return ElMessage.warning('请输入主题')
  generating.value = true
  try {
    const { data } = await http.post(`${API}/generate`, {
      topic: form.topic, style: form.style, ref_count: form.refCount,
    })
    draft.title = data.title || ''
    draft.content = data.content || ''
    draft.tags = data.tags || []
    draft.style = form.style
    draft.topic = form.topic
    ElMessage.success('生成完成')
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '生成失败')
  } finally { generating.value = false }
}

async function saveDraft(status: string) {
  if (!draft.title && !draft.content) return ElMessage.warning('内容为空')
  try {
    if (editingId.value) {
      await http.put(`${API}/posts/${editingId.value}`, {
        title: draft.title, content: draft.content, tags: draft.tags, status,
      })
    } else {
      await http.post(`${API}/posts`, {
        title: draft.title, content: draft.content, tags: draft.tags,
        style: draft.style, topic: draft.topic, status,
      })
    }
    ElMessage.success('保存成功')
    resetDraft()
    fetchPosts()
  } catch { ElMessage.error('保存失败') }
}

function resetDraft() {
  editingId.value = null
  Object.assign(draft, { id: null, title: '', content: '', tags: [], style: '', topic: '' })
}

function editPost(row: any) {
  editingId.value = row.id
  Object.assign(draft, { title: row.title, content: row.content, tags: [...row.tags], style: row.style, topic: row.topic })
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

async function regeneratePost(row: any) {
  row._regen = true
  try {
    const { data } = await http.post(`${API}/posts/${row.id}/regenerate`)
    Object.assign(row, data)
    ElMessage.success('重新生成完成')
  } catch { ElMessage.error('重新生成失败') }
  finally { row._regen = false }
}

async function deletePost(row: any) {
  await ElMessageBox.confirm('确定删除？', '提示', { type: 'warning' })
  await http.delete(`${API}/posts/${row.id}`)
  ElMessage.success('已删除')
  fetchPosts()
}

async function fetchPosts() {
  loadingPosts.value = true
  try {
    const { data } = await http.get(`${API}/posts`, {
      params: { page: currentPage.value, page_size: pageSize },
    })
    posts.value = data.items || []
    postTotal.value = data.total || 0
  } finally { loadingPosts.value = false }
}

onMounted(fetchPosts)
</script>

<style scoped>
.creative-write { padding: 0; }
.section-card { margin-bottom: 0; }
.tag-area { display: flex; flex-wrap: wrap; align-items: center; }
</style>
