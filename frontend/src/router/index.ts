import {createRouter,createWebHistory} from 'vue-router'
import AppLayout from '../layouts/AppLayout.vue'
const routes=[{path:'/',component:AppLayout,children:[
 {path:'',redirect:'/dashboard'},{path:'dashboard',component:()=>import('../views/DashboardView.vue'),meta:{title:'项目总览'}},{path:'workflow',component:()=>import('../views/WorkflowView.vue'),meta:{title:'实施流程'}},{path:'areas',component:()=>import('../views/AreasView.vue'),meta:{title:'项目与区域'}},{path:'equipment',component:()=>import('../views/EquipmentView.vue'),meta:{title:'设备与位置'}},
 {path:'datasets',component:()=>import('../views/DatasetsView.vue'),meta:{title:'多源数据'}},{path:'inference',component:()=>import('../views/InferenceView.vue'),meta:{title:'积水识别'}},
 {path:'forecast',component:()=>import('../views/ForecastView.vue'),meta:{title:'趋势推演'}},
 {path:'results/:id',component:()=>import('../views/ResultView.vue'),meta:{title:'识别成果'}},{path:'compare',component:()=>import('../views/CompareView.vue'),meta:{title:'变化分析'}},{path:'risk',component:()=>import('../views/RiskView.vue'),meta:{title:'风险研判'}},{path:'model',component:()=>import('../views/ModelView.vue'),meta:{title:'模型状态'}},
 {path:'assistance',component:()=>import('../views/AssistanceView.vue'),meta:{title:'安全作业'}},
 {path:'ai',component:()=>import('../views/AIView.vue'),meta:{title:'智能助手'}},{path:'history',component:()=>import('../views/HistoryView.vue'),meta:{title:'任务成果'}},{path:'data-management',component:()=>import('../views/DataManagementView.vue'),meta:{title:'数据管理'}},{path:'settings',component:()=>import('../views/SettingsView.vue'),meta:{title:'系统配置'}}]}]
export default createRouter({history:createWebHistory(),routes})
