<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { WORKSPACE_AUTH_REQUIRED_EVENT, api } from './api'
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
  logs: false,
  finalVideos: false
})

const PAGE_WORKSPACE = 'workspace'
const PAGE_FINAL_VIDEOS = 'final-videos'
const activePage = ref(PAGE_WORKSPACE)
const finalVideos = ref([])
const workspaceAuthRequired = ref(false)
const workspaceUnlocked = ref(true)
const workspaceAuthLoading = ref(false)
const workspacePasswordInput = ref('')
const workspaceAuthError = ref('')
const workspaceDataBootstrapped = ref(false)

const form = reactive({
  text: '',
  analysis_depth: 'detailed',
  segment_method: 'sentence',
  sentences_per_segment: 5,
  max_segment_groups: 0,
  segment_groups_range: '0',
  resolution: '1920x1080',
  image_aspect_ratio: '',
  subtitle_style: 'white_black',
  camera_motion: 'vertical',
  fps: 30,
  render_mode: 'balanced',
  bgm_enabled: true,
  bgm_volume: 0.07,
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

function normalizeCharacterIdentityFlags(source, options = {}) {
  const { ensureOneMain = false } = options
  const list = Array.isArray(source) ? source : []

  const mainIndexes = []
  const selfIndexes = []
  list.forEach((item, index) => {
    if (!item || typeof item !== 'object') return
    item.is_main_character = Boolean(item.is_main_character)
    item.is_story_self = Boolean(item.is_story_self)
    if (item.is_main_character) mainIndexes.push(index)
    if (item.is_story_self) selfIndexes.push(index)
  })

  if (mainIndexes.length > 1) {
    const keep = mainIndexes[0]
    mainIndexes.slice(1).forEach((index) => {
      list[index].is_main_character = false
    })
    mainIndexes.length = 0
    mainIndexes.push(keep)
  }

  if (selfIndexes.length > 1) {
    const keep = selfIndexes[0]
    selfIndexes.slice(1).forEach((index) => {
      list[index].is_story_self = false
    })
  }

  if (ensureOneMain && !mainIndexes.length && list.length) {
    const bestIndex = list.reduce((best, item, index) => {
      const score = Number(item?.importance || 0)
      return score > Number(list[best]?.importance || 0) ? index : best
    }, 0)
    list[bestIndex].is_main_character = true
  }

  return list
}

function setMainCharacter(index, value) {
  const enabled = Boolean(value)
  if (!enabled) {
    const target = characters.value[index]
    if (target && target.is_main_character) {
      target.is_main_character = true
    }
    return
  }
  characters.value.forEach((item, itemIndex) => {
    item.is_main_character = enabled && itemIndex === index
  })
}

function setStorySelf(index, value) {
  const enabled = Boolean(value)
  characters.value.forEach((item, itemIndex) => {
    item.is_story_self = enabled && itemIndex === index
  })
}

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
  clipPreviewUrls: [],
  imageSourceReport: null
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
  if (!total) return t('hint.sceneNotStarted')
  return t('hint.sceneProgress', { current: Math.min(current, total), total })
})

const workspaceLocked = computed(() => {
  return workspaceAuthRequired.value && !workspaceUnlocked.value
})

const imageSourceSummary = computed(() => {
  const report = normalizeImageSourceReport(job.imageSourceReport)
  if (!report || report.total_images <= 0) return null
  return {
    ...report,
    cacheRatioText: formatRatioPercent(report.cache_ratio),
    generatedRatioText: formatRatioPercent(report.generate_ratio),
    otherRatioText: formatRatioPercent(report.other_ratio)
  }
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
const WORKSPACE_DRAFT_STORAGE_KEY = 'genvideo_workspace_draft_v1'

const WORKSPACE_DRAFT_FORM_NUMBER_FIELDS = [
  'sentences_per_segment',
  'max_segment_groups',
  'fps',
  'bgm_volume',
  'watermark_opacity',
  'scene_reuse_no_repeat_window'
]

const WORKSPACE_DRAFT_FORM_BOOLEAN_FIELDS = [
  'bgm_enabled',
  'watermark_enabled',
  'enable_scene_image_reuse'
]

const WORKSPACE_DRAFT_FORM_STRING_FIELDS = [
  'text',
  'analysis_depth',
  'segment_method',
  'segment_groups_range',
  'resolution',
  'image_aspect_ratio',
  'subtitle_style',
  'camera_motion',
  'render_mode',
  'novel_alias',
  'watermark_type',
  'watermark_text',
  'watermark_image_path'
]

let pollingTimer = null
let pollingBusy = false
let workspaceDraftPersistTimer = null
let restoringWorkspaceDraft = false

const jobs = ref([])
const activeJobId = ref('')
const recoverJobIdInput = ref('')
const novelAliasInputRef = ref(null)
const clipVideoEnabled = reactive({})
const finalVideoEnabled = reactive({})

const sortedJobs = computed(() => {
  return [...jobs.value].sort((a, b) => {
    const createdDelta = Number(b.createdAt || 0) - Number(a.createdAt || 0)
    if (createdDelta !== 0) return createdDelta
    return String(a.id || '').localeCompare(String(b.id || ''))
  })
})

const effectiveSegmentGroups = computed(() => {
  if (!segmentPreview.total_segments) return 0
  const parsed = parseSegmentGroupsRange(form.segment_groups_range, segmentPreview.total_segments)
  if (parsed.valid && parsed.indexes.length) {
    return parsed.indexes.length
  }
  if (form.max_segment_groups > 0) {
    return Math.min(segmentPreview.total_segments, form.max_segment_groups)
  }
  return segmentPreview.total_segments
})

function parseSegmentGroupsRange(value, totalSegments = 0) {
  const text = String(value || '').trim()
  if (!text) {
    return { valid: true, indexes: [] }
  }

  const normalized = text
    .replaceAll('，', ',')
    .replaceAll('；', ',')
    .replaceAll(';', ',')
    .replaceAll('～', '-')
    .replaceAll('~', '-')
    .replaceAll('—', '-')
    .replaceAll('–', '-')
    .replaceAll('到', '-')

  const parts = normalized
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)

  if (!parts.length) {
    return { valid: true, indexes: [] }
  }

  const indexes = []
  const seen = new Set()
  const singleTokenMode = parts.length === 1
  for (const part of parts) {
    let start = 0
    let end = 0
    if (singleTokenMode && /^-?\d+$/.test(part)) {
      const value = Number(part)
      if (!Number.isFinite(value)) {
        return { valid: false, indexes: [], error: part }
      }
      if (value <= 0) {
        if (totalSegments > 0) {
          for (let number = 1; number <= totalSegments; number += 1) {
            if (seen.has(number)) continue
            seen.add(number)
            indexes.push(number)
          }
        }
        continue
      }
      start = 1
      end = value
    } else if (/^\d+$/.test(part)) {
      if (singleTokenMode) {
        start = 1
        end = Number(part)
      } else {
        start = Number(part)
        end = Number(part)
      }
    } else {
      const matched = part.match(/^(\d+)\s*-\s*(\d+)$/)
      if (!matched) {
        return { valid: false, indexes: [], error: part }
      }
      start = Number(matched[1])
      end = Number(matched[2])
    }

    if (!Number.isFinite(start) || !Number.isFinite(end) || start <= 0 || end <= 0) {
      return { valid: false, indexes: [], error: part }
    }

    const lo = Math.min(start, end)
    const hi = Math.max(start, end)
    for (let number = lo; number <= hi; number += 1) {
      if (totalSegments > 0 && number > totalSegments) break
      if (seen.has(number)) continue
      seen.add(number)
      indexes.push(number)
    }
  }

  return { valid: true, indexes }
}

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

const SEGMENT_REDUCE_ALERT_THRESHOLD = 120

const generatingRef = reactive({})
const characterKeyMap = new WeakMap()
let characterKeySeq = 0

function getCharacterKey(character, index) {
  if (!character || typeof character !== 'object') {
    return `char-${index}`
  }
  if (!characterKeyMap.has(character)) {
    characterKeySeq += 1
    characterKeyMap.set(character, `char-${characterKeySeq}`)
  }
  return characterKeyMap.get(character)
}

function isGeneratingRef(character, index) {
  const key = getCharacterKey(character, index)
  return !!generatingRef[key]
}

function normalizeDraftCharacter(item) {
  if (!item || typeof item !== 'object') return null
  const importance = Number(item.importance || 5)
  return {
    name: String(item.name || '').trim(),
    role: String(item.role || 'supporting').trim() || 'supporting',
    importance: Math.max(1, Math.min(10, Number.isFinite(importance) ? importance : 5)),
    is_main_character: Boolean(item.is_main_character),
    is_story_self: Boolean(item.is_story_self),
    appearance: String(item.appearance || '').trim(),
    personality: String(item.personality || '').trim(),
    voice_id: String(item.voice_id || voices.value[0]?.id || 'zh-CN-YunxiNeural'),
    base_prompt: String(item.base_prompt || '').trim(),
    reference_image_path: String(item.reference_image_path || ''),
    reference_image_url: String(item.reference_image_url || '')
  }
}

function buildWorkspaceDraftPayload() {
  const formSnapshot = {}
  for (const field of WORKSPACE_DRAFT_FORM_NUMBER_FIELDS) {
    formSnapshot[field] = Number(form[field] ?? 0)
  }
  for (const field of WORKSPACE_DRAFT_FORM_BOOLEAN_FIELDS) {
    formSnapshot[field] = Boolean(form[field])
  }
  for (const field of WORKSPACE_DRAFT_FORM_STRING_FIELDS) {
    formSnapshot[field] = String(form[field] ?? '')
  }

  return {
    selectedModel: String(selectedModel.value || ''),
    confidence: Number(confidence.value || 0),
    form: formSnapshot,
    characters: (characters.value || []).map((item) => normalizeDraftCharacter(item)).filter(Boolean),
    nameReplace: {
      enabled: Boolean(nameReplace.enabled),
      maxCandidates: Math.max(1, Number(nameReplace.maxCandidates || 24))
    },
    replacementEntries: (replacementEntries.value || []).map((item) => ({
      word: String(item?.word || ''),
      count: Math.max(0, Number(item?.count || 0)),
      enabled: Boolean(item?.enabled),
      replacement: String(item?.replacement || '')
    })),
    novelAliases: (novelAliases.value || []).map((item) => String(item || '')).filter(Boolean),
    customAliasInput: String(customAliasInput.value || ''),
    savedAt: Date.now()
  }
}

function persistWorkspaceDraft() {
  if (typeof window === 'undefined') return
  try {
    const payload = buildWorkspaceDraftPayload()
    window.localStorage.setItem(WORKSPACE_DRAFT_STORAGE_KEY, JSON.stringify(payload))
  } catch (error) {
    console.warn('[workspace] persist draft failed:', error)
  }
}

function schedulePersistWorkspaceDraft() {
  if (typeof window === 'undefined') return
  if (restoringWorkspaceDraft) return
  if (workspaceDraftPersistTimer) {
    clearTimeout(workspaceDraftPersistTimer)
  }
  workspaceDraftPersistTimer = setTimeout(() => {
    workspaceDraftPersistTimer = null
    persistWorkspaceDraft()
  }, 600)
}

function restoreWorkspaceDraft() {
  if (typeof window === 'undefined') return
  try {
    const raw = window.localStorage.getItem(WORKSPACE_DRAFT_STORAGE_KEY)
    if (!raw) return
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return

    restoringWorkspaceDraft = true

    const savedModel = String(parsed.selectedModel || '').trim()
    if (savedModel) {
      selectedModel.value = savedModel
    }

    const savedForm = parsed.form && typeof parsed.form === 'object' ? parsed.form : {}
    for (const field of WORKSPACE_DRAFT_FORM_NUMBER_FIELDS) {
      if (!Object.prototype.hasOwnProperty.call(savedForm, field)) continue
      const value = Number(savedForm[field])
      if (Number.isFinite(value)) {
        form[field] = value
      }
    }
    for (const field of WORKSPACE_DRAFT_FORM_BOOLEAN_FIELDS) {
      if (!Object.prototype.hasOwnProperty.call(savedForm, field)) continue
      form[field] = Boolean(savedForm[field])
    }
    for (const field of WORKSPACE_DRAFT_FORM_STRING_FIELDS) {
      if (!Object.prototype.hasOwnProperty.call(savedForm, field)) continue
      form[field] = String(savedForm[field] ?? '')
    }

    const savedCharacters = Array.isArray(parsed.characters)
      ? parsed.characters.map((item) => normalizeDraftCharacter(item)).filter(Boolean)
      : []
    if (savedCharacters.length) {
      characters.value = normalizeCharacterIdentityFlags(savedCharacters, { ensureOneMain: true })
    }

    const savedConfidence = Number(parsed.confidence)
    confidence.value = Number.isFinite(savedConfidence) ? savedConfidence : 0

    const savedNameReplace = parsed.nameReplace && typeof parsed.nameReplace === 'object' ? parsed.nameReplace : {}
    nameReplace.enabled = Boolean(savedNameReplace.enabled)
    const maxCandidates = Number(savedNameReplace.maxCandidates)
    nameReplace.maxCandidates = Number.isFinite(maxCandidates) ? Math.max(1, maxCandidates) : 24

    replacementEntries.value = Array.isArray(parsed.replacementEntries)
      ? parsed.replacementEntries.map((item) => ({
          word: String(item?.word || ''),
          count: Math.max(0, Number(item?.count || 0)),
          enabled: Boolean(item?.enabled),
          replacement: String(item?.replacement || '')
        }))
      : []

    novelAliases.value = Array.isArray(parsed.novelAliases)
      ? parsed.novelAliases.map((item) => String(item || '')).filter(Boolean)
      : []
    customAliasInput.value = String(parsed.customAliasInput || '')
  } catch (error) {
    console.warn('[workspace] restore draft failed:', error)
  } finally {
    restoringWorkspaceDraft = false
  }
}

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
    ElMessage.warning(t('toast.textRequired'))
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
    ElMessage.warning(t('toast.noReplacementCandidates'))
    return
  }
  ElMessage.success(t('toast.replacementCandidatesDetected', { count: replacementEntries.value.length }))
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
    ElMessage.warning(t('toast.textRequired'))
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
    ElMessage.info(t('toast.chapterHeadingNotFound'))
    return
  }

  form.text = normalized.join('\n')
  ElMessage.success(t('toast.chapterHeadingProcessed', { count: changed }))
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
    ElMessage.warning(t('toast.textRequired'))
    return
  }

  const transformed = applyNameReplacements(form.text)
  if (transformed === form.text) {
    ElMessage.info(t('toast.noReplacementToApply'))
    return
  }

  form.text = transformed
  ElMessage.success(t('toast.replacementApplied'))
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
  job.imageSourceReport = null
}

function createLocalJobRecord(jobId) {
  return {
    id: String(jobId || ''),
    status: 'queued',
    step: 'queued',
    message: t('toast.jobQueuedMessage'),
    progress: 0,
    currentSegment: 0,
    totalSegments: 0,
    videoUrl: '',
    clipPreviewUrls: [],
    imageSourceReport: null,
    updatedAt: Date.now(),
    createdAt: Date.now()
  }
}

function normalizeImageSourceReport(raw) {
  if (!raw || typeof raw !== 'object') return null
  const toInt = (value) => Math.max(0, Number.parseInt(value, 10) || 0)
  const toRatio = (value) => {
    const num = Number(value)
    if (!Number.isFinite(num)) return 0
    return Math.max(0, Math.min(1, num))
  }
  const total = toInt(raw.total_images)
  if (!total) return null
  return {
    total_images: total,
    cache_images: toInt(raw.cache_images),
    generated_images: toInt(raw.generated_images),
    reference_images: toInt(raw.reference_images),
    other_images: toInt(raw.other_images),
    cache_ratio: toRatio(raw.cache_ratio),
    generate_ratio: toRatio(raw.generate_ratio),
    reference_ratio: toRatio(raw.reference_ratio),
    other_ratio: toRatio(raw.other_ratio)
  }
}

function formatRatioPercent(value) {
  const ratio = Number(value)
  if (!Number.isFinite(ratio)) return '0.0%'
  const safe = Math.max(0, Math.min(1, ratio))
  return `${(safe * 100).toFixed(1)}%`
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
  job.imageSourceReport = normalizeImageSourceReport(record.imageSourceReport)
}

function resetClipVideoEnabledForJob(jobId) {
  const id = String(jobId || '').trim()
  if (!id) return
  const prefix = `${id}:`
  Object.keys(clipVideoEnabled).forEach((key) => {
    if (key.startsWith(prefix)) {
      delete clipVideoEnabled[key]
    }
  })
}

function clipVideoKey(index) {
  return `${String(job.id || '')}:${Number(index)}`
}

function isClipVideoEnabled(index) {
  return !!clipVideoEnabled[clipVideoKey(index)]
}

function enableClipVideo(index) {
  clipVideoEnabled[clipVideoKey(index)] = true
}

function getClipThumbnailUrl(index) {
  if (!job.id) return ''
  return api.getClipThumbnailUrl(job.id, Number(index))
}

function getClipVideoUrl(index) {
  if (!job.id) return ''
  return api.getClipUrl(job.id, Number(index))
}

function finalVideoKey(item, index) {
  const name = String(item?.filename || '').trim()
  if (name) return `final-video:${name}`
  return `final-video-index:${Number(index)}`
}

function isFinalVideoEnabled(item, index) {
  return !!finalVideoEnabled[finalVideoKey(item, index)]
}

function enableFinalVideo(item, index) {
  finalVideoEnabled[finalVideoKey(item, index)] = true
}

function resetFinalVideoEnabled() {
  Object.keys(finalVideoEnabled).forEach((key) => {
    delete finalVideoEnabled[key]
  })
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

function resolvePageFromPath() {
  if (typeof window === 'undefined') return PAGE_WORKSPACE
  const pathname = String(window.location.pathname || '/').trim().toLowerCase()
  if (pathname === '/final-videos') {
    return PAGE_FINAL_VIDEOS
  }
  return PAGE_WORKSPACE
}

function setPagePath(page, options = {}) {
  if (typeof window === 'undefined') return
  const { replace = false } = options
  const target = page === PAGE_FINAL_VIDEOS ? '/final-videos' : '/workspace'
  const current = String(window.location.pathname || '/').trim()
  if (current !== target) {
    const method = replace ? 'replaceState' : 'pushState'
    window.history[method]({}, '', target)
  }
}

async function loadFinalVideos(options = {}) {
  const { silent = false } = options
  loading.finalVideos = true
  try {
    const data = await api.listFinalVideos(500)
    const items = Array.isArray(data?.videos) ? data.videos : []
    finalVideos.value = items.map((item) => ({
      filename: String(item.filename || ''),
      size: Number(item.size || 0),
      createdAt: String(item.created_at || ''),
      updatedAt: String(item.updated_at || ''),
      videoUrl: normalizeRuntimeUrl(item.video_url || ''),
      thumbnailUrl: normalizeRuntimeUrl(item.thumbnail_url || ''),
      downloadUrl: normalizeRuntimeUrl(item.download_url || '')
    }))
    resetFinalVideoEnabled()
  } catch (error) {
    if (!silent) {
      ElMessage.error(t('toast.finalVideosLoadFailed', { error: error.message }))
    }
    finalVideos.value = []
    resetFinalVideoEnabled()
  } finally {
    loading.finalVideos = false
  }
}

function setWorkspaceLockedState() {
  workspaceUnlocked.value = false
  workspacePasswordInput.value = ''
  stopPolling()
}

async function refreshWorkspaceAuthStatus(options = {}) {
  const { silent = false } = options
  try {
    const data = await api.getWorkspaceAuthStatus()
    workspaceAuthRequired.value = !!data?.required
    workspaceAuthError.value = ''

    if (!workspaceAuthRequired.value) {
      workspaceUnlocked.value = true
      return true
    }

    const savedPassword = String(api.getWorkspacePassword() || '')
    if (!savedPassword) {
      setWorkspaceLockedState()
      return false
    }

    try {
      await api.loginWorkspace(savedPassword)
      workspaceUnlocked.value = true
      return true
    } catch {
      api.clearWorkspacePassword()
      setWorkspaceLockedState()
      if (!silent) {
        workspaceAuthError.value = t('toast.workspacePasswordInvalid')
      }
      return false
    }
  } catch (error) {
    if (!silent) {
      ElMessage.error(t('toast.workspaceAuthStatusFailed', { error: error.message }))
    }
    setWorkspaceLockedState()
    return false
  }
}

async function bootstrapWorkspaceData() {
  if (workspaceDataBootstrapped.value) {
    if (jobs.value.length && !pollingTimer) {
      startPolling()
    }
    return
  }
  restoreJobSnapshot()
  restoreWorkspaceDraft()
  await Promise.all([loadModels(), loadVoices(), loadRefImages()])
  await loadBgmLibrary()
  await loadBgmStatus()
  await syncJobsFromBackend({ activateLatest: true })
  if (jobs.value.length) {
    startPolling()
  }
  workspaceDataBootstrapped.value = true
}

async function submitWorkspacePassword() {
  const password = String(workspacePasswordInput.value || '')
  if (!password) {
    workspaceAuthError.value = t('toast.workspacePasswordRequired')
    return
  }

  workspaceAuthLoading.value = true
  workspaceAuthError.value = ''
  try {
    await api.loginWorkspace(password)
    workspaceUnlocked.value = true
    workspacePasswordInput.value = ''
    await bootstrapWorkspaceData()
  } catch (error) {
    api.clearWorkspacePassword()
    setWorkspaceLockedState()
    workspaceAuthError.value = t('toast.workspaceLoginFailed', { error: error.message })
  } finally {
    workspaceAuthLoading.value = false
  }
}

function handleWorkspaceAuthRequired() {
  if (!workspaceAuthRequired.value) return
  api.clearWorkspacePassword()
  setWorkspaceLockedState()
  workspaceAuthError.value = t('toast.workspaceSessionExpired')
}

async function logoutWorkspace() {
  try {
    await api.logoutWorkspace()
  } catch {
  }
  api.clearWorkspacePassword()
  setWorkspaceLockedState()
  workspaceAuthError.value = ''
  ElMessage.success(t('toast.workspaceLoggedOut'))
}

function formatDateTime(value) {
  if (value == null) return '-'

  let timestamp = null
  if (typeof value === 'number' && Number.isFinite(value)) {
    timestamp = value
  } else {
    const text = String(value || '').trim()
    if (!text) return '-'

    if (/^-?\d+$/.test(text)) {
      const numeric = Number(text)
      if (Number.isFinite(numeric)) {
        timestamp = numeric
      }
    }

    if (timestamp == null) {
      const parsed = new Date(text)
      if (Number.isNaN(parsed.getTime())) return text
      timestamp = parsed.getTime()
    }
  }

  const normalized = Math.abs(timestamp) < 100000000000 ? timestamp * 1000 : timestamp
  const result = new Date(normalized)
  if (Number.isNaN(result.getTime())) return String(value)
  return result.toLocaleString()
}

function parseApiTimeToMs(raw) {
  if (raw == null) return null
  const text = String(raw).trim()
  if (!text) return null
  const parsed = new Date(text)
  const ms = parsed.getTime()
  if (!Number.isFinite(ms) || Number.isNaN(ms)) return null
  return ms
}

async function switchPage(page) {
  const next = page === PAGE_FINAL_VIDEOS ? PAGE_FINAL_VIDEOS : PAGE_WORKSPACE
  activePage.value = next
  setPagePath(next)
  if (next === PAGE_FINAL_VIDEOS) {
    if (!finalVideos.value.length) {
      await loadFinalVideos()
    }
    return
  }

  const ok = await refreshWorkspaceAuthStatus({ silent: true })
  if (ok) {
    await bootstrapWorkspaceData()
  }
}

async function handleLocationChange() {
  const next = resolvePageFromPath()
  activePage.value = next
  if (next === PAGE_FINAL_VIDEOS) {
    await loadFinalVideos({ silent: true })
    return
  }
  const ok = await refreshWorkspaceAuthStatus({ silent: true })
  if (ok) {
    await bootstrapWorkspaceData()
  }
}

function selectJob(jobId) {
  const id = String(jobId || '')
  if (!id) return
  resetClipVideoEnabledForJob(id)
  activeJobId.value = id
  const found = jobs.value.find((item) => item.id === id)
  if (found) {
    syncJobViewFromRecord(found)
  }
}

function syncActiveJobRecordFromApiStatus(jobId, status) {
  const id = String(jobId || '')
  if (!id || !status) return null
  const createdAtMs = parseApiTimeToMs(status.created_at)
  const updatedAtMs = parseApiTimeToMs(status.updated_at)
  const next = {
    id,
    status: status.status || '',
    step: status.step || '',
    message: status.message || '',
    progress: Number(status.progress || 0),
    currentSegment: Number(status.current_segment || 0),
    totalSegments: Number(status.total_segments || 0),
    clipPreviewUrls: (status.clip_preview_urls || []).map((item) => normalizeRuntimeUrl(item)),
    imageSourceReport: normalizeImageSourceReport(status.image_source_report),
    videoUrl: status.status === 'completed' ? normalizeRuntimeUrl(status.output_video_url || api.getVideoUrl(id)) : '',
    updatedAt: updatedAtMs || Date.now(),
    createdAt: createdAtMs || undefined
  }
  upsertJobRecord(next)
  if (activeJobId.value === id) {
    syncJobViewFromRecord(next)
  }
  return next
}

async function forceRefreshJobStatus(jobId, options = {}) {
  const id = String(jobId || '').trim()
  const { silent = false } = options
  if (!id) return null
  try {
    const status = await api.getJob(id)
    return syncActiveJobRecordFromApiStatus(id, status)
  } catch (error) {
    if (!silent) {
      ElMessage.error(t('toast.statusFailed', { error: error.message }))
    }
    return null
  }
}

async function syncJobsFromBackend(options = {}) {
  const { activateLatest = false } = options
  try {
    const data = await api.listJobs(120)
    const serverJobs = Array.isArray(data?.jobs) ? data.jobs : []
    serverJobs.forEach((item) => {
      const id = String(item?.job_id || item?.id || '').trim()
      if (!id) return
      syncActiveJobRecordFromApiStatus(id, item)
    })

    if (!activeJobId.value && activateLatest && jobs.value.length) {
      const first = sortedJobs.value[0]
      if (first?.id) {
        selectJob(first.id)
      }
    } else if (activeJobId.value) {
      const current = jobs.value.find((item) => item.id === activeJobId.value)
      if (current) {
        syncJobViewFromRecord(current)
      }
    }

    persistJobSnapshot()
    return serverJobs.length
  } catch (error) {
    console.warn('[jobs] sync from backend failed:', error)
    return 0
  }
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
      imageSourceReport: normalizeImageSourceReport(item.imageSourceReport),
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
        imageSourceReport: normalizeImageSourceReport(item.imageSourceReport),
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

function withImageCacheBust(url) {
  const text = String(url || '').trim()
  if (!text) return ''
  const separator = text.includes('?') ? '&' : '?'
  return `${text}${separator}t=${Date.now()}`
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
    const restored = models.value.find((item) => item.id === selectedModel.value && item.available)
    selectedModel.value = restored?.id || preferred?.id || models.value.find((item) => item.available)?.id || models.value[0]?.id || ''
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
    const normalizedCharacters = (data.characters || []).map((item) => ({
      ...item,
      is_main_character: Boolean(item?.is_main_character),
      is_story_self: Boolean(item?.is_story_self),
      reference_image_path: item.reference_image_path || '',
      reference_image_url: item.reference_image_url || '',
      voice_id: item.voice_id || voices.value[0]?.id || 'zh-CN-YunxiNeural'
    }))
    characters.value = normalizeCharacterIdentityFlags(normalizedCharacters, { ensureOneMain: true })
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
  if (!ids.length) {
    const synced = await syncJobsFromBackend({ activateLatest: true })
    if (!synced || !jobs.value.length) {
      stopPolling()
    }
    return
  }

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
          const statusCode = Number(error?.status || error?.statusCode || 0)
          const msg = String(error?.message || '')
          if (statusCode === 404 || msg.includes('404') || msg.toLowerCase().includes('job not found')) {
            removeJobRecord(id)
          }
        }
      })
    )
  } finally {
    pollingBusy = false
    if (!jobs.value.length) {
      stopPolling()
    }
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

  const segmentRangeCheck = parseSegmentGroupsRange(form.segment_groups_range)
  if (!segmentRangeCheck.valid) {
    ElMessage.warning(t('toast.segmentRangeInvalid'))
    return
  }

  const effectiveSegments = Math.max(0, Number(effectiveSegmentGroups.value || 0))
  if (effectiveSegments > SEGMENT_REDUCE_ALERT_THRESHOLD) {
    try {
      await ElMessageBox.confirm(
        t('dialog.tooManySegmentsMessage', {
          count: effectiveSegments,
          limit: SEGMENT_REDUCE_ALERT_THRESHOLD
        }),
        t('dialog.tipTitle'),
        {
          confirmButtonText: t('dialog.tooManySegmentsContinue'),
          cancelButtonText: t('dialog.tooManySegmentsReduce'),
          type: 'warning',
          distinguishCancelAndClose: true
        }
      )
    } catch (action) {
      if (action === 'cancel') {
        return
      }
      return
    }
  }

  if (!String(form.novel_alias || '').trim()) {
    try {
      await ElMessageBox.confirm(t('dialog.missingAliasMessage'), t('dialog.tipTitle'), {
        confirmButtonText: t('dialog.goSetup'),
        cancelButtonText: t('dialog.continueGenerate'),
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
      segment_groups_range: String(form.segment_groups_range || '').trim() || null,
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
      message: t('toast.jobQueuedMessage'),
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

async function resumeCurrentJob() {
  if (!activeJobId.value) return
  try {
    await api.resumeJob(activeJobId.value)
    ElMessage.success(t('toast.resumeRequested'))
    await forceRefreshJobStatus(activeJobId.value, { silent: true })
    await syncJobsFromBackend()
    startPolling()
  } catch (error) {
    ElMessage.error(t('toast.resumeFailed', { error: error.message }))
  }
}

async function uploadRefImage(event, character) {
  const file = event.target.files?.[0]
  if (!file) return
  try {
    const created = await api.uploadCharacterRefImage(file)
    character.reference_image_path = created.path
    character.reference_image_url = withImageCacheBust(created.url || api.getCharacterRefImageUrl(created.path))
    await loadRefImages()
    ElMessage.success(t('toast.uploadSuccess'))
  } catch (error) {
    ElMessage.error(t('toast.uploadFailed', { error: error.message }))
  } finally {
    event.target.value = ''
  }
}

function buildCharacterReferencePrompt(character) {
  const name = String(character?.name || '').trim()
  const role = String(character?.role || '').trim()
  const appearance = String(character?.appearance || '').trim()
  const personality = String(character?.personality || '').trim()
  const basePrompt = String(character?.base_prompt || '').trim()

  const sections = [
    name ? `Character: ${name}` : '',
    role ? `Role: ${role}` : '',
    appearance ? `Appearance: ${appearance}` : '',
    personality ? `Personality: ${personality}` : '',
    basePrompt ? `Style and details: ${basePrompt}` : ''
  ].filter(Boolean)

  if (!sections.length) {
    return 'Character reference illustration, anime style, detailed design'
  }

  return sections.join('\n')
}

async function generateRefImage(character, index) {
  const loadingKey = getCharacterKey(character, index)
  generatingRef[loadingKey] = true
  try {
    const prompt = buildCharacterReferencePrompt(character)
    const created = await api.generateCharacterRefImage({
      character_name: character.name || 'character',
      prompt,
      resolution: '768x768'
    })
    character.reference_image_path = created.path
    character.reference_image_url = withImageCacheBust(created.url || api.getCharacterRefImageUrl(created.path))
    await loadRefImages()
    ElMessage.success(t('toast.refGenerated'))
  } catch (error) {
    ElMessage.error(t('toast.refGenerateFailed', { error: error.message }))
  } finally {
    generatingRef[loadingKey] = false
  }
}

async function generateNovelAliases() {
  const textForRun = applyNameReplacements(form.text)
  if (!textForRun.trim()) {
    ElMessage.warning(t('toast.textRequired'))
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
      ElMessage.warning(t('toast.aliasEmpty'))
    } else {
      ElMessage.success(t('toast.aliasGenerated', { count: novelAliases.value.length }))
    }
  } catch (error) {
    ElMessage.error(t('toast.aliasGenerateFailed', { error: error.message }))
  } finally {
    loading.aliases = false
  }
}

function applyAlias(alias) {
  if (!alias) return
  form.novel_alias = alias
  ElMessage.success(t('toast.aliasApplied', { alias }))
}

function applyCustomAlias() {
  const alias = (customAliasInput.value || '').trim()
  if (!alias) {
    ElMessage.warning(t('toast.aliasInputRequired'))
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
    ElMessage.success(t('toast.bgmUploadSuccess'))
    await loadBgmLibrary()
    await loadBgmStatus()
  } catch (error) {
    ElMessage.error(t('toast.bgmUploadFailed', { error: error.message }))
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
    ElMessage.success(t('toast.watermarkUploadSuccess'))
  } catch (error) {
    ElMessage.error(t('toast.watermarkUploadFailed', { error: error.message }))
  } finally {
    event.target.value = ''
  }
}

async function pickBgm(item) {
  try {
    await api.selectBgm(item.filename)
    ElMessage.success(t('toast.bgmSwitchSuccess'))
    await loadBgmStatus()
    closeBgmPicker()
  } catch (error) {
    ElMessage.error(t('toast.bgmSwitchFailed', { error: error.message }))
  }
}

async function deleteCurrentBgm() {
  try {
    await api.deleteCurrentBgm()
    ElMessage.success(t('toast.bgmDeleteSuccess'))
    await loadBgmStatus()
  } catch (error) {
    ElMessage.error(t('toast.bgmDeleteFailed', { error: error.message }))
  }
}

async function remixCurrentVideoBgm() {
  if (!activeJobId.value) {
    ElMessage.warning(t('toast.remixNeedVideo'))
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
    ElMessage.success(t('toast.remixSuccess'))
  } catch (error) {
    ElMessage.error(t('toast.remixFailed', { error: error.message }))
  } finally {
    loading.generate = false
  }
}

async function recoverJobById() {
  const id = String(recoverJobIdInput.value || '').trim()
  if (!id) {
    ElMessage.warning(t('toast.jobIdRequired'))
    return
  }
  try {
    const updated = await forceRefreshJobStatus(id, { silent: true })
    if (!updated) {
      throw new Error('job not found')
    }
    await syncJobsFromBackend({ activateLatest: true })
    selectJob(id)
    recoverJobIdInput.value = ''
    persistJobSnapshot()
    startPolling()
    ElMessage.success(t('toast.recoverSuccess', { id }))
  } catch (error) {
    ElMessage.error(t('toast.recoverFailed', { error: error.message }))
  }
}

async function openJob(jobId) {
  selectJob(jobId)
  await forceRefreshJobStatus(jobId, { silent: true })
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
      const statusCode = Number(error?.status || error?.statusCode || 0)
      const message = String(error?.message || '')
      if (statusCode !== 404 && !message.includes('404') && !message.toLowerCase().includes('job not found')) {
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
  if (typeof window !== 'undefined') {
    activePage.value = resolvePageFromPath()
    setPagePath(activePage.value, { replace: true })
    window.addEventListener('popstate', handleLocationChange)
    window.addEventListener(WORKSPACE_AUTH_REQUIRED_EVENT, handleWorkspaceAuthRequired)
  }

  const workspaceReady = await refreshWorkspaceAuthStatus({ silent: true })

  if (activePage.value === PAGE_WORKSPACE && workspaceReady) {
    await bootstrapWorkspaceData()
  }
  if (activePage.value === PAGE_FINAL_VIDEOS) {
    await loadFinalVideos({ silent: true })
  }
})

watch(
  form,
  () => {
    schedulePersistWorkspaceDraft()
  },
  { deep: true }
)

watch(
  characters,
  () => {
    schedulePersistWorkspaceDraft()
  },
  { deep: true }
)

watch(selectedModel, () => {
  schedulePersistWorkspaceDraft()
})

watch(confidence, () => {
  schedulePersistWorkspaceDraft()
})

watch(
  nameReplace,
  () => {
    schedulePersistWorkspaceDraft()
  },
  { deep: true }
)

watch(
  replacementEntries,
  () => {
    schedulePersistWorkspaceDraft()
  },
  { deep: true }
)

watch(
  novelAliases,
  () => {
    schedulePersistWorkspaceDraft()
  },
  { deep: true }
)

watch(customAliasInput, () => {
  schedulePersistWorkspaceDraft()
})

onUnmounted(() => {
  stopPolling()
  if (workspaceDraftPersistTimer) {
    clearTimeout(workspaceDraftPersistTimer)
    workspaceDraftPersistTimer = null
  }
  persistWorkspaceDraft()
  if (typeof window !== 'undefined') {
    window.removeEventListener('popstate', handleLocationChange)
    window.removeEventListener(WORKSPACE_AUTH_REQUIRED_EVENT, handleWorkspaceAuthRequired)
  }
})
</script>

<template>
  <div class="page">
    <header class="header">
      <h1>{{ t('app.title') }}</h1>
      <p>{{ t('app.subtitle') }}</p>
    </header>

    <div class="page-nav">
      <el-button :type="activePage === PAGE_WORKSPACE ? 'primary' : 'default'" @click="switchPage(PAGE_WORKSPACE)">
        {{ t('page.workspace') }}
      </el-button>
      <el-button :type="activePage === PAGE_FINAL_VIDEOS ? 'primary' : 'default'" @click="switchPage(PAGE_FINAL_VIDEOS)">
        {{ t('page.finalVideos') }}
      </el-button>
      <el-button v-if="workspaceAuthRequired && workspaceUnlocked" @click="logoutWorkspace">
        {{ t('action.workspaceLogout') }}
      </el-button>
    </div>

    <template v-if="activePage === PAGE_WORKSPACE">
    <template v-if="workspaceLocked">
      <section class="card">
        <h2>{{ t('section.workspaceAuth') }}</h2>
        <p class="muted">{{ t('hint.workspaceLocked') }}</p>
        <div class="job-restore-row">
          <el-input
            v-model="workspacePasswordInput"
            :placeholder="t('placeholder.workspacePassword')"
            show-password
            style="max-width: 360px"
            @keyup.enter.prevent="submitWorkspacePassword"
          />
          <el-button type="primary" :loading="workspaceAuthLoading" @click="submitWorkspacePassword">
            {{ t('action.workspaceLogin') }}
          </el-button>
        </div>
        <p v-if="workspaceAuthError" class="muted">{{ workspaceAuthError }}</p>
      </section>
    </template>

    <template v-else>

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
          <label>{{ t('field.maxSegmentGroups') }}（{{ t('field.segmentGroupsRangeHelp') }}）</label>
          <el-input
            v-model="form.segment_groups_range"
            :placeholder="t('placeholder.segmentGroupsRange')"
            clearable
          />
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
            <el-option :label="t('option.subtitleYellowBlack')" value="yellow_black" />
            <el-option :label="t('option.subtitleBlackWhite')" value="black_white" />
            <el-option :label="t('option.subtitleWhiteBlack')" value="white_black" />
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
          <label>{{ t('field.novelAliasTitle') }}</label>
          <el-input ref="novelAliasInputRef" v-model="form.novel_alias" :placeholder="t('placeholder.novelAlias')" clearable />
          <p class="muted" style="margin: 6px 0 0">{{ t('hint.novelAliasHelp') }}</p>
        </div>

        <div>
          <label>{{ t('field.bgmVolumeLabel') }}</label>
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

      <div class="replace-toolbar">
        <div class="actions">
          <el-input v-model="customAliasInput" :placeholder="t('placeholder.customAlias')" clearable style="max-width: 260px" @keyup.enter="applyCustomAlias" />
          <el-button @click="applyCustomAlias">{{ t('action.addAlias') }}</el-button>
          <el-button :loading="loading.aliases" @click="generateNovelAliases">{{ t('action.generateAliases') }}</el-button>
          <el-button :disabled="loading.aliases" @click="generateNovelAliases">{{ t('action.regenerateAliases') }}</el-button>
          <span class="muted" v-if="novelAliases.length">{{ t('hint.aliasClickToApply') }}</span>
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
        <span>{{ t('field.bgmEnabledLabel') }}</span>
        <label class="upload-btn">
          <input type="file" accept=".mp3,audio/mpeg" @change="uploadBgmFile" />
          {{ t('action.uploadBgm') }}
        </label>
        <el-button @click="openBgmPicker">{{ t('action.selectBgmFromLibrary') }}</el-button>
        <el-button type="danger" plain @click="deleteCurrentBgm">{{ t('action.deleteCurrentBgm') }}</el-button>
      </div>
      <div class="switch-row">
        <el-switch v-model="form.watermark_enabled" />
        <span>{{ t('field.watermarkEnabledLabel') }}</span>
        <el-select v-model="form.watermark_type" style="width: 140px" :disabled="!form.watermark_enabled">
          <el-option :label="t('option.watermarkText')" value="text" />
          <el-option :label="t('option.watermarkImage')" value="image" />
        </el-select>
        <el-input
          v-model="form.watermark_text"
          :placeholder="t('placeholder.watermarkText')"
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
          {{ t('action.uploadWatermark') }}
        </label>
        <span>{{ t('field.watermarkOpacityLabel') }}</span>
        <el-input-number
          v-model="form.watermark_opacity"
          :min="0.05"
          :max="1"
          :step="0.01"
          :precision="2"
          :controls="false"
          :disabled="!form.watermark_enabled"
          style="width: 110px"
        />
      </div>
      <div class="muted bgm-status" v-if="form.watermark_image_path">
        <span>{{ t('hint.watermarkImageSelected', { filename: form.watermark_image_path.split('/').pop() }) }}</span>
      </div>
      <div class="muted bgm-status">
        <span v-if="bgmStatus.exists">
          {{ t('hint.currentBgm', { filename: bgmStatus.filename, size: formatFileSize(bgmStatus.size) }) }}
          <span v-if="bgmStatus.source_filename"> ｜ {{ t('hint.source', { source: bgmStatus.source_filename }) }}</span>
          <span v-if="bgmStatus.updated_at"> ｜ {{ t('hint.updatedAt', { time: bgmStatus.updated_at }) }}</span>
        </span>
        <span v-else>{{ t('hint.currentBgmFallback') }}</span>
      </div>
    </section>

    <section class="card">
      <h2>{{ t('section.text') }}</h2>
      <div class="replace-toolbar">
        <div class="switch-row">
          <el-switch v-model="nameReplace.enabled" />
          <span>{{ t('field.nameReplacementEnabled') }}</span>
        </div>
        <div class="actions">
          <el-button type="primary" @click="applyReplacementsToSourceText">{{ t('action.applyReplacement') }}</el-button>
          <el-button @click="detectNameReplacementCandidates">{{ t('action.detectReplacementCandidates') }}</el-button>
          <el-button @click="clearNameReplacementEntries">{{ t('action.clearReplacementDict') }}</el-button>
          <span class="muted" v-if="replacementEntries.length">
            {{ t('hint.replacementSummary', { total: replacementEntries.length, enabled: replacementEnabledCount }) }}
          </span>
        </div>
      </div>

      <div v-if="replacementEntries.length" class="replace-list">
        <div class="replace-item" v-for="entry in replacementEntries" :key="entry.word">
          <el-checkbox v-model="entry.enabled" :disabled="!entry.replacement.trim()" />
          <span class="word">{{ entry.word }}</span>
          <span class="muted">×{{ entry.count }}</span>
          <span class="arrow">→</span>
          <el-input v-model="entry.replacement" :placeholder="t('placeholder.replacementTarget')" clearable @input="syncReplacementEntry(entry)" />
        </div>
      </div>

      <el-input v-model="form.text" type="textarea" :rows="12" :placeholder="t('placeholder.textInput')" />
      <div class="actions">
        <el-button @click="removeChapterHeadings">{{ t('action.filterChapterHeadings') }}</el-button>
        <el-button :loading="loading.segment" @click="runSegmentPreview">{{ t('action.segmentPreview') }}</el-button>
        <el-button type="primary" :loading="loading.analyze" @click="runAnalyze">{{ t('action.analyze') }}</el-button>
      </div>

      <el-alert
        v-if="hasReplacementEffect"
        type="info"
        show-icon
        :closable="false"
        :title="t('hint.replacementAppliedInfo')"
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
          <span class="muted">{{ t('hint.segmentSentenceCount', { count: item.sentence_count || '?' }) }}</span>
          <div>{{ item.text }}</div>
        </li>
      </ol>
    </section>

    <section class="card">
      <h2>{{ t('section.characters') }}</h2>
      <p class="muted">{{ t('field.confidence') }}：{{ (confidence * 100).toFixed(0) }}%</p>

      <div class="character-card" v-for="(character, index) in characters" :key="getCharacterKey(character, index)">
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
            <label>{{ t('field.mainCharacter') }}</label>
            <el-checkbox
              :model-value="!!character.is_main_character"
              @change="(value) => setMainCharacter(index, value)"
            >
              {{ t('field.mainCharacter') }}
            </el-checkbox>
          </div>

          <div>
            <label>{{ t('field.storySelf') }}</label>
            <el-checkbox
              :model-value="!!character.is_story_self"
              @change="(value) => setStorySelf(index, value)"
            >
              {{ t('field.storySelf') }}
            </el-checkbox>
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
          <el-button :loading="isGeneratingRef(character, index)" @click="generateRefImage(character, index)">
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
      <h2>{{ t('section.jobRecovery') }}</h2>
      <div class="job-restore-row">
        <el-input
          v-model="recoverJobIdInput"
          :placeholder="t('placeholder.recoverJobId')"
          clearable
          style="max-width: 360px"
          @keyup.enter.prevent="recoverJobById"
        />
        <el-button native-type="button" @click="recoverJobById">{{ t('action.recoverJob') }}</el-button>
      </div>
    </section>

    <section class="card">
      <h2>{{ t('section.render') }}</h2>
      <div v-if="sortedJobs.length" class="job-list">
        <div
          v-for="item in sortedJobs"
          :key="item.id"
          class="job-list-item"
          :class="{ active: item.id === activeJobId }"
          @click="openJob(item.id)"
        >
          <div class="job-list-main">
            <div class="job-list-id">{{ item.id }}</div>
            <div class="job-list-meta">
              <span>{{ item.status }}</span>
              <span>·</span>
              <span>{{ Math.round((Number(item.progress || 0) * 100)) }}%</span>
              <span v-if="item.totalSegments">· Scene {{ item.currentSegment || 0 }}/{{ item.totalSegments }}</span>
              <span>· {{ t('hint.jobCreatedAt', { time: formatDateTime(item.createdAt) }) }}</span>
            </div>
          </div>
          <el-button size="small" type="danger" plain @click.stop="removeJob(item.id)">{{ t('action.remove') }}</el-button>
        </div>
      </div>

      <div class="actions">
        <el-button type="primary" :loading="loading.generate" @click="runGenerate">{{ t('action.generateVideo') }}</el-button>
        <el-button :loading="loading.generate" :disabled="!activeJobId" @click="remixCurrentVideoBgm">{{ t('action.remixBgmOnly') }}</el-button>
        <el-button :disabled="!activeJobId" type="danger" @click="cancelCurrentJob">{{ t('action.cancelJob') }}</el-button>
        <el-button :disabled="!activeJobId" @click="resumeCurrentJob">{{ t('action.resumeJob') }}</el-button>
      </div>

      <div v-if="job.id" class="job">
        <p><strong>{{ t('hint.jobId') }}：</strong>{{ job.id }}</p>
        <p><strong>{{ t('hint.jobStatus') }}：</strong>{{ job.status }} / {{ job.step }}</p>
        <p><strong>{{ t('hint.jobMessage') }}：</strong>{{ job.message }}</p>
        <p><strong>{{ t('hint.currentScene') }}：</strong>{{ sceneProgressText }}</p>
        <el-progress :percentage="sceneProgressPercent" :status="job.status === 'failed' ? 'exception' : (job.status === 'completed' ? 'success' : '')">
          <span>{{ sceneProgressPercent }}%</span>
        </el-progress>
        <p><strong>{{ t('hint.overallProgress') }}：</strong>{{ uiProgressPercent }}%</p>
        <el-progress :percentage="uiProgressPercent" :status="job.status === 'failed' ? 'exception' : (job.status === 'completed' ? 'success' : '')" />
      </div>

      <div v-if="job.clipPreviewUrls.length" class="clip-grid">
        <div v-for="(url, index) in job.clipPreviewUrls" :key="`${job.id}-${index}`" class="clip-item">
          <p>{{ t('hint.clip', { index: index + 1 }) }}</p>
          <div
            v-if="!isClipVideoEnabled(index)"
            class="clip-thumb-wrap"
            role="button"
            tabindex="0"
            @click="enableClipVideo(index)"
            @keydown.enter.prevent="enableClipVideo(index)"
          >
            <img :src="getClipThumbnailUrl(index)" class="video clip-thumb" loading="lazy" alt="clip thumbnail" />
            <div class="clip-thumb-play">▶</div>
          </div>
          <video
            v-else
            :src="getClipVideoUrl(index)"
            :poster="getClipThumbnailUrl(index)"
            controls
            preload="none"
            class="video"
          />
        </div>
      </div>

      <div v-if="job.videoUrl" class="preview">
        <h3>{{ t('hint.finalVideo') }}</h3>
        <p v-if="imageSourceSummary" class="muted">
          {{ t('hint.imageSourceReportSummary', {
            cache: imageSourceSummary.cache_images,
            generated: imageSourceSummary.generated_images,
            total: imageSourceSummary.total_images,
            cacheRatio: imageSourceSummary.cacheRatioText,
            generatedRatio: imageSourceSummary.generatedRatioText
          }) }}
        </p>
        <p v-if="imageSourceSummary && imageSourceSummary.other_images > 0" class="muted">
          {{ t('hint.imageSourceReportOther', {
            other: imageSourceSummary.other_images,
            total: imageSourceSummary.total_images,
            otherRatio: imageSourceSummary.otherRatioText
          }) }}
        </p>
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

    </template>
    </template>

    <template v-else>
      <section class="card">
        <h2>{{ t('section.finalVideos') }}</h2>
        <div class="actions">
          <el-button :loading="loading.finalVideos" @click="loadFinalVideos">{{ t('action.refreshFinalVideos') }}</el-button>
        </div>

        <div v-if="finalVideos.length" class="final-video-grid">
          <article v-for="(item, index) in finalVideos" :key="item.filename" class="final-video-item">
            <div
              v-if="!isFinalVideoEnabled(item, index)"
              class="clip-thumb-wrap"
              role="button"
              tabindex="0"
              @click="enableFinalVideo(item, index)"
              @keydown.enter.prevent="enableFinalVideo(item, index)"
            >
              <img :src="item.thumbnailUrl" class="final-video-thumb" loading="lazy" :alt="item.filename" />
              <div class="clip-thumb-play">▶</div>
            </div>
            <video
              v-else
              :src="item.videoUrl"
              :poster="item.thumbnailUrl"
              controls
              preload="none"
              class="video"
            />
            <div class="final-video-name">{{ item.filename }}</div>
            <div class="muted">{{ t('hint.finalVideoCreatedAt', { time: formatDateTime(item.createdAt) }) }}</div>
            <div class="muted">{{ t('hint.finalVideoSize', { size: formatFileSize(item.size) }) }}</div>
            <div class="actions">
              <el-button text @click="enableFinalVideo(item, index)">{{ t('action.openFinalVideo') }}</el-button>
              <a :href="item.downloadUrl" target="_blank" rel="noopener noreferrer" download>{{ t('action.downloadFinal') }}</a>
            </div>
          </article>
        </div>
        <el-empty v-else :description="t('hint.noFinalVideos')" />
      </section>
    </template>

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

    <el-dialog v-model="bgmPicker.visible" :title="t('section.bgmLibrary')" width="820px" :close-on-click-modal="false" @closed="closeBgmPicker">
      <div class="ref-library-modal" v-if="bgmLibrary.length">
        <div v-for="item in bgmLibrary" :key="item.path" class="ref-option" @click="pickBgm(item)">
          <div class="filename">{{ item.filename }}</div>
          <div class="muted">{{ formatFileSize(item.size) }}</div>
          <audio :src="item.url" controls preload="none" class="bgm-audio" @click.stop></audio>
        </div>
      </div>
      <el-empty v-else :description="t('hint.noBgm')" />
      <template #footer>
        <el-button @click="closeBgmPicker">{{ t('action.close') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

