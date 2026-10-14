<template>
  <div class="page-head">
    <div><h2>变化分析</h2><span class="muted">建立可追溯的 T1→T2 施工区域积水变化成果</span></div>
    <n-space v-if="current"><n-button v-if="aiRunning" @click="stopAI">停止生成</n-button><n-button v-else :disabled="current.status!=='completed'" @click="interpret">生成分析说明</n-button></n-space>
  </div>
  <n-tabs v-model:value="tab" type="segment" animated class="change-tabs">
    <n-tab-pane name="create" tab="新建分析">
      <n-card title="选择两期识别成果" class="selector-card">
        <div class="time-selector">
          <div><label>T1 · 前期成果</label><n-select v-model:value="t1Id" filterable :options="resultOptions" placeholder="选择前期已完成成果"/></div>
          <n-button circle secondary class="swap" :disabled="!t1Id&&!t2Id" @click="swap">⇄</n-button>
          <div><label>T2 · 后期成果</label><n-select v-model:value="t2Id" filterable :options="resultOptions" placeholder="选择后期已完成成果"/></div>
        </div>
        <div v-if="t1Task||t2Task" class="source-summary"><span>T1：{{sourceText(t1Task)}}</span><span>T2：{{sourceText(t2Task)}}</span></div>
        <n-space justify="end" style="margin-top:16px"><n-select v-model:value="projectId" :options="projectOptions" clearable placeholder="关联施工项目（降雨背景所需）" style="width:260px"/><n-input v-model:value="analysisName" placeholder="分析名称（可选）" maxlength="255" style="width:280px"/><n-button type="primary" :loading="creating" :disabled="!canCreate" @click="createAnalysis">开始变化分析</n-button></n-space>
      </n-card>
    </n-tab-pane>
    <n-tab-pane name="history" tab="分析历史">
      <n-card><div class="history-head"><span>共 {{history.length}} 条变化分析记录</span><n-button :loading="loadingHistory" @click="loadHistory">刷新</n-button></div><n-data-table :columns="columns" :data="history" :loading="loadingHistory" :row-key="(r:ChangeAnalysis)=>r.id" :pagination="{pageSize:10}" :scroll-x="1050"/></n-card>
    </n-tab-pane>
  </n-tabs>
  <n-card v-if="!current&&tab==='create'" class="onboarding-panel">
    <div class="onboarding-content"><div class="onboarding-mark"><img src="/icons/shield-search.svg" alt=""></div><h3>选择两个时期的识别成果进行比较</h3><p>两期成果需具有一致的空间参考、范围与像素网格，分析结果将保存为可追溯的正式记录。</p><div class="onboarding-steps"><div><b>T1 · 前期</b><span>选择作为比较基准的已完成成果</span></div><div><b>T2 · 后期</b><span>选择同一区域的后期识别成果</span></div><div><b>变化成果</b><span>查看新增、持续与消退积水分布</span></div></div><n-button @click="$router.push('/history')">查看已完成识别成果</n-button></div>
  </n-card>
  <n-spin :show="loadingDetail">
    <template v-if="current">
      <n-alert v-if="current.status==='failed'" type="error" style="margin:16px 0">{{current.error_message || '变化分析失败'}}</n-alert>
      <section v-else-if="current.status==='completed'" class="change-result">
        <div class="result-heading">
          <div><h3>{{current.name}}</h3><span>分析 ID {{current.id}} · {{fmt(current.completed_at)}} · {{current.algorithm_version}} · 空间一致性检查已通过</span></div>
          <n-space><n-button tag="a" :href="api.changeCsvUrl(current.id)" download>导出 CSV</n-button><n-button tag="a" :href="current.geotiff_url || undefined" download>下载 GeoTIFF</n-button><n-button tag="a" :href="api.changeHtmlUrl(current.id)" target="_blank">在线报告</n-button><n-button tag="a" :href="api.changePdfUrl(current.id)" download>下载 PDF</n-button></n-space>
        </div>
        <div class="period-grid">
          <n-card title="T1 · 前期"><n-descriptions :column="2" bordered size="small"><n-descriptions-item label="数据">{{current.t1.dataset_name}}</n-descriptions-item><n-descriptions-item label="识别时间">{{fmt(current.t1.finished_at)}}</n-descriptions-item><n-descriptions-item label="模型">{{current.t1.model_name}}</n-descriptions-item><n-descriptions-item label="识别阈值">{{current.t1.threshold}}</n-descriptions-item><n-descriptions-item label="积水占比">{{pct(current.t1.water_ratio)}}</n-descriptions-item><n-descriptions-item label="结果 ID">{{current.t1.result_id}}</n-descriptions-item></n-descriptions></n-card>
          <n-card title="T2 · 后期"><n-descriptions :column="2" bordered size="small"><n-descriptions-item label="数据">{{current.t2.dataset_name}}</n-descriptions-item><n-descriptions-item label="识别时间">{{fmt(current.t2.finished_at)}}</n-descriptions-item><n-descriptions-item label="模型">{{current.t2.model_name}}</n-descriptions-item><n-descriptions-item label="识别阈值">{{current.t2.threshold}}</n-descriptions-item><n-descriptions-item label="积水占比">{{pct(current.t2.water_ratio)}}</n-descriptions-item><n-descriptions-item label="结果 ID">{{current.t2.result_id}}</n-descriptions-item></n-descriptions></n-card>
        </div>
        <div class="stat-grid change-stat-grid">
          <metric-card tone="orange" label="新增积水" :value="pct(current.ratios.new_water)" :hint="count(current.counts.new_water)+' 像素'"/>
          <metric-card tone="blue" label="持续积水" :value="pct(current.ratios.persistent_water)" :hint="count(current.counts.persistent_water)+' 像素'"/>
          <metric-card tone="green" label="消退积水" :value="pct(current.ratios.receded_water)" :hint="count(current.counts.receded_water)+' 像素'"/>
          <metric-card label="净变化（T2−T1）" :value="signedPct(current.net_change_ratio)" :hint="count(current.valid_pixels)+' 有效像素'"/>
        </div>
        <n-card title="降雨背景" class="rain-card"><template #header-extra><n-space><n-button size="small" :disabled="!current.project_id" :loading="rainLoading" @click="runRain">生成/更新背景</n-button><n-button v-if="rainContext" size="small" tag="a" :href="api.changeRainfallCsvUrl(current.id)" download>导出CSV</n-button></n-space></template><n-alert v-if="!current.project_id" type="warning">该变化分析尚未关联施工项目，无法加载项目降雨背景。</n-alert><template v-else-if="rainContext"><n-descriptions bordered :column="3" size="small"><n-descriptions-item label="时间范围">{{fmt(rainContext.window_start)}} 至 {{fmt(rainContext.window_end)}}</n-descriptions-item><n-descriptions-item label="累计降雨">{{rainContext.total_rainfall_mm==null?'无对应观测':rainContext.total_rainfall_mm.toFixed(2)+' mm'}}</n-descriptions-item><n-descriptions-item label="降雨事件">{{rainContext.rainfall_event_count}}</n-descriptions-item><n-descriptions-item label="峰值时段平均雨强">{{rainContext.peak_rainfall_rate_mm_h==null?'--':rainContext.peak_rainfall_rate_mm_h.toFixed(2)+' mm/h'}}</n-descriptions-item><n-descriptions-item label="覆盖状态">{{rainContext.coverage_label}}</n-descriptions-item><n-descriptions-item label="规则版本">{{rainContext.rule_version}}</n-descriptions-item></n-descriptions><div v-if="rainContext.series?.length" class="rain-series"><div v-for="item in rainContext.series" :key="item.observed_at+item.station_id"><span>{{fmt(item.observed_at)}}</span><b :style="{width:Math.min(100,item.rainfall_mm*5)+'%'}"></b><em>{{item.rainfall_mm.toFixed(2)}} mm</em></div></div><n-alert v-else type="info" style="margin-top:12px">当前系统没有对应时间范围的降雨观测数据；这不表示该时段没有降雨。</n-alert><p class="muted">同期降雨仅作为时间背景参考，不表示降雨与积水变化之间存在已验证因果关系。</p></template><n-empty v-else description="尚未生成降雨背景"/></n-card>
        <n-card title="三图联读" class="visual-card">
          <div class="three-images">
            <figure><div class="preview-box"><img v-if="taskImage(t1Detail)" :src="taskImage(t1Detail)" alt="T1积水分布"><span v-else>图像不可用</span></div><figcaption>T1 积水识别分布</figcaption></figure>
            <figure><div class="preview-box"><img v-if="current.image_url" :src="current.image_url" alt="积水变化分布"><span v-else>图像不可用</span></div><figcaption>T1→T2 变化分布</figcaption></figure>
            <figure><div class="preview-box"><img v-if="taskImage(t2Detail)" :src="taskImage(t2Detail)" alt="T2积水分布"><span v-else>图像不可用</span></div><figcaption>T2 积水识别分布</figcaption></figure>
          </div>
          <div class="legend"><span class="new">新增积水</span><span class="persistent">持续积水</span><span class="receded">消退积水</span><span class="stable">持续非积水</span></div>
          <n-alert type="info" :bordered="false">四类变化均以两期有效像素为统计口径；净变化表示 T2 与 T1 积水占比之差，不代表水深、面积或现场安全等级。</n-alert>
        </n-card>
        <n-card title="分析说明" class="explanation-card"><div class="explanation-grid"><p><b>新增积水</b>：T1 未识别为积水、T2 识别为积水的有效像素。</p><p><b>持续积水</b>：T1、T2 均识别为积水的有效像素。</p><p><b>消退积水</b>：T1 识别为积水、T2 未识别为积水的有效像素。</p><p><b>使用边界</b>：结果反映两期遥感识别的空间变化，应结合现场核查及施工安全管理制度综合使用。</p></div></n-card>
        <n-card v-if="aiText||aiRunning" title="智盾助手 · 变化解读" class="ai-card"><n-skeleton v-if="aiRunning&&!aiText" text :repeat="4"/><div v-if="aiText" class="ai-content" v-html="safeMarkdown(aiText)"></div><p>仅基于本条已保存变化统计辅助解读，不替代现场检查和正式安全决策。</p></n-card>
      </section>
    </template>
  </n-spin>
</template>

<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, ref } from 'vue'
import { NButton, NPopconfirm, NTag, useMessage } from 'naive-ui'
import { useRoute, useRouter } from 'vue-router'
import { api, streamChangeAI } from '../api'
import type { ChangeAnalysis, ConstructionProject, RainfallContext, Task } from '../types'
import MetricCard from '../components/MetricCard.vue'
import { formatDateTime } from '../utils/format'
import { safeMarkdown } from '../utils/markdown'
const route=useRoute(),router=useRouter(),message=useMessage()
const tab=ref<'create'|'history'>('create'),tasks=ref<Task[]>([]),history=ref<ChangeAnalysis[]>([]),projects=ref<ConstructionProject[]>([])
const t1Id=ref<string|null>(null),t2Id=ref<string|null>(null),analysisName=ref('')
const projectId=ref<string|null>(null),rainContext=ref<RainfallContext|null>(null),rainLoading=ref(false)
const current=ref<ChangeAnalysis|null>(null),t1Detail=ref<Task|null>(null),t2Detail=ref<Task|null>(null)
const creating=ref(false),loadingHistory=ref(false),loadingDetail=ref(false),aiRunning=ref(false),aiText=ref('')
let aiController:AbortController|null=null,aiVersion=0
const completeTasks=computed(()=>tasks.value.filter(t=>t.status==='completed'&&t.result))
const resultOptions=computed(()=>completeTasks.value.map(t=>({label:`${t.dataset_name} · ${formatDateTime(t.finished_at)} · ${(t.result!.flood_ratio*100).toFixed(2)}%`,value:t.result!.id})))
const projectOptions=computed(()=>projects.value.filter(x=>x.status==='active').map(x=>({label:x.project_name,value:x.id})))
const t1Task=computed(()=>completeTasks.value.find(t=>t.result?.id===t1Id.value)||null),t2Task=computed(()=>completeTasks.value.find(t=>t.result?.id===t2Id.value)||null)
const canCreate=computed(()=>Boolean(t1Id.value&&t2Id.value&&t1Id.value!==t2Id.value))
const fmt=(v:string|null)=>v?formatDateTime(v):'--',pct=(v:number|null)=>v==null?'--':`${(v*100).toFixed(2)}%`,signedPct=(v:number|null)=>v==null?'--':`${v>=0?'+':''}${(v*100).toFixed(2)}%`,count=(v:number|null)=>v==null?'--':v.toLocaleString('zh-CN')
const sourceText=(t:Task|null)=>t?`${t.dataset_name} / ${fmt(t.finished_at)} / ${t.model_name} / 阈值 ${t.threshold} / 积水占比 ${pct(t.result?.flood_ratio ?? null)}`:'未选择',taskImage=(t:Task|null)=>t?.result?.files.mask_preview||''
const swap=()=>{const old=t1Id.value;t1Id.value=t2Id.value;t2Id.value=old}
async function loadTasks(){tasks.value=await api.results()}
async function loadHistory(){loadingHistory.value=true;try{history.value=await api.changeAnalyses()}catch(e:any){message.error(e.message)}finally{loadingHistory.value=false}}
async function showAnalysis(row:ChangeAnalysis){loadingDetail.value=true;stopAI();aiText.value='';rainContext.value=null;try{current.value=await api.changeAnalysis(row.id);[t1Detail.value,t2Detail.value,rainContext.value]=await Promise.all([api.result(current.value.t1.result_id),api.result(current.value.t2.result_id),api.changeRainfallContext(row.id)]);await router.replace({path:'/compare',query:{change:row.id}})}catch(e:any){message.error(e.message)}finally{loadingDetail.value=false}}
async function createAnalysis(){if(!canCreate.value)return;creating.value=true;try{const row=await api.createChangeAnalysis({result_t1_id:t1Id.value!,result_t2_id:t2Id.value!,project_id:projectId.value,...(analysisName.value.trim()?{name:analysisName.value.trim()}:{})});message.success('时空变化分析已生成并保存');await loadHistory();await showAnalysis(row)}catch(e:any){message.error(e.message)}finally{creating.value=false}}
async function runRain(){if(!current.value)return;rainLoading.value=true;try{rainContext.value=await api.runChangeRainfallContext(current.value.id);message.success('降雨背景已更新')}catch(e:any){message.error(e.message)}finally{rainLoading.value=false}}
async function removeAnalysis(row:ChangeAnalysis){try{await api.removeChangeAnalysis(row.id);if(current.value?.id===row.id){current.value=null;t1Detail.value=null;t2Detail.value=null;await router.replace('/compare')}await loadHistory();message.success('变化分析已删除')}catch(e:any){message.error(e.message)}}
const statusType=(s:string)=>s==='completed'?'success':s==='failed'?'error':'info'
const columns=[{title:'分析 ID',render:(r:ChangeAnalysis)=>r.id.slice(0,8),width:100},{title:'分析名称',key:'name',ellipsis:{tooltip:true},width:180},{title:'T1 → T2',render:(r:ChangeAnalysis)=>`${r.t1.dataset_name} → ${r.t2.dataset_name}`,ellipsis:{tooltip:true},width:230},{title:'T1占比',render:(r:ChangeAnalysis)=>pct(r.t1.water_ratio),width:90},{title:'T2占比',render:(r:ChangeAnalysis)=>pct(r.t2.water_ratio),width:90},{title:'新增',render:(r:ChangeAnalysis)=>pct(r.ratios.new_water),width:90},{title:'持续',render:(r:ChangeAnalysis)=>pct(r.ratios.persistent_water),width:90},{title:'消退',render:(r:ChangeAnalysis)=>pct(r.ratios.receded_water),width:90},{title:'状态',render:(r:ChangeAnalysis)=>h(NTag,{size:'small',type:statusType(r.status)},{default:()=>r.status==='completed'?'已完成':r.status==='failed'?'失败':'处理中'}),width:90},{title:'创建时间',render:(r:ChangeAnalysis)=>fmt(r.created_at),width:170},{title:'操作',fixed:'right' as const,width:190,render:(r:ChangeAnalysis)=>h('div',{style:'display:flex;gap:7px'},[h(NButton,{size:'small',onClick:()=>showAnalysis(r)},{default:()=>'查看'}),r.status==='completed'&&h(NButton,{size:'small',tag:'a',href:api.changeCsvUrl(r.id)},{default:()=>'下载'}),h(NPopconfirm,{onPositiveClick:()=>removeAnalysis(r)},{trigger:()=>h(NButton,{size:'small',type:'error',secondary:true},{default:()=>'删除'}),default:()=>'删除该记录及其成果文件？'})])}]
function stopAI(){aiVersion++;aiController?.abort();aiController=null;aiRunning.value=false}
async function interpret(){if(!current.value)return;const version=++aiVersion;aiController=new AbortController();aiText.value='';aiRunning.value=true;try{await streamChangeAI(current.value.id,t=>{if(version===aiVersion)aiText.value+=t},aiController.signal)}catch(e:any){if(version===aiVersion&&e?.name!=='AbortError')message.error(e.message)}finally{if(version===aiVersion){aiRunning.value=false;aiController=null}}}
onMounted(async()=>{try{await Promise.all([loadTasks(),loadHistory(),api.projects().then(x=>projects.value=x)]);const a=typeof route.query.a==='string'?route.query.a:null,b=typeof route.query.b==='string'?route.query.b:null,change=typeof route.query.change==='string'?route.query.change:null;if(a)t1Id.value=a;if(b)t2Id.value=b;if(change){const found=history.value.find(r=>r.id===change);if(found)await showAnalysis(found)}}catch(e:any){message.error(e.message)}})
onBeforeUnmount(stopAI)
</script>

<style scoped>
.change-tabs{margin-bottom:16px}.selector-card{margin-top:12px}.time-selector{display:grid;grid-template-columns:1fr 46px 1fr;gap:14px;align-items:end}.time-selector label{display:block;margin-bottom:8px;font-size:12px;color:#8795a3}.swap{margin-bottom:1px}.source-summary{display:grid;grid-template-columns:1fr 1fr;gap:60px;margin-top:12px;padding:10px 12px;border:1px solid rgba(120,150,165,.2);border-radius:6px;font-size:12px;color:#78909c}.history-head,.result-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:14px}.change-result{margin-top:16px}.result-heading h3{margin:0 0 5px;font-size:18px}.result-heading span{color:#8795a3;font-size:12px}.period-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.change-stat-grid{grid-template-columns:repeat(4,1fr);margin:16px 0}.rain-card,.visual-card{margin-bottom:16px}.rain-series{margin-top:14px}.rain-series>div{display:grid;grid-template-columns:165px minmax(20px,1fr) 80px;gap:10px;align-items:center;font-size:12px}.rain-series b{display:block;min-width:2px;height:8px;background:#3f8fb0;border-radius:3px}.rain-series em{font-style:normal}.three-images{display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:14px}.three-images figure{margin:0}.three-images .preview-box{height:310px;display:flex;align-items:center;justify-content:center}.three-images img{width:100%;height:100%;object-fit:contain}.three-images figcaption{text-align:center;margin-top:8px;color:#78909c;font-size:12px}.legend{display:flex;justify-content:center;gap:20px;margin:14px 0}.legend span:before{content:'';display:inline-block;width:10px;height:10px;margin-right:6px;border-radius:2px;border:1px solid rgba(70,90,100,.25)}.legend .new:before{background:#e64b3c}.legend .persistent:before{background:#3478d4}.legend .receded:before{background:#35a66f}.legend .stable:before{background:#eef2f3}.explanation-card{margin-bottom:16px}.explanation-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px 24px}.explanation-grid p{margin:0;color:#78909c;font-size:13px}.explanation-grid b{color:inherit}.ai-card{margin-top:16px}.ai-content{line-height:1.8;font-size:14px}.ai-card>p{color:#8795a3;font-size:11px;border-top:1px solid rgba(120,150,165,.18);padding-top:10px}@media(max-width:1000px){.time-selector,.period-grid,.three-images,.explanation-grid{grid-template-columns:1fr}.swap{justify-self:center}.change-stat-grid{grid-template-columns:repeat(2,1fr)}.source-summary{grid-template-columns:1fr;gap:6px}}@media(max-width:620px){.change-stat-grid{grid-template-columns:1fr}.result-heading{align-items:flex-start;flex-direction:column}}
</style>
