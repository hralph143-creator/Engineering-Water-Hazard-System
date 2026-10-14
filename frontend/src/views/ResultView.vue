<template>
  <div class="page-head">
    <div><h2>识别成果</h2><span class="muted">任务 {{task?.id}}</span></div>
    <n-space v-if="r">
      <n-button @click="$router.push({path:'/ai',query:{task:task?.id}})">分析说明</n-button>
      <n-button type="primary" tag="a" :href="api.reportUrl(r.id)" target="_blank">查看报告</n-button>
      <n-dropdown :options="exportOptions" @select="exportResult"><n-button>导出成果 <span class="dropdown-caret">⌄</span></n-button></n-dropdown>
    </n-space>
  </div>
  <n-spin :show="loading"><template v-if="task&&r">
    <n-alert v-if="r.file_status.status!=='complete'" :type="r.file_status.status==='missing'?'error':'warning'" style="margin-bottom:16px">结果文件缺失：{{r.file_status.missing_files.join('、')}}，请重新识别或检查输出目录。</n-alert>
    <n-alert type="info" :bordered="false" style="margin-bottom:16px">识别结果反映遥感影像中模型判定的积水/水体空间范围，不代表真实水深，也不直接等同于现场安全风险等级。</n-alert>
    <div class="stat-grid result-metrics"><metric-card tone="blue" label="积水像素数" :value="r.flood_pixel_count"/><metric-card tone="green" label="有效像素数" :value="r.valid_pixel_count"/><metric-card tone="purple" label="积水占比" :value="formatPercent(r.flood_ratio)"/><metric-card tone="red" label="基础水害提示" :value="r.risk_level" :risk="r.risk_level"/></div>
    <n-card class="result-canvas" title="遥感识别图层分析"><n-tabs type="line" animated>
      <n-tab-pane name="vv" tab="VV"><div class="preview-box result-layer"><img class="preview" :src="r.files.vv_preview"/></div></n-tab-pane><n-tab-pane name="vh" tab="VH"><div class="preview-box result-layer"><img class="preview" :src="r.files.vh_preview"/></div></n-tab-pane><n-tab-pane name="prob" tab="积水概率图"><div class="preview-box result-layer"><img class="preview" :src="r.files.probability_preview"/></div></n-tab-pane><n-tab-pane name="mask" tab="积水分布图"><div class="preview-box result-layer"><img class="preview" :src="r.files.mask_preview"/></div></n-tab-pane><n-tab-pane name="flow" tab="水体动态示意"><div class="flow-tab-tools"><span>动态纹理仅辅助观察识别区域，不表示实际流向或流速。</span><n-button size="small" @click="flowPlaying=!flowPlaying">{{flowPlaying?'暂停':'播放'}}</n-button></div><div class="water-stage result-flow-stage" :class="{paused:!flowPlaying}"><img :src="r.files.vv_preview" alt="遥感识别底图"><div class="water-mask" :style="{'-webkit-mask-image':`url(${r.files.mask_preview})`,'mask-image':`url(${r.files.mask_preview})`}"></div><div class="flow-lines" aria-hidden="true"><i v-for="n in 8" :key="n" :style="{top:`${8+n*10}%`,animationDelay:`-${n*.42}s`}"></i></div><div class="stage-badge"><span class="pulse"></span>积水区域流动示意</div></div></n-tab-pane><n-tab-pane name="comparison" tab="结果对比图"><div class="preview-box result-layer"><img class="preview" :src="r.files.comparison"/></div></n-tab-pane>
    </n-tabs></n-card>
    <n-card title="像素与概率统计" class="result-record"><n-descriptions bordered :column="3" size="small">
      <n-descriptions-item label="有效像素数">{{r.valid_pixel_count}}</n-descriptions-item><n-descriptions-item label="积水像素数">{{r.flood_pixel_count}}</n-descriptions-item><n-descriptions-item label="高置信积水像素数">{{r.high_confidence_flood_pixel_count??'--'}}</n-descriptions-item><n-descriptions-item label="最小概率">{{r.probability_min!=null?r.probability_min.toFixed(4):'--'}}</n-descriptions-item><n-descriptions-item label="最大概率">{{r.probability_max!=null?r.probability_max.toFixed(4):'--'}}</n-descriptions-item><n-descriptions-item label="平均概率">{{r.probability_mean!=null?r.probability_mean.toFixed(4):'--'}}</n-descriptions-item><n-descriptions-item label="中位概率">{{r.probability_median!=null?r.probability_median.toFixed(4):'--'}}</n-descriptions-item><n-descriptions-item label="高置信积水比例">{{r.high_confidence_flood_ratio!=null?formatPercent(r.high_confidence_flood_ratio):'--'}}</n-descriptions-item><n-descriptions-item label="高置信阈值">{{(Math.max(task.threshold,0.8)).toFixed(2)}}</n-descriptions-item>
    </n-descriptions></n-card>
    <n-card title="识别任务记录" class="result-record"><n-descriptions bordered :column="3"><n-descriptions-item label="数据名称">{{task.dataset_name}}</n-descriptions-item><n-descriptions-item label="模型">{{task.model_name}}</n-descriptions-item><n-descriptions-item label="阈值">{{task.threshold}}</n-descriptions-item><n-descriptions-item label="推理耗时">{{formatSeconds(r.inference_seconds)}}</n-descriptions-item><n-descriptions-item label="平均积水置信度">{{formatPercent(r.mean_confidence)}}</n-descriptions-item><n-descriptions-item label="完成时间">{{formatDateTime(r.created_at)}}</n-descriptions-item></n-descriptions></n-card>
  </template></n-spin>
</template>
<script setup lang="ts">
import{computed,onMounted,ref}from'vue'
import{useRoute}from'vue-router'
import{api}from'../api'
import type{Task}from'../types'
import MetricCard from'../components/MetricCard.vue'
import{formatDateTime,formatPercent,formatSeconds}from'../utils/format'
const route=useRoute(),task=ref<Task|null>(null),loading=ref(true),flowPlaying=ref(true),r=computed(()=>task.value?.result)
const exportOptions=[{label:'积水概率 GeoTIFF',key:'probability'},{label:'积水分布 GeoTIFF',key:'mask'},{label:'结果对比 PNG',key:'comparison'},{label:'统计数据 CSV',key:'csv'},{label:'PDF 分析报告',key:'pdf'}]
const exportResult=(key:string)=>{if(!r.value)return;const files:any=r.value.files;const url=key==='pdf'?api.reportPdfUrl(r.value.id):files[key];if(url)window.location.href=url}
onMounted(async()=>{try{task.value=await api.result(route.params.id as string)}finally{loading.value=false}})
</script>
