<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { api } from './api'

const models = ref([])
const voices = ref([])
const refImages = ref([])
const selectedModel = ref('')

const loading = reactive({
  models: false,
  voices: false,
  refs: false,
  analyze: false,
  segment: false,
  generate: false,
  logs: false
})

const form = reactive({
  text: '',
  analysis_depth: 'detailed',
  segment_method: 'sentence',
  sentences_per_segment: 5,
  max_segment_groups: 0,
  resolution: '1080x1920',
  subtitle_style: 'highlight',
  fps: 30
})

const characters = ref([])
const confidence = ref(0)

const segmentPreview = reactive({
  total_segments: 0,
  total_sentences: 0,
  segments: []
})

const backendLogs = ref([])

const job = reactive({
  id: '',
  status: '',
  progress: 0,
  step: '',
  message: '',
  videoUrl: '',
  clipPreviewUrls: []
})

let pollingTimer = null

const effectiveSegmentGroups = computed(() => {
  if (!segmentPreview.total_segments) return 0
  if (form.max_segment_groups > 0) {
    return Math.min(segmentPreview.total_segments, form.max_segment_groups)
  }
  return segmentPreview.total_segments
})

const refPicker = reactive({
  visible: false,
  characterIndex: -1
})

const generatingRef = reactive({})

function resetJob() {
  job.id = ''
  job.status = ''
  job.progress = 0
  job.step = ''
  job.message = ''
  job.videoUrl = ''
  job.clipPreviewUrls = []
}

function openRefPicker(index) {
  refPicker.characterIndex = index
  refPicker.visible = true
}

function closeRefPicker() {
  refPicker.visible = false
  refPicker.characterIndex = -1
}

function pickRefImage(image) {
  const target = characters.value[refPicker.characterIndex]
  if (!target) return
  target.reference_image_path = image.path
  target.reference_image_url = resolveRefImageUrl(image)
  closeRefPicker()
}

function clearRefImage(character) {
  character.reference_image_path = ''
  character.reference_image_url = ''
}

async function loadModels() {
  loading.models = true
  try {
    const data = await api.getModels()
    models.value = data.models || []
    selectedModel.value = models.value.find((item) => item.available)?.id || models.value[0]?.id || ''
  } catch (error) {
    ElMessage.error(`模型加载失败：${error.message}`)
  } finally {
    loading.models = false
  }
}

async function loadVoices() {
  loading.voices = true
  try {
    const data = await api.getVoices()
    voices.value = data.voices || []
  } catch (error) {
    ElMessage.error(`音色列表加载失败：${error.message}`)
  } finally {
    loading.voices = false
  }
}

async function loadRefImages() {
  loading.refs = true
  try {
    const data = await api.listCharacterRefImages()
    refImages.value = data.images || []
  } catch (error) {
    ElMessage.error(`角色参考图加载失败：${error.message}`)
  } finally {
    loading.refs = false
  }
}

async function loadLogs() {
  loading.logs = true
  try {
    const data = await api.getLogs(200)
    backendLogs.value = data.lines || []
  } catch (error) {
    ElMessage.error(`读取日志失败：${error.message}`)
  } finally {
    loading.logs = false
  }
}

async function runAnalyze() {
  if (!form.text.trim()) {
    ElMessage.warning('请先输入文本')
    return
  }
  loading.analyze = true
  try {
    const data = await api.analyzeCharacters({
      text: form.text,
      analysis_depth: form.analysis_depth,
      model_id: selectedModel.value || null
    })
    characters.value = (data.characters || []).map((item) => ({
      ...item,
      reference_image_path: item.reference_image_path || '',
      reference_image_url: item.reference_image_url || '',
      voice_id: item.voice_id || voices.value[0]?.id || 'zh-CN-YunxiNeural'
    }))
    confidence.value = Number(data.confidence || 0)
    ElMessage.success('角色分析完成')
  } catch (error) {
    ElMessage.error(`角色分析失败：${error.message}`)
  } finally {
    loading.analyze = false
  }
}

async function runSegmentPreview() {
  if (!form.text.trim()) {
    ElMessage.warning('请先输入文本')
    return
  }
  loading.segment = true
  try {
    const data = await api.segmentText({
      text: form.text,
      method: form.segment_method,
      sentences_per_segment: form.sentences_per_segment,
      model_id: selectedModel.value || null
    })
    segmentPreview.total_segments = data.total_segments || 0
    segmentPreview.total_sentences = data.total_sentences || 0
    segmentPreview.segments = data.segments || []
    ElMessage.success(`分段完成：${segmentPreview.total_segments} 段（总句数：${segmentPreview.total_sentences || '-'}）`)
  } catch (error) {
    ElMessage.error(`分段失败：${error.message}`)
  } finally {
    loading.segment = false
  }
}

async function confirmCharacters() {
  try {
    await api.confirmCharacters({ characters: characters.value })
    ElMessage.success('角色配置已确认')
  } catch (error) {
    ElMessage.error(`确认失败：${error.message}`)
  }
}

function stopPolling() {
  if (pollingTimer) {
    clearInterval(pollingTimer)
    pollingTimer = null
  }
}

function startPolling() {
  stopPolling()
  pollingTimer = setInterval(async () => {
    if (!job.id) return
    try {
      const status = await api.getJob(job.id)
      job.status = status.status
      job.progress = Number(status.progress || 0)
      job.step = status.step || ''
      job.message = status.message || ''
      job.clipPreviewUrls = status.clip_preview_urls || []

      if (status.status === 'completed') {
        job.videoUrl = api.getVideoUrl(job.id)
        stopPolling()
        loading.generate = false
        ElMessage.success('视频生成完成')
      }
      if (status.status === 'failed' || status.status === 'cancelled') {
        stopPolling()
        loading.generate = false
        ElMessage.warning(status.message || `任务${status.status}`)
      }
    } catch (error) {
      stopPolling()
      loading.generate = false
      ElMessage.error(`轮询任务失败：${error.message}`)
    }
  }, 1500)
}

async function runGenerate() {
  if (!form.text.trim()) {
    ElMessage.warning('请先输入文本')
    return
  }
  if (!characters.value.length) {
    ElMessage.warning('请先分析并确认角色')
    return
  }

  loading.generate = true
  resetJob()

  try {
    const payload = {
      text: form.text,
      characters: characters.value,
      segment_method: form.segment_method,
      sentences_per_segment: form.sentences_per_segment,
      max_segment_groups: form.max_segment_groups,
      resolution: form.resolution,
      subtitle_style: form.subtitle_style,
      fps: form.fps,
      model_id: selectedModel.value || null
    }
    const data = await api.generateVideo(payload)
    job.id = data.job_id
    job.status = data.status
    startPolling()
    ElMessage.success('任务已提交')
  } catch (error) {
    loading.generate = false
    ElMessage.error(`提交任务失败：${error.message}`)
  }
}

async function cancelCurrentJob() {
  if (!job.id) return
  try {
    await api.cancelJob(job.id)
    ElMessage.success('已发送取消请求')
  } catch (error) {
    ElMessage.error(`取消失败：${error.message}`)
  }
}

async function uploadRefImage(event, character) {
  const file = event.target.files?.[0]
  if (!file) return
  try {
    const created = await api.uploadCharacterRefImage(file)
    character.reference_image_path = created.path
    character.reference_image_url = created.url || api.getCharacterRefImageUrl(created.path)
    await loadRefImages()
    ElMessage.success('参考图上传成功')
  } catch (error) {
    ElMessage.error(`上传失败：${error.message}`)
  }
}

async function generateRefImage(character, index) {
  generatingRef[index] = true
  try {
    const created = await api.generateCharacterRefImage({
      character_name: character.name || 'character',
      prompt: character.base_prompt || character.appearance || `${character.name} 角色立绘`,
      resolution: '768x768'
    })
    character.reference_image_path = created.path
    character.reference_image_url = created.url || api.getCharacterRefImageUrl(created.path)
    await loadRefImages()
    ElMessage.success('参考图生成成功')
  } catch (error) {
    ElMessage.error(`生成失败：${error.message}`)
  } finally {
    generatingRef[index] = false
  }
}

function bindRefImage(character, path) {
  if (!path) {
    clearRefImage(character)
    return
  }
  const found = refImages.value.find((item) => item.path === path)
  character.reference_image_path = path
  character.reference_image_url = found ? resolveRefImageUrl(found) : api.getCharacterRefImageUrl(path)
}

function resolveRefImageUrl(image) {
  return image.url || api.getCharacterRefImageUrl(image.path)
}

onMounted(async () => {
  await Promise.all([loadModels(), loadVoices(), loadRefImages()])
})
</script>

<template>
  <div class="page">
    <header class="header">
      <h1>小说视频生成（工作流重构版）</h1>
      <p>支持按句分段、每段小视频预览、任务取消、角色参考图复用</p>
    </header>

    <section class="card">
      <h2>基础设置</h2>
      <div class="grid">
        <div>
          <label>模型</label>
          <el-select
            v-model="selectedModel"
            style="width: 100%"
            filterable
            clearable
            reserve-keyword
            default-first-option
            placeholder="输入模型名进行搜索"
            no-match-text="无匹配模型"
          >
            <el-option
              v-for="item in models"
              :key="item.id"
              :label="`${item.name} (${item.provider}) ${item.available ? '' : '[不可用]'}`"
              :value="item.id"
              :disabled="!item.available"
            />
          </el-select>
        </div>
        <div>
          <label>分析深度</label>
          <el-select v-model="form.analysis_depth" style="width: 100%">
            <el-option label="basic" value="basic" />
            <el-option label="detailed" value="detailed" />
          </el-select>
        </div>
        <div>
          <label>分段方式</label>
          <el-select v-model="form.segment_method" style="width: 100%">
            <el-option label="按句分段" value="sentence" />
            <el-option label="智能分段" value="smart" />
            <el-option label="固定字数" value="fixed" />
          </el-select>
        </div>
        <div>
          <label>每段句数</label>
          <el-input-number v-model="form.sentences_per_segment" :min="1" :max="50" />
        </div>
        <div>
          <label>最大处理段数（0=全部）</label>
          <el-input-number v-model="form.max_segment_groups" :min="0" :max="10000" />
        </div>
        <div>
          <label>分辨率</label>
          <el-select v-model="form.resolution" style="width: 100%">
            <el-option label="1080x1920" value="1080x1920" />
            <el-option label="720x1280" value="720x1280" />
            <el-option label="1920x1080" value="1920x1080" />
          </el-select>
        </div>
        <div>
          <label>字幕样式</label>
          <el-select v-model="form.subtitle_style" style="width: 100%">
            <el-option label="basic" value="basic" />
            <el-option label="highlight" value="highlight" />
            <el-option label="danmaku" value="danmaku" />
            <el-option label="center" value="center" />
          </el-select>
        </div>
        <div>
          <label>FPS</label>
          <el-input-number v-model="form.fps" :min="15" :max="60" />
        </div>
      </div>
    </section>

    <section class="card">
      <h2>文本输入</h2>
      <el-input v-model="form.text" type="textarea" :rows="12" placeholder="粘贴小说文本" />
      <div class="actions">
        <el-button :loading="loading.segment" @click="runSegmentPreview">预览分段</el-button>
        <el-button type="primary" :loading="loading.analyze" @click="runAnalyze">分析角色</el-button>
      </div>
      <el-alert
        v-if="segmentPreview.total_segments"
        type="warning"
        show-icon
        :closable="false"
        :title="`成本提示：总句数 ${segmentPreview.total_sentences || '-'}，总分段 ${segmentPreview.total_segments}，实际处理 ${effectiveSegmentGroups}`"
      />
    </section>

    <section class="card" v-if="segmentPreview.total_segments">
      <h2>分段预览</h2>
      <p class="muted">当前规则：每 {{ form.sentences_per_segment }} 句为一段（句号/问号/叹号/分号/逗号都可切句）</p>
      <ol class="segments">
        <li v-for="item in segmentPreview.segments" :key="item.index">
          <strong>#{{ item.index + 1 }}</strong>
          <span class="muted">（约 {{ item.sentence_count || '?' }} 句）</span>
          <div>{{ item.text }}</div>
        </li>
      </ol>
    </section>

    <section class="card">
      <h2>角色配置（音色选择 + 参考图选择/上传/生成）</h2>
      <p class="muted">分析置信度：{{ (confidence * 100).toFixed(0) }}%</p>
      <div class="character-card" v-for="(character, index) in characters" :key="index">
        <div class="grid">
          <div>
            <label>角色名</label>
            <el-input v-model="character.name" />
          </div>
          <div>
            <label>角色定位</label>
            <el-input v-model="character.role" />
          </div>
          <div>
            <label>TTS音色</label>
            <el-select v-model="character.voice_id" style="width: 100%">
              <el-option
                v-for="voice in voices"
                :key="voice.id"
                :label="`${voice.name} (${voice.id})`"
                :value="voice.id"
              />
            </el-select>
          </div>
          <div>
            <label>参考图</label>
            <div class="ref-inline-actions">
              <el-button @click="openRefPicker(index)">选择参考图</el-button>
              <el-button @click="clearRefImage(character)">清除</el-button>
            </div>
            <p class="muted" v-if="character.reference_image_path">
              当前：{{ character.reference_image_path.split('/').pop() }}
            </p>
          </div>
        </div>
        <div class="grid">
          <div>
            <label>外貌描述</label>
            <el-input v-model="character.appearance" type="textarea" :rows="3" />
          </div>
          <div>
            <label>性格描述</label>
            <el-input v-model="character.personality" type="textarea" :rows="3" />
          </div>
          <div>
            <label>角色Prompt（生成参考图时用）</label>
            <el-input v-model="character.base_prompt" type="textarea" :rows="3" />
          </div>
        </div>
        <div class="actions">
          <label class="upload-btn">
            <input type="file" accept="image/*" @change="(event) => uploadRefImage(event, character)" />
            上传参考图
          </label>
          <el-button :loading="!!generatingRef[index]" @click="generateRefImage(character, index)">生成角色参考图</el-button>
        </div>
        <img v-if="character.reference_image_url" :src="character.reference_image_url" class="ref-thumb" alt="reference" />
      </div>
      <div class="actions">
        <el-button type="primary" @click="confirmCharacters">确认角色配置</el-button>
      </div>
    </section>

    <section class="card">
      <h2>生成与预览</h2>
      <div class="actions">
        <el-button type="primary" :loading="loading.generate" @click="runGenerate">开始生成</el-button>
        <el-button :disabled="!job.id" type="danger" @click="cancelCurrentJob">取消任务</el-button>
      </div>

      <div v-if="job.id" class="job">
        <p><strong>任务ID：</strong>{{ job.id }}</p>
        <p><strong>状态：</strong>{{ job.status }} / {{ job.step }}</p>
        <p><strong>消息：</strong>{{ job.message }}</p>
        <el-progress :percentage="Math.round(job.progress * 100)" />
      </div>

      <div v-if="job.clipPreviewUrls.length" class="clip-grid">
        <div v-for="(url, index) in job.clipPreviewUrls" :key="index" class="clip-item">
          <p>段落预览 #{{ index + 1 }}</p>
          <video :src="url" controls preload="metadata" class="video" />
        </div>
      </div>

      <div v-if="job.videoUrl" class="preview">
        <h3>最终视频</h3>
        <video :src="job.videoUrl" controls preload="metadata" class="video" />
        <a :href="job.videoUrl" target="_blank" rel="noopener noreferrer">下载最终视频</a>
      </div>
    </section>

    <section class="card">
      <h2>后端日志</h2>
      <div class="actions">
        <el-button :loading="loading.logs" @click="loadLogs">刷新日志</el-button>
      </div>
      <pre class="logs">{{ backendLogs.join('\n') }}</pre>
    </section>

    <el-dialog
      v-model="refPicker.visible"
      title="选择角色参考图"
      width="820px"
      :close-on-click-modal="false"
      @closed="closeRefPicker"
    >
      <div class="ref-library-modal" v-if="refImages.length">
        <div v-for="image in refImages" :key="image.path" class="ref-option" @click="pickRefImage(image)">
          <img :src="resolveRefImageUrl(image)" alt="ref" />
          <div class="filename">{{ image.filename }}</div>
        </div>
      </div>
      <el-empty v-else description="暂无参考图，请先上传或生成" />
      <template #footer>
        <el-button @click="closeRefPicker">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>
