<template><div ref="el" class="chart"></div></template>
<script setup lang="ts">
import{computed,inject,onBeforeUnmount,onMounted,ref,watch}from'vue';
import*as echarts from'echarts';
import{themeKey}from'../composables/theme';
const props=defineProps<{option:any}>();
const theme=inject(themeKey)
const isLight=computed(()=>theme?.mode.value==='light')
const el=ref<HTMLElement>();
let chart:echarts.ECharts;
let resizeObserver:ResizeObserver|undefined;
const resize=()=>chart?.resize();
const colors=computed(()=>isLight.value?['#2563EB','#3F8A63','#5B7FA3','#B7791F','#718096','#C2414B']:['#4F8FEF','#5DA37A','#7C91AE','#C58A32','#94A3B8','#D05C65']);
const axis=computed(()=>isLight.value?{axisLine:{lineStyle:{color:'#CAD4DF'}},axisTick:{lineStyle:{color:'#CAD4DF'}},axisLabel:{color:'#7A8897'},splitLine:{lineStyle:{color:'#E7EDF3'}}}:{axisLine:{lineStyle:{color:'rgba(108,160,178,.18)'}},axisTick:{lineStyle:{color:'rgba(108,160,178,.18)'}},axisLabel:{color:'#759eab'},splitLine:{lineStyle:{color:'rgba(108,160,178,.10)'}}});
const tooltip=computed(()=>isLight.value?{backgroundColor:'#FFFFFF',borderColor:'#D0D5DD',padding:[8,10],textStyle:{color:'#344054'},extraCssText:'box-shadow:none'}:{backgroundColor:'#18212F',borderColor:'#475569',padding:[8,10],textStyle:{color:'#E5E7EB'},extraCssText:'box-shadow:none'});
const textStyle=computed(()=>({color:isLight.value?'#7A8897':'#b6d1d9'}));
const legendText=computed(()=>isLight.value?'#5E6B7A':'#7fa5b2');
const axisWithTheme=(input:any)=>Array.isArray(input)?input.map(item=>({...axis.value,...item})):({...axis.value,...(input||{})});
const seriesWithTheme=(series:any[]=[])=>series.map((item,index)=>{if(item.type!=='line')return item;const{areaStyle:_areaStyle,...rest}=item;return{...rest,smooth:false,symbol:'circle',symbolSize:4,showSymbol:false,emphasis:{focus:'series'},lineStyle:{width:2,...(item.lineStyle||{})},itemStyle:{color:colors.value[index%colors.value.length],borderColor:isLight.value?'#FFFFFF':'#E5E7EB',borderWidth:1,...(item.itemStyle||{})}}});
const draw=()=>{if(!chart)return;const option=props.option||{};chart.setOption({backgroundColor:'transparent',animationDuration:250,textStyle:textStyle.value,color:colors.value,...option,series:seriesWithTheme(option.series),tooltip:{...tooltip.value,...(option.tooltip||{})},legend:{textStyle:{color:legendText.value},itemWidth:12,itemHeight:7,...(option.legend||{})},xAxis:axisWithTheme(option.xAxis),yAxis:axisWithTheme(option.yAxis)},true)};
// 监听容器而不只监听窗口，确保侧边栏展开、收起时图表同步重排。
onMounted(()=>{chart=echarts.init(el.value!);draw();resizeObserver=new ResizeObserver(resize);resizeObserver.observe(el.value!);window.addEventListener('resize',resize)});
watch(()=>props.option,draw,{deep:true});
watch(()=>theme?.mode.value,draw);
onBeforeUnmount(()=>{resizeObserver?.disconnect();window.removeEventListener('resize',resize);chart.dispose()});
</script>
