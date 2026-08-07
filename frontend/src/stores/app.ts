import { defineStore } from 'pinia'
import type { AuthSession } from '../types'

const SESSION_KEY = 'solochef-session'
const RUN_KEY = 'solochef-run-id'

function initialSession(): AuthSession | null {
  try { return JSON.parse(localStorage.getItem(SESSION_KEY) || 'null') as AuthSession | null }
  catch { return null }
}

export const useAppStore = defineStore('app', {
  state: () => ({
    session: initialSession(),
    sidebarOpen: false,
    lastRunId: sessionStorage.getItem(RUN_KEY) || '',
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.session?.access_token),
    userName: (state) => state.session?.user.display_name || '用户',
  },
  actions: {
    toggleSidebar() { this.sidebarOpen = !this.sidebarOpen },
    closeSidebar() { this.sidebarOpen = false },
    rememberRun(id: string) { this.lastRunId = id; sessionStorage.setItem(RUN_KEY, id) },
    setSession(session: AuthSession) { this.session = session; localStorage.setItem(SESSION_KEY, JSON.stringify(session)) },
    logout() { this.session = null; this.lastRunId = ''; localStorage.removeItem(SESSION_KEY); sessionStorage.removeItem(RUN_KEY) },
  },
})
