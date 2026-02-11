<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { api } from './api'
import { t } from './i18n'

const models = ref([])
const voices = ref([])
const refImages = ref([])
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
  sentences_per_segment: 5,
  max_segment_groups: 0,
  resolution: '1080x1920',
  subtitle_style: 'highlight',
  fps: 30,
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

const generatingRef = reactive({})

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
  if (!form.text.trim()) {
    ElMessage.warning(t('toast.textRequired'))
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
    ElMessage.success(t('toast.analyzeSuccess'))
  } catch (error) {
    ElMessage.error(t('toast.analyzeFailed', { error: error.message }))
  } finally {
    loading.analyze = false
  }
}

async function runSegmentPreview() {
  if (!form.text.trim()) {
    ElMessage.warning(t('toast.textRequired'))
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
  if (!form.text.trim()) {
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
      text: form.text,
      characters: characters.value,
      segment_method: form.segment_method,
      sentences_per_segment: form.sentences_per_segment,
      max_segment_groups: form.max_segment_groups,
      resolution: form.resolution,
      subtitle_style: form.subtitle_style,
      fps: form.fps,
      model_id: selectedModel.value || null,
      enable_scene_image_reuse: form.enable_scene_image_reuse
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

onMounted(async () => {
  await Promise.all([loadModels(), loadVoices(), loadRefImages()])
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
          <label>{{ t('field.subtitleStyle') }}</label>
          <el-select v-model="form.subtitle_style" style="width: 100%">
            <el-option :label="t('option.subtitleBasic')" value="basic" />
            <el-option :label="t('option.subtitleHighlight')" value="highlight" />
            <el-option :label="t('option.subtitleDanmaku')" value="danmaku" />
            <el-option :label="t('option.subtitleCenter')" value="center" />
          </el-select>
        </div>

        <div>
          <label>{{ t('field.fps') }}</label>
          <el-input-number v-model="form.fps" :min="15" :max="60" />
        </div>
      </div>

      <div class="switch-row">
        <el-switch v-model="form.enable_scene_image_reuse" />
        <span>{{ t('field.sceneReuse') }}</span>
      </div>
    </section>

    <section class="card">
      <h2>{{ t('section.text') }}</h2>
      <el-input v-model="form.text" type="textarea" :rows="12" :placeholder="t('placeholder.textInput')" />
      <div class="actions">
        <el-button :loading="loading.segment" @click="runSegmentPreview">{{ t('action.segmentPreview') }}</el-button>
        <el-button type="primary" :loading="loading.analyze" @click="runAnalyze">{{ t('action.analyze') }}</el-button>
      </div>

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
  </div>
</template>

