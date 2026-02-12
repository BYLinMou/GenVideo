<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

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
  aliases: false,
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
  resolution: '1920x1080',
  image_aspect_ratio: '',
  subtitle_style: 'white_black',
  camera_motion: 'vertical',
  fps: 30,
  render_mode: 'balanced',
  bgm_enabled: true,
  bgm_volume: 0.08,
  novel_alias: '',
  watermark_enabled: true,
  watermark_type: 'text',
  watermark_text: '咕嘟看漫',
  watermark_image_path: '',
  watermark_opacity: 0.6,
  enable_scene_image_reuse: true,
  scene_reuse_no_repeat_window: 3
})

const characters = ref([])
const confidence = ref(0)

const segmentPreview = reactive({
  total_segments: 0,
  total_sentences: 0,
  segments: [],
  request_signature: ''
})

const backendLogs = ref([])

const job = reactive({
  id: '',
  status: '',
  progress: 0,
  step: '',
  message: '',
  currentSegment: 0,
  totalSegments: 0,
  videoUrl: '',
  clipPreviewUrls: []
})

const uiProgressPercent = computed(() => {
  const raw = Number(job.progress || 0)
  const ratio = Number.isFinite(raw) ? Math.min(Math.max(raw, 0), 1) : 0
  return Math.round(ratio * 100)
})

const sceneProgressPercent = computed(() => {
  const total = Math.max(0, Number(job.totalSegments || 0))
  const current = Math.max(0, Number(job.currentSegment || 0))
  if (!total) return 0
  return Math.min(100, Math.round((Math.min(current, total) / total) * 100))
})

const sceneProgressText = computed(() => {
  const total = Math.max(0, Number(job.totalSegments || 0))
  const current = Math.max(0, Number(job.currentSegment || 0))
  if (!total) return '未开始'
  return `Scene ${Math.min(current, total)}/${total}`
})

const bgmStatus = reactive({
  exists: false,
  filename: 'bgm.mp3',
  size: 0,
  updated_at: '',
  source_filename: ''
})

const JOB_IDS_STORAGE_KEY = 'genvideo_job_ids_v1'
const ACTIVE_JOB_ID_STORAGE_KEY = 'genvideo_active_job_id_v1'

let pollingTimer = null
let pollingBusy = false

const jobs = ref([])
const activeJobId = ref('')
const recoverJobIdInput = ref('')
const novelAliasInputRef = ref(null)

const sortedJobs = computed(() => {
  return [...jobs.value].sort((a, b) => Number(b.updatedAt || 0) - Number(a.updatedAt || 0))
})

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
const novelAliases = ref([])
const customAliasInput = ref('')

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
    const replacement = previous?.replacement || ''
    return {
      word: item.word,
      count: item.count,
      enabled: Boolean(previous?.enabled && replacement.trim()),
      replacement
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

function syncReplacementEntry(entry) {
  if (!entry) return
  if (!(entry.replacement || '').trim()) {
    entry.enabled = false
  }
}

function removeChapterHeadings() {
  const source = form.text || ''
  if (!source.trim()) {
    ElMessage.warning('请先输入文本')
    return
  }

  const chapterPrefixPatterns = [
    /^\s*正文\s*第[0-9０-９零〇一二两三四五六七八九十百千萬万壹贰叁肆伍陆柒捌玖拾佰仟廿卅]+[章节卷集部篇回]\s*[：:、，,。.!！?？;；·\-—~\s]*/,
    /^\s*第[0-9０-９零〇一二两三四五六七八九十百千萬万壹贰叁肆伍陆柒捌玖拾佰仟廿卅]+[章节卷集部篇回]\s*[：:、，,。.!！?？;；·\-—~\s]*/,
    /^\s*卷\s*[0-9０-９零〇一二两三四五六七八九十百千萬万壹贰叁肆伍陆柒捌玖拾佰仟廿卅]+\s*[：:、，,。.!！?？;；·\-—~\s]*/,
    /^\s*chapter\s*[0-9ivxlcdm]+\b\s*[：:、，,。.!！?？;；·\-—~\s]*/i,
  ]

  const lines = source.split(/\r?\n/)
  let changed = 0
  const normalized = lines.map((line) => {
    let next = line
    for (const pattern of chapterPrefixPatterns) {
      if (pattern.test(next)) {
        const replaced = next.replace(pattern, '')
        if (replaced !== next) {
          next = replaced
          changed += 1
        }
        break
      }
    }
    return next
  })

  if (!changed) {
    ElMessage.info('未检测到可过滤的章节前缀')
    return
  }

  form.text = normalized.join('\n')
  ElMessage.success(`已处理 ${changed} 处章节前缀`)
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

function applyReplacementsToSourceText() {
  if (!form.text.trim()) {
    ElMessage.warning('请先输入文本')
    return
  }

  const transformed = applyNameReplacements(form.text)
  if (transformed === form.text) {
    ElMessage.info('没有可应用的替换项')
    return
  }

  form.text = transformed
  ElMessage.success('已将字典替换应用到上方文本')
}

function resetJob() {
  job.id = ''
  job.status = ''
  job.progress = 0
  job.step = ''
  job.message = ''
  job.currentSegment = 0
  job.totalSegments = 0
  job.videoUrl = ''
  job.clipPreviewUrls = []
}

function createLocalJobRecord(jobId) {
  return {
    id: String(jobId || ''),
    status: 'queued',
    step: 'queued',
    message: 'Job queued',
    progress: 0,
    currentSegment: 0,
    totalSegments: 0,
    videoUrl: '',
    clipPreviewUrls: [],
    updatedAt: Date.now(),
    createdAt: Date.now()
  }
}

function upsertJobRecord(record) {
  if (!record?.id) return
  const id = String(record.id)
  const list = jobs.value
  const index = list.findIndex((item) => item.id === id)
  const now = Date.now()
  const merged = {
    ...(index >= 0 ? list[index] : createLocalJobRecord(id)),
    ...record,
    id,
    updatedAt: Number(record.updatedAt || now)
  }

  if (index >= 0) {
    list[index] = merged
  } else {
    list.push(merged)
  }
}

function removeJobRecord(jobId) {
  const id = String(jobId || '')
  if (!id) return
  jobs.value = jobs.value.filter((item) => item.id !== id)
  if (activeJobId.value === id) {
    activeJobId.value = jobs.value[0]?.id || ''
    const next = jobs.value.find((item) => item.id === activeJobId.value)
    if (next) {
      syncJobViewFromRecord(next)
    } else {
      resetJob()
    }
  }
}

function syncJobViewFromRecord(record) {
  if (!record) {
    resetJob()
    return
  }
  job.id = record.id || ''
  job.status = record.status || ''
  job.progress = Number(record.progress || 0)
  job.step = record.step || ''
  job.message = record.message || ''
  job.currentSegment = Number(record.currentSegment || 0)
  job.totalSegments = Number(record.totalSegments || 0)
  job.videoUrl = normalizeRuntimeUrl(record.videoUrl || '')
  job.clipPreviewUrls = (record.clipPreviewUrls || []).map((item) => normalizeRuntimeUrl(item))
}

function normalizeRuntimeUrl(raw) {
  const value = String(raw || '').trim()
  if (!value) return ''
  if (value.startsWith('/')) {
    return `${window.location.origin}${value}`
  }
  try {
    const parsed = new URL(value)
    if (window.location.hostname && parsed.hostname !== window.location.hostname) {
      if (parsed.pathname.startsWith('/api/') || parsed.pathname.startsWith('/assets/')) {
        return `${window.location.origin}${parsed.pathname}${parsed.search || ''}`
      }
    }
    return value
  } catch {
    return value
  }
}

function selectJob(jobId) {
  const id = String(jobId || '')
  if (!id) return
  activeJobId.value = id
  const found = jobs.value.find((item) => item.id === id)
  if (found) {
    syncJobViewFromRecord(found)
  }
}

function syncActiveJobRecordFromApiStatus(jobId, status) {
  const id = String(jobId || '')
  if (!id || !status) return null
  const next = {
    id,
    status: status.status || '',
    step: status.step || '',
    message: status.message || '',
    progress: Number(status.progress || 0),
    currentSegment: Number(status.current_segment || 0),
    totalSegments: Number(status.total_segments || 0),
    clipPreviewUrls: (status.clip_preview_urls || []).map((item) => normalizeRuntimeUrl(item)),
    videoUrl: status.status === 'completed' ? normalizeRuntimeUrl(status.output_video_url || api.getVideoUrl(id)) : '',
    updatedAt: Date.now()
  }
  upsertJobRecord(next)
  if (activeJobId.value === id) {
    syncJobViewFromRecord(next)
  }
  return next
}

function persistJobSnapshot() {
  if (typeof window === 'undefined') return
  const payload = jobs.value
    .slice(0, 100)
    .map((item) => ({
      id: item.id,
      status: item.status,
      step: item.step,
      message: item.message,
      progress: Number(item.progress || 0),
      currentSegment: Number(item.currentSegment || 0),
      totalSegments: Number(item.totalSegments || 0),
      videoUrl: item.videoUrl || '',
      clipPreviewUrls: item.clipPreviewUrls || [],
      updatedAt: Number(item.updatedAt || Date.now()),
      createdAt: Number(item.createdAt || Date.now())
    }))
  window.localStorage.setItem(JOB_IDS_STORAGE_KEY, JSON.stringify(payload))
  window.localStorage.setItem(ACTIVE_JOB_ID_STORAGE_KEY, activeJobId.value || '')
}

function restoreJobSnapshot() {
  if (typeof window === 'undefined') return
  try {
    const raw = window.localStorage.getItem(JOB_IDS_STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    if (!Array.isArray(parsed)) return
    jobs.value = parsed
      .filter((item) => item && item.id)
      .map((item) => ({
        id: String(item.id),
        status: item.status || '',
        step: item.step || '',
        message: item.message || '',
        progress: Number(item.progress || 0),
        currentSegment: Number(item.currentSegment || 0),
        totalSegments: Number(item.totalSegments || 0),
        videoUrl: item.videoUrl || '',
        clipPreviewUrls: Array.isArray(item.clipPreviewUrls) ? item.clipPreviewUrls : [],
        updatedAt: Number(item.updatedAt || Date.now()),
        createdAt: Number(item.createdAt || Date.now())
      }))

    const savedActive = window.localStorage.getItem(ACTIVE_JOB_ID_STORAGE_KEY) || ''
    if (savedActive && jobs.value.some((item) => item.id === savedActive)) {
      activeJobId.value = savedActive
    } else {
      activeJobId.value = jobs.value[0]?.id || ''
    }

    if (activeJobId.value) {
      const record = jobs.value.find((item) => item.id === activeJobId.value)
      if (record) syncJobViewFromRecord(record)
    }
  } catch {
    jobs.value = []
    activeJobId.value = ''
  }
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
  const rawUrl = (image?.url || '').trim()
  if (!rawUrl) {
    return api.getCharacterRefImageUrl(image.path)
  }

  if (rawUrl.startsWith('/')) {
    return `${window.location.origin}${rawUrl}`
  }

  try {
    const parsed = new URL(rawUrl)
    if (window.location.hostname && parsed.hostname && parsed.hostname !== window.location.hostname) {
      if (parsed.pathname.startsWith('/assets/')) {
        return `${window.location.origin}${parsed.pathname}`
      }
    }
    return rawUrl
  } catch {
    return rawUrl
  }
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
    const preferred = models.value.find((item) => item.id === 'gpt-oss-120b' && item.available)
    selectedModel.value = preferred?.id || models.value.find((item) => item.available)?.id || models.value[0]?.id || ''
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
    segmentPreview.request_signature = data.request_signature || ''
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

async function pollJobsOnce() {
  if (pollingBusy) return
  const ids = jobs.value.map((item) => String(item.id || '')).filter(Boolean)
  if (!ids.length) return

  pollingBusy = true
  try {
    await Promise.all(
      ids.map(async (id) => {
        try {
          const status = await api.getJob(id)
          const updated = syncActiveJobRecordFromApiStatus(id, status)
          if (!updated) return

          if (status.status === 'completed') {
            const completedUrl = normalizeRuntimeUrl(status.output_video_url || api.getVideoUrl(id))
            upsertJobRecord({ id, videoUrl: completedUrl, updatedAt: Date.now() })
            if (activeJobId.value === id) {
              job.videoUrl = completedUrl
            }
          }
        } catch (error) {
          const msg = String(error?.message || '')
          if (msg.includes('404')) {
            removeJobRecord(id)
          }
        }
      })
    )
  } finally {
    pollingBusy = false
    persistJobSnapshot()
  }
}

function startPolling() {
  stopPolling()
  pollingTimer = setInterval(pollJobsOnce, 1500)
  void pollJobsOnce()
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

  if (!String(form.novel_alias || '').trim()) {
    try {
      await ElMessageBox.confirm('还没有设置顶部书名，是否先去添加？', '提示', {
        confirmButtonText: '去设置',
        cancelButtonText: '继续生成',
        type: 'warning',
        distinguishCancelAndClose: true
      })
      await nextTick()
      novelAliasInputRef.value?.focus?.()
      novelAliasInputRef.value?.input?.focus?.()
      return
    } catch (action) {
      if (action !== 'cancel') {
        return
      }
    }
  }

  loading.generate = true

  try {
    const payload = {
      text: textForRun,
      characters: characters.value,
      segment_method: form.segment_method,
      segment_request_signature: segmentPreview.request_signature || null,
      precomputed_segments: Array.isArray(segmentPreview.segments)
        ? segmentPreview.segments.map((item) => String(item?.text || '').trim()).filter(Boolean)
        : null,
      sentences_per_segment: form.sentences_per_segment,
      max_segment_groups: form.max_segment_groups,
      resolution: form.resolution,
      subtitle_style: form.subtitle_style,
      camera_motion: form.camera_motion,
      fps: form.fps,
      render_mode: form.render_mode,
      bgm_enabled: form.bgm_enabled,
      bgm_volume: form.bgm_volume,
      novel_alias: form.novel_alias || null,
      watermark_enabled: form.watermark_enabled,
      watermark_type: form.watermark_type,
      watermark_text: form.watermark_text || null,
      watermark_image_path: form.watermark_image_path || null,
      watermark_opacity: form.watermark_opacity,
      model_id: selectedModel.value || null,
      enable_scene_image_reuse: form.enable_scene_image_reuse
    }
    payload.scene_reuse_no_repeat_window = form.scene_reuse_no_repeat_window
    if (form.image_aspect_ratio) {
      payload.image_aspect_ratio = form.image_aspect_ratio
    }
    const data = await api.generateVideo(payload)
    const id = String(data.job_id || '')
    if (!id) {
      throw new Error('missing job_id')
    }
    upsertJobRecord({
      ...createLocalJobRecord(id),
      status: data.status || 'queued',
      step: data.status || 'queued',
      message: 'Job queued',
      updatedAt: Date.now()
    })
    selectJob(id)
    persistJobSnapshot()
    startPolling()
    ElMessage.success(`${t('toast.queued')} | JobID: ${id}`)
  } catch (error) {
    loading.generate = false
    ElMessage.error(t('toast.generateFailed', { error: error.message }))
    return
  }
  loading.generate = false
}

async function cancelCurrentJob() {
  if (!activeJobId.value) return
  try {
    await api.cancelJob(activeJobId.value)
    ElMessage.success(t('toast.cancelSuccess'))
    await pollJobsOnce()
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

async function generateNovelAliases() {
  const textForRun = applyNameReplacements(form.text)
  if (!textForRun.trim()) {
    ElMessage.warning('请先输入文本')
    return
  }

  loading.aliases = true
  try {
    const data = await api.generateNovelAliases({
      text: textForRun,
      count: 10,
      model_id: selectedModel.value || null
    })
    novelAliases.value = data.aliases || []
    if (!novelAliases.value.length) {
      ElMessage.warning('未生成可用别名，请重试')
    } else {
      ElMessage.success(`已生成 ${novelAliases.value.length} 个别名`) 
    }
  } catch (error) {
    ElMessage.error(`别名生成失败：${error.message}`)
  } finally {
    loading.aliases = false
  }
}

function applyAlias(alias) {
  if (!alias) return
  form.novel_alias = alias
  ElMessage.success(`已应用别名：${alias}`)
}

function applyCustomAlias() {
  const alias = (customAliasInput.value || '').trim()
  if (!alias) {
    ElMessage.warning('请先输入别名')
    return
  }
  applyAlias(alias)
  customAliasInput.value = ''
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

async function uploadWatermarkFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  try {
    const created = await api.uploadWatermark(file)
    form.watermark_image_path = created.path || ''
    form.watermark_type = 'image'
    form.watermark_enabled = true
    ElMessage.success('水印图片上传成功')
  } catch (error) {
    ElMessage.error(`水印图片上传失败：${error.message}`)
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
  if (!activeJobId.value) {
    ElMessage.warning('请先生成至少一次视频')
    return
  }
  try {
    loading.generate = true
    await api.remixBgm(activeJobId.value, {
      bgm_enabled: form.bgm_enabled,
      bgm_volume: form.bgm_volume,
      fps: form.fps,
      novel_alias: form.novel_alias || null,
      watermark_enabled: form.watermark_enabled,
      watermark_type: form.watermark_type,
      watermark_text: form.watermark_text || null,
      watermark_image_path: form.watermark_image_path || null,
      watermark_opacity: form.watermark_opacity
    })
    const nextUrl = `${api.getVideoUrl(activeJobId.value)}?t=${Date.now()}`
    upsertJobRecord({ id: activeJobId.value, videoUrl: nextUrl, updatedAt: Date.now() })
    if (job.id === activeJobId.value) {
      job.videoUrl = nextUrl
    }
    persistJobSnapshot()
    ElMessage.success('已完成仅替换BGM（无需重跑全流程）')
  } catch (error) {
    ElMessage.error(`仅替换BGM失败：${error.message}`)
  } finally {
    loading.generate = false
  }
}

async function recoverJobById() {
  const id = String(recoverJobIdInput.value || '').trim()
  if (!id) {
    ElMessage.warning('请先输入任务ID')
    return
  }
  try {
    const status = await api.getJob(id)
    syncActiveJobRecordFromApiStatus(id, status)
    selectJob(id)
    recoverJobIdInput.value = ''
    persistJobSnapshot()
    startPolling()
    ElMessage.success(`已恢复任务：${id}`)
  } catch (error) {
    ElMessage.error(`恢复任务失败：${error.message}`)
  }
}

function openJob(jobId) {
  selectJob(jobId)
  persistJobSnapshot()
}

async function removeJob(jobId) {
  const id = String(jobId || '')
  if (!id) return

  const target = jobs.value.find((item) => item.id === id)
  const needCancel = target ? ['queued', 'running'].includes(target.status) : false

  if (needCancel) {
    try {
      await api.cancelJob(id)
    } catch (error) {
      const message = String(error?.message || '')
      if (!message.includes('404')) {
        ElMessage.error(t('toast.cancelFailed', { error: error.message }))
        return
      }
    }
  }

  removeJobRecord(id)
  persistJobSnapshot()
  if (!jobs.value.length) {
    stopPolling()
  }

  if (needCancel) {
    ElMessage.success(t('toast.cancelSuccess'))
  }
}

onMounted(async () => {
  restoreJobSnapshot()
  await Promise.all([loadModels(), loadVoices(), loadRefImages()])
  await loadBgmLibrary()
  await loadBgmStatus()
  if (jobs.value.length) {
    startPolling()
  }
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
            <el-option label="黄字黑边" value="yellow_black" />
            <el-option label="黑字白边" value="black_white" />
            <el-option label="白字黑边" value="white_black" />
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
          <label>{{ t('field.renderMode') }}</label>
          <el-select v-model="form.render_mode" style="width: 100%">
            <el-option :label="t('option.renderFast')" value="fast" />
            <el-option :label="t('option.renderBalanced')" value="balanced" />
            <el-option :label="t('option.renderQuality')" value="quality" />
          </el-select>
        </div>

        <div>
          <label>{{ t('field.fps') }}</label>
          <el-input-number v-model="form.fps" :min="15" :max="60" />
        </div>

        <div>
          <label>顶部书名（最终叠加）</label>
          <el-input ref="novelAliasInputRef" v-model="form.novel_alias" placeholder="视频顶部书名（可留空）" clearable />
          <p class="muted" style="margin: 6px 0 0">书名将作为最终合成顶栏叠加，不再写入正文</p>
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
        <span>{{ t('field.sceneReuseNoRepeatWindow') }}</span>
        <el-input-number
          v-model="form.scene_reuse_no_repeat_window"
          :min="0"
          :max="100"
          :disabled="!form.enable_scene_image_reuse"
        />
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
      <div class="switch-row">
        <el-switch v-model="form.watermark_enabled" />
        <span>启用水印</span>
        <el-select v-model="form.watermark_type" style="width: 140px" :disabled="!form.watermark_enabled">
          <el-option label="文字" value="text" />
          <el-option label="图片" value="image" />
        </el-select>
        <el-input
          v-model="form.watermark_text"
          placeholder="水印文字"
          clearable
          style="max-width: 220px"
          :disabled="!form.watermark_enabled || form.watermark_type !== 'text'"
        />
        <label class="upload-btn" :class="{ disabled: !form.watermark_enabled || form.watermark_type !== 'image' }">
          <input
            type="file"
            accept="image/*"
            @change="uploadWatermarkFile"
            :disabled="!form.watermark_enabled || form.watermark_type !== 'image'"
          />
          上传水印图
        </label>
      </div>
      <div class="grid">
        <div>
          <label>水印透明度</label>
          <el-slider
            v-model="form.watermark_opacity"
            :min="0.05"
            :max="1"
            :step="0.01"
            :disabled="!form.watermark_enabled"
            show-input
            :show-input-controls="false"
            input-size="small"
          />
        </div>
      </div>
      <div class="muted bgm-status" v-if="form.watermark_image_path">
        <span>水印图片：{{ form.watermark_image_path.split('/').pop() }}</span>
      </div>
      <div class="muted bgm-status">
        <span v-if="bgmStatus.exists">
          当前BGM：{{ bgmStatus.filename }} ｜ {{ formatFileSize(bgmStatus.size) }}
          <span v-if="bgmStatus.source_filename"> ｜ 来源：{{ bgmStatus.source_filename }}</span>
          <span v-if="bgmStatus.updated_at"> ｜ 更新时间：{{ bgmStatus.updated_at }}</span>
        </span>
        <span v-else>当前BGM：未找到（默认回退 assets/bgm/happinessinmusic-rock-trailer-417598.mp3）</span>
      </div>
    </section>

    <section class="card">
      <h2>{{ t('section.text') }}</h2>
      <el-input v-model="form.text" type="textarea" :rows="12" :placeholder="t('placeholder.textInput')" />
      <div class="actions">
        <el-button @click="removeChapterHeadings">过滤章节标题</el-button>
        <el-button :loading="loading.segment" @click="runSegmentPreview">{{ t('action.segmentPreview') }}</el-button>
        <el-button type="primary" :loading="loading.analyze" @click="runAnalyze">{{ t('action.analyze') }}</el-button>
      </div>

      <div class="replace-toolbar">
        <div class="switch-row">
          <el-switch v-model="nameReplace.enabled" />
          <span>启用名字替换字典</span>
        </div>
        <div class="actions">
          <el-button type="primary" @click="applyReplacementsToSourceText">执行替换</el-button>
          <el-button @click="detectNameReplacementCandidates">检测高频词</el-button>
          <el-button @click="clearNameReplacementEntries">清空字典</el-button>
          <span class="muted" v-if="replacementEntries.length">
            共 {{ replacementEntries.length }} 个候选，已启用 {{ replacementEnabledCount }} 个
          </span>
        </div>
      </div>

      <div class="replace-toolbar">
        <div class="actions">
          <el-input v-model="customAliasInput" placeholder="手动输入别名" clearable style="max-width: 260px" @keyup.enter="applyCustomAlias" />
          <el-button @click="applyCustomAlias">添加</el-button>
          <el-button :loading="loading.aliases" @click="generateNovelAliases">生成小说别名（10个）</el-button>
          <el-button :disabled="loading.aliases" @click="generateNovelAliases">重新生成</el-button>
          <span class="muted" v-if="novelAliases.length">点击下方任意别名即可应用</span>
        </div>
        <div class="alias-list" v-if="novelAliases.length">
          <el-tag
            v-for="item in novelAliases"
            :key="item"
            class="alias-item"
            effect="plain"
            @click="applyAlias(item)"
          >
            {{ item }}
          </el-tag>
        </div>
      </div>

      <div v-if="replacementEntries.length" class="replace-list">
        <div class="replace-item" v-for="entry in replacementEntries" :key="entry.word">
          <el-checkbox v-model="entry.enabled" :disabled="!entry.replacement.trim()" />
          <span class="word">{{ entry.word }}</span>
          <span class="muted">×{{ entry.count }}</span>
          <span class="arrow">→</span>
          <el-input v-model="entry.replacement" placeholder="替换成..." clearable @input="syncReplacementEntry(entry)" />
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
      <div class="job-restore-row">
        <el-input v-model="recoverJobIdInput" placeholder="输入任务ID后恢复进度" clearable style="max-width: 360px" />
        <el-button @click="recoverJobById">恢复任务</el-button>
      </div>

      <div v-if="sortedJobs.length" class="job-list">
        <div
          v-for="item in sortedJobs"
          :key="item.id"
          class="job-list-item"
          :class="{ active: item.id === activeJobId }"
        >
          <div class="job-list-main" @click="openJob(item.id)">
            <div class="job-list-id">{{ item.id }}</div>
            <div class="job-list-meta">
              <span>{{ item.status }}</span>
              <span>·</span>
              <span>{{ Math.round((Number(item.progress || 0) * 100)) }}%</span>
              <span v-if="item.totalSegments">· Scene {{ item.currentSegment || 0 }}/{{ item.totalSegments }}</span>
            </div>
          </div>
          <el-button size="small" type="danger" plain @click="removeJob(item.id)">移除</el-button>
        </div>
      </div>

      <div class="actions">
        <el-button type="primary" :loading="loading.generate" @click="runGenerate">{{ t('action.generateVideo') }}</el-button>
        <el-button :loading="loading.generate" :disabled="!activeJobId" @click="remixCurrentVideoBgm">仅替换BGM（最后一步）</el-button>
        <el-button :disabled="!activeJobId" type="danger" @click="cancelCurrentJob">{{ t('action.cancelJob') }}</el-button>
      </div>

      <div v-if="job.id" class="job">
        <p><strong>{{ t('hint.jobId') }}：</strong>{{ job.id }}</p>
        <p><strong>{{ t('hint.jobStatus') }}：</strong>{{ job.status }} / {{ job.step }}</p>
        <p><strong>{{ t('hint.jobMessage') }}：</strong>{{ job.message }}</p>
        <p><strong>当前场景：</strong>{{ sceneProgressText }}</p>
        <el-progress :percentage="sceneProgressPercent" :status="job.status === 'failed' ? 'exception' : (job.status === 'completed' ? 'success' : '')">
          <span>{{ sceneProgressPercent }}%</span>
        </el-progress>
        <p><strong>总体进度：</strong>{{ uiProgressPercent }}%</p>
        <el-progress :percentage="uiProgressPercent" :status="job.status === 'failed' ? 'exception' : (job.status === 'completed' ? 'success' : '')" />
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

