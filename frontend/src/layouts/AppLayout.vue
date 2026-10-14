<template>
  <div class="shell">
    <aside class="side" :class="{collapsed:navCollapsed,'hover-expanded':collapsed&&!navCollapsed}" @mouseenter="hoverExpanded=true" @mouseleave="hoverExpanded=false">
      <div class="brand"><div class="brand-mark" aria-hidden="true"><img src="/system-icon.png" alt=""></div><div><b>工程水害管理系统</b><small>监测 · 研判 · 作业</small></div></div>
      <n-menu :value="activeMenuKey" :collapsed="navCollapsed" :collapsed-width="64" :collapsed-icon-size="20" :options="menus" @update:value="(v:string)=>router.push(v)"/>
      <div class="scope"><i :class="serviceState"></i><b>{{serviceText}}</b><small v-if="serviceDetail">{{serviceDetail}}</small></div>
    </aside>
    <main class="main" :class="{collapsed:navCollapsed}">
      <header class="top">
        <div class="title-core"><div class="product-title"><h1>工程水害管理系统</h1><small>监测 · 研判 · 作业</small></div><n-select v-model:value="currentProjectId" class="project-switch" :options="projectOptions" clearable placeholder="当前项目：全部项目"/></div>
        <div class="top-wing right">
          <n-button class="menu-toggle" circle size="small" :title="collapsed?'固定展开导航':'恢复自动收起'" @click="collapsed=!collapsed"><template #icon><n-icon><MenuOutline/></n-icon></template></n-button>
          <span class="model-pill" :class="model?.loaded?'ready':'offline'" :title="model?.error||modelError">{{model?.loaded?'模型已加载':model||modelError?'模型未加载':'模型状态未知'}}</span>
          <span class="device-pill" :class="{on:model?.device}">{{model?.device?.toUpperCase()||'--'}}</span>
          <n-button class="theme-toggle" circle size="small" :title="mode==='dark'?'切换白色主题':'切换深海主题'" @click="toggle">
            <template #icon><n-icon><SunnyOutline v-if="mode==='dark'"/><MoonOutline v-else/></n-icon></template>
          </n-button>
        </div>
      </header>
      <section class="content">
        <div v-if="currentSection&&currentSection.items.length>1" class="module-bar">
          <nav class="module-tabs" :aria-label="`${currentSection.label}功能导航`">
            <button v-for="item in currentSection.items" :key="item.path" type="button" :class="{active:isModuleItemActive(item)}" @click="router.push(item.path)">
              <n-icon><component :is="item.icon"/></n-icon><span>{{item.label}}</span>
            </button>
          </nav>
        </div>
        <router-view/>
      </section>
    </main>
  </div>
</template>
<script setup lang="ts">
import{computed,h,inject,onBeforeUnmount,onMounted,ref}from'vue'
import{useRoute,useRouter}from'vue-router'
import{NIcon,type MenuOption}from'naive-ui'
import{GridOutline,ImagesOutline,ScanOutline,TimeOutline,AnalyticsOutline,SettingsOutline,SunnyOutline,MoonOutline,MenuOutline,DocumentTextOutline,ShieldCheckmarkOutline,MapOutline,ConstructOutline,ClipboardOutline,GitNetworkOutline,TrendingUpOutline,ServerOutline}from'@vicons/ionicons5'
import{api}from'../api'
import type{ConstructionProject,ModelStatus}from'../types'
import{themeKey}from'../composables/theme'
import{currentProjectId}from'../composables/projectContext'
const theme=inject(themeKey)
if(!theme)throw new Error('Theme context unavailable')
const{mode,toggle}=theme
const route=useRoute(),router=useRouter(),model=ref<ModelStatus|null>(null)
const projects=ref<ConstructionProject[]>([]),projectOptions=computed(()=>projects.value.map(x=>({label:`当前项目：${x.project_name}`,value:x.id})))
const modelError=ref('')
const serviceState=ref<'checking'|'ready'|'error'>('checking'),serviceDetail=ref('')
const serviceText=computed(()=>serviceState.value==='ready'?'系统服务正常':serviceState.value==='error'?'系统服务异常':'正在检查服务')
const collapsed=ref(true),hoverExpanded=ref(false)
const navCollapsed=computed(()=>collapsed.value&&!hoverExpanded.value)
const applyAutoCollapse=()=>{if(window.innerWidth<=992)collapsed.value=true}
const icon=(component:any)=>()=>h(NIcon,null,{default:()=>h(component)})
type ModuleItem={label:string;path:string;icon:any;matches?:string[]}
type ModuleSection={label:string;description:string;path:string;icon:any;matches:string[];items:ModuleItem[]}
const sections:ModuleSection[]=[
  {label:'项目管理',description:'项目建档、区域边界与设备位置统一管理',path:'/dashboard',icon:GridOutline,matches:['/dashboard','/workflow','/areas','/equipment'],items:[{label:'项目总览',path:'/dashboard',icon:GridOutline},{label:'实施流程',path:'/workflow',icon:GitNetworkOutline},{label:'项目与区域',path:'/areas',icon:MapOutline},{label:'设备与位置',path:'/equipment',icon:ConstructOutline}]},
  {label:'感知分析',description:'多源环境数据接入、积水识别与时空趋势推演',path:'/datasets',icon:ImagesOutline,matches:['/datasets','/inference','/compare','/forecast'],items:[{label:'多源数据',path:'/datasets',icon:ImagesOutline},{label:'积水识别',path:'/inference',icon:ScanOutline},{label:'变化分析',path:'/compare',icon:AnalyticsOutline},{label:'趋势推演',path:'/forecast',icon:TrendingUpOutline}]},
  {label:'风险作业',description:'风险研判、现场处置与智能辅助集中协同',path:'/risk',icon:ShieldCheckmarkOutline,matches:['/risk','/assistance','/ai'],items:[{label:'风险研判',path:'/risk',icon:ShieldCheckmarkOutline},{label:'安全作业',path:'/assistance',icon:ClipboardOutline},{label:'智能助手',path:'/ai',icon:DocumentTextOutline}]},
  {label:'成果档案',description:'任务过程、分析成果与模型运行状态统一归档',path:'/history',icon:TimeOutline,matches:['/history','/results','/model'],items:[{label:'任务成果',path:'/history',icon:TimeOutline,matches:['/history','/results']},{label:'模型状态',path:'/model',icon:AnalyticsOutline}]},
  {label:'系统管理',description:'本地数据、运行参数、服务连接与诊断',path:'/data-management',icon:ServerOutline,matches:['/data-management','/settings'],items:[{label:'数据管理',path:'/data-management',icon:ServerOutline},{label:'系统配置',path:'/settings',icon:SettingsOutline}]}
]
const matchesPath=(path:string,prefix:string)=>path===prefix||path.startsWith(`${prefix}/`)
const currentSection=computed(()=>sections.find(section=>section.matches.some(prefix=>matchesPath(route.path,prefix))))
const activeMenuKey=computed(()=>currentSection.value?.path||route.path)
const isModuleItemActive=(item:ModuleItem)=>(item.matches||[item.path]).some(prefix=>matchesPath(route.path,prefix))
const menus:MenuOption[]=sections.map(section=>({label:section.label,key:section.path,icon:icon(section.icon)}))
const wait=(ms:number)=>new Promise(resolve=>setTimeout(resolve,ms))
const loadSystemStatus=async()=>{try{const health=await api.health();serviceState.value=health.status==='ok'?'ready':'error';serviceDetail.value=health.status==='ok'?'':`状态：${health.status}`}catch(error:any){serviceState.value='error';serviceDetail.value=error?.message||'无法连接本地服务'}try{model.value=await api.model();modelError.value=model.value.error||''}catch(error:any){model.value=null;modelError.value=error?.message||'模型状态接口不可用'}}
const loadModel=async()=>{for(let attempt=0;attempt<5;attempt++){await loadSystemStatus();if(serviceState.value==='ready')return;if(attempt<4)await wait(1500)}}
onMounted(async()=>{applyAutoCollapse();window.addEventListener('resize',applyAutoCollapse);void loadModel();try{projects.value=(await api.projects()).filter(x=>x.status==='active');if(currentProjectId.value&&!projects.value.some(x=>x.id===currentProjectId.value))currentProjectId.value=null}catch{projects.value=[]}})
onBeforeUnmount(()=>window.removeEventListener('resize',applyAutoCollapse))
</script>
