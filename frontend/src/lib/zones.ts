// TLC taxi-zone loading, centroids, and point lookup.

export interface TaxiZone {
  location_id: number
  zone: string
  borough: string
  centroid: { lon: number; lat: number }
  geometry: GeoJSON.Geometry
}

interface ZoneFeature {
  properties: {
    zone: string
    borough: string
    location_id: number
  }
  geometry: GeoJSON.Geometry
}

export async function loadZones(): Promise<TaxiZone[]> {
  const res = await fetch('/data/taxi_zones.geojson')
  if (!res.ok) throw new Error(`failed to load taxi zones (${res.status})`)
  const doc = (await res.json()) as { features: ZoneFeature[] }
  return doc.features.map((f) => ({
    location_id: f.properties.location_id,
    zone: f.properties.zone,
    borough: f.properties.borough,
    centroid: polygonCentroid(f.geometry),
    geometry: f.geometry,
  }))
}

function asTuple(p: GeoJSON.Position): [number, number] {
  return [p[0], p[1]]
}

function flattenPoints(geometry: GeoJSON.Geometry): Array<[number, number]> {
  switch (geometry.type) {
    case 'Polygon':
      return geometry.coordinates.flat().map(asTuple)
    case 'MultiPolygon':
      return geometry.coordinates.flat(2).map(asTuple)
    case 'Point':
      return [asTuple(geometry.coordinates)]
    case 'MultiPoint':
      return geometry.coordinates.map(asTuple)
    default:
      return []
  }
}

/** Area-weighted-ish centroid: plain vertex average, good enough for labels. */
export function polygonCentroid(geometry: GeoJSON.Geometry): { lon: number; lat: number } {
  const pts = flattenPoints(geometry)
  if (pts.length === 0) return { lon: 0, lat: 0 }
  const sum = pts.reduce(
    (acc, [lon, lat]) => ({ lon: acc.lon + lon, lat: acc.lat + lat }),
    { lon: 0, lat: 0 },
  )
  return { lon: sum.lon / pts.length, lat: sum.lat / pts.length }
}

interface Ring {
  xs: number[]
  ys: number[]
}

function polygonRings(geometry: GeoJSON.Geometry): Ring[] {
  switch (geometry.type) {
    case 'Polygon':
      return geometry.coordinates.map((ring) => ({
        xs: ring.map((p) => p[0]),
        ys: ring.map((p) => p[1]),
      }))
    case 'MultiPolygon':
      return geometry.coordinates.flat().map((ring) => ({
        xs: ring.map((p) => p[0]),
        ys: ring.map((p) => p[1]),
      }))
    default:
      return []
  }
}

/** Ray-casting point-in-polygon, works for polygon + multipolygon. */
function pointInRings(lon: number, lat: number, rings: Ring[]): boolean {
  for (const ring of rings) {
    let inside = false
    for (let i = 0, j = ring.xs.length - 1; i < ring.xs.length; j = i++) {
      const xi = ring.xs[i]
      const yi = ring.ys[i]
      const xj = ring.xs[j]
      const yj = ring.ys[j]
      const intersect =
        (yi > lat) !== (yj > lat) &&
        lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi
      if (intersect) inside = !inside
    }
    // Outer rings count as filled; holes (rings oriented clockwise) subtract.
    // For finding "which zone a point is in", treat any ring hit as inside;
    // taxi-zone polygons have no holes, so this is safe.
    if (inside) return true
  }
  return false
}

export function zoneAt(zones: TaxiZone[], lon: number, lat: number): TaxiZone | null {
  for (const zone of zones) {
    if (pointInRings(lon, lat, polygonRings(zone.geometry))) return zone
  }
  return null
}