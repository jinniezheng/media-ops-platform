<template>
  <div>
    <el-card>
      <template #header>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span>媒体资源库</span>
          <div style="display:flex;gap:10px">
            <el-radio-group v-model="mediaType" @change="loadData">
              <el-radio-button value="video">视频</el-radio-button>
              <el-radio-button value="image">图片</el-radio-button>
            </el-radio-group>
            <el-button type="primary" :disabled="!selectedRows.length"
              :loading="downloading" @click="downloadSelected">
              下载选中 ({{ selectedRows.length }})
            </el-button>
          </div>
        </div>
      </template>

      <!-- 视频列表 -->
      <el-table v-if="mediaType === 'video'" :data="videos" stripe
        @selection-change="onSelectionChange">
        <el-table-column type="selection" width="45" />
        <el-table-column label="封面" width="120">
          <template #default="{ row }">
            <el-image :src="row.cover_url" fit="cover"
              style="width:100px;height:60px;border-radius:4px" />
          </template>
        </el-table-column>
        <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
        <el-table-column label="分辨率" width="100">
          <template #default="{ row }">
            {{ row.width }}&times;{{ row.height }}
          </template>
        </el-table-column>
        <el-table-column label="时长" width="80">
          <template #default="{ row }">
            {{ formatDuration(row.duration) }}
          </template>
        </el-table-column>
        <el-table-column label="画质" width="200">
          <template #default="{ row }">
            <el-tag v-if="row.video_url_1080p" size="small" type="success">1080p</el-tag>
            <el-tag v-if="row.video_url_720p" size="small">720p</el-tag>
            <el-tag v-if="row.video_url_480p" size="small" type="info">480p</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.download_status)" size="small">
              {{ statusLabel(row.download_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180">
          <template #default="{ row }">
            <el-button size="small" @click="copyUrl(row.video_url_default)">
              复制链接
            </el-button>
            <el-button size="small" type="primary"
              v-if="row.download_status !== 'done'"
              @click="downloadSingle('video', row.id)">
              下载
            </el-button>
            <el-button size="small" type="success"
              v-else @click="openFile(row.local_path)">
              查看
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 图片列表 -->
      <div v-else class="image-grid">
        <div v-for="img in images" :key="img.id" class="image-item"
          :class="{ selected: isSelected(img) }" @click="toggleSelect(img)">
          <el-image :src="img.url_watermark" fit="cover" class="image-preview"
            :preview-src-list="[img.url_original]" />
          <div class="image-info">
            <span>{{ img.width }}&times;{{ img.height }}</span>
            <el-tag :type="statusType(img.download_status)" size="small">
              {{ statusLabel(img.download_status) }}
            </el-tag>
          </div>
          <div class="image-actions">
            <el-button size="small" circle @click.stop="copyUrl(img.url_original)">
              <el-icon><CopyDocument /></el-icon>
            </el-button>
            <el-button size="small" circle type="primary"
              v-if="img.download_status !== 'done'"
              @click.stop="downloadSingle('image', img.id)">
              <el-icon><Download /></el-icon>
            </el-button>
          </div>
        </div>
      </div>

      <el-pagination
        style="margin-top:16px;justify-content:flex-end"
        layout="total, sizes, prev, pager, next"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        v-model:page-size="pageSize"
        @current-change="onPageChange"
      />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import http from '../api/http'
import { ElMessage } from 'element-plus'
import { CopyDocument, Download } from '@element-plus/icons-vue'

const mediaType = ref('video')
const videos = ref<any[]>([])
const images = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
watch(pageSize, () => { page.value = 1; loadData() })
const selectedRows = ref<any[]>([])
const downloading = ref(false)

const statusLabel = (s: string) => {
  const m: Record<string, string> = {
    pending: '待下载',
    downloading: '下载中',
    done: '已下载',
    failed: '失败',
  }
  return m[s] || s
}

const statusType = (s: string) => {
  if (s === 'done') return 'success'
  if (s === 'downloading') return ''
  if (s === 'failed') return 'danger'
  return 'info'
}

const formatDuration = (ms: number) => {
  if (!ms) return '-'
  const sec = Math.floor(ms / 1000)
  const min = Math.floor(sec / 60)
  const s = sec % 60
  return `${min}:${s.toString().padStart(2, '0')}`
}

const loadData = async () => {
  selectedRows.value = []
  try {
    if (mediaType.value === 'video') {
      const { data } = await http.get('/api/collect/xhs-videos', {
        params: { page: page.value, size: pageSize.value },
      })
      videos.value = data.items
      total.value = data.total
    } else {
      const { data } = await http.get('/api/collect/xhs-images', {
        params: { page: page.value, size: pageSize.value },
      })
      images.value = data.items
      total.value = data.total
    }
  } catch {
    ElMessage.error('加载数据失败')
  }
}

const onSelectionChange = (rows: any[]) => {
  selectedRows.value = rows
}

const isSelected = (item: any) => {
  return selectedRows.value.some((r: any) => r.id === item.id)
}

const toggleSelect = (item: any) => {
  const idx = selectedRows.value.findIndex((r: any) => r.id === item.id)
  if (idx >= 0) {
    selectedRows.value.splice(idx, 1)
  } else {
    selectedRows.value.push(item)
  }
}

const copyUrl = (url: string) => {
  if (!url) {
    ElMessage.warning('链接为空')
    return
  }
  navigator.clipboard.writeText(url)
  ElMessage.success('已复制到剪贴板')
}

const downloadSingle = async (type: 'video' | 'image', id: number) => {
  try {
    if (type === 'video') {
      const { data } = await http.post('/api/collect/xhs-download-videos', {
        video_ids: [id],
        quality: 'default',
      })
      if (data.downloaded) {
        ElMessage.success('下载成功')
        loadData()
      } else {
        ElMessage.error('下载失败')
      }
    } else {
      const { data } = await http.post('/api/collect/xhs-download-images', {
        image_ids: [id],
        use_original: true,
      })
      if (data.downloaded) {
        ElMessage.success('下载成功')
        loadData()
      } else {
        ElMessage.error('下载失败')
      }
    }
  } catch {
    ElMessage.error('下载失败')
  }
}

const downloadSelected = async () => {
  if (!selectedRows.value.length) return
  downloading.value = true
  try {
    const ids = selectedRows.value.map((r: any) => r.id)
    if (mediaType.value === 'video') {
      const { data } = await http.post('/api/collect/xhs-download-videos', {
        video_ids: ids,
        quality: 'default',
      })
      ElMessage.success(`下载完成: 成功 ${data.downloaded}, 失败 ${data.failed}`)
    } else {
      const { data } = await http.post('/api/collect/xhs-download-images', {
        image_ids: ids,
        use_original: true,
      })
      ElMessage.success(`下载完成: 成功 ${data.downloaded}, 失败 ${data.failed}`)
    }
    loadData()
  } catch {
    ElMessage.error('下载失败')
  } finally {
    downloading.value = false
  }
}

const openFile = (path: string) => {
  if (path) {
    ElMessage.info(`文件路径: ${path}`)
  }
}

const onPageChange = (p: number) => {
  page.value = p
  loadData()
}

onMounted(loadData)
</script>

<style scoped>
.image-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 16px;
  padding: 16px 0;
}

.image-item {
  position: relative;
  border: 2px solid transparent;
  border-radius: 8px;
  overflow: hidden;
  cursor: pointer;
  transition: all 0.2s;
}

.image-item:hover {
  border-color: #409eff;
}

.image-item.selected {
  border-color: #67c23a;
}

.image-preview {
  width: 100%;
  height: 200px;
}

.image-info {
  padding: 8px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: #909399;
}

.image-actions {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  gap: 4px;
  opacity: 0;
  transition: opacity 0.2s;
}

.image-item:hover .image-actions {
  opacity: 1;
}
</style>
