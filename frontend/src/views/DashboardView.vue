<template>
  <div class="page-head">
    <div><h2>项目总览</h2><span class="muted">施工项目、水害研判与现场核查概况。</span></div>
    <n-space><n-button type="primary" @click="$router.push('/workflow')">进入实施流程</n-button><n-button :loading="loading" @click="refresh">刷新</n-button></n-space>
  </div>
  <n-alert v-if="loadError" type="error" class="load-error" closable @close="loadError=''">数据加载失败：{{loadError}}。请检查本地服务后重试。</n-alert>
  <n-spin :show="loading">
    <template v-if="d">
      <section v-if="isEmptyWorkspace" class="empty-workbench">
        <div class="empty-main">
          <div class="empty-heading"><span>尚未建立项目数据</span><p>按施工业务顺序准备项目、环境数据和分析任务。系统不会自动生成示例记录。</p></div>
          <div class="setup-list">
            <div class="setup-row"><b>1</b><div><strong>建立施工项目与作业区域</strong><span>录入项目基本信息，并导入真实区域边界。</span></div><n-button @click="$router.push('/areas')">施工区域管理</n-button></div>
            <div class="setup-row"><b>2</b><div><strong>接入环境数据</strong><span>导入 Sentinel-1 SAR；DEM、降雨和无人机影像可按项目补充。</span></div><n-button @click="$router.push('/datasets')">环境数据中心</n-button></div>
            <div class="setup-row"><b>3</b><div><strong>执行识别与研判</strong><span>完成积水识别后，可继续开展变化分析和风险研判。</span></div><n-button @click="$router.push('/inference')">积水识别</n-button></div>
          </div>
        </div>
        <aside class="readiness-panel">
          <h3>当前状态</h3>
          <dl><div><dt>施工项目</dt><dd>0</dd></div><div><dt>环境数据</dt><dd>0</dd></div><div><dt>分析任务</dt><dd>0</dd></div><div><dt>模型服务</dt><dd :class="d.model?.loaded?'ok':'attention'">{{d.model?.loaded?'已就绪':'未就绪'}}</dd></div></dl>
          <p>空状态来自当前数据库，不包含模拟项目、设备或分析结果。</p>
        </aside>
      </section>
      <template v-else>
        <section class="summary-band">
          <div><n-icon class="summary-icon"><BriefcaseOutline/></n-icon><span>当前项目</span><strong>{{projectCount}}</strong><small>启用项目</small></div>
          <div><n-icon class="summary-icon"><LayersOutline/></n-icon><span>环境数据</span><strong>{{d.dataset_count??0}}</strong><small>Sentinel-1 SAR</small></div>
          <div><n-icon class="summary-icon"><PulseOutline/></n-icon><span>分析任务</span><strong>{{d.task_count??0}}</strong><small>累计任务</small></div>
          <div><n-icon class="summary-icon"><WaterOutline/></n-icon><span>最新积水占比</span><strong>{{pct(d.latest_flood_ratio)}}</strong><small>{{d.current_risk_level||'暂无研判'}}</small></div>
        </section>
        <section class="workspace-grid">
          <n-card title="当前水害状态" class="risk-panel">
            <template v-if="d.latest_risk_assessment"><div class="risk-heading"><n-tag :type="riskTagType">{{d.latest_risk_assessment.attention_level}}</n-tag><strong>{{d.latest_risk_assessment.assessment_name}}</strong></div><dl class="detail-list"><div><dt>所属项目</dt><dd>{{d.latest_risk_assessment.project_name||'未关联项目'}}</dd></div><div><dt>完成时间</dt><dd>{{formatDateTime(d.latest_risk_assessment.completed_at)}}</dd></div><div v-if="d.latest_risk_assessment.evidence_summary"><dt>研判依据</dt><dd>{{d.latest_risk_assessment.evidence_summary}}</dd></div></dl><n-button text type="primary" @click="$router.push('/risk')">查看研判记录</n-button></template>
            <template v-else><div class="plain-empty"><strong>暂无风险研判</strong><span>已有识别成果可继续进行时空变化分析和规则化研判。</span><n-button @click="$router.push('/risk')">进入风险研判</n-button></div></template>
          </n-card>
          <n-card title="现场待办" class="action-panel"><div class="action-list"><button @click="$router.push('/assistance')"><span>待核查事项</span><b>{{d.assistance?.pending_count??0}}</b></button><button @click="$router.push('/assistance')"><span>优先核查</span><b :class="{'danger':(d.assistance?.priority_count??0)>0}">{{d.assistance?.priority_count??0}}</b></button><button @click="$router.push('/areas')"><span>高水害影响区域</span><b :class="{'danger':(d.construction?.high_impact_areas??0)>0}">{{d.construction?.high_impact_areas??0}}</b></button><button @click="$router.push('/equipment')"><span>位置已过期设备</span><b :class="{'warning':(d.equipment?.stale_position_count??0)>0}">{{d.equipment?.stale_position_count??0}}</b></button></div></n-card>
          <n-card title="施工对象" class="object-panel"><dl class="compact-list"><div><dt>启用作业区域</dt><dd>{{d.construction?.active_work_areas??0}}</dd></div><div><dt>工程机械</dt><dd>{{d.equipment?.active_count??0}}</dd></div><div><dt>已有位置快照</dt><dd>{{d.equipment?.positioned_count??0}}</dd></div><div><dt>已闭环事项</dt><dd>{{d.assistance?.closed_count??0}}</dd></div></dl></n-card>
        </section>
        <n-card title="数据基础与环境背景" class="foundation-panel">
          <dl class="foundation-grid"><div><dt>SAR 影像</dt><dd>{{d.dataset_count??0}}</dd></div><div><dt>DEM 数据</dt><dd>{{d.terrain?.dem_count??0}}</dd></div><div><dt>无人机影像</dt><dd>{{d.uav?.dataset_count??0}}</dd></div><div><dt>人工复核</dt><dd>{{d.uav?.reviewed_count??0}}</dd></div><div><dt>已分析区域</dt><dd>{{d.terrain?.profiled_area_count??0}}</dd></div><div><dt>近24小时降雨</dt><dd>{{rain(d.rainfall?.rainfall_24h_mm)}}</dd></div><div><dt>有观测项目</dt><dd>{{d.rainfall?.projects_with_data??0}}</dd></div></dl>
          <p class="boundary-note">数据数量表示已接入记录；地形与历史降雨仅用于辅助解释，不代表实时天气、因果判断或未来预报。</p>
        </n-card>
        <section class="result-row">
          <n-card title="最近识别任务" class="recent-panel"><n-data-table :columns="columns" :data="recentTasks" :pagination="false"/><n-empty v-if="!recentTasks.length" description="暂无识别任务"/></n-card>
          <n-card title="最新识别成果" class="result-panel"><template v-if="latest?.result"><div class="result-preview"><img :src="latest.result.files.probability_preview" alt="最新积水识别成果"></div><dl class="result-meta"><div><dt>数据</dt><dd :title="latest.dataset_name">{{latest.dataset_name}}</dd></div><div><dt>坐标系</dt><dd>{{latestDataset?.crs||'--'}}</dd></div><div><dt>模型 / 阈值</dt><dd>{{latest.model_name}} / {{latest.threshold}}</dd></div></dl></template><div v-else class="plain-empty compact"><strong>暂无识别成果</strong><span>完成积水识别后在此查看最近成果。</span></div></n-card>
        </section>
        <section v-if="(d.trend||[]).length" class="analysis-row"><n-card title="积水占比变化"><line-chart :option="trendOption"/></n-card><n-card title="任务状态"><line-chart :option="statusOption"/></n-card></section>
      </template>
    </template>
  </n-spin>
</template>

<script setup lang="ts">
import{computed,h,inject,onBeforeUnmount,onMounted,ref}from'vue'
import{NTag}from'naive-ui'
import{BriefcaseOutline,LayersOutline,PulseOutline,WaterOutline}from'@vicons/ionicons5'
import{api}from'../api'
import type{Dataset}from'../types'
import{themeKey}from'../composables/theme'
import LineChart from'../components/LineChart.vue'
import{formatDateTime,statusText}from'../utils/format'
const d=ref<any>(),datasets=ref<Dataset[]>([]),loading=ref(false),loadError=ref('')
const theme=inject(themeKey),isLight=computed(()=>theme?.mode.value==='light')
const pct=(v:any)=>v==null?'--':`${(v*100).toFixed(2)}%`,rain=(v:number|null|undefined)=>v==null?'无数据':`${Number(v).toFixed(2)} mm`
const projectCount=computed(()=>d.value?.construction?.active_projects??0)
const isEmptyWorkspace=computed(()=>Boolean(d.value)&&projectCount.value===0&&(d.value.dataset_count??0)===0&&(d.value.task_count??0)===0&&(d.value.equipment?.active_count??0)===0)
const latest=computed(()=>d.value?.latest_result||d.value?.recent_tasks?.find((task:any)=>task.result)),latestDataset=computed(()=>datasets.value.find(item=>item.id===latest.value?.dataset_id)),recentTasks=computed(()=>(d.value?.recent_tasks||[]).slice(0,6))
const riskTagType=computed(()=>d.value?.latest_risk_assessment?.attention_level?.startsWith('高')?'error':d.value?.latest_risk_assessment?.attention_level?.startsWith('中')?'warning':'success')
let active=true
const wait=(ms:number)=>new Promise(resolve=>setTimeout(resolve,ms))
const load=async()=>{if(loading.value)return;loading.value=true;loadError.value='';let lastError:any;try{for(let attempt=0;attempt<3&&active;attempt++){try{[d.value,datasets.value]=await Promise.all([api.dashboard(),api.datasets()]);return}catch(error){lastError=error;if(attempt<2)await wait(800)}}if(active)loadError.value=lastError?.message||'无法读取工作台数据'}finally{if(active)loading.value=false}}
const refresh=()=>load()
onMounted(()=>load());onBeforeUnmount(()=>{active=false})
const columns=[{title:'数据名称',key:'dataset_name',ellipsis:{tooltip:true}},{title:'状态',render:(r:any)=>h(NTag,{type:r.status==='completed'?'success':r.status==='failed'?'error':'warning',size:'small'},{default:()=>statusText(r.status)}),width:90},{title:'积水占比',render:(r:any)=>pct(r.result?.flood_ratio),width:100,align:'right'},{title:'时间',render:(r:any)=>formatDateTime(r.started_at),width:160,align:'right'}]
const trendColor=computed(()=>isLight.value?'#2563EB':'#4F8FEF')
const trendOption=computed(()=>({animationDuration:250,tooltip:{trigger:'axis',valueFormatter:(value:any)=>`${value}%`},grid:{left:70,right:20,top:26,bottom:34},xAxis:{type:'category',data:(d.value?.trend||[]).map((x:any)=>new Date(x.time).toLocaleDateString())},yAxis:{type:'value',name:'占比（%）',axisLabel:{formatter:'{value}%'}},series:[{name:'积水占比',type:'line',smooth:false,symbolSize:4,data:(d.value?.trend||[]).map((x:any)=>(x.flood_ratio*100).toFixed(2)),lineStyle:{color:trendColor.value,width:2},itemStyle:{color:trendColor.value}}]}))
const statusColors:Record<string,string>={completed:'#3F8A63',running:'#2563EB',pending:'#8A86B8',failed:'#C2414B'}
const statusOption=computed(()=>({tooltip:{trigger:'item'},series:[{type:'pie',radius:['54%','70%'],itemStyle:{borderColor:isLight.value?'#fff':'#18212F',borderWidth:2},data:Object.entries(d.value?.status_counts||{}).map(([name,value])=>({name:statusText(name),value,itemStyle:{color:statusColors[name]||'#8A86B8'}})),label:{color:isLight.value?'#667085':'#CBD5E1'}}]}))
</script>

<style scoped>
.load-error{margin-bottom:14px}.empty-workbench{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(280px,.7fr);border:1px solid var(--border);border-radius:6px;background:#fff;overflow:hidden}.empty-main{padding:26px 28px}.empty-heading span{font-size:18px;font-weight:600}.empty-heading p{margin:7px 0 22px;color:var(--text-secondary);font-size:13px}.setup-list{border-top:1px solid var(--border)}.setup-row{display:grid;grid-template-columns:28px minmax(0,1fr) auto;align-items:center;gap:14px;padding:17px 0;border-bottom:1px solid var(--border)}.setup-row>b{display:grid;place-items:center;width:24px;height:24px;border-radius:4px;background:#eef2f6;color:#475467;font-size:12px}.setup-row strong,.setup-row span{display:block}.setup-row strong{font-size:14px;font-weight:600}.setup-row span{margin-top:4px;font-size:12px;color:var(--text-secondary)}.readiness-panel{padding:26px 24px;background:#f8fafc;border-left:1px solid var(--border)}.readiness-panel h3{margin:0 0 16px;font-size:15px}.readiness-panel dl,.detail-list,.compact-list,.data-list,.result-meta{margin:0}.readiness-panel dl>div,.detail-list>div,.compact-list>div{display:flex;justify-content:space-between;gap:18px;padding:11px 0;border-bottom:1px solid var(--border)}dt{color:var(--text-secondary);font-size:12px}dd{margin:0;color:var(--text-primary);font-size:13px;font-weight:500}.readiness-panel p,.boundary-note{margin:16px 0 0;font-size:12px;line-height:1.65;color:var(--text-secondary)}.ok{color:var(--success)!important}.attention{color:var(--warning)!important}
.summary-band{display:grid;grid-template-columns:repeat(4,1fr);margin-bottom:14px;border:1px solid var(--border);border-radius:7px;background:#fff;overflow:hidden}.summary-band>div{position:relative;padding:15px 18px 15px 58px;border-right:1px solid var(--border)}.summary-band>div:last-child{border-right:0}.summary-band span,.summary-band strong,.summary-band small{display:block}.summary-band span{font-size:12px;color:var(--text-secondary)}.summary-band strong{margin:6px 0 2px;font-size:23px;font-weight:650;color:var(--text-primary)}.summary-band small{font-size:11px;color:var(--text-tertiary)}.summary-icon{position:absolute;left:18px;top:21px;display:grid;place-items:center;width:28px;height:28px;border-radius:6px;background:var(--primary-soft);color:var(--primary);font-size:17px}
.workspace-grid{display:grid;grid-template-columns:1.25fr .85fr .7fr;gap:14px;margin-bottom:14px}.workspace-grid>.n-card{height:100%}.risk-heading{display:flex;align-items:center;gap:10px;margin-bottom:8px}.risk-heading strong{font-size:15px}.detail-list>div{justify-content:flex-start}.detail-list dt{width:70px;flex:none}.plain-empty{min-height:150px;display:flex;flex-direction:column;align-items:flex-start;justify-content:center;gap:8px}.plain-empty strong{font-size:15px}.plain-empty span{font-size:12px;color:var(--text-secondary);line-height:1.6}.plain-empty.compact{min-height:210px;align-items:center}.action-list{display:grid;grid-template-columns:1fr 1fr;border:1px solid var(--border);border-radius:5px;overflow:hidden}.action-list button{padding:14px;text-align:left;border:0;border-right:1px solid var(--border);border-bottom:1px solid var(--border);background:#fff;cursor:pointer}.action-list button:nth-child(2n){border-right:0}.action-list button:nth-last-child(-n+2){border-bottom:0}.action-list button:hover{background:#f8fafc}.action-list span,.action-list b{display:block}.action-list span{font-size:12px;color:var(--text-secondary)}.action-list b{margin-top:7px;font-size:20px;font-weight:600}.compact-list>div{padding:9px 0}.danger{color:var(--danger)}.warning{color:var(--warning)}
.foundation-panel{margin-bottom:14px}.foundation-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));margin:0;border:1px solid var(--border);border-radius:6px;overflow:hidden}.foundation-grid>div{min-width:0;padding:12px 14px;border-right:1px solid var(--border)}.foundation-grid>div:last-child{border-right:0}.foundation-grid dd{margin-top:6px;font-size:17px;font-variant-numeric:tabular-nums;white-space:nowrap}.foundation-panel .boundary-note{margin-top:11px}
.result-row{display:grid;grid-template-columns:1.1fr .9fr;gap:14px;margin-bottom:14px}.result-preview{height:250px;display:flex;align-items:center;justify-content:center;background:#eef1f4;border:1px solid var(--border);overflow:hidden}.result-preview img{width:100%;height:100%;object-fit:contain}.result-meta{display:grid;grid-template-columns:1.4fr .7fr 1fr;margin-top:10px}.result-meta>div{min-width:0;padding:0 10px;border-right:1px solid var(--border)}.result-meta>div:first-child{padding-left:0}.result-meta>div:last-child{border-right:0}.result-meta dd{margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.analysis-row{display:grid;grid-template-columns:1.4fr 1fr;gap:14px}.analysis-row .chart{height:260px}
@media(max-width:1180px){.workspace-grid{grid-template-columns:1.2fr .8fr}.object-panel{grid-column:1/-1}.compact-list{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}.compact-list>div{border-bottom:0}.foundation-grid{grid-template-columns:repeat(4,1fr)}.foundation-grid>div{border-bottom:1px solid var(--border)}.foundation-grid>div:nth-child(4n){border-right:0}.foundation-grid>div:nth-last-child(-n+3){border-bottom:0}}
@media(max-width:1050px){.empty-workbench,.workspace-grid,.result-row,.analysis-row{grid-template-columns:1fr}.readiness-panel{border-left:0;border-top:1px solid var(--border)}.object-panel{grid-column:auto}.summary-band{grid-template-columns:repeat(2,1fr)}.summary-band>div:nth-child(2){border-right:0}.summary-band>div:nth-child(-n+2){border-bottom:1px solid var(--border)}}
@media(max-width:700px){.summary-band{grid-template-columns:1fr}.summary-band>div{border-right:0;border-bottom:1px solid var(--border)}.summary-band>div:last-child{border-bottom:0}.foundation-grid{grid-template-columns:repeat(2,1fr)}.foundation-grid>div:nth-child(odd){border-right:1px solid var(--border)}.foundation-grid>div:nth-child(even){border-right:0}.foundation-grid>div:nth-last-child(-n+3){border-bottom:1px solid var(--border)}.foundation-grid>div:last-child{border-bottom:0}}
</style>
