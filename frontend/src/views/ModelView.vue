<template>
  <div class="page-head"><div><h2>模型状态</h2><span class="muted">积水识别模型运行性能与训练过程评估</span></div><n-tag :class="['model-status',data?.status.loaded?'ready':'unready']" :type="data?.status.loaded?'success':'error'">{{data?.status.loaded?'模型已加载':'模型不可用'}}</n-tag></div>
  <n-spin :show="loading">
    <div v-if="data" class="model-overview"><span><small>模型</small><b>{{data.status.model_name}}</b></span><span><small>状态</small><b :class="data.status.loaded?'online':'offline'">● {{data.status.loaded?'已加载':'不可用'}}</b></span><span><small>设备</small><b>{{data.status.device?.toUpperCase()}}</b></span><span><small>版本</small><b>{{data.status.model_version}}</b></span></div>
    <div class="model-metric-band"><div v-for="m in metrics" :key="m[0]" class="band-metric"><span>{{m[0]}}</span><strong>{{fmt(m[1])}}</strong></div></div>
    <div class="two-col model-charts"><n-card title="训练 / 验证损失"><line-chart :option="lossOption"/></n-card><n-card title="验证 IoU / F1"><line-chart :option="metricOption"/></n-card></div>
    <n-card title="模型运行与训练信息" class="model-info-panel"><n-descriptions v-if="data" bordered :column="3"><n-descriptions-item label="默认阈值">{{data.status.threshold}}</n-descriptions-item><n-descriptions-item label="常驻加载次数">{{data.status.load_count}}</n-descriptions-item><n-descriptions-item label="训练轮次">{{data.training.epochs}} 轮</n-descriptions-item><n-descriptions-item label="最佳轮次">{{data.training.best_epoch}}</n-descriptions-item><n-descriptions-item label="最佳验证 IoU">{{fmt(data.training.best_val_iou)}}</n-descriptions-item><n-descriptions-item label="训练耗时">{{data.training.elapsed_minutes?.toFixed(2)}} 分钟</n-descriptions-item></n-descriptions></n-card>
  </n-spin>
</template>
<script setup lang="ts">
import{computed,onMounted,ref}from'vue'
import{api}from'../api'
import LineChart from'../components/LineChart.vue'
const data=ref<any>(),loading=ref(true)
onMounted(async()=>{try{data.value=await api.evaluation()}finally{loading.value=false}})
const fmt=(v:any)=>v==null?'--':Number(v).toFixed(4)
const metrics=computed(()=>{const s=data.value?.status||{};return[['Precision',s.test_precision],['Recall',s.test_recall],['F1',s.test_f1],['IoU',s.test_iou],['Dice',s.test_dice],['Accuracy',s.test_accuracy]]})
const base=()=>({tooltip:{trigger:'axis'},legend:{top:0,right:8},xAxis:{type:'category',data:(data.value?.history||[]).map((x:any)=>x.epoch)},yAxis:{type:'value'},grid:{top:42,left:52,right:20,bottom:35}})
const lossOption=computed(()=>({...base(),animationDuration:250,series:[{name:'训练 Loss',type:'line',smooth:false,symbolSize:4,lineStyle:{color:'#4B829F',width:2},data:(data.value?.history||[]).map((x:any)=>x.train_loss)},{name:'验证 Loss',type:'line',smooth:false,symbolSize:4,lineStyle:{color:'#C38A58',width:2},data:(data.value?.history||[]).map((x:any)=>x.val_loss)}]}))
const metricOption=computed(()=>({...base(),animationDuration:250,series:[{name:'IoU',type:'line',smooth:false,symbolSize:4,lineStyle:{color:'#397E9F',width:2},data:(data.value?.history||[]).map((x:any)=>x.iou)},{name:'F1',type:'line',smooth:false,symbolSize:4,lineStyle:{color:'#3F8B78',width:2},data:(data.value?.history||[]).map((x:any)=>x.f1)}]}))
</script>
