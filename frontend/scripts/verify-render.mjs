// Headless verification: loads the app, captures console errors, tracks network
// responses (esp. Carto tiles), screenshots the page, and reports DOM metrics.
// Uses the system Edge binary via puppeteer-core.
import { readFileSync } from 'node:fs'
import puppeteer from 'puppeteer-core'

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
const URL = process.argv[2] || 'http://127.0.0.1:8000/'
const OUT = 'C:\\Users\\kit\\AppData\\Local\\Temp\\opencode\\mapshot.png'

const watchdog = setTimeout(() => {
  console.error('TIMEOUT: watchdog fired (90s deadline exceeded)')
  process.exit(1)
}, 90000)

let browser
try {
  browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: 'new',
    args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--use-angle=swiftshader', '--force-device-scale-factor=1', '--window-size=1000,750'],
    defaultViewport: { width: 1000, height: 750 },
  })
} catch (err) {
  console.error('LAUNCH FAILED:', err.message)
  process.exit(1)
}

const page = await browser.newPage()
const consoleLog = []
const badResponses = []
const tiles = { requested: 0, ok: 0, failed: 0 }
page.on('console', (msg) => consoleLog.push(`[${msg.type()}] ${msg.text()}`))
page.on('pageerror', (err) => consoleLog.push(`[pageerror] ${err.message}`))
page.on('requestfailed', (req) => {
  consoleLog.push(`[failed ${req.failure()?.errorText}] ${req.url()}`)
  if (req.url().includes('cartocdn.com')) tiles.failed++
})
page.on('response', (res) => {
  const url = res.url()
  if (res.status() >= 400) badResponses.push(`[${res.status()}] ${url}`)
  if (url.includes('cartocdn.com')) {
    tiles.requested++
    if (res.status() < 400) tiles.ok++
  }
})

await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 45000 })
console.log('waiting…')
await new Promise((r) => setTimeout(r, 9000))

const dom = await page.evaluate(() => {
  const q = (s) => document.querySelector(s)
  const rect = (el) => (el ? el.getBoundingClientRect().toJSON() : null)
  const el = q('.map-container')
  const style = el ? window.getComputedStyle(el) : null
  return {
    win: [window.innerWidth, window.innerHeight],
    docH: document.documentElement.clientHeight,
    mapCount: document.querySelectorAll('.map-container').length,
    mapClass: el ? el.className : null,
    mapStyle: el
      ? {
          position: style.position,
          top: style.top,
          bottom: style.bottom,
          left: style.left,
          right: style.right,
          display: style.display,
          height: style.height,
        }
      : null,
    appRect: rect(q('.app')),
    mapRect: rect(el),
    canvas: q('.maplibregl-canvas') ? [q('.maplibregl-canvas').width, q('.maplibregl-canvas').height] : null,
    deck: q('#deckgl-overlay') ? [q('#deckgl-overlay').width, q('#deckgl-overlay').height] : null,
    wrapStyle: q('.deck-widget-container') ? q('.deck-widget-container').getAttribute('style') : null,
    hud: Array.from(document.querySelectorAll('.hud-value')).map((e) => e.textContent),
    hudSubs: Array.from(document.querySelectorAll('.hud-sub')).map((e) => e.textContent),
  }
})
console.log('DOM:', JSON.stringify(dom, null, 2))

try {
  await page.screenshot({ path: OUT })
  console.log(`screenshot bytes: ${readFileSync(OUT).length}`)
} catch (err) {
  console.error('SCREENSHOT FAILED:', err.message)
}

console.log('tiles:', JSON.stringify(tiles))
console.log('--- bad responses ---')
console.log(badResponses.slice(0, 20).join('\n') || '(none)')
console.log('--- console ---')
console.log(consoleLog.slice(0, 60).join('\n') || '(none)')

clearTimeout(watchdog)
try { await browser.close() } catch {}
process.exit(0)