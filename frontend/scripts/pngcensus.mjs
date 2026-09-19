// Decodes a screenshot PNG in-page (data URI -> 2D canvas -> getImageData) to
// census the ACTUAL painted pixels: light = basemap tiles, colored = zones/rat.
import { readFileSync } from 'node:fs'
import puppeteer from 'puppeteer-core'

const PNG = process.argv[2]
const b64 = readFileSync(PNG).toString('base64')

const browser = await puppeteer.launch({
  executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  headless: 'new',
  args: ['--no-sandbox'],
})
const page = await browser.newPage()
await page.setContent('<div id="out"></div>')
const result = await page.evaluate(async (src) => {
  const img = new Image()
  img.src = 'data:image/png;base64,' + src
  await img.decode()
  const cv = document.createElement('canvas')
  cv.width = img.naturalWidth
  cv.height = img.naturalHeight
  const ctx = cv.getContext('2d', { willReadFrequently: true })
  ctx.drawImage(img, 0, 0)
  const { width: W, height: H } = cv
  const data = ctx.getImageData(0, 0, W, H).data
  const buckets = { light: 0, med: 0, dark: 0, colored: 0 }
  const avg = [0, 0, 0]
  const n = W * H
  for (let i = 0; i < data.length; i += 4) {
    const r = data[i], g = data[i + 1], b = data[i + 2]
    const s = r + g + b
    avg[0] += r; avg[1] += g; avg[2] += b
    if (s > 600) buckets.light++
    else if (s > 130) buckets.med++
    else buckets.dark++
    if (Math.max(r, g, b) - Math.min(r, g, b) > 40) buckets.colored++
  }
  return {
    size: [W, H],
    ...buckets,
    avg: avg.map((v) => Math.round(v / n)),
    lightPct: Math.round((10000 * buckets.light) / n) / 100,
    coloredPct: Math.round((10000 * buckets.colored) / n) / 100,
    darkPct: Math.round((10000 * buckets.dark) / n) / 100,
  }
}, b64)
console.log(JSON.stringify(result, null, 2))
await browser.close()
process.exit(0)