<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { api } from './api'
import { t } from './i18n'

const models = ref([])
const voices = ref([])
const refImages = ref([])
const bgmLibrary = ref([])
const selectedModel = ref('')
const modelFilterKeyword = ref('')

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
  sentences_per_segment: 1,
  max_segment_groups: 0,
  resolution: '1080x1920',
  image_aspect_ratio: '',
  subtitle_style: 'highlight',
  camera_motion: 'vertical',
  fps: 30,
  bgm_enabled: true,
  bgm_volume: 0.12,
  enable_scene_image_reuse: true
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

const bgmStatus = reactive({
  exists: false,
  filename: 'bgm.mp3',
  size: 0,
  updated_at: '',
  source_filename: ''
})

let pollingTimer = null

const effectiveSegmentGroups = computed(() => {
  if (!segmentPreview.total_segments) return 0
  if (form.max_segment_groups > 0) {
    return Math.min(segmentPreview.total_segments, form.max_segment_groups)
  }
  return segmentPreview.total_segments
})

const filteredModels = computed(() => {
  const keyword = modelFilterKeyword.value.trim().toLowerCase()
  if (!keyword) return models.value
  return models.value.filter((item) => {
    const label = formatModelLabel(item).toLowerCase()
    return label.includes(keyword) || item.id.toLowerCase().includes(keyword)
  })
})

const refPicker = reactive({
  visible: false,
  characterIndex: -1
})

const bgmPicker = reactive({
  visible: false
})

const generatingRef = reactive({})

const nameReplace = reactive({
  enabled: true,
  maxCandidates: 24
})

const replacementEntries = ref([])

const replacementEnabledCount = computed(() => {
  return replacementEntries.value.filter((item) => item.enabled && item.replacement.trim()).length
})

const replacedTextPreview = computed(() => applyNameReplacements(form.text))

const hasReplacementEffect = computed(() => {
  if (!nameReplace.enabled) return false
  return replacedTextPreview.value !== form.text
})

const aspectRatioOptions = [
  { value: '', label: t('option.aspectUnspecified') },
  { value: '21:9', label: '21:9' },
  { value: '16:9', label: '16:9' },
  { value: '3:2', label: '3:2' },
  { value: '4:3', label: '4:3' },
  { value: '5:4', label: '5:4' },
  { value: '1:1', label: '1:1' },
  { value: '4:5', label: '4:5' },
  { value: '3:4', label: '3:4' },
  { value: '2:3', label: '2:3' },
  { value: '9:16', label: '9:16' }
]

function aspectRatioIconStyle(value) {
  if (!value) {
    return { width: '16px', height: '12px' }
  }
  const parts = value.split(':')
  const widthRaw = Number(parts[0])
  const heightRaw = Number(parts[1])
  if (!widthRaw || !heightRaw) {
    return { width: '16px', height: '12px' }
  }

  const maxSize = 18
  const minSize = 8
  if (widthRaw >= heightRaw) {
    const iconHeight = Math.max(minSize, Math.round((maxSize * heightRaw) / widthRaw))
    return { width: `${maxSize}px`, height: `${iconHeight}px` }
  }

  const iconWidth = Math.max(minSize, Math.round((maxSize * widthRaw) / heightRaw))
  return { width: `${iconWidth}px`, height: `${maxSize}px` }
}

function formatModelLabel(item) {
  return `${item.name} (${item.provider})${item.available ? '' : ` [${t('option.unavailable')}]`}`
}

function handleModelFilter(query) {
  modelFilterKeyword.value = query || ''
}

function clearModelFilter() {
  modelFilterKeyword.value = ''
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function escapeHtml(value) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function highlightText(text) {
  const safeText = escapeHtml(text)
  const keyword = modelFilterKeyword.value.trim()
  if (!keyword) return safeText
  const pattern = new RegExp(`(${escapeRegExp(keyword)})`, 'ig')
  return safeText.replace(pattern, '<span class="model-match">$1</span>')
}

function highlightModelOption(item) {
  return highlightText(formatModelLabel(item))
}

function extractReplacementCandidates(text, limit = 24) {
  const content = (text || '').trim()
  if (!content) return []

  const chineseStopwords = new Set([
    '我们',
    '你们',
    '他们',
    '她们',
    '自己',
    '这个',
    '那个',
    '这里',
    '那里',
    '什么',
    '怎么',
    '为什么',
    '但是',
    '然后',
    '如果',
    '因为',
    '所以',
    '已经',
    '可以',
    '不是',
    '就是',
    '一个',
    '一种',
    '一下',
    '时候',
    '今天',
    '昨天',
    '现在'
  ])

  const tokenCount = new Map()
  const normalized = content.replace(/\s+/g, '')
  const chineseBlocks = normalized.match(/[\u4e00-\u9fff]{2,}/g) || []

  for (const block of chineseBlocks) {
    for (let size = 2; size <= 4; size += 1) {
      if (block.length < size) continue
      for (let index = 0; index <= block.length - size; index += 1) {
        const token = block.slice(index, index + size)
        if (/^(.)\1+$/.test(token)) continue
        tokenCount.set(token, (tokenCount.get(token) || 0) + 1)
      }
    }
  }

  const latinTokens = content.match(/\b[A-Za-z][A-Za-z0-9_'-]{1,30}\b/g) || []
  for (const token of latinTokens) {
    const normalizedToken = token.trim()
    if (!normalizedToken) continue
    tokenCount.set(normalizedToken, (tokenCount.get(normalizedToken) || 0) + 1)
  }

  return [...tokenCount.entries()]
    .filter(([token, count]) => {
      if (count < 2) return false
      if (token.length < 2) return false
      if (chineseStopwords.has(token)) return false
      return true
    })
    .sort((a, b) => {
      const scoreA = a[1] * a[0].length
      const scoreB = b[1] * b[0].length
      if (scoreA !== scoreB) return scoreB - scoreA
      if (a[1] !== b[1]) return b[1] - a[1]
      return b[0].length - a[0].length
    })
    .slice(0, Math.max(1, limit))
    .map(([word, count]) => ({ word, count }))
}

function detectNameReplacementCandidates() {
  if (!form.text.trim()) {
    ElMessage.warning('请先输入文本')
    return
  }

  const previousMap = new Map(replacementEntries.value.map((item) => [item.word, item]))
  const candidates = extractReplacementCandidates(form.text, nameReplace.maxCandidates)

  replacementEntries.value = candidates.map((item) => {
    const previous = previousMap.get(item.word)
    return {
      word: item.word,
      count: item.count,
      enabled: previous?.enabled || false,
      replacement: previous?.replacement || ''
    }
  })

  if (!replacementEntries.value.length) {
    ElMessage.warning('未检测到可替换的高频词（至少出现 2 次）')
    return
  }
  ElMessage.success(`已检测到 ${replacementEntries.value.length} 个候选词`)
}

function clearNameReplacementEntries() {
  replacementEntries.value = []
}

function activeReplacementPairs() {
  return replacementEntries.value
    .filter((item) => item.enabled && item.replacement.trim() && item.replacement.trim() !== item.word)
    .map((item) => ({ from: item.word, to: item.replacement.trim() }))
    .sort((a, b) => b.from.length - a.from.length)
}

function applyNameReplacements(text) {
  const source = text || ''
  if (!nameReplace.enabled) return source
  const pairs = activeReplacementPairs()
  if (!pairs.length) return source

  let transformed = source
  for (const pair of pairs) {
    transformed = transformed.split(pair.from).join(pair.to)
  }
  return transformed
}

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

function openBgmPicker() {
  bgmPicker.visible = true
}

function closeBgmPicker() {
  bgmPicker.visible = false
}

function resolveRefImageUrl(image) {
  return image.url || api.getCharacterRefImageUrl(image.path)
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
    ElMessage.error(t('toast.modelsLoadFailed', { error: error.message }))
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
    ElMessage.error(t('toast.voicesLoadFailed', { error: error.message }))
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
    ElMessage.error(t('toast.refsLoadFailed', { error: error.message }))
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
    ElMessage.error(t('toast.logsLoadFailed', { error: error.message }))
  } finally {
    loading.logs = false
  }
}

async function runAnalyze() {
  const textForRun = applyNameReplacements(form.text)
  if (!textForRun.trim()) {
    ElMessage.warning(t('toast.textRequired'))
    return
  }
  loading.analyze = true
  try {
    const data = await api.analyzeCharacters({
      text: textForRun,
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
    ElMessage.success(t('toast.analyzeSuccess'))
  } catch (error) {
    ElMessage.error(t('toast.analyzeFailed', { error: error.message }))
  } finally {
    loading.analyze = false
  }
}

async function runSegmentPreview() {
  const textForRun = applyNameReplacements(form.text)
  if (!textForRun.trim()) {
    ElMessage.warning(t('toast.textRequired'))
    return
  }
  loading.segment = true
  try {
    const data = await api.segmentText({
      text: textForRun,
      method: form.segment_method,
      sentences_per_segment: form.sentences_per_segment,
      model_id: selectedModel.value || null
    })
    segmentPreview.total_segments = data.total_segments || 0
    segmentPreview.total_sentences = data.total_sentences || 0
    segmentPreview.segments = data.segments || []
    ElMessage.success(
      t('toast.segmentSuccess', {
        segments: segmentPreview.total_segments,
        sentences: segmentPreview.total_sentences || '-'
      })
    )
  } catch (error) {
    ElMessage.error(t('toast.segmentFailed', { error: error.message }))
  } finally {
    loading.segment = false
  }
}

async function confirmCharacters() {
  try {
    await api.confirmCharacters({ characters: characters.value })
    ElMessage.success(t('toast.confirmSuccess'))
  } catch (error) {
    ElMessage.error(t('toast.confirmFailed', { error: error.message }))
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
        ElMessage.success(t('toast.completed'))
      }
      if (status.status === 'failed' || status.status === 'cancelled') {
        stopPolling()
        loading.generate = false
        ElMessage.warning(status.message || status.status)
      }
    } catch (error) {
      stopPolling()
      loading.generate = false
      ElMessage.error(t('toast.statusFailed', { error: error.message }))
    }
  }, 1500)
}

async function runGenerate() {
  const textForRun = applyNameReplacements(form.text)
  if (!textForRun.trim()) {
    ElMessage.warning(t('toast.textRequired'))
    return
  }
  if (!characters.value.length) {
    ElMessage.warning(t('toast.characterRequired'))
    return
  }

  loading.generate = true
  resetJob()

  try {
    const payload = {
      text: textForRun,
      characters: characters.value,
      segment_method: form.segment_method,
      sentences_per_segment: form.sentences_per_segment,
      max_segment_groups: form.max_segment_groups,
      resolution: form.resolution,
      subtitle_style: form.subtitle_style,
      camera_motion: form.camera_motion,
      fps: form.fps,
      bgm_enabled: form.bgm_enabled,
      bgm_volume: form.bgm_volume,
      model_id: selectedModel.value || null,
      enable_scene_image_reuse: form.enable_scene_image_reuse
    }
    if (form.image_aspect_ratio) {
      payload.image_aspect_ratio = form.image_aspect_ratio
    }
    const data = await api.generateVideo(payload)
    job.id = data.job_id
    job.status = data.status
    startPolling()
    ElMessage.success(t('toast.queued'))
  } catch (error) {
    loading.generate = false
    ElMessage.error(t('toast.generateFailed', { error: error.message }))
  }
}

async function cancelCurrentJob() {
  if (!job.id) return
  try {
    await api.cancelJob(job.id)
    ElMessage.success(t('toast.cancelSuccess'))
  } catch (error) {
    ElMessage.error(t('toast.cancelFailed', { error: error.message }))
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
    ElMessage.success(t('toast.uploadSuccess'))
  } catch (error) {
    ElMessage.error(t('toast.uploadFailed', { error: error.message }))
  } finally {
    event.target.value = ''
  }
}

async function generateRefImage(character, index) {
  generatingRef[index] = true
  try {
    const created = await api.generateCharacterRefImage({
      character_name: character.name || 'character',
      prompt: character.base_prompt || character.appearance || `${character.name} 閫?`,
      resolution: '768x768'
    })
    character.reference_image_path = created.path
    character.reference_image_url = created.url || api.getCharacterRefImageUrl(created.path)
    await loadRefImages()
    ElMessage.success(t('toast.refGenerated'))
  } catch (error) {
    ElMessage.error(t('toast.refGenerateFailed', { error: error.message }))
  } finally {
    generatingRef[index] = false
  }
}

function formatFileSize(bytes) {
  const value = Number(bytes || 0)
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  if (value < 1024) return `${value} B`
  const kb = value / 1024
  if (kb < 1024) return `${kb.toFixed(1)} KB`
  const mb = kb / 1024
  return `${mb.toFixed(2)} MB`
}

async function loadBgmStatus() {
  try {
    const data = await api.getBgmStatus()
    bgmStatus.exists = Boolean(data.exists)
    bgmStatus.filename = data.filename || 'bgm.mp3'
    bgmStatus.size = Number(data.size || 0)
    bgmStatus.updated_at = data.updated_at || ''
    bgmStatus.source_filename = data.source_filename || ''
  } catch {
    bgmStatus.exists = false
    bgmStatus.filename = 'bgm.mp3'
    bgmStatus.size = 0
    bgmStatus.updated_at = ''
    bgmStatus.source_filename = ''
  }
}

async function loadBgmLibrary() {
  try {
    const data = await api.listBgmLibrary()
    bgmLibrary.value = data.items || []
  } catch {
    bgmLibrary.value = []
  }
}

async function uploadBgmFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  try {
    await api.uploadBgm(file)
    ElMessage.success('BGM 上传成功')
    await loadBgmLibrary()
    await loadBgmStatus()
  } catch (error) {
    ElMessage.error(`BGM 上传失败：${error.message}`)
  } finally {
    event.target.value = ''
  }
}

async function pickBgm(item) {
  try {
    await api.selectBgm(item.filename)
    ElMessage.success('已切换当前BGM')
    await loadBgmStatus()
    closeBgmPicker()
  } catch (error) {
    ElMessage.error(`切换BGM失败：${error.message}`)
  }
}

async function deleteCurrentBgm() {
  try {
    await api.deleteCurrentBgm()
    ElMessage.success('已删除当前BGM')
    await loadBgmStatus()
  } catch (error) {
    ElMessage.error(`删除当前BGM失败：${error.message}`)
  }
}

async function remixCurrentVideoBgm() {
  if (!job.id) {
    ElMessage.warning('请先生成至少一次视频')
    return
  }
  try {
    loading.generate = true
    await api.remixBgm(job.id, {
      bgm_enabled: form.bgm_enabled,
      bgm_volume: form.bgm_volume,
      fps: form.fps
    })
    job.videoUrl = `${api.getVideoUrl(job.id)}?t=${Date.now()}`
    ElMessage.success('已完成仅替换BGM（无需重跑全流程）')
  } catch (error) {
    ElMessage.error(`仅替换BGM失败：${error.message}`)
  } finally {
    loading.generate = false
  }
}

onMounted(async () => {
  await Promise.all([loadModels(), loadVoices(), loadRefImages()])
  await loadBgmLibrary()
  await loadBgmStatus()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<template>
  <div class="page">
    <header class="header">
      <h1>{{ t('app.title') }}</h1>
      <p>{{ t('app.subtitle') }}</p>
    </header>

    <section class="card">
      <h2>{{ t('section.config') }}</h2>
      <div class="grid">
        <div>
          <label>{{ t('field.model') }}</label>
          <el-select
            v-model="selectedModel"
            style="width: 100%"
            filterable
            clearable
            reserve-keyword
            default-first-option
            :filter-method="handleModelFilter"
            @clear="clearModelFilter"
            :placeholder="t('placeholder.modelSearch')"
            :no-match-text="t('placeholder.modelNoMatch')"
          >
            <el-option
              v-for="item in filteredModels"
              :key="item.id"
              :label="formatModelLabel(item)"
              :value="item.id"
              :disabled="!item.available"
            >
              <span class="model-option-label" v-html="highlightModelOption(item)"></span>
            </el-option>
          </el-select>
        </div>

        <div>
          <label>{{ t('field.analysisDepth') }}</label>
          <el-select v-model="form.analysis_depth" style="width: 100%">
            <el-option :label="t('option.basic')" value="basic" />
            <el-option :label="t('option.detailed')" value="detailed" />
          </el-select>
        </div>

        <div>
          <label>{{ t('field.segmentMethod') }}</label>
          <el-select v-model="form.segment_method" style="width: 100%">
            <el-option :label="t('option.sentence')" value="sentence" />
            <el-option :label="t('option.smart')" value="smart" />
            <el-option :label="t('option.fixed')" value="fixed" />
          </el-select>
        </div>

        <div>
          <label>{{ t('field.sentencesPerSegment') }}</label>
          <el-input-number v-model="form.sentences_per_segment" :min="1" :max="50" />
        </div>

        <div>
          <label>{{ t('field.maxSegmentGroups') }}（{{ t('field.maxSegmentHelp') }}）</label>
          <el-input-number v-model="form.max_segment_groups" :min="0" :max="10000" />
        </div>

        <div>
          <label>{{ t('field.resolution') }}</label>
          <el-select v-model="form.resolution" style="width: 100%">
            <el-option :label="t('option.resolution1080x1920')" value="1080x1920" />
            <el-option :label="t('option.resolution720x1280')" value="720x1280" />
            <el-option :label="t('option.resolution1920x1080')" value="1920x1080" />
          </el-select>
        </div>

        <div>
          <label>{{ t('field.imageAspectRatio') }}</label>
          <el-select v-model="form.image_aspect_ratio" clearable style="width: 100%">
            <el-option
              v-for="item in aspectRatioOptions"
              :key="item.value || 'none'"
              :label="item.label"
              :value="item.value"
            >
              <span class="aspect-option-row">
                <span class="aspect-icon" :style="aspectRatioIconStyle(item.value)"></span>
                <span>{{ item.label }}</span>
              </span>
            </el-option>
          </el-select>
        </div>

        <div>
          <label>{{ t('field.subtitleStyle') }}</label>
          <el-select v-model="form.subtitle_style" style="width: 100%">
            <el-option :label="t('option.subtitleBasic')" value="basic" />
            <el-option :label="t('option.subtitleHighlight')" value="highlight" />
            <el-option :label="t('option.subtitleDanmaku')" value="danmaku" />
            <el-option :label="t('option.subtitleCenter')" value="center" />
          </el-select>
        </div>

        <div>
          <label>{{ t('field.cameraMotion') }}</label>
          <el-select v-model="form.camera_motion" style="width: 100%">
            <el-option :label="t('option.cameraMotionVertical')" value="vertical" />
            <el-option :label="t('option.cameraMotionHorizontal')" value="horizontal" />
            <el-option :label="t('option.cameraMotionAuto')" value="auto" />
          </el-select>
        </div>

        <div>
          <label>{{ t('field.fps') }}</label>
          <el-input-number v-model="form.fps" :min="15" :max="60" />
        </div>

        <div>
          <label>背景音乐音量</label>
          <el-slider
            v-model="form.bgm_volume"
            :min="0"
            :max="0.5"
            :step="0.01"
            :disabled="!form.bgm_enabled"
            show-input
            :show-input-controls="false"
            input-size="small"
          />
        </div>
      </div>

      <div class="switch-row">
        <el-switch v-model="form.enable_scene_image_reuse" />
        <span>{{ t('field.sceneReuse') }}</span>
      </div>
      <div class="switch-row">
        <el-switch v-model="form.bgm_enabled" />
        <span>启用背景音乐（BGM）</span>
        <label class="upload-btn">
          <input type="file" accept=".mp3,audio/mpeg" @change="uploadBgmFile" />
          上传BGM
        </label>
        <el-button @click="openBgmPicker">从BGM库选择</el-button>
        <el-button type="danger" plain @click="deleteCurrentBgm">删除当前BGM</el-button>
      </div>
      <div class="muted bgm-status">
        <span v-if="bgmStatus.exists">
          当前BGM：{{ bgmStatus.filename }} ｜ {{ formatFileSize(bgmStatus.size) }}
          <span v-if="bgmStatus.source_filename"> ｜ 来源：{{ bgmStatus.source_filename }}</span>
          <span v-if="bgmStatus.updated_at"> ｜ 更新时间：{{ bgmStatus.updated_at }}</span>
        </span>
        <span v-else>当前BGM：未上传（默认读取 assets/bgm.mp3）</span>
      </div>
    </section>

    <section class="card">
      <h2>{{ t('section.text') }}</h2>
      <el-input v-model="form.text" type="textarea" :rows="12" :placeholder="t('placeholder.textInput')" />
      <div class="actions">
        <el-button :loading="loading.segment" @click="runSegmentPreview">{{ t('action.segmentPreview') }}</el-button>
        <el-button type="primary" :loading="loading.analyze" @click="runAnalyze">{{ t('action.analyze') }}</el-button>
      </div>

      <div class="replace-toolbar">
        <div class="switch-row">
          <el-switch v-model="nameReplace.enabled" />
          <span>启用名字替换字典</span>
        </div>
        <div class="actions">
          <el-button @click="detectNameReplacementCandidates">检测高频词</el-button>
          <el-button @click="clearNameReplacementEntries">清空字典</el-button>
          <span class="muted" v-if="replacementEntries.length">
            共 {{ replacementEntries.length }} 个候选，已启用 {{ replacementEnabledCount }} 个
          </span>
        </div>
      </div>

      <div v-if="replacementEntries.length" class="replace-list">
        <div class="replace-item" v-for="entry in replacementEntries" :key="entry.word">
          <el-checkbox v-model="entry.enabled" />
          <span class="word">{{ entry.word }}</span>
          <span class="muted">×{{ entry.count }}</span>
          <span class="arrow">→</span>
          <el-input v-model="entry.replacement" placeholder="替换成..." clearable />
        </div>
      </div>

      <el-alert
        v-if="hasReplacementEffect"
        type="info"
        show-icon
        :closable="false"
        title="名字替换已生效：分段/分析/生成都将使用替换后的文本"
      />

      <el-alert
        v-if="segmentPreview.total_segments"
        type="warning"
        show-icon
        :closable="false"
        :title="
          t('hint.segmentSummary', {
            sentences: segmentPreview.total_sentences || '-',
            segments: segmentPreview.total_segments,
            effective: effectiveSegmentGroups
          })
        "
      />
    </section>

    <section class="card" v-if="segmentPreview.total_segments">
      <h2>{{ t('section.segmentPreview') }}</h2>
      <p class="muted">{{ t('hint.sentenceRule', { count: form.sentences_per_segment }) }}</p>
      <ol class="segments">
        <li v-for="item in segmentPreview.segments" :key="item.index">
          <strong>#{{ item.index + 1 }}</strong>
          <span class="muted">（{{ item.sentence_count || '?' }} 句）</span>
          <div>{{ item.text }}</div>
        </li>
      </ol>
    </section>

    <section class="card">
      <h2>{{ t('section.characters') }}</h2>
      <p class="muted">{{ t('field.confidence') }}：{{ (confidence * 100).toFixed(0) }}%</p>

      <div class="character-card" v-for="(character, index) in characters" :key="index">
        <div class="grid">
          <div>
            <label>{{ t('field.roleName') }}</label>
            <el-input v-model="character.name" />
          </div>

          <div>
            <label>{{ t('field.roleType') }}</label>
            <el-input v-model="character.role" />
          </div>

          <div>
            <label>{{ t('field.voice') }}</label>
            <el-select v-model="character.voice_id" style="width: 100%">
              <el-option v-for="voice in voices" :key="voice.id" :label="`${voice.name} (${voice.id})`" :value="voice.id" />
            </el-select>
          </div>

          <div>
            <label>{{ t('field.referenceImage') }}</label>
            <div class="ref-inline-actions">
              <el-button @click="openRefPicker(index)">{{ t('action.pickReference') }}</el-button>
              <el-button @click="clearRefImage(character)">{{ t('action.clearReference') }}</el-button>
            </div>
            <p class="muted" v-if="character.reference_image_path">
              {{ t('hint.selectedRef', { filename: character.reference_image_path.split('/').pop() }) }}
            </p>
          </div>
        </div>

        <div class="grid">
          <div>
            <label>{{ t('field.appearance') }}</label>
            <el-input v-model="character.appearance" type="textarea" :rows="3" />
          </div>

          <div>
            <label>{{ t('field.personality') }}</label>
            <el-input v-model="character.personality" type="textarea" :rows="3" />
          </div>

          <div>
            <label>{{ t('field.basePrompt') }}</label>
            <el-input v-model="character.base_prompt" type="textarea" :rows="3" />
          </div>
        </div>

        <div class="actions">
          <label class="upload-btn">
            <input type="file" accept="image/*" @change="(event) => uploadRefImage(event, character)" />
            {{ t('action.uploadReference') }}
          </label>
          <el-button :loading="!!generatingRef[index]" @click="generateRefImage(character, index)">
            {{ t('action.generateReference') }}
          </el-button>
        </div>

        <img v-if="character.reference_image_url" :src="character.reference_image_url" class="ref-thumb" alt="reference" />
      </div>

      <div class="actions">
        <el-button type="primary" @click="confirmCharacters">{{ t('action.confirmCharacters') }}</el-button>
      </div>
    </section>

    <section class="card">
      <h2>{{ t('section.render') }}</h2>
      <div class="actions">
        <el-button type="primary" :loading="loading.generate" @click="runGenerate">{{ t('action.generateVideo') }}</el-button>
        <el-button :loading="loading.generate" :disabled="!job.id" @click="remixCurrentVideoBgm">仅替换BGM（最后一步）</el-button>
        <el-button :disabled="!job.id" type="danger" @click="cancelCurrentJob">{{ t('action.cancelJob') }}</el-button>
      </div>

      <div v-if="job.id" class="job">
        <p><strong>{{ t('hint.jobId') }}：</strong>{{ job.id }}</p>
        <p><strong>{{ t('hint.jobStatus') }}：</strong>{{ job.status }} / {{ job.step }}</p>
        <p><strong>{{ t('hint.jobMessage') }}：</strong>{{ job.message }}</p>
        <el-progress :percentage="Math.round(job.progress * 100)" />
      </div>

      <div v-if="job.clipPreviewUrls.length" class="clip-grid">
        <div v-for="(url, index) in job.clipPreviewUrls" :key="index" class="clip-item">
          <p>{{ t('hint.clip', { index: index + 1 }) }}</p>
          <video :src="url" controls preload="metadata" class="video" />
        </div>
      </div>

      <div v-if="job.videoUrl" class="preview">
        <h3>{{ t('hint.finalVideo') }}</h3>
        <video :src="job.videoUrl" controls preload="metadata" class="video" />
        <a :href="job.videoUrl" target="_blank" rel="noopener noreferrer">{{ t('action.downloadFinal') }}</a>
      </div>
    </section>

    <section class="card">
      <h2>{{ t('section.logs') }}</h2>
      <div class="actions">
        <el-button :loading="loading.logs" @click="loadLogs">{{ t('action.refreshLogs') }}</el-button>
      </div>
      <pre class="logs">{{ backendLogs.join('\n') }}</pre>
    </section>

    <el-dialog
      v-model="refPicker.visible"
      :title="t('action.pickReference')"
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
      <el-empty v-else :description="t('hint.noRefImage')" />
      <template #footer>
        <el-button @click="closeRefPicker">{{ t('action.close') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="bgmPicker.visible" title="BGM音乐库" width="820px" :close-on-click-modal="false" @closed="closeBgmPicker">
      <div class="ref-library-modal" v-if="bgmLibrary.length">
        <div v-for="item in bgmLibrary" :key="item.path" class="ref-option" @click="pickBgm(item)">
          <div class="filename">{{ item.filename }}</div>
          <div class="muted">{{ formatFileSize(item.size) }}</div>
          <audio :src="item.url" controls preload="none" class="bgm-audio" @click.stop></audio>
        </div>
      </div>
      <el-empty v-else description="暂无BGM，请先上传" />
      <template #footer>
        <el-button @click="closeBgmPicker">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

