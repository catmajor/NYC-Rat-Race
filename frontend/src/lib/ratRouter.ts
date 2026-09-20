// Road graph + random walk for the rat.
// Builds a node/edge graph from a GeoJSON LineString road network, then
// walks from one connected node to the next at a constant ground speed.

export interface RoadPoint {
  lon: number
  lat: number
}

export interface RoadBounds {
  minLon: number
  maxLon: number
  minLat: number
  maxLat: number
}

function segmentMeters(a: RoadPoint, b: RoadPoint): number {
  const dy = (b.lat - a.lat) * 111_320
  const dx = (b.lon - a.lon) * 111_320 * Math.cos((a.lat * Math.PI) / 180)
  return Math.sqrt(dx * dx + dy * dy)
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

export class RatRouter {
  private nodes: RoadNode[] = []
  private readonly nodeGrid = new Map<string, number[]>()
  private precomputedRoutes = new Map<string, Map<string, RoadPoint[]>>()
  private static readonly GRID_SIZE = 0.005
  private static readonly A_STAR_HEURISTIC_WEIGHT = 2.75
  private pos: RoadPoint
  private targetIndex: number
  private prevIndex = -1

  private constructor(nodes: RoadNode[], startIndex: number) {
    this.nodes = nodes
    nodes.forEach((node, index) => {
      const key = this.gridKey(node)
      const bucket = this.nodeGrid.get(key) ?? []
      bucket.push(index)
      this.nodeGrid.set(key, bucket)
    })
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

  /** Deterministically sample actual road nodes matching a region predicate. */
  sampleRoadPoints(
    predicate: (point: RoadPoint) => boolean,
    count: number,
    seed: number,
    bounds?: RoadBounds,
  ): RoadPoint[] {
    const selected: RoadPoint[] = []
    let state = seed >>> 0
    let seen = 0
    const random = () => {
      state = (state + 0x6d2b79f5) | 0
      let value = Math.imul(state ^ (state >>> 15), 1 | state)
      value = (value + Math.imul(value ^ (value >>> 7), 61 | value)) ^ value
      return ((value ^ (value >>> 14)) >>> 0) / 4294967296
    }
    const candidates = bounds
      ? (() => {
        const indexes: number[] = []
        const minX = Math.floor(bounds.minLon / RatRouter.GRID_SIZE)
        const maxX = Math.floor(bounds.maxLon / RatRouter.GRID_SIZE)
        const minY = Math.floor(bounds.minLat / RatRouter.GRID_SIZE)
        const maxY = Math.floor(bounds.maxLat / RatRouter.GRID_SIZE)
        for (let x = minX; x <= maxX; x += 1) {
          for (let y = minY; y <= maxY; y += 1) indexes.push(...(this.nodeGrid.get(`${x}:${y}`) ?? []))
        }
        return indexes
      })()
      : this.nodes.map((_, index) => index)
    for (const index of candidates) {
      const node = this.nodes[index]
      if (!predicate(node)) continue
      seen += 1
      if (selected.length < count) {
        selected.push({ lon: node.lon, lat: node.lat })
      } else {
        const replacement = Math.floor(random() * seen)
        if (replacement < count) selected[replacement] = { lon: node.lon, lat: node.lat }
      }
    }
    return selected
  }

  private nearestNode(point: RoadPoint): number {
    let nearest = 0
    let distance = Number.POSITIVE_INFINITY
    const cellX = Math.floor(point.lon / RatRouter.GRID_SIZE)
    const cellY = Math.floor(point.lat / RatRouter.GRID_SIZE)
    for (let radius = 0; radius <= 4; radius += 1) {
      for (let x = cellX - radius; x <= cellX + radius; x += 1) {
        for (let y = cellY - radius; y <= cellY + radius; y += 1) {
          for (const index of this.nodeGrid.get(`${x}:${y}`) ?? []) {
            const node = this.nodes[index]
            const candidate = (node.lon - point.lon) ** 2 + (node.lat - point.lat) ** 2
            if (candidate < distance) {
              distance = candidate
              nearest = index
            }
          }
        }
      }
      if (distance < Number.POSITIVE_INFINITY) return nearest
    }
    return nearest
  }

  private gridKey(point: RoadPoint): string {
    return `${Math.floor(point.lon / RatRouter.GRID_SIZE)}:${Math.floor(point.lat / RatRouter.GRID_SIZE)}`
  }

  /** Return the shortest connected road-node path between two map points. */
  routeBetween(start: RoadPoint, target: RoadPoint): RoadPoint[] {
    return this.aStarRoute(start, target)
  }

  setPrecomputedRoutes(routes: Record<string, Record<string, RoadPoint[]>>): void {
    this.precomputedRoutes = new Map(
      Object.entries(routes).map(([source, destinations]) => [source, new Map(Object.entries(destinations))]),
    )
  }

  precomputedRoute(source: string, target: string): RoadPoint[] | null {
    return this.precomputedRoutes.get(source)?.get(target) ?? null
  }

  /** Find a road path with A*, using geographic distance as the heuristic. */
  aStarRoute(start: RoadPoint, target: RoadPoint): RoadPoint[] {
    const startIndex = this.nearestNode(start)
    const targetIndex = this.nearestNode(target)
    if (startIndex === targetIndex) return [{ ...this.nodes[startIndex] }]

    const previous = new Map<number, number>()
    const scores = new Map<number, number>([[startIndex, 0]])
    const open: Array<[number, number]> = []
    const closed = new Set<number>()
    const pushOpen = (entry: [number, number]) => {
      open.push(entry)
      let index = open.length - 1
      while (index > 0) {
        const parent = Math.floor((index - 1) / 2)
        if (open[parent][0] <= open[index][0]) break
        ;[open[parent], open[index]] = [open[index], open[parent]]
        index = parent
      }
    }
    const popOpen = (): [number, number] | undefined => {
      if (!open.length) return undefined
      const result = open[0]
      const last = open.pop()!
      if (open.length) {
        open[0] = last
        let index = 0
        while (true) {
          const left = index * 2 + 1
          const right = left + 1
          let smallest = index
          if (left < open.length && open[left][0] < open[smallest][0]) smallest = left
          if (right < open.length && open[right][0] < open[smallest][0]) smallest = right
          if (smallest === index) break
          ;[open[index], open[smallest]] = [open[smallest], open[index]]
          index = smallest
        }
      }
      return result
    }
    let closest = startIndex
    let closestHeuristic = this.heuristic(startIndex, target)
    let expanded = 0
    pushOpen([this.heuristic(startIndex, target) * RatRouter.A_STAR_HEURISTIC_WEIGHT, startIndex])
    while (open.length && expanded < 20_000) {
      const entry = popOpen()
      if (!entry) break
      const [, current] = entry
      if (closed.has(current)) continue
      if (current === targetIndex) return this.reconstructPath(previous, current)
      closed.add(current)
      expanded += 1
      const currentHeuristic = this.heuristic(current, target)
      if (currentHeuristic < closestHeuristic) {
        closest = current
        closestHeuristic = currentHeuristic
      }
      const currentScore = scores.get(current) ?? Number.POSITIVE_INFINITY
      for (const neighbor of this.nodes[current].neighbors) {
        if (closed.has(neighbor)) continue
        const candidate = currentScore + segmentMeters(this.nodes[current], this.nodes[neighbor])
        if (candidate >= (scores.get(neighbor) ?? Number.POSITIVE_INFINITY)) continue
        scores.set(neighbor, candidate)
        previous.set(neighbor, current)
        pushOpen([
          candidate + this.heuristic(neighbor, target) * RatRouter.A_STAR_HEURISTIC_WEIGHT,
          neighbor,
        ])
      }
    }

    return this.reconstructPath(previous, closest)
  }

  /** Walk only along road edges whose nodes satisfy the region predicate. */
  boundedRandomWalk(
    start: RoadPoint,
    predicate: (point: RoadPoint) => boolean,
    steps: number,
    seed: number,
  ): RoadPoint[] {
    const random = this.seededRandom(seed)
    let current = this.nearestNode(start)
    let previous = -1
    const path: RoadPoint[] = [{ lon: this.nodes[current].lon, lat: this.nodes[current].lat }]
    for (let step = 0; step < steps; step += 1) {
      const choices = this.nodes[current].neighbors.filter((neighbor) => (
        neighbor !== previous && predicate(this.nodes[neighbor])
      ))
      if (!choices.length) break
      const next = choices[Math.floor(random() * choices.length)]
      previous = current
      current = next
      path.push({ lon: this.nodes[current].lon, lat: this.nodes[current].lat })
    }
    return path
  }

  /** Sample a route by normalized distance, returning a road heading. */
  static pointAlongRoute(route: RoadPoint[], progress: number): { point: RoadPoint; headingDeg: number } {
    if (route.length < 2) return { point: route[0] ?? { lon: 0, lat: 0 }, headingDeg: 0 }
    const clamped = Math.max(0, Math.min(1, progress))
    return RatRouter.pointAlongRouteMeters(route, RatRouter.routeLength(route) * clamped)
  }

  static pointAlongRouteMeters(route: RoadPoint[], distance: number): { point: RoadPoint; headingDeg: number } {
    if (route.length < 2) return { point: route[0] ?? { lon: 0, lat: 0 }, headingDeg: 0 }
    const lengths = route.slice(1).map((point, index) => segmentMeters(route[index], point))
    let remaining = Math.max(0, Math.min(RatRouter.routeLength(route), distance))
    for (let index = 0; index < lengths.length; index += 1) {
      const length = lengths[index]
      if (remaining <= length || index === lengths.length - 1) {
        const from = route[index]
        const to = route[index + 1]
        const ratio = length ? Math.min(1, remaining / length) : 1
        return {
          point: {
            lon: from.lon + (to.lon - from.lon) * ratio,
            lat: from.lat + (to.lat - from.lat) * ratio,
          },
          headingDeg: (Math.atan2(to.lon - from.lon, to.lat - from.lat) * 180) / Math.PI,
        }
      }
      remaining -= length
    }
    return { point: route[route.length - 1], headingDeg: 0 }
  }

  static routeLength(route: RoadPoint[]): number {
    return route.slice(1).reduce((sum, point, index) => sum + segmentMeters(route[index], point), 0)
  }

  private heuristic(index: number, target: RoadPoint): number {
    return segmentMeters(this.nodes[index], target)
  }

  private reconstructPath(previous: Map<number, number>, destination: number): RoadPoint[] {
    const path: RoadPoint[] = []
    for (let current = destination; ; current = previous.get(current) ?? current) {
      path.push({ lon: this.nodes[current].lon, lat: this.nodes[current].lat })
      if (!previous.has(current)) break
    }
    return path.reverse()
  }

  private seededRandom(seed: number): () => number {
    let state = seed >>> 0
    return () => {
      state = (state + 0x6d2b79f5) | 0
      let value = Math.imul(state ^ (state >>> 15), 1 | state)
      value = (value + Math.imul(value ^ (value >>> 7), 61 | value)) ^ value
      return ((value ^ (value >>> 14)) >>> 0) / 4294967296
    }
  }
}
