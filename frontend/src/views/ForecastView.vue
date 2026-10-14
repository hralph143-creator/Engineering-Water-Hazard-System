<template>
  <div class="page-head forecast-head">
    <div><h2>趋势推演</h2><span class="muted">依据历史识别序列评估未来 6—24 小时积水变化方向</span></div>
    <n-space><n-button :loading="loading" @click="load">重新计算</n-button><n-button v-if="data?.source" @click="$router.push(`/results/${data.source.result_id}`)">查看源成果</n-button></n-space>
  </div>
  <n-alert type="warning" :bordered="false" class="forecast-boundary">趋势推演不是天气预报或水动力模拟，结果只用于安排复核顺序，不能替代现场巡查与停复工作业判断。</n-alert>
  <n-spin :show="loading">
    <template v-if="data?.ready&&data.source">
      <div class="forecast-summary">
        <div><span>变化方向</span><strong :class="directionClass">{{directionLabel}}</strong><small>基于 {{data.sample_count}} 次识别</small></div>
        <div><span>推演可信度</span><strong>{{confidenceLabel}}</strong><small>拟合度 {{formatScore(data.fit_r2)}}</small></div>
        <div><span>观测时间跨度</span><strong>{{data.time_span_hours}} 小时</strong><small>最近成果 {{formatDateTime(data.source.observed_at)}}</small></div>
        <div><span>近 72 小时降雨</span><strong>{{data.rainfall?.rainfall_72h_mm==null?'无记录':`${data.rainfall.rainfall_72h_mm} mm`}}</strong><small>仅作实测背景</small></div>
      </div>

      <div class="forecast-grid">
        <n-card class="forecast-visual-card">
          <template #header><div class="card-title-row"><div><b>积水动态示意</b><small>{{data.source.dataset_name}}</small></div><n-space><n-select v-model:value="speed" size="small" :options="speedOptions" style="width:104px"/><n-button size="small" @click="playing=!playing">{{playing?'暂停':'播放'}}</n-button></n-space></div></template>
          <div class="water-stage" :class="{paused:!playing}" :style="{'--flow-duration':`${speed}s`}">
            <img :src="data.source.image_url" alt="最新遥感识别底图">
            <div class="water-mask" :style="maskStyle"></div>
            <div class="flow-lines" aria-hidden="true"><i v-for="n in 8" :key="n" :style="{top:`${8+n*10}%`,animationDelay:`-${n*.42}s`}"></i></div>
            <div class="stage-badge"><span class="pulse"></span>积水区域流动示意</div>
          </div>
          <div class="visual-note"><span><i class="legend water"></i>模型识别水体</span><span><i class="legend flow"></i>动态纹理表示趋势方向，不代表实际流速</span></div>
        </n-card>

        <n-card title="未来时段变化区间" class="projection-card">
          <div class="projection-list">
            <div v-for="item in data.projections" :key="item.hours">
              <div><b>+{{item.hours}} 小时</b><span>{{formatDateTime(item.at)}}</span></div>
              <strong>{{formatPercent(item.flood_ratio)}}</strong>
              <small>可能区间 {{formatPercent(item.lower)}}—{{formatPercent(item.upper)}}</small>
              <div class="range-track"><i :style="{left:`${item.lower*100}%`,width:`${Math.max((item.upper-item.lower)*100,1)}%`}"></i><em :style="{left:`${item.flood_ratio*100}%`}"></em></div>
            </div>
          </div>
          <div class="forecast-method"><b>判读边界</b><p>{{data.disclaimer}}</p><p>{{data.rainfall?.note}}</p></div>
        </n-card>
      </div>

      <n-card title="历史依据与推演曲线" class="trend-card">
        <div class="trend-chart">
          <svg viewBox="0 0 900 220" preserveAspectRatio="none" role="img" aria-label="积水占比趋势曲线">
            <line v-for="y in [30,80,130,180]" :key="y" x1="45" :y1="y" x2="875" :y2="y" class="grid-line"/>
            <polyline v-if="historyPoints" :points="historyPoints" class="history-line"/>
            <polyline v-if="forecastPoints" :points="forecastPoints" class="forecast-line"/>
            <circle v-for="(point,index) in chartPoints" :key="index" :cx="point.x" :cy="point.y" r="5" :class="point.forecast?'forecast-dot':'history-dot'"/>
          </svg>
          <div class="chart-labels"><span>历史识别</span><span>当前</span><span>未来 24 小时</span></div>
        </div>
      </n-card>
    </template>
    <n-card v-else-if="data&&!loading" class="forecast-empty">
      <div class="empty-icon">↗</div><h3>暂时无法进行趋势推演</h3><p>{{data.message}}</p><small>请先为同一项目完成至少两次具有有效采集时间的积水识别。</small><br><n-button type="primary" style="margin-top:18px" @click="$router.push('/inference')">前往积水识别</n-button>
    </n-card>
  </n-spin>
</template>

<script setup lang="ts">
import{computed,onMounted,ref,watch}from'vue'
import{api}from'../api'
import type{ForecastData}from'../types'
import{currentProjectId}from'../composables/projectContext'
import{formatDateTime,formatPercent}from'../utils/format'

const data=ref<ForecastData|null>(null),loading=ref(false),playing=ref(true),speed=ref(4.8)
const speedOptions=[{label:'缓慢',value:7.5},{label:'正常',value:4.8},{label:'快速',value:2.8}]
const load=async()=>{loading.value=true;try{data.value=await api.forecast(currentProjectId.value)}finally{loading.value=false}}
const directionLabel=computed(()=>data.value?.direction==='RISING'?'积水范围可能扩大':data.value?.direction==='FALLING'?'积水范围可能回落':'积水范围基本稳定')
const directionClass=computed(()=>data.value?.direction==='RISING'?'rising':data.value?.direction==='FALLING'?'falling':'stable')
const confidenceLabel=computed(()=>data.value?.confidence==='HIGH'?'较高':data.value?.confidence==='MEDIUM'?'中等':'较低')
const formatScore=(value?:number)=>value==null?'--':`${Math.round(value*100)}%`
const maskStyle=computed(()=>data.value?.source?{'-webkit-mask-image':`url(${data.value.source.mask_url})`,'mask-image':`url(${data.value.source.mask_url})`}:{})
const chartPoints=computed(()=>{if(!data.value)return[];const values=[...data.value.series.map(x=>({value:x.flood_ratio,forecast:false})),...data.value.projections.map(x=>({value:x.flood_ratio,forecast:true}))];if(!values.length)return[];const max=Math.max(.05,...values.map(x=>x.value))*1.15;return values.map((item,index)=>({x:45+index*(830/Math.max(values.length-1,1)),y:190-item.value/max*150,...item}))})
const historyPoints=computed(()=>chartPoints.value.filter(x=>!x.forecast).map(x=>`${x.x},${x.y}`).join(' '))
const forecastPoints=computed(()=>{const points=chartPoints.value;const firstForecast=points.findIndex(x=>x.forecast);return firstForecast<0?'':points.slice(Math.max(0,firstForecast-1)).map(x=>`${x.x},${x.y}`).join(' ')})
onMounted(load)
watch(currentProjectId,load)
</script>
