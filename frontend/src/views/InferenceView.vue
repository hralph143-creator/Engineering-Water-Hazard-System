<template>
  <div class="page-head">
    <div><h2>积水识别</h2><span class="muted">利用 Sentinel-1 SAR 与 U-Net 识别施工区域内的积水及异常水体空间范围</span></div>
    <n-tag :type="model?.loaded?'success':'error'" :title="model?.error||modelError">{{model?.loaded?'模型已加载':model||modelError?'模型未加载':'正在检查模型'}}</n-tag>
  </div>
  <n-alert type="info" :bordered="false" style="margin-bottom:16px">识别结果可用于采坑、施工道路、低洼作业面和大型工程施工区域的积水范围辅助判读；模型并非针对特定矿区重新训练，结果需结合现场复核。</n-alert>
  <div class="inference-cockpit">
    <n-card title="识别任务配置" class="control-panel">
      <n-form label-placement="top">
        <n-form-item label="环境数据"><n-select v-model:value="datasetIds" :options="options" multiple max-tag-count="responsive" :disabled="running" placeholder="选择已校验的 Sentinel-1 影像"/></n-form-item>
        <n-form-item label="积水判定阈值"><n-slider v-model:value="threshold" :min="0" :max="1" :step="0.01" :disabled="running"/><n-input-number v-model:value="threshold" :min="0" :max="1" :step="0.01" :disabled="running" style="width:100%;margin-top:8px"/></n-form-item>
        <n-button type="primary" block :loading="running" :disabled="!datasetIds.length||running||model?.loaded===false||Boolean(modelError)" @click="run">{{running?'正在识别':'开始识别'}}</n-button>
      </n-form>
      <n-alert v-if="model?.loaded===false||modelError" type="error" style="margin-top:12px">{{model?.error||modelError||'模型未加载，请检查系统设置后重启服务。'}}</n-alert>
      <n-divider/>
      <div v-if="chosen" class="dataset-meta"><div><span>已选影像</span><b>{{datasetIds.length}} 景</b></div><div><span>当前影像</span><b>{{chosen.filename}}</b></div><div><span>影像尺寸</span><b>{{chosen.width}} × {{chosen.height}}</b></div><div><span>波段 / CRS</span><b>{{chosen.bands}} Bands · {{chosen.crs}}</b></div></div>
      <n-empty v-else description="尚未选择环境数据" size="small"/>
    </n-card>
    <n-card title="分析工作区" class="visual-panel">
      <div v-if="!task" class="preview-box hero-preview"><div class="analysis-empty"><n-icon size="36"><MapOutline/></n-icon><b>尚未开始识别</b><span>选择影像并确认阈值后开始识别。</span></div></div>
      <div v-else-if="running" class="preview-box hero-preview"><div class="analysis-empty"><n-spin size="large"/><b>{{task.status==='pending'?'等待识别':'识别中'}}</b><span>{{task.dataset_name}} · 批次进度 {{batchDone}} / {{batchTotal}}</span></div></div>
      <div v-else-if="task.status==='failed'" class="preview-box hero-preview"><div class="analysis-empty"><n-icon size="44"><MapOutline/></n-icon><b>识别失败</b><span>{{task.error_message||'任务执行失败，请稍后重试'}}</span></div></div>
      <template v-else>
        <div class="layer-grid"><div><label>VV 原始影像</label><div class="preview-box"><img class="preview" :src="task.result?.files.vv_preview"/></div></div><div><label>积水概率</label><div class="preview-box"><img class="preview" :src="task.result?.files.probability_preview"/></div></div><div><label>积水分布</label><div class="preview-box"><img class="preview" :src="task.result?.files.mask_preview"/></div></div></div>
        <n-space style="margin-top:14px"><n-button type="primary" @click="$router.push(`/results/${task.result?.id}`)">查看成果</n-button><span class="muted">模型：{{task.model_name}}</span><n-tag v-if="batchTotal>1" type="success">批次已完成 {{batchDone}} / {{batchTotal}}</n-tag></n-space>
      </template>
    </n-card>
    <aside class="result-sidebar"><n-card title="识别摘要"><div class="summary-stack"><div><span>积水占比</span><strong>{{pct(task?.result?.flood_ratio)}}</strong></div><div><span>基础水害提示</span><strong :class="task?.result?.risk_level?`risk-${task.result.risk_level}`:''">{{task?.result?.risk_level||'--'}}</strong></div><div><span>平均置信度</span><strong>{{pct(task?.result?.mean_confidence)}}</strong></div><div><span>推理耗时</span><strong>{{formatSeconds(task?.result?.inference_seconds)}}</strong></div><div><span>有效像素</span><b>{{task?.result?.valid_pixel_count||0}}</b></div></div></n-card></aside>
  </div>
  <n-card title="任务流程状态" class="process-panel"><n-steps :current="step" :status="task?.status==='failed'?'error':'process'"><n-step title="数据检查" description="GeoTIFF 与双波段元信息"/><n-step title="模型推理" description="训练预处理与真实权重"/><n-step title="结果生成" description="GeoTIFF、PNG 与统计指标"/><n-step title="完成" description="任务结果持久化"/></n-steps></n-card>
</template>

<script setup lang="ts">
import{computed,onBeforeUnmount,onMounted,ref}from'vue'
import{useRoute,useRouter}from'vue-router'
import{useMessage}from'naive-ui'
import{MapOutline}from'@vicons/ionicons5'
import{api}from'../api'
import type{Dataset,ModelStatus,Task}from'../types'
import{formatPercent,formatSeconds}from'../utils/format'

const route=useRoute(),router=useRouter(),rows=ref<Dataset[]>([]),datasetIds=ref<string[]>([]),threshold=ref(.5),running=ref(false),task=ref<Task|null>(null),model=ref<ModelStatus|null>(null),modelError=ref(''),step=ref(1),batchTotal=ref(0),batchDone=ref(0),msg=useMessage()
let pollTimer:ReturnType<typeof setTimeout>|null=null
const options=computed(()=>rows.value.map(x=>({label:`${x.filename} · ${x.width}×${x.height}`,value:x.id}))),chosen=computed(()=>rows.value.find(x=>x.id===datasetIds.value[0])),pct=formatPercent
const applyTask=(value:Task,busy=value.status==='pending'||value.status==='running')=>{task.value=value;if(!datasetIds.value.length)datasetIds.value=[value.dataset_id];threshold.value=value.threshold;running.value=busy;step.value=!busy&&value.status==='completed'?4:value.status==='running'?2:value.status==='pending'?1:value.status==='failed'?2:1}
const pollTask=async(id:string,initial=false)=>{try{const value=await api.task(id);applyTask(value);batchTotal.value=1;batchDone.value=['completed','failed'].includes(value.status)?1:0;if(running.value)pollTimer=setTimeout(()=>void pollTask(id),1200);else if(!initial&&value.status==='completed')msg.success('积水识别完成');else if(value.status==='failed')msg.error(value.error_message||'积水识别失败')}catch(e:any){running.value=false;msg.error(e.message||'加载识别任务失败')}}
const pollBatch=async(id:string,initial=false)=>{try{const values=await api.tasks({batch_id:id});batchTotal.value=values.length;batchDone.value=values.filter(x=>x.status==='completed'||x.status==='failed').length;const busy=values.some(x=>x.status==='pending'||x.status==='running');const current=values.find(x=>x.status==='running')||values.find(x=>x.status==='pending')||values.find(x=>x.status==='completed')||values[0];if(current)applyTask(current,busy);if(busy)pollTimer=setTimeout(()=>void pollBatch(id),1200);else if(!initial){const failed=values.filter(x=>x.status==='failed').length;if(failed)msg.warning(`批次识别完成，${failed} 个任务失败`);else msg.success('批次积水识别完成')}}catch(e:any){running.value=false;msg.error(e.message||'加载批次任务失败')}}
onMounted(async()=>{try{rows.value=await api.datasets();try{model.value=await api.model()}catch(e:any){modelError.value=e.message||'模型状态接口不可用'}const staged=typeof route.query.datasets==='string'?route.query.datasets.split(',').filter(Boolean):[];const valid=new Set(rows.value.map(x=>x.id));datasetIds.value=staged.filter(id=>valid.has(id));const batch=typeof route.query.batch==='string'?route.query.batch:null,id=typeof route.query.task==='string'?route.query.task:null;if(batch)await pollBatch(batch,true);else if(id)await pollTask(id,true)}catch(e:any){msg.error(e.message||'加载识别页面失败')}})
onBeforeUnmount(()=>{if(pollTimer)clearTimeout(pollTimer)})
const run=async()=>{if(!datasetIds.value.length||running.value||model.value?.loaded===false||modelError.value)return;running.value=true;task.value=null;step.value=1;try{const result=await api.batchRun(datasetIds.value,threshold.value);if(!result.task_ids.length)throw new Error('未创建可执行的识别任务');batchTotal.value=result.created_count;batchDone.value=0;await router.replace({path:'/inference',query:{task:result.task_ids[0],batch:result.batch_id}});await pollBatch(result.batch_id,true)}catch(e:any){running.value=false;step.value=1;msg.error(e.message||'创建识别任务失败')}}
</script>
