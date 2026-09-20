export interface WeatherEvent {
  event: string
  label: string
  emoji: string
  ml_delta: Record<string, number>
  tint?: [number, number, number, number]
  wind?: { dir_deg: number; speed_ms: number }
}

export interface WeatherMetrics {
  temperature_c: number
  rain_mm: number
  wind_mps: number
  visibility_km: number
}

const makeEvent = (
  event: string,
  label: string,
  emoji: string,
  ml_delta: Record<string, number>,
  tint?: [number, number, number, number],
  wind?: { dir_deg: number; speed_ms: number },
): WeatherEvent => ({ event, label, emoji, ml_delta, tint, wind })

// These cutoffs are intentionally explicit: static weather artwork is never
// shown unless the current region's simulated weather crosses one of them.
export function getWeatherEventForMetrics(metrics: WeatherMetrics | undefined): WeatherEvent | null {
  if (!metrics) return null
  if (metrics.rain_mm >= 12 && metrics.wind_mps >= 8) {
    return makeEvent('thunderstorm', 'Thunderstorm', '⛈️', { temp_c: -5, wind_ms: 9, vis_km: -7, precip_mm: 18, precip_3h_mm: 30 }, [36, 40, 54, 80], { dir_deg: 100, speed_ms: metrics.wind_mps })
  }
  if (metrics.rain_mm >= 8) {
    return makeEvent('heavy_rain', 'Heavy rain', '🌧️', { temp_c: -4, wind_ms: 6, vis_km: -9, precip_mm: 16, precip_3h_mm: 35 }, [58, 62, 76, 75], { dir_deg: 85, speed_ms: metrics.wind_mps })
  }
  if (metrics.rain_mm >= 3) {
    return makeEvent('rain', 'Rain', '🌧️', { temp_c: -3, wind_ms: 4, vis_km: -6, precip_mm: 8, precip_3h_mm: 18 }, [92, 100, 120, 60], { dir_deg: 80, speed_ms: metrics.wind_mps })
  }
  if (metrics.rain_mm >= 0.5) {
    return makeEvent('shower', 'Rain shower', '🌦️', { temp_c: -2, wind_ms: 3, vis_km: -4, precip_mm: 4, precip_3h_mm: 9 }, [120, 130, 150, 45], { dir_deg: 70, speed_ms: metrics.wind_mps })
  }
  if (metrics.visibility_km <= 2.5 && metrics.wind_mps <= 4) {
    return makeEvent('fog', 'Fog', '🌫️', { temp_c: -1, wind_ms: 0, vis_km: -10, precip_mm: 0, precip_3h_mm: 1 }, [190, 194, 202, 55])
  }
  if (metrics.temperature_c >= 30) {
    return makeEvent('heatwave', 'Heat wave', '🌡️', { temp_c: 7, wind_ms: 1, vis_km: -1, precip_mm: 0, precip_3h_mm: 0 }, [255, 120, 30, 45])
  }
  if (metrics.temperature_c <= 0) {
    return makeEvent('cold', 'Cold snap', '❄️', { temp_c: -10, wind_ms: 4, vis_km: -1, precip_mm: 0, precip_3h_mm: 0 }, [150, 196, 232, 70])
  }
  if (metrics.wind_mps >= 8) {
    return makeEvent('windy', 'Windy', '🌬️', { temp_c: -1, wind_ms: 8, vis_km: -1, precip_mm: 0, precip_3h_mm: 0 }, [170, 180, 196, 35], { dir_deg: 90, speed_ms: metrics.wind_mps })
  }
  if (metrics.visibility_km <= 12) {
    return makeEvent('cloudy', 'Cloudy', '☁️', { temp_c: 0, wind_ms: 2, vis_km: -3, precip_mm: 0, precip_3h_mm: 0 }, [168, 178, 193, 40])
  }
  return null
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
