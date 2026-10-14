<template>
  <div class="page-head">
    <div><h2>数据管理</h2></div>
    <n-space><n-button :loading="loading" @click="load">刷新统计</n-button><n-button @click="$router.push('/settings')">数据导出</n-button></n-space>
  </div>

  <n-spin :show="loading">
    <template v-if="catalog">
      <section class="database-summary">
        <div><span>业务分区</span><strong>{{catalog.groups.length}}</strong></div>
        <div><span>数据表</span><strong>{{catalog.table_count}}</strong></div>
        <div><span>本地记录</span><strong>{{catalog.record_count}}</strong></div>
        <div><span>字段总数</span><strong>{{catalog.field_count}}</strong></div>
      </section>

      <section class="catalog-workspace">
        <nav class="group-list" aria-label="数据库业务分区">
          <button v-for="group in catalog.groups" :key="group.key" :class="{active:activeGroupKey===group.key}" @click="activeGroupKey=group.key">
            <span>{{group.label}}</span>
            <b>{{groupRecordCount(group)}}</b>
          </button>
        </nav>

        <div v-if="activeGroup" class="table-register">
          <header>
            <h3>{{activeGroup.label}}</h3>
            <span>{{activeGroup.tables.length}} 张表 · {{groupRecordCount(activeGroup)}} 条记录</span>
          </header>
          <div class="register-head"><span>数据表</span><span>数据库表名</span><span>字段</span><span>本地记录</span><span></span></div>
          <button v-for="table in activeGroup.tables" :key="table.name" class="register-row" @click="openFields(table)">
            <strong>{{table.label}}</strong>
            <code>{{table.name}}</code>
            <span>{{table.field_count}}</span>
            <b>{{table.row_count}}</b>
            <span class="open-mark">查看字段</span>
          </button>
        </div>
      </section>
    </template>
    <n-alert v-if="error" type="error">{{error}}</n-alert>
  </n-spin>

  <n-drawer v-model:show="fieldDrawer" :width="760" placement="right">
    <n-drawer-content v-if="selectedTable" :title="selectedTable.label" closable>
      <div class="drawer-summary"><code>{{selectedTable.name}}</code><span>{{selectedTable.row_count}} 条记录</span><span>{{selectedTable.field_count}} 个字段</span></div>
      <n-data-table :columns="fieldColumns" :data="selectedTable.fields" :pagination="false" :max-height="650"/>
    </n-drawer-content>
  </n-drawer>
</template>

<script setup lang="ts">
import{computed,onMounted,ref}from'vue'
import{api}from'../api'
import type{DataExportCatalog,DataExportGroup,DataExportTable}from'../types'

const catalog=ref<DataExportCatalog|null>(null),loading=ref(false),error=ref(''),activeGroupKey=ref(''),selectedTable=ref<DataExportTable|null>(null),fieldDrawer=ref(false)
const activeGroup=computed(()=>catalog.value?.groups.find(group=>group.key===activeGroupKey.value)||catalog.value?.groups[0]||null)
const groupRecordCount=(group:DataExportGroup)=>group.tables.reduce((sum,table)=>sum+table.row_count,0)
const fieldColumns=[{title:'字段名称',key:'name',width:210},{title:'中文含义',key:'label',width:210},{title:'数据类型',key:'type',width:190},{title:'约束',render:(row:any)=>row.nullable?'可为空':'必填',width:90}]
const openFields=(table:DataExportTable)=>{selectedTable.value=table;fieldDrawer.value=true}
const load=async()=>{loading.value=true;error.value='';try{catalog.value=await api.dataExportCatalog();if(!catalog.value.groups.some(group=>group.key===activeGroupKey.value))activeGroupKey.value=catalog.value.groups[0]?.key||''}catch(e:any){error.value=e.message||'本地数据库统计加载失败'}finally{loading.value=false}}
onMounted(load)
</script>

<style scoped>
.database-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:14px;border:1px solid var(--border);border-radius:6px;background:var(--surface);overflow:hidden}.database-summary>div{display:flex;align-items:center;justify-content:space-between;min-height:76px;padding:0 22px;border-right:1px solid var(--border)}.database-summary>div:last-child{border-right:0}.database-summary span{font-size:14px;color:var(--text-secondary)}.database-summary strong{font-size:25px;font-weight:650;color:var(--text-primary);font-variant-numeric:tabular-nums}.catalog-workspace{display:grid;grid-template-columns:230px minmax(0,1fr);min-height:610px;border:1px solid var(--border);border-radius:6px;background:var(--surface);overflow:hidden}.group-list{padding:10px 0;border-right:1px solid var(--border);background:var(--surface-subtle)}.group-list button{display:flex;align-items:center;justify-content:space-between;width:100%;min-height:46px;padding:0 18px;border:0;border-left:3px solid transparent;background:transparent;color:var(--text-secondary);font:inherit;font-size:14px;cursor:pointer}.group-list button:hover{background:var(--surface)}.group-list button.active{border-left-color:var(--primary);background:var(--surface);color:var(--text-primary);font-weight:600}.group-list b{min-width:34px;text-align:right;font-variant-numeric:tabular-nums}.table-register{min-width:0;padding:0 22px 24px}.table-register>header{display:flex;align-items:center;justify-content:space-between;min-height:70px;border-bottom:1px solid var(--border)}.table-register h3{margin:0;font-size:18px}.table-register>header span{font-size:14px;color:var(--text-secondary)}.register-head,.register-row{display:grid;grid-template-columns:minmax(150px,1fr) minmax(220px,1.4fr) 90px 110px 100px;align-items:center;gap:16px}.register-head{min-height:43px;padding:0 14px;border-bottom:1px solid var(--border);color:var(--text-secondary);font-size:13px;font-weight:600}.register-row{width:100%;min-height:58px;padding:0 14px;border:0;border-bottom:1px solid var(--border);background:transparent;color:var(--text-primary);text-align:left;cursor:pointer}.register-row:hover{background:var(--surface-subtle)}.register-row strong{font-size:14px}.register-row code{overflow:hidden;text-overflow:ellipsis;color:var(--text-secondary);font-size:13px}.register-row>span,.register-row>b{font-size:14px;font-variant-numeric:tabular-nums}.register-row>b{font-size:17px}.open-mark{color:var(--primary);font-weight:600}.drawer-summary{display:flex;align-items:center;gap:0;margin-bottom:16px;border:1px solid var(--border);border-radius:5px;overflow:hidden}.drawer-summary>*{padding:11px 14px;border-right:1px solid var(--border)}.drawer-summary>*:last-child{border-right:0}.drawer-summary code{flex:1;overflow:hidden;text-overflow:ellipsis}.drawer-summary span{font-size:14px;font-variant-numeric:tabular-nums}@media(max-width:900px){.database-summary{grid-template-columns:repeat(2,1fr)}.database-summary>div:nth-child(2){border-right:0}.database-summary>div:nth-child(-n+2){border-bottom:1px solid var(--border)}.catalog-workspace{grid-template-columns:1fr}.group-list{display:flex;overflow-x:auto;padding:0;border-right:0;border-bottom:1px solid var(--border)}.group-list button{width:auto;min-width:140px;border-left:0;border-bottom:3px solid transparent}.group-list button.active{border-left:0;border-bottom-color:var(--primary)}.register-head,.register-row{grid-template-columns:minmax(130px,1fr) minmax(170px,1fr) 70px 90px}.register-head>*:last-child,.register-row>*:last-child{display:none}}@media(max-width:620px){.database-summary{grid-template-columns:1fr}.database-summary>div{border-right:0;border-bottom:1px solid var(--border)!important}.database-summary>div:last-child{border-bottom:0!important}.table-register{padding:0 14px 18px}.register-head,.register-row{grid-template-columns:minmax(130px,1fr) 68px 78px}.register-head>*:nth-child(2),.register-row>*:nth-child(2){display:none}}
</style>
