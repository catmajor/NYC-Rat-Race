// Road graph + random walk for the rat.
// Builds a node/edge graph from a GeoJSON LineString road network, then
// walks from one connected node to the next at a constant ground speed.

export interface RoadPoint {
  lon: number
  lat: number
}

interface RoadNode extends RoadPoint {
  neighbors: number[]
}

interface RoadGeoJson {
  type: 'FeatureCollection'
  features: Array<{
    geometry: { type: 'LineString'; coordinates: number[][] }
  }>
}

function nodeKey(lon: number, lat: number): string {
  // 6 decimal digits ~ 0.1 m; matches how the overpass coords were rounded.
  return `${lon.toFixed(6)},${lat.toFixed(6)}`
}

function segmentMeters(a: RoadPoint, b: RoadPoint): number {
  const dy = (b.lat - a.lat) * 111_320
  const dx = (b.lon - a.lon) * 111_320 * Math.cos((a.lat * Math.PI) / 180)
  return Math.sqrt(dx * dx + dy * dy)
}

export class RatRouter {
  private nodes: RoadNode[] = []
  private pos: RoadPoint
  private targetIndex: number
  private prevIndex = -1

  private constructor(nodes: RoadNode[], startIndex: number) {
    this.nodes = nodes
    this.targetIndex = startIndex
    this.pos = { lon: nodes[startIndex].lon, lat: nodes[startIndex].lat }
  }

  static fromGeoJson(doc: RoadGeoJson): RatRouter {
    const nodes: RoadNode[] = []
    const index = new Map<string, number>()

    const nodeAt = (p: RoadPoint): number => {
      const key = nodeKey(p.lon, p.lat)
      let i = index.get(key)
      if (i === undefined) {
        i = nodes.length
        nodes.push({ ...p, neighbors: [] })
        index.set(key, i)
      }
      return i
    }

    for (const feature of doc.features) {
      const coords = feature.geometry?.coordinates
      if (!coords || coords.length < 2) continue
      let prev = nodeAt({ lon: coords[0][0], lat: coords[0][1] })
      for (let k = 1; k < coords.length; k++) {
        const curr = nodeAt({ lon: coords[k][0], lat: coords[k][1] })
        nodes[prev].neighbors.push(curr)
        nodes[curr].neighbors.push(prev)
        prev = curr
      }
    }

    if (nodes.length === 0) {
      throw new Error('road network is empty; run scripts/fetch-nyc-data.mjs')
    }
    const start = Math.floor(Math.random() * nodes.length)
    const router = new RatRouter(nodes, start)
    router.pickNext()
    return router
  }

  /** Pick the next target, avoiding an immediate U-turn when possible. */
  private pickNext(): void {
    const node = this.nodes[this.targetIndex]
    let pool = node.neighbors
    if (pool.length > 1 && this.prevIndex >= 0) {
      pool = pool.filter((i) => i !== this.prevIndex)
    }
    const fallback = node.neighbors
    const choice = (pool.length > 0 ? pool : fallback)
    if (choice.length === 0) {
      this.prevIndex = this.targetIndex
      this.targetIndex = 0
      return
    }
    this.prevIndex = this.targetIndex
    this.targetIndex = choice[Math.floor(Math.random() * choice.length)]
  }

  /**
   * Advance along roads by dt seconds at the given ground speed.
   * Returns the new position and the heading (degrees, 0 = north, 90 = east).
   */
  step(dtSeconds: number, metersPerSecond: number): { point: RoadPoint; headingDeg: number } {
    if (metersPerSecond <= 0) return { point: this.pos, headingDeg: 0 }
    let remaining = dtSeconds

    while (remaining > 0) {
      const target = this.nodes[this.targetIndex]
      const dist = segmentMeters(this.pos, target)
      const stepMeters = metersPerSecond * remaining
      if (stepMeters < dist) {
        const t = stepMeters / dist
        const next = {
          lon: this.pos.lon + (target.lon - this.pos.lon) * t,
          lat: this.pos.lat + (target.lat - this.pos.lat) * t,
        }
        const bearing = (Math.atan2(target.lon - this.pos.lon, target.lat - this.pos.lat) * 180) / Math.PI
        this.pos = next
        return { point: next, headingDeg: (bearing + 360) % 360 }
      }
      remaining -= dist / metersPerSecond
      this.pos = { lon: target.lon, lat: target.lat }
      this.pickNext()
    }

    return { point: this.pos, headingDeg: 0 }
  }

  get position(): RoadPoint {
    return this.pos
  }

  get nodeCount(): number {
    return this.nodes.length
  }
}