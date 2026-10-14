<template>
  <div class="page-head"><div><h2>任务成果</h2><span class="muted">施工积水分析任务追踪、成果复核与历史追溯</span></div><n-button :loading="loading" @click="load">刷新</n-button></div>
  <n-tabs type="segment" class="result-hub"><n-tab-pane name="analysis" tab="分析任务"><p class="muted">本页按批次与状态展示积水识别任务；变化分析与风险研判保持各自的专业成果页面。</p></n-tab-pane><n-tab-pane name="archive" tab="成果归档"><n-space><n-button @click="router.push('/compare')">时空变化分析</n-button><n-button @click="router.push('/risk')">风险研判成果</n-button><n-button @click="router.push('/datasets')">多源环境数据</n-button></n-space></n-tab-pane><n-tab-pane name="assistance" tab="辅助记录"><n-space><n-button type="primary" @click="router.push('/assistance')">作业辅助事项与人工核查</n-button></n-space></n-tab-pane></n-tabs>
  <n-card class="filter-panel" style="margin-bottom:16px">
    <n-space wrap :size="10" align="center">
      <n-input v-model:value="filter.dataset_name" placeholder="数据名称" clearable style="width:190px"/>
      <n-select v-model:value="filter.status" :options="statusOptions" placeholder="任务状态" clearable style="width:130px"/>
      <n-select v-model:value="filter.risk_level" :options="riskOptions" placeholder="基础水害提示" clearable style="width:150px"/>
      <n-date-picker v-model:value="dateRange" type="daterange" clearable start-placeholder="开始日期" end-placeholder="结束日期" separator="至" style="width:280px"/>
      <n-input v-model:value="filter.batch_id" placeholder="批次编号" clearable style="width:150px"/>
      <n-button type="primary" :loading="loading" @click="applyFilter">查询</n-button>
      <n-button :disabled="loading" @click="resetFilter">重置</n-button>
      <n-button type="primary" secondary :disabled="compareSel.length!==2" @click="goCompare">{{compareSel.length?'对比分析 '+compareSel.length+'/2':'对比分析'}}</n-button>
    </n-space>
  </n-card>
  <n-card v-if="batchId" class="data-table-panel" style="margin-bottom:16px">
    <n-space justify="space-between" align="center"><strong>当前批次 {{batchId.slice(0,8)}}</strong><span class="muted">{{finished}} / {{rows.length}} 已结束</span></n-space>
    <n-progress style="margin-top:12px" type="line" :percentage="progress" :status="counts.failed?'error':progress===100?'success':'default'"/>
    <div class="count-summary" style="margin-top:10px"><span>总数 <b>{{rows.length}}</b></span><span>待处理 <b>{{counts.pending}}</b></span><span>识别中 <b>{{counts.running}}</b></span><span>已完成 <b>{{counts.completed}}</b></span><span>失败 <b>{{counts.failed}}</b></span></div>
  </n-card>
  <n-card class="data-table-panel"><n-data-table :loading="loading" :columns="columns" :data="rows" :row-key="(r:any)=>r.id" v-model:checked-row-keys="checkedKeys" :pagination="{pageSize:10}" :scroll-x="1500"/></n-card>
</template>
<script setup lang="ts">
import{computed,h,onBeforeUnmount,onMounted,ref}from'vue';import{useRoute,useRouter}from'vue-router';import{NButton,NPopconfirm,NTag,NTooltip,useMessage}from'naive-ui';import{api}from'../api';import type{Task}from'../types';import{formatDateTime,statusText}from'../utils/format';
const rows=ref<Task[]>([]),loading=ref(false),router=useRouter(),route=useRoute(),msg=useMessage(),batchId=computed(()=>typeof route.query.batch==='string'?route.query.batch:'');let timer:number|undefined;let batchNotified=false;
const counts=computed(()=>({pending:rows.value.filter(r=>r.status==='pending').length,running:rows.value.filter(r=>r.status==='running').length,completed:rows.value.filter(r=>r.status==='completed').length,failed:rows.value.filter(r=>r.status==='failed').length}));const finished=computed(()=>counts.value.completed+counts.value.failed);const progress=computed(()=>rows.value.length?Math.round(finished.value/rows.value.length*100):0);
const schedule=()=>{if(timer)window.clearTimeout(timer);if(counts.value.pending+counts.value.running>0)timer=window.setTimeout(load,2000);else if(batchId.value&&rows.value.length&&!batchNotified){batchNotified=true;msg.success(`批量识别完成：成功 ${counts.value.completed}，失败 ${counts.value.failed}`)}};
const statusOptions=[{label:'待处理',value:'pending'},{label:'识别中',value:'running'},{label:'已完成',value:'completed'},{label:'失败',value:'failed'}];
const riskOptions=[{label:'安全',value:'安全'},{label:'低风险',value:'低风险'},{label:'中风险',value:'中风险'},{label:'高风险',value:'高风险'}];
const filter=ref<{dataset_name:string;status:string|null;risk_level:string|null;batch_id:string}>({dataset_name:'',status:null,risk_level:null,batch_id:''});
const dateRange=ref<[number,number]|null>(null);const checkedKeys=ref<string[]>([]);
const buildFilter=()=>{const f:any={};if(filter.value.dataset_name)f.dataset_name=filter.value.dataset_name;if(filter.value.status)f.status=filter.value.status;if(filter.value.risk_level)f.risk_level=filter.value.risk_level;if(filter.value.batch_id||batchId.value)f.batch_id=filter.value.batch_id||batchId.value;if(dateRange.value){f.date_from=new Date(dateRange.value[0]).toISOString();f.date_to=new Date(dateRange.value[1]+86399999).toISOString()}return f};
const load=async()=>{loading.value=true;try{rows.value=await api.tasks(buildFilter());schedule()}catch(e:any){msg.error(e.message)}finally{loading.value=false}};
const applyFilter=async()=>{checkedKeys.value=[];await load()};const resetFilter=async()=>{filter.value={dataset_name:'',status:null,risk_level:null,batch_id:''};dateRange.value=null;checkedKeys.value=[];await load()};
const compareSel=computed(()=>rows.value.filter(r=>checkedKeys.value.includes(r.id)&&r.status==='completed'&&r.result));
const goCompare=()=>{const sel=compareSel.value;if(sel.length===2)router.push(`/compare?a=${sel[0].result!.id}&b=${sel[1].result!.id}`)};
onMounted(load);onBeforeUnmount(()=>{if(timer)window.clearTimeout(timer)});
const showAllTasks=async()=>{if(batchId.value)await router.replace('/history')};const retry=async(r:Task)=>{try{await api.retry(r.id);await showAllTasks();msg.success('失败任务已加入队列');await load()}catch(e:any){msg.error(e.message)}};const rerun=async(r:Task)=>{try{await api.rerun(r.id);await showAllTasks();msg.success('重新识别任务已加入队列');await load()}catch(e:any){msg.error(e.message)}};const remove=async(r:Task)=>{try{await api.removeTask(r.id);msg.success('任务已删除');await load()}catch(e:any){msg.error(e.message)}};
const statusType=(s:string)=>s==='completed'?'success':s==='failed'?'error':s==='running'?'info':'default';
const columns=[{type:'selection' as const,width:48,fixed:'left' as const},{title:'任务编号',render:(r:Task)=>h(NTooltip,null,{trigger:()=>h('span',r.id.slice(0,8)),default:()=>r.id}),width:105,fixed:'left' as const},{title:'数据名称',key:'dataset_name',ellipsis:{tooltip:true},width:150},{title:'批次',render:(r:Task)=>h('span',{class:'batch-cell',title:r.batch_id||'--'},r.batch_id?r.batch_id.slice(0,8):'--'),width:120},{title:'状态',render:(r:Task)=>h(NTag,{size:'small',type:statusType(r.status)},{default:()=>statusText(r.status)}),width:80},{title:'积水占比',render:(r:Task)=>r.result?`${(r.result.flood_ratio*100).toFixed(2)}%`:'--',width:100},{title:'基础水害提示',render:(r:Task)=>r.result?.risk_level||'--',width:120},{title:'创建时间',render:(r:Task)=>formatDateTime(r.created_at),width:150},{title:'完成时间',render:(r:Task)=>formatDateTime(r.finished_at),width:150},{title:'耗时',render:(r:Task)=>r.result?`${r.result.inference_seconds.toFixed(2)} s`:'--',width:80},{title:'操作',width:260,fixed:'right' as const,render:(r:Task)=>h('div',{style:'display:flex;gap:7px'},[r.result&&h(NButton,{size:'small',onClick:()=>router.push(`/results/${r.result!.id}`)},{default:()=> '详情'}),r.status==='failed'&&h(NTooltip,null,{trigger:()=>h(NButton,{size:'small',type:'warning',secondary:true,onClick:()=>retry(r)},{default:()=> '重试'}),default:()=>r.error_message||'任务失败'}),r.status==='completed'&&h(NButton,{size:'small',secondary:true,onClick:()=>rerun(r)},{default:()=> '重新识别'}),!['pending','running'].includes(r.status)&&h(NPopconfirm,{onPositiveClick:()=>remove(r)},{trigger:()=>h(NButton,{size:'small',type:'error',secondary:true},{default:()=> '删除'}),default:()=> '删除任务及结果文件？'})])}];
</script>
