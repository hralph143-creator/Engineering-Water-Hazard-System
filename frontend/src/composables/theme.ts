import type { InjectionKey, Ref } from 'vue'
export type ThemeMode = 'dark' | 'light'
export interface ThemeContext { mode: Ref<ThemeMode>; toggle: () => void }
export const themeKey: InjectionKey<ThemeContext> = Symbol('theme-mode')
