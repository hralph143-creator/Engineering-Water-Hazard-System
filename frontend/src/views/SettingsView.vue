<template>
  <div class="page-head">
    <div><h2>系统配置</h2><span class="muted">配置运行路径、分析规则、服务连接、诊断与备份。</span></div>
    <span class="muted">保存后生效；敏感密钥仅在本机后端加密保存。</span>
  </div>
  <n-spin :show="loading">
    <div v-if="s" class="two-col settings-grid">
      <n-card class="settings-panel" title="模型与存储">
        <div class="path-editor-list">
          <label class="path-editor"><span>模型权重路径</span><n-input v-model:value="form.model_checkpoint" placeholder="请选择本机模型权重文件" clearable /></label>
          <label class="path-editor"><span>SAR 预处理统计</span><n-input v-model:value="form.sar_stats" placeholder="请选择 SAR 预处理统计文件" clearable /></label>
          <label class="path-editor"><span>识别输出目录</span><n-input v-model:value="form.output_directory" placeholder="请选择可写的成果输出目录" clearable /></label>
          <label class="path-editor"><span>上传目录</span><n-input v-model:value="form.upload_directory" placeholder="请选择可写的数据上传目录" clearable /></label>
          <label class="path-editor"><span>SQLite 数据库</span><n-input v-model:value="form.database_path" placeholder="请选择 SQLite 数据库文件" clearable /></label>
        </div>
        <n-alert type="info" style="margin-top:14px">上传目录和识别输出目录保存后用于新操作；模型权重、预处理统计和数据库路径需重启后端服务后完全切换。</n-alert>
      </n-card>

      <n-card class="settings-panel" title="识别与基础水害提示规则">
        <div class="risk-form-grid">
          <div class="risk-row two"><div class="risk-item"><span class="risk-label">当前积水·进入关注（复用基础提示阈值二）</span><span class="risk-device">{{s.risk.medium}}</span></div><div class="risk-item"><span class="risk-label">当前积水·高关注（复用基础提示阈值三）</span><span class="risk-device">{{s.risk.high}}</span></div></div>
          <div class="risk-row two">
            <div class="risk-item"><span class="risk-label">默认识别阈值</span><n-input-number v-model:value="form.threshold" :step="0.05" :min="0.01" :max="0.99" style="width:100%" /></div>
            <div class="risk-item"><span class="risk-label">设备策略</span><span class="risk-device">{{ s.device }}</span></div>
          </div>
          <div class="risk-row three">
            <div class="risk-item"><span class="risk-label">提示阈值一（安全 / 低风险）</span><n-input-number v-model:value="form.low" :step="0.01" :min="0" :max="1" style="width:100%" /></div>
            <div class="risk-item"><span class="risk-label">提示阈值二（低 / 中风险）</span><n-input-number v-model:value="form.medium" :step="0.01" :min="0" :max="1" style="width:100%" /></div>
            <div class="risk-item"><span class="risk-label">提示阈值三（中 / 高风险）</span><n-input-number v-model:value="form.high" :step="0.01" :min="0" :max="1" style="width:100%" /></div>
          </div>
        </div>
        <n-alert type="warning" style="margin-top:16px">{{ s.risk_note }}</n-alert>
        <n-alert type="info" style="margin-top:12px">当前正式支持 Sentinel-1 SAR、DEM、人工/CSV降雨观测及无人机GeoTIFF正射影像管理与人工协同复核；无人机自动AI识别、实时视频与飞控不在当前能力范围。</n-alert>
      </n-card>

      <n-card class="settings-panel rule-settings-panel" title="辅助研判规则参数">
        <div class="version-row"><span>规则版本</span><code>{{s.risk_rules.version}}</code></div>
        <div class="risk-form-grid">
          <div class="risk-row two"><div class="risk-item"><span class="risk-label">新增积水·进入关注</span><n-input-number v-model:value="form.risk_new_attention" :step="0.01" :min="0" :max="1"/></div><div class="risk-item"><span class="risk-label">新增积水·高关注</span><n-input-number v-model:value="form.risk_new_high" :step="0.01" :min="0" :max="1"/></div></div>
          <div class="risk-row two"><div class="risk-item"><span class="risk-label">持续积水·进入关注</span><n-input-number v-model:value="form.risk_persistent_attention" :step="0.01" :min="0" :max="1"/></div><div class="risk-item"><span class="risk-label">持续积水·高关注</span><n-input-number v-model:value="form.risk_persistent_high" :step="0.01" :min="0" :max="1"/></div></div>
          <div class="risk-row two"><div class="risk-item"><span class="risk-label">净变化·扩大阈值</span><n-input-number v-model:value="form.risk_net_expansion" :step="0.01" :min="0.001" :max="1"/></div><div class="risk-item"><span class="risk-label">净变化·缩小阈值</span><n-input-number v-model:value="form.risk_net_reduction" :step="0.01" :min="-1" :max="-0.001"/></div></div>
        </div>
        <n-alert type="warning" style="margin-top:14px">{{s.risk_rules_note}} 当前积水因素复用上方基础水害提示阈值二、阈值三；变化因素使用本区域参数。</n-alert>
      </n-card>

      <n-card class="settings-panel rule-settings-panel" title="作业区域空间影响规则">
        <div class="version-row"><span>算法版本</span><code>{{s.work_area_impact.version}}</code></div>
        <div class="risk-row two">
          <div class="risk-item"><span class="risk-label">高水害影响阈值（高关注占比）</span><n-input-number v-model:value="form.impact_high_attention_ratio" :step="0.01" :min="0" :max="1"/></div>
          <div class="risk-item"><span class="risk-label">重点巡检影响阈值（巡检占比）</span><n-input-number v-model:value="form.impact_inspection_ratio" :step="0.01" :min="0" :max="1"/></div>
        </div>
        <n-alert type="warning" style="margin-top:14px">{{s.work_area_impact_note}}</n-alert>
      </n-card>

      <n-card class="settings-panel rule-settings-panel" title="设备位置快照配置">
        <div class="version-row"><span>关联规则版本</span><code>{{s.equipment_position.version}}</code></div>
        <div class="risk-row two"><div class="risk-item"><span class="risk-label">位置过期阈值（小时）</span><n-input-number v-model:value="form.position_stale_hours" :step="1" :min="0.1" :max="8760"/></div><div class="risk-item"><span class="risk-label">能力边界</span><span class="risk-device">位置快照，不代表实时定位</span></div></div>
        <n-alert type="warning" style="margin-top:14px">{{s.equipment_position_note}}</n-alert>
      </n-card>

      <n-card class="settings-panel rule-settings-panel" title="地形分析参数">
        <div class="version-row"><span>地形规则版本</span><code>{{s.terrain.version}}</code></div>
        <div class="risk-row two"><div class="risk-item"><span class="risk-label">低高程分位阈值（%）</span><n-input-number v-model:value="form.low_elevation_percentile" :step="1" :min="0.1" :max="99.9"/></div><div class="risk-item"><span class="risk-label">坡度标准</span><span class="risk-device">度（°），仅米制投影DEM</span></div></div>
        <n-alert type="warning" style="margin-top:14px">{{s.terrain_note}}</n-alert>
      </n-card>

      <n-card class="settings-panel rule-settings-panel" title="降雨事件参数">
        <div class="version-row"><span>事件规则版本</span><code>{{s.rainfall.version}}</code></div>
        <div class="risk-row two"><div class="risk-item"><span class="risk-label">事件无雨间隔（小时）</span><n-input-number v-model:value="form.rain_event_gap_hours" :step="1" :min="0.1" :max="168"/></div><div class="risk-item"><span class="risk-label">变化分析前置窗口（小时）</span><n-input-number v-model:value="form.change_rainfall_pre_window_hours" :step="1" :min="0" :max="720"/></div></div>
        <n-alert type="warning" style="margin-top:14px">{{s.rainfall_note}}</n-alert>
      </n-card>

      <n-card class="settings-panel ai-settings-panel" title="分析服务">
        <div class="ai-form-grid">
          <div class="ai-switch-row">
            <div><b>启用外部分析服务</b><p class="muted">连接兼容 OpenAI Chat Completions 协议的模型服务</p></div>
            <n-switch v-model:value="form.ai_enabled" />
          </div>
          <label class="path-editor"><span>接口地址</span><n-input v-model:value="form.ai_base_url" placeholder="例如 https://api.openai.com/v1" clearable /></label>
          <label class="path-editor"><span>模型名称</span><n-input v-model:value="form.ai_model" placeholder="填写服务商提供的模型 ID" clearable /></label>
          <label class="path-editor">
            <span>API Key <n-tag size="small" :type="s.ai.key_configured ? 'success' : 'warning'">{{ s.ai.key_configured ? `已配置 ${s.ai.key_hint || ''}` : '未配置' }}</n-tag></span>
            <n-input v-model:value="form.ai_api_key" type="password" show-password-on="mousedown" :placeholder="s.ai.key_configured ? '留空则保留当前 Key' : '请输入 API Key'" clearable autocomplete="new-password" />
          </label>
          <div class="ai-number-row">
            <label class="risk-item"><span class="risk-label">请求超时（秒）</span><n-input-number v-model:value="form.ai_timeout" :min="5" :max="300" style="width:100%" /></label>
            <label class="risk-item"><span class="risk-label">生成温度</span><n-input-number v-model:value="form.ai_temperature" :step="0.1" :min="0" :max="2" style="width:100%" /></label>
            <label class="risk-item"><span class="risk-label">最大输出 Token</span><n-input-number v-model:value="form.ai_max_output_tokens" :step="128" :min="128" :max="8192" style="width:100%" /></label>
          </div>
          <n-checkbox v-model:checked="form.ai_clear_api_key" :disabled="!s.ai.key_configured">清除当前已保存的 API Key</n-checkbox>
        </div>
        <n-alert type="info" style="margin-top:14px">Key 只发送到本机后端，并使用 Windows DPAPI 按当前用户加密保存；接口永不返回 Key 明文。保存本机配置后以页面配置为准，无本机配置时才读取环境变量。</n-alert>
      </n-card>

      <n-card class="settings-panel export-settings-panel" title="字段与数据导出">
        <n-spin :show="exportCatalogLoading">
          <div v-if="exportCatalog" class="export-toolbar">
            <div class="export-summary">
              <b>数据库业务分区</b>
              <span>已选 {{ selectedTables.length }} / {{ exportCatalog.table_count }} 张表</span>
              <span>{{ selectedRecordCount }} 条记录</span>
              <span>{{ selectedFieldCount }} 个字段</span>
            </div>
            <div class="export-controls">
              <n-radio-group v-model:value="exportMode" size="small">
                <n-radio-button value="all">字段与数据</n-radio-button>
                <n-radio-button value="data">仅数据</n-radio-button>
                <n-radio-button value="dictionary">仅字段字典</n-radio-button>
              </n-radio-group>
              <n-button size="small" @click="selectAllTables">全选</n-button>
              <n-button size="small" @click="selectedTables=[]">清空</n-button>
              <n-button type="primary" :loading="exporting" :disabled="!selectedTables.length" @click="exportXlsx">导出 Excel</n-button>
            </div>
          </div>
          <div v-if="exportCatalog" class="export-groups">
            <section v-for="group in exportCatalog.groups" :key="group.key" class="export-group">
              <div class="export-group-head">
                <n-checkbox
                  :checked="isGroupSelected(group)"
                  :indeterminate="isGroupPartial(group)"
                  @update:checked="toggleGroup(group, $event)"
                >{{ group.label }}</n-checkbox>
                <span>{{ group.tables.length }} 张表</span>
              </div>
              <div class="export-table-list">
                <label v-for="table in group.tables" :key="table.name" class="export-table-row">
                  <n-checkbox :checked="selectedTables.includes(table.name)" @update:checked="toggleTable(table.name, $event)" />
                  <span class="export-table-name">{{ table.label }}</span>
                  <code>{{ table.name }}</code>
                  <span>{{ table.field_count }} 字段</span>
                  <span>{{ table.row_count }} 记录</span>
                </label>
              </div>
            </section>
          </div>
          <div v-if="exportCatalog" class="simulation-toolbar">
            <div class="simulation-title">
              <b>模拟数据生成</b>
              <span>按当前所选数据表生成全字段测试数据，不写入正式数据库</span>
            </div>
            <label><span>每表行数</span><n-input-number v-model:value="simulationRows" :min="1" :max="1000" /></label>
            <label><span>随机种子</span><n-input-number v-model:value="simulationSeed" :min="0" :max="2147483647" /></label>
            <n-button type="error" ghost :loading="simulating" :disabled="!selectedTables.length" @click="simulateXlsx">生成模拟 Excel</n-button>
          </div>
          <n-alert v-if="exportCatalogError" type="error">{{ exportCatalogError }}</n-alert>
        </n-spin>
      </n-card>

      <n-card class="settings-panel rule-settings-panel" title="系统诊断与数据保护">
        <n-spin :show="systemLoading">
          <div v-if="diagnostic" class="diagnostic-grid">
            <div><span>总体状态</span><n-tag :type="diagnostic.status==='ok'?'success':'warning'">{{diagnostic.status==='ok'?'运行正常':'需要关注'}}</n-tag></div>
            <div><span>数据库</span><b>{{diagnostic.checks.database.status}}</b></div>
            <div><span>前端资源</span><b>{{diagnostic.checks.frontend.status}}</b></div>
            <div><span>模型文件</span><b>{{diagnostic.checks.model.status}}</b></div>
            <div><span>AI 服务</span><b>{{diagnostic.checks.ai.status}}</b></div>
            <div><span>可用磁盘</span><b>{{(diagnostic.checks.disk.free_bytes/1073741824).toFixed(1)}} GB</b></div>
          </div>
          <n-space style="margin-top:16px">
            <n-button @click="loadDiagnostic">重新检查</n-button><n-button tag="a" :href="api.diagnosticsUrl('txt')">导出 TXT</n-button><n-button tag="a" :href="api.diagnosticsUrl('json')">导出 JSON</n-button>
            <n-button type="primary" @click="backup(false)">快速备份</n-button><n-button @click="backup(true)">生成完整备份清单</n-button><n-button @click="checkArtifacts">校验成果文件</n-button>
          </n-space>
          <n-alert v-if="operationResult" :type="operationResultType" style="margin-top:14px">{{operationResult}}</n-alert>
        </n-spin>
      </n-card>

      <div class="settings-actions">
        <n-alert v-if="error" type="error">{{ error }}</n-alert>
        <n-space><n-button type="primary" :loading="saving" @click="save">保存全部设置</n-button><n-button :disabled="saving" @click="reset">重置</n-button></n-space>
      </div>
    </div>
  </n-spin>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { api } from '../api'
import type { DataExportCatalog, DataExportGroup, Settings } from '../types'

const s = ref<Settings | null>(null)
const loading = ref(true), saving = ref(false), error = ref(''), msg = useMessage()
const diagnostic=ref<any>(null),systemLoading=ref(false),operationResult=ref(''),operationResultType=ref<'success'|'warning'>('success')
const exportCatalog=ref<DataExportCatalog|null>(null),exportCatalogLoading=ref(false),exportCatalogError=ref(''),exporting=ref(false)
const selectedTables=ref<string[]>([]),exportMode=ref<'all'|'data'|'dictionary'>('all')
const simulationRows=ref(20),simulationSeed=ref(20261014),simulating=ref(false)
const selectedTableObjects=computed(()=>exportCatalog.value?.groups.flatMap(group=>group.tables).filter(table=>selectedTables.value.includes(table.name))??[])
const selectedRecordCount=computed(()=>selectedTableObjects.value.reduce((total,table)=>total+table.row_count,0))
const selectedFieldCount=computed(()=>selectedTableObjects.value.reduce((total,table)=>total+table.field_count,0))
const selectAllTables=()=>{selectedTables.value=exportCatalog.value?.groups.flatMap(group=>group.tables.map(table=>table.name))??[]}
const toggleTable=(name:string,checked:boolean)=>{selectedTables.value=checked?[...new Set([...selectedTables.value,name])]:selectedTables.value.filter(item=>item!==name)}
const isGroupSelected=(group:DataExportGroup)=>group.tables.length>0&&group.tables.every(table=>selectedTables.value.includes(table.name))
const isGroupPartial=(group:DataExportGroup)=>group.tables.some(table=>selectedTables.value.includes(table.name))&&!isGroupSelected(group)
const toggleGroup=(group:DataExportGroup,checked:boolean)=>{const names=group.tables.map(table=>table.name);selectedTables.value=checked?[...new Set([...selectedTables.value,...names])]:selectedTables.value.filter(name=>!names.includes(name))}
const loadExportCatalog=async()=>{exportCatalogLoading.value=true;exportCatalogError.value='';try{exportCatalog.value=await api.dataExportCatalog();selectAllTables()}catch(e:any){exportCatalogError.value=e.message||'数据库字段目录加载失败'}finally{exportCatalogLoading.value=false}}
const exportXlsx=async()=>{if(!selectedTables.value.length)return;exporting.value=true;try{const result=await api.exportDataXlsx({table_names:selectedTables.value,include_data:exportMode.value!=='dictionary',include_field_dictionary:exportMode.value!=='data'});const url=URL.createObjectURL(result.blob);const link=document.createElement('a');link.href=url;link.download=result.filename;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);msg.success('Excel 数据文件已生成')}catch(e:any){msg.error(e.message||'Excel 导出失败')}finally{exporting.value=false}}
const downloadBlob=(blob:Blob,filename:string)=>{const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=filename;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}
const simulateXlsx=async()=>{if(!selectedTables.value.length)return;simulating.value=true;try{const result=await api.simulateDataXlsx({table_names:selectedTables.value,rows_per_table:simulationRows.value,seed:simulationSeed.value});downloadBlob(result.blob,result.filename);msg.success('模拟数据工作簿已生成')}catch(e:any){msg.error(e.message||'模拟数据生成失败')}finally{simulating.value=false}}
const loadDiagnostic=async()=>{systemLoading.value=true;try{diagnostic.value=await api.diagnostics()}catch(e:any){msg.error(e.message||'系统诊断失败')}finally{systemLoading.value=false}}
const backup=async(full:boolean)=>{systemLoading.value=true;try{const r=full?await api.manifestBackup():await api.quickBackup();operationResultType.value='success';operationResult.value=`备份已完成：${r.path}`}catch(e:any){operationResultType.value='warning';operationResult.value=e.message||'备份失败'}finally{systemLoading.value=false}}
const checkArtifacts=async()=>{systemLoading.value=true;try{const r=await api.artifactIntegrity();operationResultType.value=r.missing?'warning':'success';operationResult.value=`已检查 ${r.checked} 个成果文件：正常 ${r.ok}，缺失 ${r.missing}` }catch(e:any){operationResultType.value='warning';operationResult.value=e.message||'成果校验失败'}finally{systemLoading.value=false}}
const defaultAI = { enabled: false, base_url: 'https://api.openai.com/v1', model: '', key_configured: false, timeout: 60, temperature: 0.2, max_output_tokens: 1600 }
const defaultRules = {version:'water-risk-v1',new_attention:.01,new_high:.05,persistent_attention:.02,persistent_high:.08,net_expansion:.01,net_reduction:-.01}
const defaultImpact = {version:'work-area-impact-v1',high_attention_ratio:.05,inspection_ratio:.10}
const defaultEquipmentPosition = {version:'equipment-water-link-v1',position_stale_hours:24}
const defaultTerrain = {version:'terrain-analysis-v1',low_elevation_percentile:20}
const defaultRainfall = {version:'rainfall-event-v1',rain_event_gap_hours:6,change_rainfall_pre_window_hours:24}
const normalizeSettings = (value: Settings): Settings => ({ ...value, ai: value.ai ?? defaultAI, risk_rules:value.risk_rules??defaultRules, risk_rules_note:value.risk_rules_note??'当前参数用于科研与工程场景验证，不属于行业强制安全标准。',work_area_impact:value.work_area_impact??defaultImpact,work_area_impact_note:value.work_area_impact_note??'区域影响等级仅反映空间叠加结果，不等同于综合施工安全等级。',equipment_position:value.equipment_position??defaultEquipmentPosition,equipment_position_note:value.equipment_position_note??'设备位置过期阈值仅用于位置快照时效判断，不属于安全法规阈值。',terrain:value.terrain??defaultTerrain,terrain_note:value.terrain_note??'低高程分位参数只用于相对地形分区，不属于行业水害或安全标准。',rainfall:value.rainfall??defaultRainfall,rainfall_note:value.rainfall_note??'降雨事件参数仅用于数据组织和辅助分析，不属于气象预警或工程停工标准。' })
const form = ref({
  threshold: 0.5, low: 0.02, medium: 0.08, high: 0.2,
  model_checkpoint: '', sar_stats: '', output_directory: '', upload_directory: '', database_path: '',
  ai_enabled: false, ai_base_url: 'https://api.openai.com/v1', ai_model: '', ai_api_key: '',
  ai_clear_api_key: false, ai_timeout: 60, ai_temperature: 0.2, ai_max_output_tokens: 1600,
  risk_new_attention:.01,risk_new_high:.05,risk_persistent_attention:.02,risk_persistent_high:.08,risk_net_expansion:.01,risk_net_reduction:-.01,
  impact_high_attention_ratio:.05,impact_inspection_ratio:.10,
  position_stale_hours:24,
  low_elevation_percentile:20,
  rain_event_gap_hours:6,change_rainfall_pre_window_hours:24,
})
const sync = () => {
  if (!s.value) return
  form.value = {
    threshold: s.value.default_threshold, low: s.value.risk.low, medium: s.value.risk.medium, high: s.value.risk.high,
    model_checkpoint: s.value.model_checkpoint, sar_stats: s.value.sar_stats,
    output_directory: s.value.output_directory, upload_directory: s.value.upload_directory,
    database_path: s.value.database_path, ai_enabled: s.value.ai.enabled,
    ai_base_url: s.value.ai.base_url, ai_model: s.value.ai.model, ai_api_key: '',
    ai_clear_api_key: false, ai_timeout: s.value.ai.timeout,
    ai_temperature: s.value.ai.temperature, ai_max_output_tokens: s.value.ai.max_output_tokens,
    risk_new_attention:s.value.risk_rules.new_attention,risk_new_high:s.value.risk_rules.new_high,
    risk_persistent_attention:s.value.risk_rules.persistent_attention,risk_persistent_high:s.value.risk_rules.persistent_high,
    risk_net_expansion:s.value.risk_rules.net_expansion,risk_net_reduction:s.value.risk_rules.net_reduction,
    impact_high_attention_ratio:s.value.work_area_impact.high_attention_ratio,impact_inspection_ratio:s.value.work_area_impact.inspection_ratio,
    position_stale_hours:s.value.equipment_position.position_stale_hours,
    low_elevation_percentile:s.value.terrain.low_elevation_percentile,
    rain_event_gap_hours:s.value.rainfall.rain_event_gap_hours,change_rainfall_pre_window_hours:s.value.rainfall.change_rainfall_pre_window_hours,
  }
}
onMounted(async () => {
  void loadDiagnostic()
  void loadExportCatalog()
  try { s.value = normalizeSettings(await api.settings()); sync() }
  catch (e: any) { msg.error(e.message || '设置加载失败') }
  finally { loading.value = false }
})
const reset = () => { sync(); error.value = ''; msg.info('已恢复为当前保存的设置值') }
const save = async () => {
  error.value = ''
  const values = form.value
  if (!(values.threshold > 0 && values.threshold < 1)) { error.value = '识别阈值必须满足 0 < 阈值 < 1'; return }
  if (!(values.low >= 0 && values.low < values.medium && values.medium < values.high && values.high <= 1)) { error.value = '风险阈值必须满足 0 ≤ 低 < 中 < 高 ≤ 1'; return }
  if (!(values.risk_new_attention>=0&&values.risk_new_attention<values.risk_new_high)) { error.value='新增积水研判阈值顺序无效'; return }
  if (!(values.risk_persistent_attention>=0&&values.risk_persistent_attention<values.risk_persistent_high)) { error.value='持续积水研判阈值顺序无效'; return }
  if (!(values.risk_net_reduction<0&&values.risk_net_expansion>0)) { error.value='净变化研判阈值顺序无效'; return }
  if (!(values.impact_high_attention_ratio>=0&&values.impact_high_attention_ratio<=1&&values.impact_inspection_ratio>=0&&values.impact_inspection_ratio<=1)) { error.value='作业区域影响阈值必须处于 0 到 1 之间'; return }
  if (!(values.position_stale_hours>0&&values.position_stale_hours<=8760)) { error.value='设备位置过期阈值必须大于0且不超过8760小时'; return }
  if (!(values.low_elevation_percentile>0&&values.low_elevation_percentile<100)) { error.value='低高程分位阈值必须位于0到100之间'; return }
  if (!(values.rain_event_gap_hours>0&&values.rain_event_gap_hours<=168&&values.change_rainfall_pre_window_hours>=0&&values.change_rainfall_pre_window_hours<=720)) { error.value='降雨事件参数超出允许范围'; return }
  const requiredPaths: Array<[keyof typeof values, string]> = [['model_checkpoint', '模型权重路径'], ['sar_stats', 'SAR 预处理统计路径'], ['output_directory', '识别输出目录'], ['upload_directory', '上传目录'], ['database_path', 'SQLite 数据库路径']]
  for (const [key, label] of requiredPaths) { if (!String(values[key]).trim()) { error.value = `${label}不能为空`; return } }
  if (!/^https?:\/\//i.test(values.ai_base_url.trim())) { error.value = 'AI 接口地址必须以 http:// 或 https:// 开头'; return }
  if (values.ai_enabled && !values.ai_model.trim()) { error.value = '启用 AI 前请填写模型名称'; return }
  if (values.ai_enabled && !values.ai_api_key.trim() && (!s.value?.ai.key_configured || values.ai_clear_api_key)) { error.value = '启用 AI 前请配置 API Key'; return }
  saving.value = true
  try {
    const payload = {
      ...values,
      model_checkpoint: values.model_checkpoint.trim(), sar_stats: values.sar_stats.trim(),
      output_directory: values.output_directory.trim(), upload_directory: values.upload_directory.trim(),
      database_path: values.database_path.trim(), ai_base_url: values.ai_base_url.trim(),
      ai_model: values.ai_model.trim(), ai_api_key: values.ai_api_key.trim() || undefined,
    }
    s.value = normalizeSettings(await api.updateSettings(payload))
    msg.success('系统与 AI 配置已保存')
    sync()
  } catch (e: any) { error.value = e.message || '保存设置失败' }
  finally { saving.value = false }
}
</script>

<style scoped>
.settings-panel:nth-child(-n+3),.ai-settings-panel,.export-settings-panel,.settings-panel:nth-child(10),.settings-actions{grid-column:1/-1}.ai-form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px 18px}.ai-switch-row{grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;padding:4px 0}.ai-switch-row p{margin:4px 0 0}.ai-number-row{grid-column:1/-1;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.diagnostic-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.diagnostic-grid>div{display:flex;justify-content:space-between;align-items:center;padding:13px;border-radius:6px;background:var(--surface-soft)}.export-toolbar{display:flex;align-items:center;justify-content:space-between;gap:18px;padding-bottom:16px;border-bottom:1px solid var(--border)}.export-summary,.export-controls{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.export-summary>span{color:var(--muted);font-variant-numeric:tabular-nums}.export-groups{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0;border:1px solid var(--border);border-radius:6px;overflow:hidden;margin-top:16px}.export-group{min-width:0;border-right:1px solid var(--border);border-bottom:1px solid var(--border)}.export-group:nth-child(3n){border-right:0}.export-group:nth-last-child(-n+3){border-bottom:0}.export-group-head{display:flex;align-items:center;justify-content:space-between;padding:11px 13px;background:var(--surface-soft);border-bottom:1px solid var(--border)}.export-group-head>span,.export-table-row>span:last-child,.export-table-row>span:nth-last-child(2){color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}.export-table-list{padding:4px 0}.export-table-row{display:grid;grid-template-columns:22px minmax(86px,1fr) minmax(120px,1.4fr) 68px 68px;align-items:center;gap:8px;padding:8px 12px;cursor:pointer}.export-table-row:hover{background:var(--surface-soft)}.export-table-name{font-weight:600}.export-table-row code{overflow:hidden;text-overflow:ellipsis;color:var(--muted);font-size:12px}.simulation-toolbar{display:grid;grid-template-columns:minmax(260px,1fr) 150px 190px auto;gap:14px;align-items:end;margin-top:16px;padding:16px;border:1px solid var(--border);border-left:3px solid #9c2f2f;border-radius:6px;background:var(--surface-soft)}.simulation-title{display:flex;flex-direction:column;gap:5px}.simulation-title>span,.simulation-toolbar label>span{color:var(--muted)}.simulation-toolbar label{display:flex;flex-direction:column;gap:6px}.settings-actions{display:flex;flex-direction:column;gap:12px}@media(max-width:1200px){.export-groups{grid-template-columns:repeat(2,minmax(0,1fr))}.export-group:nth-child(3n){border-right:1px solid var(--border)}.export-group:nth-child(2n){border-right:0}.export-group:nth-last-child(-n+3){border-bottom:1px solid var(--border)}.export-group:nth-last-child(-n+2){border-bottom:0}.simulation-toolbar{grid-template-columns:1fr 150px 190px}}@media(max-width:900px){.settings-panel{grid-column:1/-1!important}.ai-form-grid,.ai-number-row,.diagnostic-grid,.export-groups{grid-template-columns:1fr}.ai-switch-row,.ai-number-row{grid-column:auto}.export-toolbar{align-items:flex-start;flex-direction:column}.export-group{border-right:0!important;border-bottom:1px solid var(--border)!important}.export-group:last-child{border-bottom:0!important}.export-table-row{grid-template-columns:22px minmax(90px,1fr) 70px 70px}.export-table-row code{display:none}.simulation-toolbar{grid-template-columns:1fr}}
</style>
