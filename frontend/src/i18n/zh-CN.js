const zhCN = {
  app: {
    title: '小说转视频工作台',
    subtitle: '先做分段和角色，再生成参考图、分镜短片与最终视频。'
  },
  section: {
    config: '基础配置',
    text: '文本与预处理',
    segmentPreview: '分段预览',
    characters: '角色配置（语音 + 参考图）',
    render: '视频生成',
    logs: '后端日志'
  },
  field: {
    model: '模型',
    analysisDepth: '分析深度',
    segmentMethod: '分段方式',
    sentencesPerSegment: '每段句数',
    maxSegmentGroups: '最大处理段数',
    maxSegmentHelp: '0 表示处理全部段',
    resolution: '分辨率',
    imageAspectRatio: '图片比例（Poe）',
    subtitleStyle: '字幕样式',
    cameraMotion: '镜头运动',
    renderMode: '渲染速度模式',
    fps: 'FPS',
    bgmEnabled: '启用背景音乐（BGM）',
    bgmVolume: '背景音乐音量',
    sceneReuse: '启用图片复用缓存',
    sceneReuseNoRepeatWindow: '复用去重窗口（前N段）',
    textInput: '小说文本',
    confidence: '识别置信度',
    roleName: '角色名称',
    roleType: '角色定位',
    voice: 'TTS 音色',
    referenceImage: '参考图',
    appearance: '外观描述',
    personality: '性格描述',
    basePrompt: '角色基础 Prompt'
  },
  option: {
    basic: '基础',
    detailed: '详细',
    sentence: '按句分段',
    smart: '智能分段',
    fixed: '固定字数',
    resolution1080x1920: '1080x1920（竖屏）',
    resolution720x1280: '720x1280（竖屏）',
    resolution1920x1080: '1920x1080（横屏）',
    aspectUnspecified: '不指定',
    subtitleBasic: '基础',
    subtitleHighlight: '高亮',
    subtitleDanmaku: '弹幕',
    subtitleCenter: '居中',
    cameraMotionVertical: '上→下（推荐）',
    cameraMotionHorizontal: '左→右',
    cameraMotionAuto: '自动',
    renderFast: '极速（推荐）',
    renderBalanced: '均衡',
    renderQuality: '高质量（较慢）',
    unavailable: '不可用'
  },
  action: {
    segmentPreview: '预览分段',
    analyze: '分析角色',
    confirmCharacters: '确认角色配置',
    pickReference: '选择参考图',
    clearReference: '清除参考图',
    uploadReference: '上传参考图',
    generateReference: '生成角色参考图',
    generateVideo: '开始生成视频',
    cancelJob: '取消任务',
    refreshLogs: '刷新日志',
    close: '关闭',
    downloadFinal: '下载最终视频'
  },
  placeholder: {
    modelSearch: '选择模型（支持输入搜索）',
    modelNoMatch: '无匹配模型',
    textInput: '输入小说文本'
  },
  hint: {
    segmentSummary: '总句数 {sentences}，分段数 {segments}，预计处理 {effective} 段',
    sentenceRule: '当前按每 {count} 句一段（句号/问号/感叹号/分号/逗号均可切分）',
    selectedRef: '已选择：{filename}',
    noRefImage: '暂无参考图，请先上传或生成。',
    clip: '分镜短片 #{index}',
    finalVideo: '最终视频',
    jobId: '任务 ID',
    jobStatus: '状态',
    jobMessage: '消息'
  },
  toast: {
    textRequired: '请先输入文本',
    characterRequired: '请先分析并确认角色配置',
    modelsLoadFailed: '模型加载失败：{error}',
    voicesLoadFailed: '音色列表加载失败：{error}',
    refsLoadFailed: '参考图库加载失败：{error}',
    logsLoadFailed: '读取日志失败：{error}',
    analyzeSuccess: '角色分析完成',
    analyzeFailed: '角色分析失败：{error}',
    segmentSuccess: '分段完成：{segments} 段（总句数：{sentences}）',
    segmentFailed: '分段失败：{error}',
    confirmSuccess: '角色配置已确认',
    confirmFailed: '确认失败：{error}',
    statusFailed: '获取任务状态失败：{error}',
    completed: '视频生成完成',
    queued: '任务已创建，开始生成',
    generateFailed: '创建任务失败：{error}',
    cancelSuccess: '已请求取消任务',
    cancelFailed: '取消任务失败：{error}',
    uploadSuccess: '参考图上传成功',
    uploadFailed: '上传参考图失败：{error}',
    refGenerated: '角色参考图生成成功',
    refGenerateFailed: '生成参考图失败：{error}'
  }
}

Object.assign(zhCN.hint, {
  sceneNotStarted: '未开始',
  sceneProgress: 'Scene {current}/{total}',
  novelAliasHelp: '书名将作为最终合成顶栏叠加，不再写入正文',
  aliasClickToApply: '点击下方任意别名即可应用',
  watermarkImageSelected: '水印图片：{filename}',
  currentBgm: '当前BGM：{filename} ｜ {size}',
  source: '来源：{source}',
  updatedAt: '更新时间：{time}',
  currentBgmFallback: '当前BGM：未找到（默认回退 assets/bgm/happinessinmusic-rock-trailer-417598.mp3）',
  replacementSummary: '共 {total} 个候选，已启用 {enabled} 个',
  replacementAppliedInfo: '名字替换已生效：分段/分析/生成都将使用替换后的文本',
  segmentSentenceCount: '（{count} 句）',
  currentScene: '当前场景',
  overallProgress: '总体进度',
  noBgm: '暂无BGM，请先上传'
})

Object.assign(zhCN.toast, {
  noReplacementCandidates: '未检测到可替换的高频词（至少出现 2 次）',
  replacementCandidatesDetected: '已检测到 {count} 个候选词',
  chapterHeadingNotFound: '未检测到可过滤的章节前缀',
  chapterHeadingProcessed: '已处理 {count} 处章节前缀',
  noReplacementToApply: '没有可应用的替换项',
  replacementApplied: '已将字典替换应用到上方文本',
  jobQueuedMessage: '任务已排队',
  resumeRequested: '已请求继续生成',
  resumeFailed: '继续生成失败：{error}',
  aliasEmpty: '未生成可用别名，请重试',
  aliasGenerated: '已生成 {count} 个别名',
  aliasGenerateFailed: '别名生成失败：{error}',
  aliasApplied: '已应用别名：{alias}',
  aliasInputRequired: '请先输入别名',
  bgmUploadSuccess: 'BGM 上传成功',
  bgmUploadFailed: 'BGM 上传失败：{error}',
  watermarkUploadSuccess: '水印图片上传成功',
  watermarkUploadFailed: '水印图片上传失败：{error}',
  bgmSwitchSuccess: '已切换当前BGM',
  bgmSwitchFailed: '切换BGM失败：{error}',
  bgmDeleteSuccess: '已删除当前BGM',
  bgmDeleteFailed: '删除当前BGM失败：{error}',
  remixNeedVideo: '请先生成至少一次视频',
  remixSuccess: '已完成仅替换BGM（无需重跑全流程）',
  remixFailed: '仅替换BGM失败：{error}',
  jobIdRequired: '请先输入任务ID',
  recoverSuccess: '已恢复任务：{id}',
  recoverFailed: '恢复任务失败：{error}'
})

Object.assign(zhCN.dialog || (zhCN.dialog = {}), {
  missingAliasMessage: '还没有设置顶部书名，是否先去添加？',
  tipTitle: '提示',
  goSetup: '去设置',
  continueGenerate: '继续生成'
})

Object.assign(zhCN.option, {
  subtitleYellowBlack: '黄字黑边',
  subtitleBlackWhite: '黑字白边',
  subtitleWhiteBlack: '白字黑边',
  watermarkText: '文字',
  watermarkImage: '图片'
})

Object.assign(zhCN.field, {
  novelAliasTitle: '顶部书名（最终叠加）',
  bgmVolumeLabel: '背景音乐音量',
  bgmEnabledLabel: '启用背景音乐（BGM）',
  watermarkEnabledLabel: '启用水印',
  watermarkOpacityLabel: '水印透明度',
  nameReplacementEnabled: '启用名字替换字典'
})

Object.assign(zhCN.placeholder, {
  novelAlias: '视频顶部书名（可留空）',
  customAlias: '手动输入别名',
  watermarkText: '水印文字',
  replacementTarget: '替换成...',
  recoverJobId: '输入任务ID后恢复进度'
})

Object.assign(zhCN.action, {
  addAlias: '添加',
  generateAliases: '生成小说别名（10个）',
  regenerateAliases: '重新生成',
  uploadBgm: '上传BGM',
  selectBgmFromLibrary: '从BGM库选择',
  deleteCurrentBgm: '删除当前BGM',
  uploadWatermark: '上传水印图',
  applyReplacement: '执行替换',
  detectReplacementCandidates: '检测高频词',
  clearReplacementDict: '清空字典',
  filterChapterHeadings: '过滤章节标题',
  recoverJob: '恢复任务',
  remove: '移除',
  remixBgmOnly: '仅替换BGM（最后一步）',
  resumeJob: '继续生成'
})

Object.assign(zhCN.section, {
  jobRecovery: '任务恢复',
  bgmLibrary: 'BGM音乐库'
})

Object.assign(zhCN.field, {
  segmentGroupsRangeHelp: '1开始，可写 1-80,81-90；单写 60=1-60；0/-1=全部'
})

Object.assign(zhCN.placeholder, {
  segmentGroupsRange: '例如：1-80,81-90 / 60 / 0（全部）'
})

Object.assign(zhCN.toast, {
  segmentRangeInvalid: '段数范围格式不正确，请使用 1-80,81-90 / 60（1-60）/ 0 或 -1（全部）'
})

Object.assign(zhCN.dialog || (zhCN.dialog = {}), {
  tooManySegmentsMessage: '本次将处理 {count} 段，超过建议上限 {limit} 段。建议先减少段数，是否继续生成？',
  tooManySegmentsContinue: '继续生成',
  tooManySegmentsReduce: '先去减少段数'
})

Object.assign(zhCN.hint, {
  imageSourceReportSummary: '生图来源统计：缓存 {cache}/{total}（{cacheRatio}）｜新生成 {generated}/{total}（{generatedRatio}）',
  imageSourceReportOther: '其他来源 {other}/{total}（{otherRatio}）'
})

Object.assign(zhCN.page || (zhCN.page = {}), {
  workspace: '生成工作台',
  finalVideos: '成片库'
})

Object.assign(zhCN.section, {
  finalVideos: '最终视频列表'
})

Object.assign(zhCN.action, {
  refreshFinalVideos: '刷新成片列表',
  openFinalVideo: '查看视频'
})

Object.assign(zhCN.hint, {
  noFinalVideos: '暂无最终视频',
  finalVideoCreatedAt: '创建时间：{time}',
  finalVideoSize: '文件大小：{size}'
})

Object.assign(zhCN.toast, {
  finalVideosLoadFailed: '加载最终视频列表失败：{error}'
})

Object.assign(zhCN.section, {
  workspaceAuth: '工作台访问验证'
})

Object.assign(zhCN.action, {
  workspaceLogin: '进入工作台'
})

Object.assign(zhCN.action, {
  workspaceLogout: '退出工作台'
})

Object.assign(zhCN.placeholder, {
  workspacePassword: '请输入工作台密码'
})

Object.assign(zhCN.hint, {
  workspaceLocked: '已启用工作台密码保护。请输入密码后再加载工作台数据。'
})

Object.assign(zhCN.toast, {
  workspacePasswordRequired: '请输入工作台密码',
  workspacePasswordInvalid: '缓存密码无效，请重新输入',
  workspaceLoginFailed: '工作台登录失败：{error}',
  workspaceAuthStatusFailed: '获取工作台鉴权状态失败：{error}',
  workspaceSessionExpired: '工作台登录已失效，请重新输入密码',
  workspaceLoggedOut: '已退出工作台'
})

Object.assign(zhCN.hint, {
  jobCreatedAt: '创建时间：{time}'
})

Object.assign(zhCN.field, {
  mainCharacter: '主角',
  storySelf: '第一人称'
})

export default zhCN
