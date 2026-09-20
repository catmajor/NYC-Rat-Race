import { readFileSync } from 'node:fs'
import puppeteer from 'puppeteer-core'

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
const URL = process.argv[2] || 'http://127.0.0.1:8000/'
const OUT = 'C:\\Users\\kit\\AppData\\Local\\Temp\\opencode\\buildings-shot.png'

const watchdog = setTimeout(() => { console.error('TIMEOUT'); process.exit(1) }, 180000)

const browser = await puppeteer.launch({
  executablePath: EDGE,
  headless: 'new',
  args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--use-angle=swiftshader', '--force-device-scale-factor=1', '--window-size=1200,800', '--log-level=3'],
  defaultViewport: { width: 1200, height: 800 },
})
const page = await browser.newPage()
const consoleLog = []
page.on('console', (msg) => consoleLog.push(`[${msg.type()}] ${msg.text()}`))
page.on('pageerror', (err) => consoleLog.push(`[pageerror] ${err.message}`))
const net = { buildingsFetch: null, otherBad: [] }
page.on('response', (res) => {
  const u = res.url()
  if (u.includes('/data/buildings.geojson')) net.buildingsFetch = res.status()
  else if (res.status() >= 400) net.otherBad.push(`${res.status()} ${u}`)
})

await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 45000 })
await new Promise((r) => setTimeout(r, 8000))
console.log('buildings.geojson status:', net.buildingsFetch)

await page.evaluate(async () => {
  const start = Date.now()
  while (Date.now() - start < 60000) {
    const layers = window.__overlay?._deck?.layerManager?.layers ?? []
    if (layers.find((l) => l.id === 'buildings')) return { waitedFor: 'buildings-in-list' }
    await new Promise((r) => setTimeout(r, 500))
  }
  return { waitedFor: 'timeout-60s' }
}).then((r) => console.log('wait:', JSON.stringify(r)))

const step = (label) => async (at) => page.evaluate(async (arg) => {
  const { label, at } = arg
  const map = window.__map
  map.flyTo({ center: [at.lon, at.lat], zoom: at.zoom, pitch: 55, duration: 0 })
  await new Promise((r) => setTimeout(r, 7000))
  const layers = window.__overlay?._deck?.layerManager?.layers ?? []
  const bl = layers.find((l) => l.id === 'buildings')
  return {
    label,
    zoom: map.getZoom(),
    zoomRef: window.__zoomRefGet ? window.__zoomRefGet() : null,
    inList: !!bl,
    numInstances: bl?.numInstances ?? null,
    visible: bl?.props?.visible ?? null,
    elevationScale: bl?.props?.elevationScale ?? null,
    layerIds: layers.map((l) => l.id),
  }
}, { label, at })

const in1 = await step('initialz13')({ lon: -73.9857, lat: 40.7484, zoom: 13.4 })
console.log(JSON.stringify(in1))
const out = await step('zoomedOut')({ lon: -73.9857, lat: 40.7484, zoom: 10.8 })
console.log(JSON.stringify(out))
const back = await step('backIn')({ lon: -73.9857, lat: 40.7484, zoom: 13.4 })
console.log(JSON.stringify(back))

try {
  await page.screenshot({ path: OUT })
  console.log(`screenshot bytes: ${readFileSync(OUT).length}`)
} catch (err) {
  console.error('SCREENSHOT FAILED:', err.message)
}
console.log('--- console (all) ---')
console.log(consoleLog.slice(0, 80).join('\n') || '(none)')
clearTimeout(watchdog)
try { await browser.close() } catch {}
process.exit(0)