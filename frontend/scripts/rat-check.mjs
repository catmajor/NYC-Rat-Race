// Headless rat-color check: flies the map to the rat, zooms in, screenshots,
// and reports a pixel census around the rat to detect PBR baseColorFactor colors.
import { readFileSync } from 'node:fs'
import puppeteer from 'puppeteer-core'

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
const URL = process.argv[2] || 'http://127.0.0.1:8000/'
const OUT = 'C:\\Users\\kit\\AppData\\Local\\Temp\\opencode\\ratshot.png'

// Fixed, known rat pose for orientation calibration.
const RAT = { lon: -73.985, lat: 40.7508, heading: 90 }

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
page.on('console', (msg) => consoleLog.push(`[${msg.type()}] ${msg.text()}`))
page.on('pageerror', (err) => consoleLog.push(`[pageerror] ${err.message}`))

await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 45000 })
console.log('waiting for boot…')
await new Promise((r) => setTimeout(r, 9000))

const rat = await page.evaluate((fixed) => {
  const map = window.__map
  if (!map) return { ok: false, hasMap: !!map }
  // Pin the rat to a known pose + heading so orientation is inspectable.
  window.__ratOverride = { lon: fixed.lon, lat: fixed.lat, heading: fixed.heading }
  map.flyTo({ center: [fixed.lon, fixed.lat], zoom: 15, duration: 0 })
  return { ok: true, pose: fixed }
}, RAT)
console.log('rat:', JSON.stringify(rat))

await new Promise((r) => setTimeout(r, 5000))

const matrix = await page.evaluate(() => {
  const deck = window.__overlay?._deck
  const layer = deck?.getLayers?.().find?.((l) => l.id === 'rat')
  const a = layer?.getAttributeManager?.()
  if (!a) return { ok: false, note: 'no rat layer attributes' }
  const grab = (name) => Array.from(a.attributes[name].value ?? []).map((v) => +v.toFixed(6))
  const m = {
    col0: grab('instanceModelMatrixCol0'),
    col1: grab('instanceModelMatrixCol1'),
    col2: grab('instanceModelMatrixCol2'),
    translation: grab('instanceTranslation'),
  }
  // Expected: proper rotation mapping model -x -> heading (sin h, cos h, 0),
  // model +y -> world up (0,0,1). With heading 90 = east: col_x = (-1,0,0),
  // col_y = (0,0,1), col_z = (0,1,0).
  const exp = [[-1, 0, 0], [0, 0, 1], [0, 1, 0]]
  const cols = [m.col0, m.col1, m.col2]
  const pass = cols.every((c, i) => c.every((v, k) => Math.abs(v - exp[i][k]) < 1e-4))
  return { ok: true, ...m, expected: exp, pass }
})
console.log('instanceMatrix:', JSON.stringify(matrix, null, 2))

try {
  await page.screenshot({ path: OUT })
  console.log(`screenshot bytes: ${readFileSync(OUT).length}`)
} catch (err) {
  console.error('SCREENSHOT FAILED:', err.message)
}

const census = await page.evaluate(async (b64) => {
  const readRegion = (img, x0, y0, w, h) => {
    const c = document.createElement('canvas')
    c.width = w
    c.height = h
    const ctx = c.getContext('2d')
    ctx.drawImage(img, x0, y0, w, h, 0, 0, w, h)
    const px = ctx.getImageData(0, 0, w, h).data
    let light = 0, colored = 0, dark = 0
    const acc = { r: 0, g: 0, b: 0 }
    for (let i = 0; i < px.length; i += 4) {
      const r = px[i], g = px[i + 1], b = px[i + 2]
      acc.r += r; acc.g += g; acc.b += b
      const maxc = Math.max(r, g, b)
      if (maxc > 200 && maxc - Math.min(r, g, b) < 25) light++
      else if (maxc < 60) dark++
      else if (maxc - Math.min(r, g, b) > 30) colored++
    }
    const n = px.length / 4
    return {
      n,
      lightPct: (light / n * 100).toFixed(1),
      coloredPct: (colored / n * 100).toFixed(1),
      darkPct: (dark / n * 100).toFixed(1),
      avg: [acc.r / n, acc.g / n, acc.b / n].map((v) => Math.round(v)),
    }
  }
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = () => {
      // central 60% of the viewport = rat close-up region
      const w = img.width, h = img.height
      resolve(readRegion(img, Math.round(w * 0.2), Math.round(h * 0.2), Math.round(w * 0.6), Math.round(h * 0.6)))
    }
    img.onerror = () => resolve({ error: 'image decode failed' })
    img.src = `data:image/png;base64,${b64}`
  })
}, readFileSync(OUT).toString('base64'))
console.log('census:', JSON.stringify(census, null, 2))
console.log('--- console ---')
console.log(consoleLog.slice(0, 60).join('\n') || '(none)')

clearTimeout(watchdog)
try { await browser.close() } catch {}
process.exit(0)