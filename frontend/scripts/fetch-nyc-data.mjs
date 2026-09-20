/**
 * One-time data setup for the NYC Rat Race frontend.
 *
 * Writes into ./public:
 *   rat.glb                  - copied from the repo root
 *   data/taxi_zones.geojson  - TLC taxi-zone shapefile (d37ci6vzurychx.cloudfront.net)
 *   data/nyc_roads.geojson   - NYC street network from OpenStreetMap (Overpass)
 *
 * Run once from the frontend directory:  node scripts/fetch-nyc-data.mjs
 */
import { mkdir, copyFile, writeFile, access, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFile } from 'node:child_process'
import os from 'node:os'
import shpjs from 'shpjs'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const publicDir = path.join(root, 'public')
const dataDir = path.join(publicDir, 'data')

const UA = 'nyc-rat-race-hackmit/setup'
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function fetchBuffer(url, options = {}, retries = 4) {
  for (let attempt = 1; ; attempt++) {
    try {
      const res = await fetch(url, {
        headers: { 'User-Agent': UA, Accept: 'application/octet-stream, */*', ...options.headers },
        ...options,
      })
      if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`)
      return Buffer.from(await res.arrayBuffer())
    } catch (err) {
      if (attempt > retries) throw err
      console.warn(`  retry ${attempt}/${retries}: ${err.message}`)
      await sleep(2500 * attempt)
    }
  }
}

// Citywide bbox covering all twelve game regions.
// Citywide bbox covering all twelve game regions, including Staten Island and
// the airport zones. Keep the road classes to drivable streets so the graph
// stays useful for taxi movement without pulling in footways and paths.
const BBOX = { south: 40.48, west: -74.28, north: 40.93, east: -73.65 }
const HIGHWAY_RE = '(motorway|trunk|primary|secondary|tertiary|residential|unclassified|living_street)'
const OVERPASS_MIRRORS = [
  'https://overpass-api.de/api/interpreter',
  'https://overpass.kumi.systems/api/interpreter',
  'https://overpass.openstreetmap.ru/api/interpreter',
  'https://overpass.osm.jp/api/interpreter',
]
const BBBIKE_ROADS_URL = 'https://download.bbbike.org/osm/bbbike/NewYork/NewYork.osm.pbf'

function overpassQuery({ south, west, north, east }) {
  const bbox = `(${south},${west},${north},${east})`
  // Include named and unnamed drivable ways. Unnamed local connectors are
  // important in the outer boroughs, where omitting them leaves isolated
  // fragments and forces routeBetween() to stop far from its destination.
  const roads = `way["highway"~"^${HIGHWAY_RE}$"]${bbox};`
  return `[out:json][timeout:120][maxsize:536870912];(${roads});out geom;`
}

// curl.exe streams the response to a file, avoiding node's fetch fingerprint
// (some Overpass instances 406 it) and any child-process buffer limits.
function curlToFile(url, outfile, body) {
  return new Promise((resolve, reject) => {
    execFile(
      'curl',
      ['-sS', '-f', '--max-time', '150', '-A', 'Mozilla/5.0 nyc-rat-race/setup', '--data', `data=${encodeURIComponent(body)}`, '-o', outfile, url],
      { timeout: 180000 },
      (err) => {
        if (err) return reject(err)
        resolve()
      },
    )
  })
}

function downloadFile(url, outfile) {
  return new Promise((resolve, reject) => {
    execFile(
      'curl',
      ['-L', '--fail', '--retry', '3', '-A', 'Mozilla/5.0 nyc-rat-race/setup', '-o', outfile, url],
      { timeout: 900000 },
      (err) => {
        if (err) return reject(err)
        resolve()
      },
    )
  })
}

function convertBbbikeRoads(input, output) {
  return new Promise((resolve, reject) => {
    execFile(
      'python',
      [path.join(root, 'scripts', 'convert-bbbike-roads.py'), input, output],
      { timeout: 900000 },
      (err) => {
        if (err) return reject(err)
        resolve()
      },
    )
  })
}

async function fetchRoadsRaw() {
  // Prefer a local cache of a previous Overpass response so a flaky network
  // never bricks a rebuild. Save fresh downloads to scripts/cache/.
  const cacheDir = path.join(root, 'scripts', 'cache')
  const cacheFile = path.join(cacheDir, 'overpass_nyc.json')
  await mkdir(cacheDir, { recursive: true })
  try {
    const cached = await readFile(cacheFile, 'utf8')
    const parsed = JSON.parse(cached)
    if (Array.isArray(parsed.elements)) {
      console.log(`  roads loaded from cache (${parsed.elements.length} ways)`)
      return parsed
    }
  } catch {
    /* no cache yet */
  }
  // Split the citywide request into tiles. A single NYC-wide Overpass query is
  // large enough to time out or be rejected even when the mirror is healthy.
  const rows = 3
  const cols = 3
  const elements = new Map()
  const tmp = path.join(os.tmpdir(), 'nyc_rat_race_roads_tile.json')
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      const tile = {
        south: BBOX.south + (BBOX.north - BBOX.south) * row / rows,
        north: BBOX.south + (BBOX.north - BBOX.south) * (row + 1) / rows,
        west: BBOX.west + (BBOX.east - BBOX.west) * col / cols,
        east: BBOX.west + (BBOX.east - BBOX.west) * (col + 1) / cols,
      }
      let fetched = false
      for (let round = 1; round <= 2 && !fetched; round++) {
        for (const ep of OVERPASS_MIRRORS) {
          try {
            await curlToFile(ep, tmp, overpassQuery(tile))
            const parsed = JSON.parse(await readFile(tmp, 'utf8'))
            if (!Array.isArray(parsed.elements)) throw new Error('missing elements')
            for (const element of parsed.elements) {
              if (element.type === 'way' && element.id !== undefined) elements.set(`${element.type}/${element.id}`, element)
            }
            console.log(`  tile ${row + 1}/${rows},${col + 1}/${cols}: ${parsed.elements.length} ways from ${ep}`)
            fetched = true
            break
          } catch (err) {
            console.log(`  tile ${row + 1}/${rows},${col + 1}/${cols} ${ep} (round ${round}): ${err.message}`)
          }
        }
        if (!fetched) await sleep(2000)
      }
      if (!fetched) console.warn(`  tile ${row + 1}/${rows},${col + 1}/${cols} unavailable; continuing`)
    }
  }
  if (!elements.size) return null
  const parsed = { version: 0.6, generator: 'nyc-rat-race', elements: [...elements.values()] }
  try {
    await writeFile(cacheFile, JSON.stringify(parsed), 'utf8')
  } catch {
    /* caching is best-effort */
  }
  console.log(`  roads fetched citywide: ${parsed.elements.length} unique ways`)
  return parsed
}

// Deterministic fallback: a stylized citywide grid so the demo still has
// streets even if every Overpass instance is down. It is not OSM data.
function generatedGrid() {
  const { south, west, north, east } = BBOX
  const features = []
  let id = 0
  // Avenues run N-S (fixed longitude), streets run E-W (fixed latitude).
  for (let lon = west + 0.008; lon <= east - 0.008; lon += 0.003) {
    features.push({
      type: 'Feature',
      properties: { name: `Avenue ${id}`, highway: 'residential', oneway: false },
      geometry: { type: 'LineString', coordinates: [[lon, south + 0.01], [lon, north - 0.01]] },
    })
    id += 1
  }
  for (let lat = south + 0.01; lat <= north - 0.01; lat += 0.0042) {
    features.push({
      type: 'Feature',
      properties: { name: `Street ${id}`, highway: 'secondary', oneway: false },
      geometry: { type: 'LineString', coordinates: [[west + 0.008, lat], [east - 0.008, lat]] },
    })
    id += 1
  }
  return { type: 'FeatureCollection', features }
}

function roadsToGeojson(elements) {
  const features = []
  for (const el of elements) {
    if (el.type !== 'way' || !Array.isArray(el.geometry) || el.geometry.length < 2) continue
    features.push({
      type: 'Feature',
      properties: {
        name: el.tags?.name ?? '',
        highway: el.tags?.highway ?? '',
        oneway: el.tags?.oneway === 'yes',
      },
      geometry: {
        type: 'LineString',
        coordinates: el.geometry.map((p) => roundCoord([p.lon, p.lat], 6)),
      },
    })
  }
  return { type: 'FeatureCollection', features }
}

function roundCoord(c, digits) {
  const f = 10 ** digits
  return [Math.round(c[0] * f + Number.EPSILON) / f, Math.round(c[1] * f + Number.EPSILON) / f]
}

function roundGeometry(geom, digits) {
  switch (geom.type) {
    case 'Point':
      return { type: geom.type, coordinates: roundCoord(geom.coordinates, digits) }
    case 'MultiPoint':
      return { type: geom.type, coordinates: geom.coordinates.map((c) => roundCoord(c, digits)) }
    case 'LineString':
      return { type: geom.type, coordinates: geom.coordinates.map((c) => roundCoord(c, digits)) }
    case 'Polygon':
      return {
        type: geom.type,
        coordinates: geom.coordinates.map((ring) => ring.map((c) => roundCoord(c, digits))),
      }
    case 'MultiLineString':
      return {
        type: geom.type,
        coordinates: geom.coordinates.map((line) => line.map((c) => roundCoord(c, digits))),
      }
    case 'MultiPolygon':
      return {
        type: geom.type,
        coordinates: geom.coordinates.map((poly) =>
          poly.map((ring) => ring.map((c) => roundCoord(c, digits))),
        ),
      }
    default:
      return geom
  }
}

function zonesToGeojson(doc) {
  const features = (doc.features ?? []).map((f) => {
    const props = f.properties ?? {}
    return {
      type: 'Feature',
      properties: {
        zone: props.zone ?? 'Unknown',
        borough: props.borough ?? '',
        location_id: Number(props.LocationID ?? props.location_id ?? -1),
      },
      geometry: roundGeometry(f.geometry, 5),
    }
  })
  return { type: 'FeatureCollection', features }
}

async function main() {
  await mkdir(dataDir, { recursive: true })

  // 1. rat.glb from repo root
  const glbSource = path.resolve(root, '../rat.glb')
  const glbTarget = path.join(publicDir, 'rat.glb')
  try {
    await access(glbSource)
    await copyFile(glbSource, glbTarget)
    console.log('rat.glb      -> public/rat.glb')
  } catch {
    console.warn('rat.glb not found at repo root; skipping (place it in public/rat.glb manually)')
  }

  // 2. taxi zones (TLC shapefile). Socrata (NYC Open Data) is a primary source but
  //    has proven flaky; the TLC-distributed shapefile is the authoritative geometry.
  console.log('fetching taxi zones (TLC shapefile)...')
  const zipBuf = await fetchBuffer('https://d37ci6vzurychx.cloudfront.net/misc/taxi_zones.zip')
  const zones = zonesToGeojson(await shpjs(zipBuf.buffer.slice(zipBuf.byteOffset, zipBuf.byteOffset + zipBuf.byteLength)))
  await writeFile(path.join(dataDir, 'taxi_zones.geojson'), JSON.stringify(zones), 'utf8')
  console.log(`taxi_zones.geojson done: ${zones.features.length} zones`)

  // 3. roads: BBBike's NYC PBF is a stable citywide extract. It avoids the
  // rate limits, query-size failures, and regional gaps of Overpass mirrors.
  console.log('fetching roads (BBBike NYC PBF)...')
  const pbf = path.join(os.tmpdir(), 'nyc-rat-race-NewYork.osm.pbf')
  try {
    await access(pbf)
    console.log('  using cached BBBike PBF')
  } catch {
    await downloadFile(BBBIKE_ROADS_URL, pbf)
  }
  const roadsPath = path.join(dataDir, 'nyc_roads.geojson')
  await convertBbbikeRoads(pbf, roadsPath)
  console.log('nyc_roads.geojson done')

  console.log('\nAll data written. Commit public/rat.glb and public/data/.')
}

main().catch((err) => {
  console.error('\nfetch failed:', err)
  process.exit(1)
})
