<template>
  <div class="page-head">
    <div><h2>实施流程</h2><span class="muted">从项目建档到作业辅助的真实数据链路</span></div>
    <n-tag v-if="workflow" type="info">完成度 {{workflow.completion_percent}}%</n-tag>
  </div>
  <n-card v-if="!currentProjectId" class="onboarding-panel">
    <div class="onboarding-content"><div class="onboarding-mark"><img src="/icons/shield-check.svg" alt=""></div><h3>选择项目后查看完整业务链路</h3><p>工作流会按当前项目汇总数据准备、积水识别、变化分析、风险研判及现场闭环状态。</p><div class="onboarding-steps"><div><b>项目与区域</b><span>确认施工对象及真实空间边界</span></div><div><b>分析与研判</b><span>形成可追溯的环境分析成果</span></div><div><b>核查与闭环</b><span>记录现场人工核查及处置结果</span></div></div><n-button type="primary" @click="router.push('/areas')">管理施工项目</n-button></div>
  </n-card>
  <n-spin v-else :show="loading">
    <n-card v-if="workflow" class="workflow-card">
      <div class="project-line"><div><b>{{workflow.project.name}}</b><span>{{workflow.project.code}}</span></div><n-progress type="line" :percentage="workflow.completion_percent" :show-indicator="false"/></div>
      <div class="workflow-chain">
        <button v-for="(step,index) in workflow.steps" :key="step.key" class="workflow-step" :class="step.status" @click="router.push(step.route)">
          <i>{{index+1}}</i><b>{{step.label}}</b><span>{{step.count}} 条</span><em>{{step.status==='completed'?'已完成':'待补充'}}</em>
        </button>
      </div>
      <n-alert v-if="workflow.next_step" type="warning" style="margin-top:18px">建议下一步：{{workflow.next_step.label}}。点击对应节点进入办理。</n-alert>
      <n-alert v-else type="success" style="margin-top:18px">当前项目已形成“数据—识别—变化—研判—作业辅助”闭环。</n-alert>
    </n-card>
    <n-card v-if="completeness" title="项目数据完整度" style="margin-top:16px">
      <div class="data-grid"><div v-for="item in completeness.items" :key="item.key" class="data-item"><n-tag :type="item.status==='ready'?'success':item.required?'error':'default'">{{item.status==='ready'?'已具备':item.required?'暂无数据':'可选，暂无数据'}}</n-tag><b>{{item.label}}</b><span>{{item.count}} 条</span><small>{{item.required?'核心数据':'增强数据'}}</small></div></div>
      <p class="muted">核心数据用于启动主要分析流程；增强数据缺失时系统保持可用，并明确标注能力限制。</p>
    </n-card>
  </n-spin>
</template>
<script setup lang="ts">
import{ref,watch}from'vue'
import{useMessage}from'naive-ui'
import{useRouter}from'vue-router'
import{api}from'../api'
import{currentProjectId}from'../composables/projectContext'
const router=useRouter(),message=useMessage(),loading=ref(false),workflow=ref<any>(null),completeness=ref<any>(null)
const load=async()=>{if(!currentProjectId.value){workflow.value=null;completeness.value=null;return}loading.value=true;try{[workflow.value,completeness.value]=await Promise.all([api.projectWorkflow(currentProjectId.value),api.projectCompleteness(currentProjectId.value)])}catch(e:any){message.error(e.message||'项目工作流加载失败')}finally{loading.value=false}}
watch(currentProjectId,load,{immediate:true})
</script>
<style scoped>
.project-line{display:grid;grid-template-columns:minmax(220px,.7fr) 1fr;align-items:center;gap:24px}.project-line div{display:flex;gap:12px;align-items:baseline}.project-line span,.workflow-step span,.workflow-step small{color:var(--text-secondary)}.workflow-chain{display:grid;grid-template-columns:repeat(4,minmax(130px,1fr));gap:10px;margin-top:18px}.workflow-step{position:relative;display:flex;flex-direction:column;align-items:flex-start;gap:7px;padding:14px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:inherit;text-align:left;cursor:pointer}.workflow-step:hover{background:var(--surface-subtle);border-color:var(--border-strong)}.workflow-step i{display:grid;place-items:center;width:24px;height:24px;border-radius:4px;background:#667085;color:#fff;font-style:normal}.workflow-step.completed i{background:var(--success)}.workflow-step em{font-size:12px;font-style:normal;color:var(--warning)}.workflow-step.completed em{color:var(--success)}.data-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:0;border:1px solid var(--border);border-radius:6px;overflow:hidden}.data-item{display:flex;flex-direction:column;gap:6px;padding:12px;border-right:1px solid var(--border);background:var(--surface)}.data-item:last-child{border-right:0}.data-item small{color:var(--text-secondary)}@media(max-width:1100px){.workflow-chain,.data-grid{grid-template-columns:repeat(2,1fr)}.data-item{border-bottom:1px solid var(--border)}}@media(max-width:700px){.project-line{grid-template-columns:1fr}.workflow-chain,.data-grid{grid-template-columns:1fr}}
</style>
