import puppeteer from 'puppeteer-core'
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
const URL = process.argv[2] || 'http://127.0.0.1:8000/'
const browser = await puppeteer.launch({
  executablePath: EDGE,
  headless: 'new',
  args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--use-angle=swiftshader', '--force-device-scale-factor=1', '--window-size=1200,750'],
  defaultViewport: { width: 1200, height: 750 },
})
const page = await browser.newPage()
await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 45000 })
await new Promise((r) => setTimeout(r, 9000))
const probe = await page.evaluate(() => {
  const census = (buf) => {
    const buckets = { light: 0, med: 0, dark: 0, colored: 0 }
    let n = 0
    const avg = [0, 0, 0]
    for (let i = 0; i < buf.length; i += 4) {
      const r = buf[i], g = buf[i + 1], b = buf[i + 2]
      const s = r + g + b
      n++
      avg[0] += r; avg[1] += g; avg[2] += b
      if (s > 600) buckets.light++
      else if (s > 200) buckets.med++
      else buckets.dark++
      const max = Math.max(r, g, b), min = Math.min(r, g, b)
      if (max - min > 40) buckets.colored++
    }
    return {
      pixels: n,
      ...buckets,
      avg: avg.map((v) => Math.round(v / n)),
      coloredPct: Math.round((10000 * buckets.colored) / n) / 100,
      lightPct: Math.round((10000 * buckets.light) / n) / 100,
    }
  }
  const read = (canvas) => {
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl')
    if (!gl) return { ok: false, message: pingErr }
    const { width: W, height: H } = canvas
    const buf = new Uint8Array(W * H * 4)
    try { gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, buf) } catch (e) { return { ok: false, message: e.message } }
    return { ok: true, size: [W, H], ...census(buf) }
  }
  const mc = document.querySelector('canvas.maplibregl-canvas')
  const dc = document.querySelector('#deckgl-overlay')
  const img = document.createElement('img')
  return {
    maplibre: mc ? read(mc) : { ok: false, message: 'missing' },
    deck: dc ? read(dc) : { ok: false, message: 'missing' },
    hud: document.querySelector('.hud-value')?.textContent,
  }
})
console.log(JSON.stringify(probe, null, 2))
await browser.close()
process.exit(0)