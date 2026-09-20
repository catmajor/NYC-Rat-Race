import { readFile, writeFile } from 'node:fs/promises'

const roadsPath = new URL('../public/data/nyc_roads.geojson', import.meta.url)
const regionsPath = new URL('../public/data/regions.geojson', import.meta.url)
const taxiZonesPath = new URL('../public/data/taxi_zones.geojson', import.meta.url)
const outputPath = new URL('../public/data/region_routes.json', import.meta.url)

const roads = JSON.parse(await readFile(roadsPath, 'utf8'))
const regions = JSON.parse(await readFile(regionsPath, 'utf8'))
const taxiZones = JSON.parse(await readFile(taxiZonesPath, 'utf8'))
const keyOf = (point) => `${point[0].toFixed(6)},${point[1].toFixed(6)}`
const distance = (a, b) => {
  const dy = (b[1] - a[1]) * 111320
  const dx = (b[0] - a[0]) * 111320 * Math.cos((a[1] * Math.PI) / 180)
  return Math.hypot(dx, dy)
}
const averageGeometry = (geometry) => {
  const points = []
  const visit = (value) => {
    if (!Array.isArray(value)) return
    if (value.length >= 2 && typeof value[0] === 'number') points.push(value)
    else value.forEach(visit)
  }
  visit(geometry.coordinates)
  return [
    points.reduce((sum, point) => sum + point[0], 0) / points.length,
    points.reduce((sum, point) => sum + point[1], 0) / points.length,
  ]
}

const nodes = []
const nodeIndex = new Map()
const adjacent = []
const nodeAt = (point) => {
  const key = keyOf(point)
  let index = nodeIndex.get(key)
  if (index === undefined) {
    index = nodes.length
    nodeIndex.set(key, index)
    nodes.push([point[0], point[1]])
    adjacent.push([])
  }
  return index
}
for (const feature of roads.features) {
  const coordinates = feature.geometry?.coordinates ?? []
  for (let i = 1; i < coordinates.length; i += 1) {
    const from = nodeAt(coordinates[i - 1])
    const to = nodeAt(coordinates[i])
    const weight = distance(nodes[from], nodes[to])
    adjacent[from].push([to, weight])
    adjacent[to].push([from, weight])
  }
}

const destinations = Object.fromEntries(regions.features.map((feature) => [
  feature.properties.name,
  averageGeometry(feature.geometry),
]))
const jfk = taxiZones.features.find((feature) => feature.properties?.location_id === 132)
if (jfk) destinations.airports = averageGeometry(jfk.geometry)

const nearest = (point, component = null) => {
  let result = 0
  let best = Number.POSITIVE_INFINITY
  for (let index = 0; index < nodes.length; index += 1) {
    const candidate = distance(nodes[index], point)
    if ((component === null || componentOf[index] === component) && candidate < best) {
      best = candidate
      result = index
    }
  }
  return result
}

// Airport road coverage can contain small disconnected service-road islands.
// Use the nearest JFK-area node in the main connected component so a route can
// cross the city graph instead of terminating at an isolated runway node.
const componentOf = new Int32Array(nodes.length)
componentOf.fill(-1)
const componentSizes = []
for (let start = 0; start < nodes.length; start += 1) {
  if (componentOf[start] >= 0) continue
  const component = componentSizes.length
  const queue = [start]
  componentOf[start] = component
  let size = 0
  for (let cursor = 0; cursor < queue.length; cursor += 1) {
    const current = queue[cursor]
    size += 1
    for (const [neighbor] of adjacent[current]) {
      if (componentOf[neighbor] >= 0) continue
      componentOf[neighbor] = component
      queue.push(neighbor)
    }
  }
  componentSizes.push(size)
}
const mainComponent = componentSizes.reduce(
  (best, size, component) => size > componentSizes[best] ? component : best,
  0,
)
const airportNode = nearest(destinations.airports, mainComponent)

const dijkstra = (target, forcedTarget = null) => {
  const targetIndex = forcedTarget ?? nearest(target)
  const distances = new Float64Array(nodes.length)
  const next = new Int32Array(nodes.length)
  distances.fill(Number.POSITIVE_INFINITY)
  next.fill(-1)
  distances[targetIndex] = 0
  const heap = [[0, targetIndex]]
  const push = (entry) => {
    heap.push(entry)
    let index = heap.length - 1
    while (index > 0) {
      const parent = Math.floor((index - 1) / 2)
      if (heap[parent][0] <= heap[index][0]) break
      ;[heap[parent], heap[index]] = [heap[index], heap[parent]]
      index = parent
    }
  }
  const pop = () => {
    if (!heap.length) return undefined
    const result = heap[0]
    const last = heap.pop()
    if (heap.length) {
      heap[0] = last
      let index = 0
      while (true) {
        const left = index * 2 + 1
        const right = left + 1
        let smallest = index
        if (left < heap.length && heap[left][0] < heap[smallest][0]) smallest = left
        if (right < heap.length && heap[right][0] < heap[smallest][0]) smallest = right
        if (smallest === index) break
        ;[heap[index], heap[smallest]] = [heap[smallest], heap[index]]
        index = smallest
      }
    }
    return result
  }
  while (heap.length) {
    const [currentDistance, current] = pop()
    if (currentDistance !== distances[current]) continue
    for (const [neighbor, weight] of adjacent[current]) {
      const candidate = currentDistance + weight
      if (candidate >= distances[neighbor]) continue
      distances[neighbor] = candidate
      next[neighbor] = current
      push([candidate, neighbor])
    }
  }
  return { targetIndex, next }
}

const names = Object.keys(destinations)
const trees = Object.fromEntries(names.map((name) => [
  name,
  dijkstra(destinations[name], name === 'airports' ? airportNode : null),
]))
const routes = {}
for (const source of names) {
  routes[source] = {}
  const sourceIndex = source === 'airports' ? airportNode : nearest(destinations[source])
  for (const target of names) {
    const tree = trees[target]
    const path = []
    let current = sourceIndex
    let guard = 0
    while (current >= 0 && guard < nodes.length) {
      path.push(nodes[current])
      if (current === tree.targetIndex) break
      current = tree.next[current]
      guard += 1
    }
    routes[source][target] = path.length > 1 && current === tree.targetIndex
      ? path.map(([lon, lat]) => ({ lon, lat }))
      : []
  }
}

await writeFile(outputPath, `${JSON.stringify({ version: 1, routes }, null, 0)}\n`)
console.log(`wrote ${outputPath.pathname} from ${nodes.length} nodes`)
