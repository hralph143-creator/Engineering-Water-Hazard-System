import { spawn } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const port = 9341
const profile = await mkdtemp(join(tmpdir(), 'floodrisk-dashboard-check-'))
const browser = spawn(chrome, ['--headless', '--no-sandbox', '--disable-gpu', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' })
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

try {
  let target
  for (let i = 0; i < 30 && !target; i++) {
    try { target = (await fetch(`http://127.0.0.1:${port}/json`).then(r => r.json())).find(x => x.type === 'page') } catch {}
    if (!target) await sleep(200)
  }
  if (!target) throw new Error('Chrome DevTools connection failed')
  const ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject })
  let id = 0
  const pending = new Map()
  ws.onmessage = event => { const message = JSON.parse(event.data); if (message.id && pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id) } }
  const send = (method, params = {}) => new Promise(resolve => { const callId = ++id; pending.set(callId, resolve); ws.send(JSON.stringify({ id: callId, method, params })) })
  await send('Runtime.enable'); await send('Page.enable')
  const checks = []
  for (const [width, height] of [[1920, 1080], [1600, 900], [1366, 768]]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false })
    await send('Page.navigate', { url: 'http://127.0.0.1:5173/dashboard' })
    await sleep(2200)
    await send('Runtime.evaluate', { expression: `document.querySelector('.menu-toggle')?.click()` })
    await sleep(600)
    const result = await send('Runtime.evaluate', { returnByValue: true, expression: `(() => { const cards=[...document.querySelectorAll('.dashboard-mission > .n-card')].map(el=>{const r=el.getBoundingClientRect();return {left:Math.round(r.left),right:Math.round(r.right),top:Math.round(r.top),width:Math.round(r.width)}}); return {cards,sameRow:cards.length===3&&cards.every(x=>Math.abs(x.top-cards[0].top)<3),insideViewport:cards.every(x=>x.right<=innerWidth),horizontalOverflow:document.documentElement.scrollWidth>document.documentElement.clientWidth}; })()` })
    checks.push({ size: `${width}x${height}`, ...result.result.result.value })
  }
  console.log(JSON.stringify(checks, null, 2))
  ws.close()
} finally {
  browser.kill()
  await sleep(300)
  await rm(profile, { recursive: true, force: true }).catch(() => {})
}
