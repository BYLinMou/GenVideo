<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { api } from './api'

const steps = ['選擇模型', '輸入文本', '角色分析', '角色確認', '生成視頻']
const activeStep = ref(0)

const loading = reactive({
  models: false,
  analyze: false,
  segment: false,
  generate: false
})

const models = ref([])
const selectedModel = ref('')

const form = reactive({
  text: '',
  analysis_depth: 'detailed',
  segment_method: 'smart',
  resolution: '1080x1920',
  subtitle_style: 'highlight',
  fps: 30
})

const characters = ref([])
const confidence = ref(0)
const segments = ref([])

const job = reactive({
  id: '',
  status: '',
  progress: 0,
  step: '',
  message: '',
  videoUrl: ''
})

let pollingTimer = null

const canAnalyze = computed(() => Boolean(selectedModel.value && form.text.trim()))
const canGenerate = computed(() => characters.value.length > 0 && form.text.trim())

function nextStep() {
  activeStep.value = Math.min(activeStep.value + 1, steps.length - 1)
}

function prevStep() {
  activeStep.value = Math.max(activeStep.value - 1, 0)
}

async function loadModels() {
  loading.models = true
  try {
    const data = await api.getModels()
    models.value = data.models || []
    const available = models.value.find((item) => item.available)
    selectedModel.value = available?.id || models.value[0]?.id || ''
  } catch (error) {
    ElMessage.error(`獲取模型失敗：${error.message}`)
  } finally {
    loading.models = false
  }
}

async function runAnalyze() {
  if (!canAnalyze.value) {
    ElMessage.warning('請先選擇模型並輸入文本')
    return
  }
  loading.analyze = true
  try {
    const data = await api.analyzeCharacters({
      text: form.text,
      analysis_depth: form.analysis_depth,
      model_id: selectedModel.value
    })
    characters.value = data.characters || []
    confidence.value = Number(data.confidence || 0)
    activeStep.value = 2
    ElMessage.success('角色分析完成')
  } catch (error) {
    ElMessage.error(`分析失敗：${error.message}`)
  } finally {
    loading.analyze = false
  }
}

async function runSegmentPreview() {
  if (!form.text.trim()) {
    ElMessage.warning('請先輸入文本')
    return
  }
  loading.segment = true
  try {
    const data = await api.segmentText({
      text: form.text,
      method: form.segment_method,
      model_id: selectedModel.value
    })
    segments.value = data.segments || []
    ElMessage.success(`分段完成，共 ${segments.value.length} 段`)
  } catch (error) {
    ElMessage.error(`分段失敗：${error.message}`)
  } finally {
    loading.segment = false
  }
}

async function confirmCharacters() {
  if (!characters.value.length) {
    ElMessage.warning('沒有可確認的角色')
    return
  }
  try {
    await api.confirmCharacters({ characters: characters.value })
    activeStep.value = 4
    ElMessage.success('角色配置已確認')
  } catch (error) {
    ElMessage.error(`確認失敗：${error.message}`)
  }
}

function startPolling() {
  stopPolling()
  pollingTimer = setInterval(async () => {
    if (!job.id) {
      return
    }
    try {
      const data = await api.getJob(job.id)
      job.status = data.status
      job.progress = Number(data.progress || 0)
      job.step = data.step || ''
      job.message = data.message || ''
      if (data.status === 'completed') {
        job.videoUrl = api.getVideoUrl(job.id)
        stopPolling()
        loading.generate = false
        ElMessage.success('視頻生成完成')
      }
      if (data.status === 'failed') {
        stopPolling()
        loading.generate = false
        ElMessage.error(data.message || '視頻生成失敗')
      }
    } catch (error) {
      stopPolling()
      loading.generate = false
      ElMessage.error(`查詢任務狀態失敗：${error.message}`)
    }
  }, 2000)
}

function stopPolling() {
  if (pollingTimer) {
    clearInterval(pollingTimer)
    pollingTimer = null
  }
}

async function runGenerate() {
  if (!canGenerate.value) {
    ElMessage.warning('請先完成角色分析')
    return
  }
  loading.generate = true
  job.id = ''
  job.status = 'queued'
  job.progress = 0
  job.step = ''
  job.message = ''
  job.videoUrl = ''

  try {
    const data = await api.generateVideo({
      text: form.text,
      characters: characters.value,
      segment_method: form.segment_method,
      resolution: form.resolution,
      subtitle_style: form.subtitle_style,
      fps: form.fps,
      model_id: selectedModel.value
    })
    job.id = data.job_id
    job.status = data.status
    startPolling()
    ElMessage.success('任務已提交，開始生成')
  } catch (error) {
    loading.generate = false
    ElMessage.error(`提交任務失敗：${error.message}`)
  }
}

onMounted(() => {
  loadModels()
})
</script>

<template>
  <div class="page">
    <header class="header">
      <h1>小說推文視頻生成系統</h1>
      <p>模型選擇 → 角色分析 → 配置確認 → 自動生成視頻</p>
    </header>

    <el-steps :active="activeStep" finish-status="success" align-center>
      <el-step v-for="step in steps" :key="step" :title="step" />
    </el-steps>

    <section class="card">
      <h2>1) 模型選擇</h2>
      <el-skeleton :loading="loading.models" animated>
        <template #default>
          <el-select v-model="selectedModel" placeholder="請選擇模型" style="width: 100%">
            <el-option
              v-for="item in models"
              :key="item.id"
              :label="`${item.name} (${item.provider}) ${item.available ? '' : '[不可用]'}`"
              :value="item.id"
              :disabled="!item.available"
            />
          </el-select>
        </template>
      </el-skeleton>
    </section>

    <section class="card">
      <h2>2) 小說文本與視頻參數</h2>
      <el-form label-position="top">
        <el-form-item label="小說文本">
          <el-input
            v-model="form.text"
            type="textarea"
            :rows="10"
            placeholder="貼上小說內容..."
          />
        </el-form-item>
        <div class="grid">
          <el-form-item label="分析深度">
            <el-select v-model="form.analysis_depth">
              <el-option label="Basic" value="basic" />
              <el-option label="Detailed" value="detailed" />
            </el-select>
          </el-form-item>
          <el-form-item label="分段方式">
            <el-select v-model="form.segment_method">
              <el-option label="智能分段" value="smart" />
              <el-option label="按句分段" value="sentence" />
              <el-option label="固定字數" value="fixed" />
            </el-select>
          </el-form-item>
          <el-form-item label="分辨率">
            <el-select v-model="form.resolution">
              <el-option label="1080x1920 (9:16)" value="1080x1920" />
              <el-option label="720x1280 (9:16)" value="720x1280" />
              <el-option label="1920x1080 (16:9)" value="1920x1080" />
            </el-select>
          </el-form-item>
          <el-form-item label="字幕樣式">
            <el-select v-model="form.subtitle_style">
              <el-option label="基礎字幕" value="basic" />
              <el-option label="逐字高亮" value="highlight" />
              <el-option label="彈幕樣式" value="danmaku" />
              <el-option label="居中大字" value="center" />
            </el-select>
          </el-form-item>
          <el-form-item label="FPS">
            <el-input-number v-model="form.fps" :min="15" :max="60" />
          </el-form-item>
        </div>
        <div class="actions">
          <el-button :loading="loading.segment" @click="runSegmentPreview">預覽分段</el-button>
          <el-button type="primary" :loading="loading.analyze" :disabled="!canAnalyze" @click="runAnalyze">
            AI 分析角色
          </el-button>
        </div>
      </el-form>
      <el-alert
        title="場景時長由音頻自動決定，無需手動設定"
        type="success"
        show-icon
        :closable="false"
      />
    </section>

    <section v-if="segments.length" class="card">
      <h2>分段預覽（{{ segments.length }} 段）</h2>
      <ol class="segments">
        <li v-for="item in segments" :key="item.index">{{ item.text }}</li>
      </ol>
    </section>

    <section class="card">
      <h2>3) 角色配置確認</h2>
      <p class="muted">分析信心：{{ (confidence * 100).toFixed(0) }}%</p>
      <el-table :data="characters" empty-text="尚未分析角色" style="width: 100%">
        <el-table-column prop="name" label="角色名" width="130">
          <template #default="scope">
            <el-input v-model="scope.row.name" />
          </template>
        </el-table-column>
        <el-table-column prop="role" label="角色定位" width="120">
          <template #default="scope">
            <el-input v-model="scope.row.role" />
          </template>
        </el-table-column>
        <el-table-column prop="appearance" label="外貌描述">
          <template #default="scope">
            <el-input v-model="scope.row.appearance" type="textarea" :rows="2" />
          </template>
        </el-table-column>
        <el-table-column prop="personality" label="性格特點">
          <template #default="scope">
            <el-input v-model="scope.row.personality" type="textarea" :rows="2" />
          </template>
        </el-table-column>
        <el-table-column prop="suggested_voice" label="音色" width="190">
          <template #default="scope">
            <el-input v-model="scope.row.suggested_voice" />
          </template>
        </el-table-column>
        <el-table-column prop="suggested_style" label="圖片風格" width="220">
          <template #default="scope">
            <el-input v-model="scope.row.suggested_style" />
          </template>
        </el-table-column>
      </el-table>

      <div class="actions">
        <el-button @click="prevStep">上一步</el-button>
        <el-button type="primary" :disabled="!characters.length" @click="confirmCharacters">確認角色配置</el-button>
      </div>
    </section>

    <section class="card">
      <h2>4) 生成與下載</h2>
      <div class="actions">
        <el-button type="primary" :loading="loading.generate" :disabled="!canGenerate" @click="runGenerate">
          開始生成視頻
        </el-button>
      </div>

      <div v-if="job.id" class="job">
        <p><strong>任務ID：</strong>{{ job.id }}</p>
        <p><strong>狀態：</strong>{{ job.status }} / {{ job.step }}</p>
        <p><strong>訊息：</strong>{{ job.message }}</p>
        <el-progress :percentage="Math.round(job.progress * 100)" />
      </div>

      <div v-if="job.videoUrl" class="preview">
        <video :src="job.videoUrl" controls preload="metadata" class="video" />
        <a :href="job.videoUrl" target="_blank" rel="noopener noreferrer">下載視頻</a>
      </div>
    </section>
  </div>
</template>

