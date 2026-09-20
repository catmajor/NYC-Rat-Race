export interface WeatherEvent {
  event: string
  label: string
  emoji: string
  ml_delta: Record<string, number>
  tint?: [number, number, number, number]
  wind?: { dir_deg: number; speed_ms: number }
}

export interface WeatherConfig {
  default: WeatherEvent
  regions: Record<string, {
    current: string
    possible: WeatherEvent[]
    wind?: { dir_deg: number; speed_ms: number }
  }>
}

export function getActiveWeatherEvent(weather: WeatherConfig | null, slug: string): WeatherEvent | null {
  if (!weather) return null
  const override = (window as any).__weatherOverride as Record<string, string> | undefined
  const currentId = override?.[slug] ?? weather.regions?.[slug]?.current ?? weather.default.event
  if (currentId === weather.default.event) return null
  const found = weather.regions?.[slug]?.possible.find((event) => event.event === currentId)
  if (!found) return null
  return found
}
