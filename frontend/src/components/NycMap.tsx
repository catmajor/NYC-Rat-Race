import { useEffect, useRef, useState } from 'react'
import { Map as MapLibreMap, NavigationControl, type StyleSpecification } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { LightingEffect, AmbientLight, DirectionalLight } from '@deck.gl/core'
import { GeoJsonLayer, ScatterplotLayer } from '@deck.gl/layers'
import { ScenegraphLayer } from '@deck.gl/mesh-layers'
import { RatRouter } from '../lib/ratRouter'
import { zoneAt, polygonCentroid, type TaxiZone } from '../lib/zones'

// Tune-by-eye constants (visual calibration happens in the browser):
export const RAT_SIZE_SCALE = 50// rat.glb is ~1 world unit; this makes the rat
// a ~1.5 km-wide "giant rat taxi" so it is visible at city zoom (~57 m/px at z11.4).
export const RAT_SPEED_MPS = 55 // playful "taxi rat" ground speed

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
// NYC building footprints + Overture heights, pre-extracted to GeoJSON
// (5 boroughs + EWR, filtered to height >= 10 m). Attribution required per
// ODbL: shown in map credits.
const BUILDINGS_URL = '/data/buildings.geojson'

const BOROUGH_COLORS: Record<string, [number, number, number]> = {
  Manhattan: [242, 148, 60],
  Brooklyn: [72, 158, 218],
  Queens: [106, 199, 108],
  Bronx: [232, 99, 99],
  'Staten Island': [167, 140, 222],
  EWR: [180, 180, 180],
}

const MAP_STYLE: StyleSpecification = {
  version: 8,
  glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
  sources: {
    carto: {
      type: 'raster',
      tiles: ['https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors, © CARTO',
    },
  },
  layers: [
    { id: 'carto-basemap', type: 'raster', source: 'carto' },
  ],
}

interface RatPose {
  lon: number
  lat: number
  heading: number
}

export default function NycMap() {
  const containerRef = useRef<HTMLDivElement>(null)
  const overlayRef = useRef<MapboxOverlay | null>(null)
  const routerRef = useRef<RatRouter | null>(null)
  const zonesRef = useRef<TaxiZone[]>([])
  const zonesLayerRef = useRef<GeoJsonLayer | null>(null)
  // Building data is kept separate from the layer so the layer can be rebuilt
  // with a different `visible` flag on zoom-gate crossings. Rebuilding preserves
  // the same `data` reference, so deck reuses GPU buffers instead of unloading
  // (removing the layer from the array permanently destroys it — the layer would
  // never come back after zooming back in).
  const buildingsDataRef = useRef<Array<any> | null>(null)
  const zoomRef = useRef(11.4)
  const poseRef = useRef<RatPose>({ lon: -73.985, lat: 40.755, heading: 0 })
  const footstepsRef = useRef<Array<{ lon: number; lat: number; born: number }>>([])

  const [status, setStatus] = useState<string>('loading map…')
  const [inZone, setInZone] = useState<string>('')
  const [inBorough, setInBorough] = useState<string>('')
  const [hovered, setHovered] = useState<string | null>(null)

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
      sun: new DirectionalLight({ color: [255, 255, 255], intensity: 3.0, direction: [30, 80, 50] }),
      fill: new DirectionalLight({ color: [255, 214, 170], intensity: 1.0, direction: [-60, -40, -60] }),
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

    // Building layer factory: extruded footprints colored by the taxi zone
    // borough they were joined to (see data prep). `visible` toggles the
    // z12+ zoom gate without removing the layer from the scene.
    const makeBuildingsLayer = (visible: boolean): GeoJsonLayer | null => {
      const feats = buildingsDataRef.current
      if (!feats) return null
      const w = window as any
      const heightScale = w.__buildingHeightScale ?? BUILDING_HEIGHT_SCALE
      return new GeoJsonLayer({
        id: 'buildings',
        data: feats as any,
        visible,
        extruded: true,
        pickable: false,
        stroked: false,
        filled: true,
        wireframe: false,
        opacity: 0.9,
        getElevation: (f: { properties: { height: number } }) => f.properties.height,
        elevationScale: heightScale,
        getFillColor: (f: { properties: { borough: string } }) => {
          const c = BOROUGH_COLORS[f.properties.borough] ?? [170, 170, 170]
          return [c[0], c[1], c[2], 200]
        },
        material: {
          ambient: 0.35,
          diffuse: 0.75,
          shininess: 8,
          specularColor: [60, 60, 60],
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

    // Static layers: 3D building extrusion appears only when zoomed in, keeping
    // the city-wide roaming view cheap. Buildings always stay in the layer list
    // (just hidden), otherwise deck unloads them and they never return after
    // zooming back in.
    const baseLayers = (pose: RatPose) => {
      const showTiles = zoomRef.current >= 12
      return [
        makeRatLayer(pose),
        makeFootstepsLayer(),
        makeBuildingsLayer(showTiles),
        zonesLayerRef.current,
      ].filter(Boolean)
    }

    const tick = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.25)
      last = now
      const router = routerRef.current
      if (router) {
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

        const features = zones.map((z) => ({
          type: 'Feature' as const,
          properties: z,
          geometry: z.geometry,
        }))
        zonesLayerRef.current = new GeoJsonLayer({
          id: 'zones',
          data: features,
          pickable: true,
          stroked: true,
          filled: true,
          wireframe: false,
          lineWidthUnits: 'pixels',
          lineWidthMinPixels: 1,
          getFillColor: (f) => {
            const c = BOROUGH_COLORS[f.properties.borough] ?? [160, 160, 160]
            return [c[0], c[1], c[2], 55]
          },
          getLineColor: (f) => {
            const c = BOROUGH_COLORS[f.properties.borough] ?? [200, 200, 200]
            return [c[0], c[1], c[2], 230]
          },
          onHover: (info) => {
            const z = info.object?.properties as TaxiZone | undefined
            setHovered(z ? `${z.zone} · ${z.borough}` : null)
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
        setStatus(`roaming ${router.nodeCount.toLocaleString()} road nodes`)
        overlay.setProps({ layers: baseLayers(poseRef.current) })
        rafId = requestAnimationFrame(tick)
      } catch (err) {
        console.error(err)
        setStatus(`failed to load: ${String(err)}`)
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

  // Update the HUD a few times per second, not every frame.
  useEffect(() => {
    const id = window.setInterval(() => {
      const router = routerRef.current
      if (!router) return
      const z = zoneAt(zonesRef.current, poseRef.current.lon, poseRef.current.lat)
      setInZone(z ? z.zone : '—')
      setInBorough(z ? z.borough : '')
      setStatus('roaming')
    }, 400)
    return () => window.clearInterval(id)
  }, [])

  return (
    <>
      <div ref={containerRef} className="map-container" />
      <div className="hud">
        <div className="hud-row">
          <span className="hud-label">ZONE</span>
          <span className="hud-value">{inZone || '—'}</span>
          <span className="hud-sub">{inBorough}</span>
        </div>
        <div className="hud-row">
          <span className="hud-label">UNIT</span>
          <span className="hud-value">RAT-01</span>
          <span className="hud-sub">{status}</span>
        </div>
        <div className="hud-credit">buildings © Overture / ODbL</div>
      </div>
      {hovered && <div className="tooltip">{hovered}</div>}
    </>
  )
}
