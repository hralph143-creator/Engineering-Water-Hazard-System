<template>
  <div class="page-head">
    <div><h2>智能助手</h2><span class="muted">基于已有识别统计生成参考说明和补充查询结果。</span></div>
    <n-tag :type="status?.available?'success':'warning'">{{status?.available?'服务可用':status?.message||'正在检查服务'}}</n-tag>
  </div>
  <n-alert v-if="status&&!status.available" type="warning" style="margin-bottom:16px">
    {{status.message}}。请前往“系统设置 → 分析服务”完成配置；密钥不会在页面中回显。
  </n-alert>
  <div class="ai-grid">
    <n-card title="分析对象" class="ai-source">
      <n-select v-model:value="taskId" :options="taskOptions" filterable placeholder="选择一个已完成任务" />
      <div v-if="selected" class="ai-task-summary">
        <div><span>任务编号</span><b>{{selected.id}}</b></div>
        <div><span>数据名称</span><b>{{selected.dataset_name}}</b></div>
        <div><span>完成时间</span><b>{{fmt(selected.finished_at)}}</b></div>
        <div><span>积水占比</span><b>{{pct(selected.result?.flood_ratio)}}</b></div>
        <div><span>平均置信度</span><b>{{pct(selected.result?.mean_confidence)}}</b></div>
        <div><span>积水 / 有效像素</span><b>{{selected.result?.flood_pixel_count??'--'}} / {{selected.result?.valid_pixel_count??'--'}}</b></div>
        <div><span>高置信积水占比</span><b>{{pct(selected.result?.high_confidence_flood_ratio)}}</b></div>
        <div><span>基础水害提示</span><b>{{selected.result?.risk_level||'--'}}</b></div>
        <div><span>模型 / 阈值</span><b>{{selected.model_name}} / {{selected.threshold}}</b></div>
      </div>
      <n-empty v-else description="尚未选择已完成任务" size="small" style="margin-top:28px"/>
    </n-card>

    <n-card title="分析结果" class="ai-analysis" content-style="display:flex;flex-direction:column;min-height:0">
      <template #header-extra>
        <n-space>
          <n-button v-if="analysisRunning" size="small" @click="stop">停止生成</n-button>
          <n-button v-else size="small" :disabled="!canUse" @click="generate">{{analysis?'重新生成':'生成分析'}}</n-button>
        </n-space>
      </template>
      <div class="analysis-scroll">
        <div v-if="analysis" class="markdown-body" v-html="safeMarkdown(analysis)"></div>
        <div v-else class="ai-empty"><b>暂无分析结果</b><span>选择已完成任务后，可根据已有统计生成参考说明。</span></div>
        <n-skeleton v-if="analysisRunning&&!analysis" text :repeat="5" />
      </div>
      <p class="ai-disclaimer">智盾助手内容仅供辅助研判，不替代现场检查、工程安全鉴定或施工单位正式安全决策。</p>
    </n-card>

    <n-card title="补充查询" class="ai-chat">
      <template #header-extra><n-button text size="small" :disabled="!messages.length" @click="clearChat">清空记录</n-button></template>
      <div class="prompt-list">
        <n-button v-for="prompt in prompts" :key="prompt" size="tiny" @click="question=prompt">{{prompt}}</n-button>
      </div>
      <div class="chat-list">
        <div v-if="!messages.length" class="ai-empty compact"><span>可围绕当前任务提交补充问题；切换任务后记录自动隔离。</span></div>
        <div v-for="(item,index) in messages" :key="index" :class="['chat-entry',item.role]">
          <small>{{item.role==='user'?'查询':'分析结果'}}</small>
          <div v-if="item.role==='assistant'" class="markdown-body" v-html="safeMarkdown(item.content)"></div>
          <p v-else>{{item.content}}</p>
        </div>
      </div>
      <n-input v-model:value="question" type="textarea" :maxlength="2000" show-count :autosize="{minRows:2,maxRows:5}" placeholder="输入关于当前识别结果的问题" @keydown.ctrl.enter.prevent="send" />
      <div class="chat-actions"><span>Ctrl + Enter 发送</span><n-space><n-button v-if="chatRunning" @click="stop">停止</n-button><n-button type="primary" :disabled="!canSend" :loading="chatRunning" @click="send">发送</n-button></n-space></div>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import{computed,onBeforeUnmount,onMounted,ref,watch}from'vue'
import{useRoute}from'vue-router'
import{useMessage}from'naive-ui'
import{api,streamTaskAI}from'../api'
import type{AIMessage,AIStatus,Task}from'../types'
import{formatDateTime}from'../utils/format'
import{safeMarkdown}from'../utils/markdown'

const route=useRoute(),notice=useMessage()
const tasks=ref<Task[]>([]),taskId=ref<string|null>(null),status=ref<AIStatus|null>(null)
const analysis=ref(''),messages=ref<AIMessage[]>([]),question=ref('')
const analysisRunning=ref(false),chatRunning=ref(false)
let controller:AbortController|null=null
let streamVersion=0
const prompts=['概括本次积水识别的主要发现','哪些结论需要现场复核？','当前数据有哪些限制？','如何理解积水占比和基础水害提示？']
const taskOptions=computed(()=>tasks.value.filter(item=>item.status==='completed'&&item.result).map(item=>({label:`${item.dataset_name} · ${fmt(item.finished_at)}`,value:item.id})))
const selected=computed(()=>tasks.value.find(item=>item.id===taskId.value)||null)
const canUse=computed(()=>Boolean(taskId.value&&status.value?.available&&!analysisRunning.value&&!chatRunning.value))
const canSend=computed(()=>Boolean(canUse.value&&question.value.trim()))
const fmt=(value:string|null|undefined)=>value?formatDateTime(value):'--'
const pct=(value:number|null|undefined)=>value==null?'--':`${(value*100).toFixed(2)}%`
// 提示词模式升级后使用新会话空间，避免旧的重复回答继续参与上下文。
const storageKey=(id:string)=>`floodrisk-ai-chat-v3:${id}`
const saveChat=(id=taskId.value)=>{if(id)sessionStorage.setItem(storageKey(id),JSON.stringify(messages.value))}
const restoreChat=(id:string|null)=>{if(!id){messages.value=[];return}try{const value=JSON.parse(sessionStorage.getItem(storageKey(id))||'[]');messages.value=Array.isArray(value)?value.slice(-12):[]}catch{messages.value=[]}}
const stop=()=>{streamVersion++;controller?.abort();controller=null;analysisRunning.value=false;chatRunning.value=false}
const runStream=async(body:{task_id:string;mode:'analysis'|'chat';question:string;messages?:AIMessage[];existing_analysis?:string},append:(text:string)=>void,version:number)=>{const ownController=new AbortController();controller=ownController;try{await streamTaskAI(body,text=>{if(version===streamVersion)append(text)},ownController.signal)}catch(error:any){if(version===streamVersion&&error?.name!=='AbortError')notice.error(error.message||'AI 生成失败')}finally{if(version===streamVersion){controller=null;analysisRunning.value=false;chatRunning.value=false}}}
const generate=async()=>{if(!taskId.value||!canUse.value)return;const version=++streamVersion;analysis.value='';analysisRunning.value=true;await runStream({task_id:taskId.value,mode:'analysis',question:'请生成本次识别结果的专业分析。'},text=>analysis.value+=text,version)}
const send=async()=>{const value=question.value.trim(),activeTask=taskId.value;if(!activeTask||!value||!canSend.value)return;const version=++streamVersion,history=messages.value.slice(-10);messages.value.push({role:'user',content:value},{role:'assistant',content:''});question.value='';chatRunning.value=true;const index=messages.value.length-1;await runStream({task_id:activeTask,mode:'chat',question:value,messages:history,existing_analysis:analysis.value||undefined},text=>{if(taskId.value===activeTask&&messages.value[index])messages.value[index].content+=text},version);if(version===streamVersion&&taskId.value===activeTask){if(!messages.value[index]?.content)messages.value.splice(index,1);saveChat()}}
const clearChat=()=>{if(!taskId.value)return;messages.value=[];sessionStorage.removeItem(storageKey(taskId.value))}
watch(taskId,(value,old)=>{if(old)saveChat(old);stop();analysis.value='';restoreChat(value)})
onMounted(async()=>{try{[status.value,tasks.value]=await Promise.all([api.aiStatus(),api.tasks({status:'completed'})]);const query=typeof route.query.task==='string'?route.query.task:null;taskId.value=tasks.value.some(item=>item.id===query)?query:(tasks.value[0]?.id||null)}catch(error:any){notice.error(error.message||'加载 AI 分析页面失败')}})
onBeforeUnmount(()=>{saveChat();stop()})
</script>

<style scoped>
.ai-grid{display:grid;grid-template-columns:minmax(260px,.72fr) minmax(420px,1.15fr) minmax(360px,1fr);gap:16px;align-items:stretch}.ai-source,.ai-analysis,.ai-chat{height:calc(100vh - 138px);min-height:590px}.ai-task-summary{margin-top:16px}.ai-task-summary>div{display:flex;flex-direction:column;gap:4px;padding:10px 0;border-bottom:1px solid #e5e7eb}.ai-task-summary span,.chat-actions>span{font-size:12px;color:#667085}.ai-task-summary b{font-size:13px;font-weight:500;overflow-wrap:anywhere}.ai-empty{min-height:320px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:6px;color:#98a2b3}.ai-empty b{color:#667085;font-weight:600}.ai-empty.compact{min-height:100px}.analysis-scroll{flex:1;min-height:0;overflow-y:auto;padding-right:8px;scrollbar-gutter:stable}.analysis-scroll,.chat-list{scrollbar-width:thin;scrollbar-color:#b8c0cc #f2f4f7}.markdown-body{font-size:14px;line-height:1.7;overflow-wrap:anywhere}.markdown-body :deep(h3),.markdown-body :deep(h4),.markdown-body :deep(h5){margin:18px 0 8px;font-size:15px}.markdown-body :deep(p){margin:7px 0}.markdown-body :deep(ul),.markdown-body :deep(ol){margin:8px 0;padding-left:22px}.markdown-body :deep(code){padding:1px 5px;border-radius:4px;background:#f2f4f7}.ai-disclaimer{flex:none;margin:12px 0 0;padding-top:12px;border-top:1px solid #e5e7eb;font-size:12px;color:#667085}.prompt-list{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}.chat-list{height:calc(100vh - 365px);min-height:290px;overflow-y:auto;padding:0 6px 10px 0;scrollbar-gutter:stable}.chat-entry{margin:0;padding:12px 0;border-bottom:1px solid #e5e7eb}.chat-entry.user{background:#f8fafc;padding:12px}.chat-entry small{display:block;margin-bottom:6px;color:#667085;font-weight:600}.chat-entry p{margin:0;white-space:pre-wrap}.chat-actions{display:flex;align-items:center;justify-content:space-between;margin-top:10px}@media(max-width:1280px){.ai-grid{grid-template-columns:280px 1fr}.ai-chat{grid-column:1/-1;height:auto;min-height:480px}.chat-list{height:320px}}@media(max-width:900px){.ai-grid{grid-template-columns:1fr}.ai-source,.ai-analysis,.ai-chat{height:auto;min-height:auto}.ai-chat{grid-column:auto}.analysis-scroll{height:440px;flex:none}.chat-list{height:340px}}
</style>
