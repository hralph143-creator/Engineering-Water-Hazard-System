import { spawn } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const port = 9333
const profile = await mkdtemp(join(tmpdir(), 'floodrisk-ui-audit-'))
const browser = spawn(chrome, [
  '--headless', '--no-sandbox', '--disable-gpu', `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, 'about:blank'
], { stdio: 'ignore' })

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
async function debuggerTarget() {
  for (let i = 0; i < 30; i++) {
    try {
      const targets = await fetch(`http://127.0.0.1:${port}/json`).then(r => r.json())
      const page = targets.find(target => target.type === 'page')
      if (page) return page
    } catch {}
    await sleep(200)
  }
  throw new Error('Chrome DevTools connection failed')
}

const target = await debuggerTarget()
const ws = new WebSocket(target.webSocketDebuggerUrl)
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject })
let sequence = 0
const pending = new Map()
let browserErrors = []
ws.onmessage = event => {
  const message = JSON.parse(event.data)
  if (message.id && pending.has(message.id)) {
    pending.get(message.id)(message)
    pending.delete(message.id)
  }
  if (message.method === 'Runtime.exceptionThrown') browserErrors.push(`exception: ${message.params.exceptionDetails.text}`)
  if (message.method === 'Log.entryAdded' && ['error', 'warning'].includes(message.params.entry.level)) browserErrors.push(`log: ${message.params.entry.text}`)
  if (message.method === 'Network.responseReceived' && message.params.response.status >= 400) browserErrors.push(`http ${message.params.response.status}: ${message.params.response.url}`)
}
const send = (method, params = {}) => new Promise(resolve => {
  const id = ++sequence
  pending.set(id, resolve)
  ws.send(JSON.stringify({ id, method, params }))
})
await send('Runtime.enable'); await send('Log.enable'); await send('Network.enable'); await send('Page.enable')

const tasks = await fetch('http://127.0.0.1:8001/api/results').then(r => r.json())
const resultId = tasks.data.find(task => task.result)?.result.id
const routes = ['/dashboard', '/datasets', '/inference', `/results/${resultId}`, '/model', '/history', '/data-management', '/settings']
const sizes = [[1920, 1080], [1600, 900], [1366, 768]]
const report = []
for (const [width, height] of sizes) {
  await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false })
  for (const route of routes) {
    browserErrors = []
    await send('Page.navigate', { url: `http://127.0.0.1:5173${route}` })
    await sleep(2200)
    const evaluation = await send('Runtime.evaluate', { returnByValue: true, expression: `(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      brokenImages: [...document.images].filter(i => !i.complete || i.naturalWidth === 0).length,
      suspiciousText: /undefined|NaN/.test(document.body.innerText),
      title: document.querySelector('.top h1')?.textContent || ''
    }))()` })
    report.push({ size: `${width}x${height}`, route, ...evaluation.result.result.value, errors: [...new Set(browserErrors)] })
  }
}
await send('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false })
await send('Page.navigate', { url: 'http://127.0.0.1:5173/dashboard' })
await sleep(1800)
const beforeTheme = await send('Runtime.evaluate', { returnByValue: true, expression: `({ light: document.documentElement.classList.contains('theme-light'), button: !!document.querySelector('.theme-toggle') })` })
await send('Runtime.evaluate', { expression: `document.querySelector('.theme-toggle')?.click()` })
await sleep(800)
const afterTheme = await send('Runtime.evaluate', { returnByValue: true, expression: `({ light: document.documentElement.classList.contains('theme-light'), stored: localStorage.getItem('flood-risk-theme'), overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth, bodyBackground: getComputedStyle(document.body).backgroundColor, cardBackground: getComputedStyle(document.querySelector('.n-card')).backgroundColor })` })
console.log(JSON.stringify({ pages: report, themeToggle: { before: beforeTheme.result.result.value, after: afterTheme.result.result.value } }, null, 2))
ws.close(); browser.kill(); await sleep(500)
try { await rm(profile, { recursive: true, force: true }) } catch {}
