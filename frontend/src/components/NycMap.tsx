import { useEffect, useRef, useState } from 'react'
import { Map as MapLibreMap, NavigationControl, type StyleSpecification } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { LightingEffect, AmbientLight, DirectionalLight, type Layer } from '@deck.gl/core'
import { GeoJsonLayer, IconLayer, LineLayer, PolygonLayer, ScatterplotLayer } from '@deck.gl/layers'
import { ScenegraphLayer } from '@deck.gl/mesh-layers'
import { RatRouter, type RoadPoint } from '../lib/ratRouter'
import { polygonCentroid, type TaxiZone } from '../lib/zones'
import { type WeatherEvent } from '../lib/weatherEvents'

// Tune-by-eye constants (visual calibration happens in the browser):
export const RAT_SIZE_SCALE = 50// rat.glb is ~1 world unit; this makes the rat
// a ~1.5 km-wide "giant rat taxi" so it is visible at city zoom (~57 m/px at z11.4).
export const RAT_SPEED_MPS = 800 // fast dispatch speed across the road graph
export const DISPATCH_ASTAR_SPEED_MPS = 1800
export const DISPATCH_RANDOM_WALK_SPEED_MPS = 120

// Footstep-trail feel: dots dropped at the rat's position that fade away.
export const FOOTSTEP_DROP_MS = 250 // interval between new dots
export const FOOTSTEP_LIFETIME_MS = 4500 // how long each dot lingers before fading out

// Building extrusion exaggeration: multiplies real Overture heights (m) for
// drama. Tune live via window.__buildingHeightScale before the layer updates.
export const BUILDING_HEIGHT_SCALE = 2.5

// rat.glb model frame: forward = -x, up = +y (standard glTF/Blender Y-up):
// nose at negative x, eyes/body elevated along +y, and z is the lateral axis
// (+z = left eye side). We align it to world space (deck lnglat: +x = east,
// +y = north, +z = up) via a raw rotation matrix so the nose points along the
// travel heading. h = compass bearing in degrees (0 = north, 90 = east), so the
// heading direction is F = (sin h, cos h, 0). A proper rotation with columns
//   col_x = (-sin h, -cos h, 0)   (maps model -x -> F)
//   col_y = (0, 0, 1)             (maps model +y -> up)
//   col_z = (-cos h, sin h, 0)    (= col_x x col_y, maps model +z -> left)
// is right-handed (col_x x col_y = col_z).
// getTransformMatrix overrides getOrientation/getScale/getTranslation;
// sizeScale is still applied by the layer shader.
const ratModelMatrix = (headingDeg: number): number[] => {
  const h = (headingDeg * Math.PI) / 180
  const s = Math.sin(h)
  const c = Math.cos(h)
  return [
    -s, -c, 0, 0,
    0, 0, 1, 0,
    -c, s, 0, 0,
    0, 0, 0, 1,
  ]
}

const RAT_URL = '/rat.glb'
const ROADS_URL = '/data/nyc_roads.geojson'
const ZONES_URL = '/data/taxi_zones.geojson'
const REGIONS_URL = '/data/regions.geojson'
// NYC building footprints + Overture heights, pre-extracted to GeoJSON and
// tagged with the custom region (harlem/upper_west/.../airports) they fall in.
// Attribution required per ODbL: shown in map credits.
const BUILDINGS_URL = '/data/buildings.geojson'
// Per-region weather/event instructions (see weather-graphics-handoff.md §4).
// The game will later emit this instruction object and the map just re-renders;
// the frontend never hardcodes event→region.

// Multiply a base rgb region color by an event tint (rgba). Returns the base
// color when no tint is set so the weather reads as a subtle region-wide wash.
function tintColor(base: [number, number, number], event: WeatherEvent | null) {
  const t = event?.tint
  if (!t) return base
  return [
    Math.round(base[0] * Math.min(t[0] / 255, 1)),
    Math.round(base[1] * Math.min(t[1] / 255, 1)),
    Math.round(base[2] * Math.min(t[2] / 255, 1)),
  ]
}

// Per-region weather renders as layered atmospheric effects, never text/emoji:
//   cloudy       - fluffy cloud polygons drifting high in the atmosphere
//   shower/rain  - cloud cover above + streaks of rain falling from it
//   heavy_rain   - heavier rain streaks + strong region darkening (tint)
//   thunderstorm - near-black clouds + rain + lightning flash in the sky
//   fog          - wide translucent mist puffs hugging the ground
//   heatwave     - pulsing hot-orange region outline (no floaters), red tint
//   windy        - sparse high clouds drifting fast + elevated wind streaks
//   cold         - pulsing icy-blue region outline (no floaters), blue tint
// Everything is deterministic per region (a string hash seeds a PRNG), then
// advanced by wall-clock time, so the ~8fps deck redeploys animate the effects
// with stable, allocation-light patterns.
const REGION_CELL_SCALE: Record<string, number> = {
  harlem: 1,
  upper_west: 1,
  upper_east: 1,
  midtown: 1,
  downtown: 1,
  north_brooklyn: 1.2,
  south_brooklyn: 1.3,
  queens_west: 1.5,
  queens_east: 2.3,
  // Keep airport weather local to JFK rather than spanning the whole airport
  // macro-region (which also contains LaGuardia and broad approach areas).
  airports: 0.65,
  bronx: 1.5,
  staten_island: 2.4,
}

function hashStr(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

// Record shapes feeding the two weather FX layers. Positions are
// [lon, lat, elevation-meters]; ground effects use a small z (a few metres) so
// they blend over the region polygons instead of depth-coplanar replacing them.
// Clouds are fluffy polygon rings floating at altitude (no discs). h = the
// extrusion thickness in metres that lifts the cloud ABOVE the map plane.
interface CloudPoly {
  ring: [number, number, number][]
  color: [number, number, number, number]
  h?: number
}

type WxPosition = [number, number] | [number, number, number]

interface WxStreak {
  source: WxPosition
  target: WxPosition
  color: [number, number, number, number]
}

// A cumulus-ish cloud silhouette: a closed ring of `N+1` vertices whose radius
// swells toward each of `lobes` puffy bumps, with per-vertex jitter so the edge
// is never a clean circle. "breathe" slowly scales the whole cloud up/down.
function puffyRing(
  cx: number,
  cy: number,
  R: number,
  seed: number,
  lobes: number,
  z: number,
  breathe = 1,
  stretchX = 1.3,
  stretchY = 0.8,
): [number, number, number][] {
  const ring: [number, number, number][] = []
  const N = 30
  for (let k = 0; k <= N; k++) {
    const a = (k / N) * Math.PI * 2 + seed * 6.28
    let rr = 1
    for (let m = 0; m < lobes; m++) {
      const la = seed * 6.28 + (m * Math.PI * 2) / lobes
      const d = Math.abs(Math.atan2(Math.sin(a - la), Math.cos(a - la)))
      rr += 0.45 * Math.pow(Math.max(0, Math.cos(d)), 1.2)
    }
    const jit = 0.95 + 0.1 * Math.sin(seed * 9 + k * 3.1)
    const r = R * rr * (0.5 + 0.5 * breathe) * jit
    ring.push([cx + Math.cos(a) * r * stretchX, cy + Math.sin(a) * r * stretchY, z])
  }
  return ring
}

const REGION_COLORS: Record<string, [number, number, number]> = {
  harlem: [78, 121, 167],
  upper_west: [242, 142, 43],
  upper_east: [225, 87, 89],
  midtown: [118, 183, 178],
  downtown: [89, 161, 79],
  north_brooklyn: [237, 201, 72],
  south_brooklyn: [176, 122, 161],
  queens_west: [255, 157, 167],
  queens_east: [230, 120, 44],
  airports: [156, 117, 95],
  bronx: [126, 90, 190],
  staten_island: [60, 160, 190],
}

const WEATHER_CENTERS: Record<string, { lon: number; lat: number }> = {
  // JFK terminal/airfield center; airport weather FX should not bridge JFK and
  // the other airports in the macro-region.
  airports: { lon: -73.7781, lat: 40.6413 },
}

const MAP_STYLE: StyleSpecification = {
  version: 8,
  glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors',
    },
  },
  layers: [
    { id: 'osm-basemap', type: 'raster', source: 'osm' },
  ],
}

interface RatPose {
  lon: number
  lat: number
  heading: number
}

export interface DispatchRun {
  id: number
  allocation: Record<string, number>
}

interface DispatchRat {
  zoneId: string
  routes: Array<RoadPoint[]>
  routeLengths: number[]
  routeSpeeds: number[]
  totalRouteDuration: number
}

interface FleetPosition extends RoadPoint {
  zoneId: string
  heading: number
}

interface EventBubble {
  position: [number, number, number]
  icon: string
  size: number
  score: number
}

const eventFaceIcon = (tone: number): string => {
  const face = tone >= 4
    ? '<circle cx="61" cy="67" r="5"/><circle cx="99" cy="67" r="5"/><path d="M55 91 Q80 116 105 91" fill="none" stroke="#23272f" stroke-width="6" stroke-linecap="round"/>'
    : tone >= -2
      ? '<circle cx="61" cy="70" r="5"/><circle cx="99" cy="70" r="5"/><path d="M58 94 L102 94" fill="none" stroke="#23272f" stroke-width="6" stroke-linecap="round"/>'
      : tone >= -8
        ? '<circle cx="61" cy="70" r="5"/><circle cx="99" cy="70" r="5"/><path d="M55 103 Q80 78 105 103" fill="none" stroke="#23272f" stroke-width="6" stroke-linecap="round"/>'
        : '<circle cx="61" cy="70" r="5"/><circle cx="99" cy="70" r="5"/><path d="M55 103 Q80 78 105 103" fill="none" stroke="#23272f" stroke-width="6" stroke-linecap="round"/><path d="M102 73 Q113 83 105 96 Q96 83 102 73" fill="#62b9ed" stroke="#23272f" stroke-width="3"/>'
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="160" height="180" viewBox="0 0 160 180"><path d="M80 174 C80 174 81 157 61 147" fill="none" stroke="#23272f" stroke-width="5" stroke-linecap="round"/><circle cx="80" cy="80" r="62" fill="#fff8e1" stroke="#23272f" stroke-width="5"/>${face}</svg>`
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
}

function geometryPoints(geometry: GeoJSON.Geometry): Array<[number, number]> {
  const cached = GEOMETRY_POINTS_CACHE.get(geometry as object)
  if (cached) return cached
  const points: Array<[number, number]> = []
  const visit = (value: unknown) => {
    if (!Array.isArray(value)) return
    if (value.length >= 2 && typeof value[0] === 'number' && typeof value[1] === 'number') {
      points.push([value[0], value[1]])
      return
    }
    value.forEach(visit)
  }
  visit((geometry as any).coordinates)
  GEOMETRY_POINTS_CACHE.set(geometry as object, points)
  return points
}

const GEOMETRY_POINTS_CACHE = new WeakMap<object, Array<[number, number]>>()
const GEOMETRY_BOUNDS_CACHE = new WeakMap<object, [number, number, number, number]>()

function cachedGeometryBounds(geometry: GeoJSON.Geometry): [number, number, number, number] {
  const cached = GEOMETRY_BOUNDS_CACHE.get(geometry as object)
  if (cached) return cached
  const points = geometryPoints(geometry)
  const bounds: [number, number, number, number] = [
    Math.min(...points.map(([lon]) => lon)),
    Math.max(...points.map(([lon]) => lon)),
    Math.min(...points.map(([, lat]) => lat)),
    Math.max(...points.map(([, lat]) => lat)),
  ]
  GEOMETRY_BOUNDS_CACHE.set(geometry as object, bounds)
  return bounds
}

function pointInRing(point: [number, number], ring: Array<[number, number]>): boolean {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [x, y] = ring[i]
    const [previousX, previousY] = ring[j]
    if ((y > point[1]) !== (previousY > point[1]) && point[0] < (previousX - x) * (point[1] - y) / (previousY - y) + x) {
      inside = !inside
    }
  }
  return inside
}

function pointInGeometry(point: [number, number], geometry: GeoJSON.Geometry): boolean {
  const [minLon, maxLon, minLat, maxLat] = cachedGeometryBounds(geometry)
  if (point[0] < minLon || point[0] > maxLon || point[1] < minLat || point[1] > maxLat) return false
  const coordinates = (geometry as any).coordinates
  if (geometry.type === 'Polygon') {
    return pointInRing(point, coordinates[0]) && !coordinates.slice(1).some((ring: Array<[number, number]>) => pointInRing(point, ring))
  }
  if (geometry.type === 'MultiPolygon') {
    return coordinates.some((polygon: Array<Array<[number, number]>>) => pointInRing(point, polygon[0]) && !polygon.slice(1).some((ring) => pointInRing(point, ring)))
  }
  return false
}

function pointInRegion(geometry: GeoJSON.Geometry | undefined, seed: number): [number, number] | null {
  if (!geometry) return null
  const points = geometryPoints(geometry)
  if (!points.length) return null
  const minLon = Math.min(...points.map(([lon]) => lon))
  const maxLon = Math.max(...points.map(([lon]) => lon))
  const minLat = Math.min(...points.map(([, lat]) => lat))
  const maxLat = Math.max(...points.map(([, lat]) => lat))
  const random = mulberry32(seed)
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const point: [number, number] = [
      minLon + random() * (maxLon - minLon),
      minLat + random() * (maxLat - minLat),
    ]
    if (pointInGeometry(point, geometry)) return point
  }
  return points[Math.floor(random() * points.length)]
}

export interface NycMapProps {
  allocationByZone?: Record<string, number>
  events?: Record<string, number>
  weatherEventsByZone?: Record<string, WeatherEvent | null>
  selectedZone?: string | null
  onZoneSelect?: (zoneId: string) => void
  dispatchRun?: DispatchRun | null
}

export default function NycMap({
  allocationByZone = {},
  events = {},
  weatherEventsByZone = {},
  selectedZone = null,
  onZoneSelect,
  dispatchRun = null,
}: NycMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const overlayRef = useRef<MapboxOverlay | null>(null)
  const routerRef = useRef<RatRouter | null>(null)
  const zonesRef = useRef<TaxiZone[]>([])
  const regionCentroidsRef = useRef<Record<string, { lon: number; lat: number }>>({})
  const regionGeometryRef = useRef<Record<string, GeoJSON.Geometry>>({})
  const airportGeometryRef = useRef<GeoJSON.Geometry | null>(null)
  const regionsLayerRef = useRef<GeoJsonLayer | null>(null)
  // Per-region weather metrics from the game state; all visual effects derive
  // from these values and refresh as the simulation advances.
  const weatherEventsRef = useRef(weatherEventsByZone)
  // Building data is kept separate from the layer so the layer can be rebuilt
  // with a different `visible` flag on zoom-gate crossings. Rebuilding preserves
  // the same `data` reference, so deck reuses GPU buffers instead of unloading
  // (removing the layer from the array permanently destroys it — the layer would
  // never come back after zooming back in).
  const buildingsDataRef = useRef<Array<any> | null>(null)
  const zoomRef = useRef(11.4)
  const poseRef = useRef<RatPose>({ lon: -73.985, lat: 40.755, heading: 0 })
  const footstepsRef = useRef<Array<{ lon: number; lat: number; born: number }>>([])
  const allocationRef = useRef(allocationByZone)
  const eventsRef = useRef(events)
  const selectedZoneRef = useRef(selectedZone)
  const onZoneSelectRef = useRef(onZoneSelect)
  const dispatchRunRef = useRef(dispatchRun)
  const dispatchAnimationIdRef = useRef<number | null>(null)
  const dispatchAnimationStartedAtRef = useRef<number | null>(null)
  const dispatchRatsRef = useRef<DispatchRat[]>([])
  const restingFleetDataRef = useRef<FleetPosition[]>([])

  const [hovered, setHovered] = useState<string | null>(null)

  useEffect(() => {
    allocationRef.current = allocationByZone
    eventsRef.current = events
    weatherEventsRef.current = weatherEventsByZone
    selectedZoneRef.current = selectedZone
    onZoneSelectRef.current = onZoneSelect
    dispatchRunRef.current = dispatchRun
  }, [allocationByZone, dispatchRun, events, onZoneSelect, selectedZone, weatherEventsByZone])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const map = new MapLibreMap({
      container,
      style: MAP_STYLE,
      center: [-73.985, 40.755],
      zoom: 11.4,
      pitch: 55,
      maxPitch: 80,
      minZoom: 10,
      maxBounds: [
        [-74.45, 40.3],
        [-73.3, 41.05],
      ],
      attributionControl: { compact: true },
    })
    ;(window as any).__map = map

    // Overlaid (not interleaved): MapLibre v6 removed the private `map.transform`
    // that deck.gl's interleaved path reads, so interleaved renders crash.
    // Overlaid still forwards mouse events to deck picking via map handlers.
    const lighting = new LightingEffect({
      ambient: new AmbientLight({ color: [255, 255, 255], intensity: 1.1 }),
      // Soft, near-flat lighting: one gentle directional light keeps the
      // extrusion readable as 3D via mild face darkening, without blowing
      // sun-facing sides to white (sun intensity 3.0 + diffuse 0.75 did that).
      sun: new DirectionalLight({ color: [255, 255, 255], intensity: 1.0, direction: [50, 90, -35] }),
    })
    const overlay = new MapboxOverlay({ layers: [], effects: [lighting] })
    ;(window as any).__overlay = overlay
    map.addControl(overlay)
    overlayRef.current = overlay
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')

    // MapLibre v6 sizes its canvas independently; deck's MapboxOverlay measures
    // map.getContainer().clientHeight at add time and ends up 0px tall.
    // Force the deck widget container to track the real canvas size.
    const syncDeckSize = () => {
      try {
        const wrap = overlayRef.current?.getCanvas()?.parentElement
        const cvs = map.getCanvas()
        if (!wrap || !cvs) return
        const w = cvs.clientWidth
        const h = cvs.clientHeight
        if (w > 0 && h > 0 && (wrap.style.width !== `${w}px` || wrap.style.height !== `${h}px`)) {
          wrap.style.width = `${w}px`
          wrap.style.height = `${h}px`
        }
      } catch {
        // deck not initialized yet; next tick will retry
      }
    }
    map.on('load', syncDeckSize)
    map.on('resize', syncDeckSize)
    map.on('zoom', () => {
      zoomRef.current = map.getZoom()
    })
    ;(window as any).__zoomRefGet = () => zoomRef.current

    // Building layer factory: extruded footprints colored by the taxi region
    // they were joined to (see data prep). The city remains 3D at every zoom.
    const makeBuildingsLayer = (): GeoJsonLayer | null => {
      const feats = buildingsDataRef.current
      if (!feats) return null
      const w = window as any
      const heightScale = w.__buildingHeightScale ?? BUILDING_HEIGHT_SCALE
      if (w.__buildingsDebug) console.log('[buildings] make always visible')
      return new GeoJsonLayer({
        id: 'buildings',
        data: feats as any,
        visible: true,
        extruded: true,
        pickable: false,
        stroked: false,
        filled: true,
        wireframe: false,
        opacity: 0.9,
        getElevation: (f: { properties: { height: number } }) => f.properties.height,
        elevationScale: heightScale,
        getFillColor: (f: { properties: { region: string } }) => {
          const c = REGION_COLORS[f.properties.region] ?? [170, 170, 170]
           const tinted = tintColor(c, weatherEventsRef.current[f.properties.region] ?? null)
          return [
            Math.round(tinted[0] * 0.58),
            Math.round(tinted[1] * 0.58),
            Math.round(tinted[2] * 0.58),
            220,
          ]
        },
        material: {
          // Near-flat look: high ambient keeps the color mostly solid, modest
          // diffuse gives each face just enough brightening/darkening to read
          // as extruded, no specular so no white glints.
          ambient: 0.7,
          diffuse: 0.4,
          shininess: 0,
          specularColor: [0, 0, 0],
        },
      })
    }

    const loadBuildings = async () => {
      const res = await fetch(BUILDINGS_URL)
      if (disposed) return
      if (!res.ok) throw new Error(`buildings ${res.status}`)
      const doc = (await res.json()) as {
        features: Array<{ properties: { height: number; borough: string }; geometry: GeoJSON.Geometry }>
      }
      buildingsDataRef.current = doc.features
      console.log(`[buildings] loaded ${doc.features.length} features`)
    }

    const makeDispatchRats = (run: DispatchRun): DispatchRat[] => {
      const rats: DispatchRat[] = []
      const targetsByZone = new Map<string, RoadPoint[]>()
      const availableParked = [...restingFleetDataRef.current]
      for (const [zoneId, allocation] of Object.entries(run.allocation)) {
        if (allocation <= 0) continue
        const count = Math.max(0, Math.floor(allocation))
        const center = regionCentroidsRef.current[zoneId]
        if (!center) continue
        let targets = targetsByZone.get(zoneId)
        const regionGeometry = regionGeometryRef.current[zoneId]
        const destinationGeometry = zoneId === 'airports'
          ? airportGeometryRef.current ?? regionGeometry
          : regionGeometry
        if (!targets) {
          const seed = hashStr(`${run.id}:${zoneId}`)
          const fallback: RoadPoint = { lon: center.lon, lat: center.lat }
          targets = []
          for (let targetIndex = 0; targetIndex < 6; targetIndex += 1) {
            const point = pointInRegion(destinationGeometry, seed + targetIndex + 1)
            const target: RoadPoint = point
              ? { lon: point[0], lat: point[1] }
              : fallback
            targets.push(target)
          }
          targetsByZone.set(zoneId, targets)
        }
        for (let index = 0; index < count; index += 1) {
          const seed = hashStr(`${run.id}:${zoneId}:${index}`)
          const fallback: RoadPoint = { lon: center.lon, lat: center.lat }
          const preferredIndex = availableParked.findIndex((rat) => rat.zoneId === zoneId)
          const parkedPosition = availableParked.splice(preferredIndex >= 0 ? preferredIndex : 0, 1)[0]
          const randomStart = pointInRegion(destinationGeometry, seed)
          const origin: RoadPoint = parkedPosition
            ? { lon: parkedPosition.lon, lat: parkedPosition.lat }
            : randomStart
              ? { lon: randomStart[0], lat: randomStart[1] }
              : fallback
          const insideDestination = Boolean(destinationGeometry && pointInGeometry([origin.lon, origin.lat], destinationGeometry))
          const routes: Array<RoadPoint[]> = []
          const routeSpeeds: number[] = []
          let current = origin
          if (!insideDestination) {
            const target = targets[index % Math.max(targets.length, 1)] ?? fallback
            const approach = routerRef.current?.aStarRoute(current, target) ?? [current, target]
            routes.push(approach)
            routeSpeeds.push(DISPATCH_ASTAR_SPEED_MPS)
            current = approach[approach.length - 1] ?? target
          }
          for (let segment = routes.length; segment < 10; segment += 1) {
            const walk = routerRef.current?.boundedRandomWalk(
              current,
              (point) => Boolean(destinationGeometry && pointInGeometry([point.lon, point.lat], destinationGeometry)),
              18,
              seed + segment,
            ) ?? [current]
            routes.push(walk)
            routeSpeeds.push(DISPATCH_RANDOM_WALK_SPEED_MPS)
            current = walk[walk.length - 1] ?? current
          }
          const routeLengths = routes.map((route) => RatRouter.routeLength(route))
          const totalRouteDuration = routeLengths.reduce(
            (sum, length, index) => sum + length / routeSpeeds[index],
            0,
          )
          rats.push({
            zoneId,
            routes,
            routeLengths,
            routeSpeeds,
            totalRouteDuration,
          })
        }
      }
      return rats
    }

    const dispatchFleetData = (now: number): FleetPosition[] => {
      const elapsedSeconds = Math.max(0, now - (dispatchAnimationStartedAtRef.current ?? now)) / 1000
      return dispatchRatsRef.current.map((rat) => {
        let remainingTime = rat.totalRouteDuration
          ? elapsedSeconds % rat.totalRouteDuration
          : 0
        let sampled = RatRouter.pointAlongRouteMeters(rat.routes[0], 0)
        for (let routeIndex = 0; routeIndex < rat.routes.length; routeIndex += 1) {
          const route = rat.routes[routeIndex]
          const length = rat.routeLengths[routeIndex]
          const speed = rat.routeSpeeds[routeIndex]
          const routeDuration = length / speed
          if (remainingTime <= routeDuration) {
            sampled = RatRouter.pointAlongRouteMeters(route, remainingTime * speed)
            break
          }
          remainingTime -= routeDuration
        }
        return { ...sampled.point, zoneId: rat.zoneId, heading: (sampled.headingDeg + 360) % 360 }
      })
    }

    let rafId = 0
    let last = performance.now()
    let lastProps = 0
    let lastDrop = 0
    let disposed = false

    const makeRatLayer = (pose: RatPose): ScenegraphLayer => {
      return new ScenegraphLayer({
        id: 'rat',
        data: [{ ...pose }],
        scenegraph: RAT_URL,
        getPosition: (d) => [d.lon, d.lat],
        getTransformMatrix: (d) => ratModelMatrix(d.heading),
        sizeScale: RAT_SIZE_SCALE,
        // PBR mode: the flat rendering path outputs `vColor` (instance color,
        // white) and never reads the glTF materials' baseColorFactor, so the rat
        // renders as a flat white silhouette. PBR mode routes through
        // pbr_filterColor which applies per-material baseColorFactor + lighting.
        _lighting: 'pbr',
      })
    }

    const makeFleetLayer = (): ScenegraphLayer | null => {
      const data = dispatchRatsRef.current.length
        ? dispatchFleetData(performance.now())
        : restingFleetDataRef.current.length
          ? restingFleetDataRef.current
        : Object.entries(regionCentroidsRef.current).flatMap(([zoneId, center]) => {
          const count = Math.max(0, Math.floor(allocationRef.current[zoneId] ?? 0))
          return Array.from({ length: count }, (_, index) => ({
            lon: center.lon + ((index % 10) - 4.5) * 0.0009,
            lat: center.lat + (Math.floor(index / 10) - 2) * 0.0008,
            heading: (index * 47 + zoneId.length * 11) % 360,
          }))
        })
      if (!data.length) return null
      return new ScenegraphLayer({
        id: 'fleet-rats',
        data,
        scenegraph: RAT_URL,
        getPosition: (d) => [d.lon, d.lat],
        getTransformMatrix: (d) => ratModelMatrix(d.heading),
        sizeScale: RAT_SIZE_SCALE * 0.52,
        _lighting: 'pbr',
        getColor: () => [244, 192, 22, 255],
      })
    }

    const makeEventLayers = (): Layer[] => {
      const signals = eventsRef.current
      const firstNumber = (keys: string[]) => {
        for (const key of keys) {
          const value = signals[key]
          if (Number.isFinite(value)) return value
        }
        return 0
      }
      const candidates: Array<{ zone: string; position: [number, number]; score: number; tone: number }> = []
      for (const [zone, centroid] of Object.entries(regionCentroidsRef.current)) {
        const count = firstNumber([
          `zone_event_count:${zone}`,
          `event_count:${zone}`,
        ])
        const mentions = firstNumber([
          `zone_event_mentions:${zone}`,
          `event_mentions:${zone}`,
        ])
        const intensity = firstNumber([zone])
        const score = Math.max(count, mentions / 1000, intensity)
        if (score <= 0) continue
        candidates.push({
          zone,
          position: [centroid.lon, centroid.lat],
          score,
          tone: firstNumber([
            `zone_avg_tone:${zone}`,
            `avg_tone:${zone}`,
          ]),
        })
      }
      if (!candidates.length) return []

      const maxScore = Math.max(...candidates.map((event) => Math.log1p(event.score)))
      const bubbles: EventBubble[] = candidates.map((event) => {
        const relative = Math.log1p(event.score) / maxScore
        return {
          position: [event.position[0], event.position[1], 450],
          icon: eventFaceIcon(event.tone),
          size: 260 + relative * 220,
          score: event.score,
        }
      })

      return [
        new IconLayer<EventBubble>({
          id: 'gdelt-event-bubbles',
          data: bubbles,
          getPosition: (event) => event.position,
          getIcon: (event) => ({
            url: event.icon,
            width: 160,
            height: 180,
            anchorY: 180,
          }),
          getSize: (event) => event.size,
          sizeUnits: 'meters',
          sizeScale: 1,
          billboard: true,
          pickable: false,
        }),
      ]
    }

    const makeFootstepsLayer = (): ScatterplotLayer => {
      const now = performance.now()
      // Keep only live dots, fading alpha + radius with age. The layer is
      // recreated every ~125ms in tick, so the fade advances smoothly.
      const data = footstepsRef.current
        .filter((f) => now - f.born < FOOTSTEP_LIFETIME_MS)
        .map((f) => {
          // t = 1 fresh -> 0 gone. sqrt(t) keeps dots large/visible for most of
          // their life, then shrinks/fades them quickly near the tail.
          const life = (now - f.born) / FOOTSTEP_LIFETIME_MS
          const t = 1 - life
          const hold = Math.sqrt(Math.max(t, 0))
          return {
            position: [f.lon, f.lat] as [number, number],
            radius: Math.round(RAT_SIZE_SCALE * 0.5 * hold),
            color: [70, 64, 58, Math.round(200 * hold)] as [number, number, number, number],
          }
        })
      return new ScatterplotLayer({
        id: 'footsteps',
        data,
        getPosition: (d) => d.position,
        getRadius: (d) => d.radius,
        radiusUnits: 'meters',
        getFillColor: (d) => d.color,
        stroked: false,
      })
    }

    // Weather graphics: layered atmospheric effects assembled from a
    // deterministic per-region seed and animated by wall-clock time. Clouds and
    // fog are fluffy polygon rings (not discs) floated by their z coordinates;
    // rain and wind streak along a line layer. Heat/cold render purely as a
    // pulsing region outline in the regions layer below. One layer per family
    // keeps deck buffer churn low while still updating ~8fps.
    const makeWeatherFX = (): Layer[] => {
      if (!regionCentroidsRef.current) return []
      const t = performance.now() / 1000
      const clouds: CloudPoly[] = []
      const fog: CloudPoly[] = []
      const streaks: WxStreak[] = []
      const activeSlugs: string[] = []

      for (const slug of Object.keys(regionCentroidsRef.current)) {
        const ev = weatherEventsRef.current[slug]
        if (!ev) continue
        activeSlugs.push(slug)
        const center = WEATHER_CENTERS[slug] ?? regionCentroidsRef.current[slug]
        // Area wind: region-level prevailing wind wins, event wind is fallback.
        const wind = ev.wind
        const rand = mulberry32(hashStr(slug) || 1)
        const scale = REGION_CELL_SCALE[slug] ?? 1
        const lonHalf = 0.03 * scale
        const latHalf = 0.02 * scale
        // Polygon ring offsets are baked straight into lat/lon (degrees), so a
        // metre-measured cloud radius must be converted per degree at this lat.
        // Without this, a "900 m" cloud becomes a 900-degree blob covering the
        // whole globe as one faint uniform wash.
        const degPerM = 1 / (111320 * Math.max(0.7, Math.cos((center.lat * Math.PI) / 180)))

        // A "cloud bank": `count` drifting cloud masses drawn as fluffy
        // polygons (lumpy rings of lobes) floating high in the atmosphere via
        // the ring's z coordinates — well above the streets so they never cover
        // the city. The whole bank drifts at driftDegPerS (fast for wind, slow
        // for overcast/rain) and gently breathes with a slow sin-wave. Returns
        // the live centers so rain can fall from exactly under each cloud.
        const addCloudBank = (opts: {
          count: number
          lobes: number
          shade: [number, number, number]
          alpha: number
          altMin: number
          altMax: number
          radMin: number
          radMax: number
          driftDegPerS: number
          latDrift: number
        }): Array<{ lon: number; lat: number }> => {
          const centers: Array<{ lon: number; lat: number }> = []
          const period = (2 * lonHalf * 1.6) / opts.driftDegPerS
          for (let i = 0; i < opts.count; i++) {
            const r0 = rand()
            const frac = ((t * opts.driftDegPerS + r0 * period) % period) / period
            const baseLon = center.lon + (frac - 0.5) * 2 * lonHalf * 1.6
            const baseLat = center.lat + (frac - 0.5) * opts.latDrift + (rand() - 0.5) * latHalf * 1.2
            const mainR = (opts.radMin + rand() * (opts.radMax - opts.radMin)) * scale * degPerM
            const seed = rand()
            const breathe = 0.92 + 0.08 * Math.sin(t * 0.8 + seed * 6.28)
            const z = opts.altMin + rand() * (opts.altMax - opts.altMin)
            centers.push({ lon: baseLon, lat: baseLat })
            clouds.push({
              ring: puffyRing(baseLon, baseLat, mainR, seed, opts.lobes, z, breathe, 1.3, 0.78),
              // ~55% fill: visible against the sky but the city stays readable.
              color: [...opts.shade, Math.round(opts.alpha * 0.55)],
              // Solid-ish thickness so the cloud reads as a volume in the sky.
              h: 90 + rand() * 60,
            })
          }
          return centers
        }

// Rain: short streaks that fall downward from just under the rendered cloud
        // banks (each streak latches onto a cloud center, so it never rains in
        // bare sky). The fall happens in elevation, not latitude: columns stay
        // under their clouds while streaks descend toward the streets. Wind
        // only shifts the lower endpoint downwind.
        const addRain = (opts: {
          count: number
          len: number
          speedDegPerS: number
          top: number
          color: [number, number, number, number]
          under: Array<{ lon: number; lat: number }>
        }) => {
          const b = ((wind?.dir_deg ?? 90) * Math.PI) / 180
          const dl = Math.sin(b) // east component (+lon)
          const dn = Math.cos(b) // north component (+lat)
          const windMs = wind?.speed_ms ?? 0
          // Rain leans with the wind: the lower endpoint is displaced downwind
          // by roughly wind speed / terminal fall speed over the visible shaft.
          const visibleFall = 280
          const blow = (windMs / 9) * visibleFall * degPerM
          const fallCycle = Math.max(opts.top, 700)
          const fallSpeed = Math.max(160, opts.speedDegPerS * 111320 * 0.35)
          const pool = opts.under.length ? opts.under : [center]
          const fallSpan = 0.01
          for (let i = 0; i < opts.count; i++) {
            const base = pool[Math.floor(rand() * pool.length)]
            const lon = base.lon + (rand() - 0.5) * 0.006
            const phase = rand() * fallCycle
            const altitudePhase = (t * fallSpeed + phase) % fallCycle
            const topZ = visibleFall + fallCycle - altitudePhase
            const bottomZ = topZ - visibleFall
            // The geographic spawn point stays under the cloud; falling is
            // represented by the source/target elevation changing over time.
            const top = base.lat + (rand() - 0.5) * fallSpan
            const jitLon = (rand() - 0.5) * 0.001
            const jitLat = (rand() - 0.5) * 0.001
            const slantLon = dl * blow + jitLon
            const slantLat = dn * blow + jitLat
            streaks.push({
              source: [lon, top, topZ],
              target: [lon + slantLon, top + slantLat, bottomZ],
              color: opts.color,
            })
          }
        }

        // Wind: chevron ">" brackets racing across the region high in the sky,
        // oriented along the area's wind direction (the apex leads downwind, the
        // two arms trail upwind) and marching faster the stronger the wind.
        const addWindStreaks = () => {
          const b = ((wind?.dir_deg ?? 90) * Math.PI) / 180
          const dl = Math.sin(b) // east component (+lon)
          const dn = Math.cos(b) // north component (+lat)
          const perpL = -dn // left of the wind
          const perpN = dl
          const windMs = wind?.speed_ms ?? 8
          // March speed scales with the wind; 8 m/s keeps the original tempo.
          const march = 0.0022 * (windMs / 8)
          const span = lonHalf * 2
          for (let i = 0; i < 9; i++) {
            const r = rand()
            if (r < 0.3) continue
            const along = (t * march + r * span) % span
            const lateral = (rand() - 0.5) * latHalf * 1.2
            const ax = center.lon + (along - span / 2) * dl + perpL * lateral
            const ay = center.lat + (along - span / 2) * dn + perpN * lateral
            const z = 1000 + rand() * 400
            const arm = 0.005 + rand() * 0.003
            const spread = 0.001 + rand() * 0.0008
            const color: [number, number, number, number] = [210, 226, 240, 170]
            streaks.push(
              {
                source: [ax - dl * arm + perpL * spread, ay - dn * arm + perpN * spread, z + 8],
                target: [ax, ay, z],
                color,
              },
              {
                source: [ax - dl * arm - perpL * spread, ay - dn * arm - perpN * spread, z + 8],
                target: [ax, ay, z],
                color,
              },
            )
          }
        }

        // Fog: a drifting band of wide, very translucent ground-level puffs so
        // the mist hugs the streets instead of floating above them. Kept out
        // of the `clouds` array so it stays on the flat ground plane.
        const addFogPuffs = () => {
          const span = lonHalf * 2
          const period = span / 0.00006
          for (let i = 0; i < 9; i++) {
            const r0 = rand()
            const frac = ((t * 0.00006 + r0 * period) % period) / period
            const breathe = 0.75 + 0.25 * Math.sin(t * 0.6 + r0 * 6)
            const seed = rand()
            fog.push({
              ring: puffyRing(
                center.lon - lonHalf + frac * span,
                center.lat + (rand() - 0.5) * latHalf * 1.2,
                (600 + rand() * 800) * scale * degPerM,
                seed,
                4,
                8 + rand() * 18,
                breathe,
                1.5,
                0.9,
              ),
              color: [186, 193, 202, Math.round((38 + rand() * 26) * breathe)],
            })
          }
        }

        // Lightning: a bright jagged wash over the region twice per ~4.4s.
        const flashLevel = (phase: number): number => {
          const cycle = (t + phase * 4.4) % 4.4
          const p1 = Math.max(0, 1 - Math.abs(cycle - 0.07) / 0.07)
          const p2 = Math.max(0, 1 - Math.abs(cycle - 0.46) / 0.06) * 0.6
          return Math.min(1, p1 + p2)
        }

switch (ev.event) {
          case 'cloudy':
            addCloudBank({
              count: 5, lobes: 4, shade: [172, 182, 196], alpha: 185,
              altMin: 1500, altMax: 2400, radMin: 450, radMax: 850,
              driftDegPerS: 0.00008, latDrift: 0.004,
            })
            break
          case 'shower':
            addRain({
              count: 22, len: 0.014, speedDegPerS: 0.012, top: 1300, color: [150, 185, 225, 150],
              under: addCloudBank({
                count: 3, lobes: 5, shade: [128, 138, 152], alpha: 205,
                altMin: 1300, altMax: 2000, radMin: 420, radMax: 800,
                driftDegPerS: 0.00012, latDrift: 0.006,
              }),
            })
            break
          case 'rain':
            addRain({
              count: 34, len: 0.017, speedDegPerS: 0.016, top: 1200, color: [120, 160, 210, 160],
              under: addCloudBank({
                count: 3, lobes: 5, shade: [108, 118, 134], alpha: 210,
                altMin: 1200, altMax: 1900, radMin: 450, radMax: 800,
                driftDegPerS: 0.00015, latDrift: 0.007,
              }),
            })
            break
          case 'heavy_rain':
            addRain({
              count: 48, len: 0.021, speedDegPerS: 0.02, top: 1100, color: [95, 135, 190, 180],
              under: addCloudBank({
                count: 4, lobes: 5, shade: [78, 88, 104], alpha: 215,
                altMin: 1100, altMax: 1700, radMin: 500, radMax: 820,
                driftDegPerS: 0.00018, latDrift: 0.008,
              }),
            })
            break
          case 'thunderstorm': {
            addRain({
              count: 44, len: 0.019, speedDegPerS: 0.018, top: 1000, color: [95, 125, 185, 170],
              under: addCloudBank({
                count: 4, lobes: 6, shade: [56, 66, 86], alpha: 220,
                altMin: 1000, altMax: 1500, radMin: 520, radMax: 860,
                driftDegPerS: 0.0002, latDrift: 0.01,
              }),
            })
            const lvl = flashLevel((hashStr(slug) % 10) / 10)
            if (lvl > 0.02) {
              // Lightning: a modest bright prism high in the sky below the
              // thunderhead, sized inside the region cell so it never floods
              // its neighbours.
              clouds.push({
                ring: puffyRing(
                  center.lon,
                  center.lat,
                  (lonHalf > latHalf ? lonHalf : latHalf) * 0.7,
                  hashStr(slug) / 1e9,
                  7,
                  1250,
                  1,
                  1.4,
                  1.05,
                ),
                color: [205, 215, 255, Math.round(lvl * 120)],
                h: 70,
              })
            }
            break
          }
          case 'fog':
            addFogPuffs()
            break
          case 'heatwave':
            // Glowing pulsing outline handled by the regions layer's getLineColor.
            break
          case 'windy':
            addCloudBank({
              count: 4, lobes: 4, shade: [205, 215, 228], alpha: 160,
              altMin: 1700, altMax: 2600, radMin: 420, radMax: 780,
              driftDegPerS: 0.001, latDrift: 0.016,
            })
            addWindStreaks()
            break
          case 'cold':
            // Glowing pulsing outline handled by the regions layer's getLineColor.
            break
        }
      }
      ;(window as any).__weatherBadges = activeSlugs
      ;(window as any).__weatherStats = {
        slugs: activeSlugs,
        clouds: clouds.length,
        fog: fog.length,
        streaks: streaks.length,
      }

      const fx: Layer[] = []
      if (clouds.length) {
        // Clouds & lightning: thin extruded prisms so altitude is real (deck
        // drops non-extruded polygon z to 0, which made clouds lie on the map
        // and block the city), matching how the 3D buildings read.
        fx.push(
          new PolygonLayer({
            id: 'wx-clouds',
            data: clouds,
            getPolygon: (d) => d.ring,
            getFillColor: (d) => d.color,
            getElevation: (d) => d.h ?? 60,
            stroked: true,
            filled: true,
            extruded: true,
            wireframe: false,
            lineWidthUnits: 'pixels',
            lineWidthMinPixels: 2,
            getLineWidth: 3,
            getLineColor: (d: { color: [number, number, number, number] }) =>
              d.color[3] > 60
                ? [d.color[0], d.color[1], d.color[2], Math.min(255, d.color[3] + 28)] as [number, number, number, number]
                : [0, 0, 0, 0] as [number, number, number, number],
          }),
        )
      }
      if (fog.length) {
        // Ground fog stays flat on the streets (soft, no volume, no silhouette).
        fx.push(
          new PolygonLayer({
            id: 'wx-fog',
            data: fog,
            getPolygon: (d) => d.ring,
            getFillColor: (d) => d.color,
            stroked: false,
            filled: true,
            extruded: false,
          }),
        )
      }
      if (streaks.length) {
        fx.push(
          new LineLayer({
            id: 'wx-streaks',
            data: streaks,
            getSourcePosition: (d) => d.source,
            getTargetPosition: (d) => d.target,
            getWidth: 2.4,
            widthUnits: 'pixels',
            getColor: (d) => d.color,
            // Rain/wind must never be depth-culled by buildings, region
            // polygons or the map's own depth buffer — always draw on top.
            parameters: { depthTest: false },
          }),
        )
      }
      return fx
    }

    // Static layers: keep the building extrusion in the scene at every zoom so
    // the city never collapses into a flat map while the player explores.
    const baseLayers = (pose: RatPose) => {
      // Re-run the weather outline accessors at a modest cadence. Without an
      // update trigger, Date.now() inside a deck accessor is not observable.
      const weatherPulseTick = Math.floor(performance.now() / 180)
      // Weather FX go AFTER buildings/regions: translucent geometry still
      // writes depth and depth-culls anything painted behind it, so if the
      // clouds render first they silently hide building extrusion and region
      // outlines that fall underneath. Drawn last, they just blend on top.
      return [
        makeRatLayer(pose),
        makeFleetLayer(),
        makeFootstepsLayer(),
        makeBuildingsLayer(),
        // A broad, translucent pulse sits underneath the crisp event border.
        // As it expands, the opaque border remains fixed and makes the motion
        // read as a soft inward breath rather than a flashing outline.
        regionsLayerRef.current?.clone({
          id: 'regions-weather-pulse',
          pickable: false,
          filled: false,
          getLineWidth: (f: { properties: { name: string } }) => {
             const event = weatherEventsRef.current[f.properties.name]?.event
            if (event !== 'heatwave' && event !== 'cold') return 0
            const phase = hashStr(f.properties.name) / 4294967296
            const pulse = 0.5 + 0.5 * Math.sin(performance.now() / 900 + phase * Math.PI * 2)
            return 5 + pulse * 7
          },
          getLineColor: (f: { properties: { name: string } }) => {
             const event = weatherEventsRef.current[f.properties.name]?.event
            const phase = hashStr(f.properties.name) / 4294967296
            const pulse = 0.5 + 0.5 * Math.sin(performance.now() / 900 + phase * Math.PI * 2)
            const alpha = Math.round(28 + pulse * 34)
            return event === 'heatwave' ? [255, 75, 35, alpha] : [80, 175, 255, alpha]
          },
          updateTriggers: {
            getLineColor: [weatherPulseTick],
            getLineWidth: [weatherPulseTick],
          },
        }),
        regionsLayerRef.current?.clone({
          updateTriggers: {
            getFillColor: [selectedZoneRef.current, ...Object.entries(allocationRef.current).flat()],
            getLineColor: [selectedZoneRef.current, weatherPulseTick],
            getLineWidth: [selectedZoneRef.current, weatherPulseTick],
          },
        }),
        ...makeEventLayers(),
        ...makeWeatherFX(),
      ].filter(Boolean)
    }

    const tick = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.25)
      last = now
      const router = routerRef.current
      if (router) {
        const dispatchRun = dispatchRunRef.current
        if (dispatchRun && dispatchAnimationIdRef.current !== dispatchRun.id) {
          dispatchAnimationIdRef.current = dispatchRun.id
          dispatchAnimationStartedAtRef.current = now
          dispatchRatsRef.current = makeDispatchRats(dispatchRun)
        } else if (!dispatchRun && dispatchAnimationIdRef.current !== null) {
          restingFleetDataRef.current = dispatchFleetData(now)
          dispatchAnimationIdRef.current = null
          dispatchAnimationStartedAtRef.current = null
          dispatchRatsRef.current = []
        }
        // Debug hook: window.__ratOverride pins the rat to a fixed pose so a
        // headless harness can screenshot a controlled orientation.
        const override = (window as any).__ratOverride as
          | { lon: number; lat: number; heading: number }
          | undefined
        const pose = override ?? (() => {
          const moved = router.step(dt, RAT_SPEED_MPS)
          return { lon: moved.point.lon, lat: moved.point.lat, heading: moved.headingDeg }
        })()
        poseRef.current = pose
        ;(window as any).__ratPose = poseRef.current
        // Throttle deck layer updates to ~8fps: recreating the ScenegraphLayer on
        // every frame forces constant GL work for no visible benefit.
        if (now - lastProps > 125) {
          lastProps = now
          if (now - lastDrop >= FOOTSTEP_DROP_MS) {
            lastDrop = now
            footstepsRef.current.push({ ...pose, born: now })
          }
          overlay.setProps({ layers: baseLayers(poseRef.current) })
        }
      }
      rafId = requestAnimationFrame(tick)
    }

    const boot = async () => {
      try {
        loadBuildings().catch((err) => console.error('[buildings]', err))
        const zonesRes = await fetch(ZONES_URL)
        if (disposed) return
        if (!zonesRes.ok) throw new Error(`zones ${zonesRes.status}`)
        const zonesDoc = (await zonesRes.json()) as {
          features: Array<{ properties: { zone: string; borough: string; location_id: number }; geometry: GeoJSON.Geometry }>
        }
        const zones: TaxiZone[] = zonesDoc.features.map((f) => ({
          location_id: f.properties.location_id,
          zone: f.properties.zone,
          borough: f.properties.borough,
          centroid: polygonCentroid(f.geometry),
          geometry: f.geometry,
        }))
        zonesRef.current = zones
        airportGeometryRef.current = zones.find(
          (zone) => zone.location_id === 132 || zone.zone.toLowerCase().includes('jfk'),
        )?.geometry ?? null

        const regionsRes = await fetch(REGIONS_URL)
        if (disposed) return
        if (!regionsRes.ok) throw new Error(`regions ${regionsRes.status}`)
        const regionsDoc = (await regionsRes.json()) as {
          features: Array<{ properties: { name: string }; geometry: GeoJSON.Geometry }>
        }
        const regionFeatures = regionsDoc.features.map((f) => ({
          type: 'Feature' as const,
          properties: { name: f.properties.name },
          geometry: f.geometry,
        }))
        for (const feature of regionsDoc.features) {
          regionCentroidsRef.current[feature.properties.name] = polygonCentroid(feature.geometry)
          regionGeometryRef.current[feature.properties.name] = feature.geometry
        }
        regionsLayerRef.current = new GeoJsonLayer({
          id: 'regions',
          data: regionFeatures as any,
          pickable: true,
          stroked: true,
          filled: true,
          wireframe: false,
          lineWidthUnits: 'pixels',
          lineWidthMinPixels: 1.5,
          getLineWidth: (f: { properties: { name: string } }) => {
            if (selectedZoneRef.current === f.properties.name) return 4.5
             const e = weatherEventsRef.current[f.properties.name] ?? null
            return e?.event === 'heatwave' || e?.event === 'cold' ? 2.2 : 1.5
          },
          getFillColor: (f: { properties: { name: string } }) => {
            const c = REGION_COLORS[f.properties.name] ?? [160, 160, 160]
            const selected = selectedZoneRef.current === f.properties.name
            const taxis = allocationRef.current[f.properties.name] ?? 0
            if (selected) return [245, 194, 24, 150]
             const tinted = tintColor(c, weatherEventsRef.current[f.properties.name] ?? null)
            return [tinted[0], tinted[1], tinted[2], Math.min(125, 32 + taxis * 2)]
          },
          getLineColor: (f: { properties: { name: string } }) => {
            const name = f.properties.name
            if (selectedZoneRef.current === name) return [245, 194, 24, 255]
             const ev = weatherEventsRef.current[name] ?? null
            if (ev?.event === 'heatwave') {
              return [255, 82, 42, 235]
            }
            if (ev?.event === 'cold') {
              return [88, 176, 255, 235]
            }
            const c = REGION_COLORS[name] ?? [200, 200, 200]
            return [c[0], c[1], c[2], 220]
          },
          onHover: (info) => {
            const r = info.object?.properties?.name as string | undefined
            setHovered(r ? r.replace(/_/g, ' ') : null)
          },
          onClick: (info) => {
            const r = info.object?.properties?.name as string | undefined
            if (r) onZoneSelectRef.current?.(r)
          },
        })

        const roadsRes = await fetch(ROADS_URL)
        if (disposed) return
        if (!roadsRes.ok) throw new Error(`roads ${roadsRes.status}`)
        const roads = await roadsRes.json()
        const router = RatRouter.fromGeoJson(roads)
        routerRef.current = router
        const p = router.position
        poseRef.current = { lon: p.lon, lat: p.lat, heading: 0 }
        overlay.setProps({ layers: baseLayers(poseRef.current) })
        rafId = requestAnimationFrame(tick)
      } catch (err) {
        console.error(err)
      }
    }

    boot()

    return () => {
      disposed = true
      cancelAnimationFrame(rafId)
      overlayRef.current = null
      map.remove()
    }
  }, [])

  return (
    <>
      <div ref={containerRef} className="map-container" />
      {hovered && <div className="tooltip">{hovered}</div>}
    </>
  )
}
