import {ref,watch} from 'vue'

const storageKey='zhiyushield.currentProjectId'
export const currentProjectId=ref<string|null>(localStorage.getItem(storageKey)||null)
watch(currentProjectId,value=>value?localStorage.setItem(storageKey,value):localStorage.removeItem(storageKey))
