import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import NycMap from './components/NycMap.tsx'
import { getActiveWeatherEvent, type WeatherConfig } from './lib/weatherEvents'

const ZONE_IDS = [
  'harlem',
  'upper_west',
  'upper_east',
  'midtown',
  'downtown',
  'north_brooklyn',
  'south_brooklyn',
  'queens_west',
  'airports',
  'queens_east',
  'bronx',
  'staten_island',
] as const

type ZoneId = (typeof ZONE_IDS)[number]
const REGION_IDS = ZONE_IDS
type RegionId = ZoneId
type NumericMap = Record<string, number>

interface ZoneState {
  id: ZoneId
  name: string
  recent_demand: number
  baseline_demand: number
  model_forecast: number
  historic_mean: number
  model_share: number
  idle_taxis: number
  trend: 'up' | 'flat'
  weather?: WeatherState
}

interface WeatherState {
  label: string
  temperature_c: number
  rain_mm: number
  wind_mps: number
  visibility_km: number
  note: string
}

interface NewsItem {
  zone: string
  zone_id: ZoneId
  headline: string
  age: string
  intensity: 'low' | 'medium' | 'high'
}

interface GameState {
  game_id: string
  day: number
  round: number
  total_rounds: number
  timestamp: string
  time_start: string
  time_end: string
  fleet_size: number
  fleet_available: number
  assigned: number
  currency: number
  score: number
  rounds_completed: number
  total_trips_captured: number
  total_net_revenue: number
  average_model_match: number
  best_model_match: number
  zones: Record<ZoneId, ZoneState>
  weekday_label: string
  weather: WeatherState
  events: NumericMap
  news: NewsItem[]
  model_name: string
  model_confidence: number
  data_source: string
  completed: boolean
  game_over_reason: 'turns_complete' | 'time_expired' | null
}

interface AdviserResponse {
  adviser_id: string
  adviser_name: string
  specialty: string
  recommendation: string
  confidence: number
  moves: Array<{ from_zone: string; to_zone: string; taxi_count: number }>
  evidence: Array<{ label: string; detail: string; zone_id?: string }>
  forecast: { demand_by_zone: NumericMap; predicted_revenue: number }
  data_source: string
  narrative_source: string
}

interface RoundResult {
  day: number
  round: number
  time_start: string
  time_end: string
  gross_revenue: number
  reposition_cost: number
  net_revenue: number
  score_gain: number
  total_score: number
  currency: number
  trips_captured: number
  trips_model: number
  model_match_percentage: number
  model_allocation: Record<ZoneId, number>
  verdict: string
}

const LABELS: Record<ZoneId, string> = {
  harlem: 'Harlem',
  upper_west: 'Upper West',
  upper_east: 'Upper East',
  midtown: 'Midtown',
  downtown: 'Downtown',
  north_brooklyn: 'North Brooklyn',
  south_brooklyn: 'South Brooklyn',
  queens_west: 'Queens West',
  airports: 'Airports',
  queens_east: 'Queens East',
  bronx: 'The Bronx',
  staten_island: 'Staten Island',
}

const REGION_LABELS: Record<RegionId, string> = {
  ...LABELS,
  queens_east: 'Queens East',
  bronx: 'The Bronx',
  staten_island: 'Staten Island',
}

const ADVISER_IDS = ['twitch', 'stormy', 'grandpa', 'gossip'] as const
type AdviserId = (typeof ADVISER_IDS)[number]

const ADVISER_PROFILES: Record<AdviserId, { specialty: string; role: string; filter: string; accent: string }> = {
  twitch: { specialty: 'MOMENTUM', role: 'Mobility', filter: 'sepia(0.7) saturate(0.8) contrast(1.15)', accent: '#f2bf24' },
  stormy: { specialty: 'WEATHER', role: 'Weather', filter: 'hue-rotate(165deg) saturate(0.55) contrast(1.12)', accent: '#87a6af' },
  grandpa: { specialty: 'ANALOGUES', role: 'History', filter: 'grayscale(0.55) sepia(0.4) contrast(1.2)', accent: '#d8a767' },
  gossip: { specialty: 'NEWS / EVENTS', role: 'Events', filter: 'hue-rotate(300deg) saturate(0.6) contrast(1.15)', accent: '#c99aa0' },
}

const INITIAL_ALLOCATION: Record<ZoneId, number> = {
  harlem: 7,
  upper_west: 10,
  upper_east: 12,
  midtown: 30,
  downtown: 18,
  north_brooklyn: 12,
  south_brooklyn: 6,
  queens_west: 12,
  airports: 18,
  queens_east: 6,
  bronx: 4,
  staten_island: 2,
}

function makeFallbackState(): GameState {
  const forecast: Record<ZoneId, number> = {
    harlem: 31,
    upper_west: 34,
    upper_east: 43,
    midtown: 88,
    downtown: 64,
    north_brooklyn: 48,
    south_brooklyn: 29,
    queens_west: 43,
    airports: 58,
    queens_east: 37,
    bronx: 32,
    staten_island: 18,
  }
  const recent: Record<ZoneId, number> = {
    harlem: 28,
    upper_west: 29,
    upper_east: 35,
    midtown: 71,
    downtown: 52,
    north_brooklyn: 44,
    south_brooklyn: 25,
    queens_west: 39,
    airports: 49,
    queens_east: 31,
    bronx: 27,
    staten_island: 15,
  }
  const forecastTotal = Object.values(forecast).reduce((sum, value) => sum + value, 0)
  const zones = Object.fromEntries(
    ZONE_IDS.map((id) => [id, {
      id,
      name: LABELS[id],
      recent_demand: recent[id],
      baseline_demand: Math.round(forecast[id] * 0.82),
       model_forecast: forecast[id],
       historic_mean: Math.round(forecast[id] * 0.72),
      model_share: forecast[id] / forecastTotal,
      idle_taxis: INITIAL_ALLOCATION[id],
      trend: forecast[id] > recent[id] ? 'up' : 'flat',
    }]),
  ) as Record<ZoneId, ZoneState>
  return {
    game_id: 'rat-cab-preview',
    day: 1,
    round: 1,
    total_rounds: 12,
    timestamp: '2024-10-18T08:00:00',
    time_start: '08:00',
    time_end: '11:00',
    fleet_size: 130,
    fleet_available: 130,
    assigned: 130,
    currency: 2450,
    score: 0,
    rounds_completed: 0,
    total_trips_captured: 0,
    total_net_revenue: 0,
    average_model_match: 0,
    best_model_match: 0,
    zones,
    weather: {
      label: 'Bright / crisp',
      temperature_c: 17,
      rain_mm: 0,
      wind_mps: 3.4,
      visibility_km: 10,
      note: 'Good visibility across the core',
    },
    weekday_label: 'FRIDAY',
    events: { event_count: 4, num_articles: 10, midtown: 1.35 },
    news: [{ zone: 'MIDTOWN', zone_id: 'midtown', headline: 'Morning arrivals are compressing around Penn Station', age: '12 min ago', intensity: 'high' }],
    model_name: 'Demand model / scenario replay',
    model_confidence: 0.78,
    data_source: 'local-preview',
    completed: false,
    game_over_reason: null,
  }
}

const currency = (value: number) => new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
}).format(value)

const shortNumber = (value: number) => new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(value)

const allocationFromState = (state: GameState): Record<ZoneId, number> =>
  Object.fromEntries(ZONE_IDS.map((zoneId) => [zoneId, state.zones[zoneId].idle_taxis])) as Record<ZoneId, number>

const numericSignals = (record: Record<string, unknown>): NumericMap =>
  Object.fromEntries(Object.entries(record).filter(([, value]) => typeof value === 'number')) as NumericMap

function fallbackAdviser(id: AdviserId, state: GameState): AdviserResponse {
  const profile = ADVISER_PROFILES[id]
  const topZone = ZONE_IDS.reduce((best, zoneId) => state.zones[zoneId].model_forecast > state.zones[best].model_forecast ? zoneId : best, ZONE_IDS[0])
  const name = id === 'grandpa' ? 'Grandpa' : id[0].toUpperCase() + id.slice(1)
  return {
    adviser_id: id,
    adviser_name: name,
    specialty: profile.specialty.toLowerCase().replaceAll(' / ', '_').replaceAll(' ', '_'),
    recommendation: `${LABELS[topZone]} is carrying the strongest signal. Hold a measured reserve there and avoid chasing the whole city at once.`,
    confidence: 0.67,
    moves: [],
    evidence: [{ label: 'MODEL PULSE', detail: `${LABELS[topZone]} leads the next three-hour forecast.`, zone_id: topZone }],
    forecast: { demand_by_zone: Object.fromEntries(ZONE_IDS.map((zoneId) => [zoneId, state.zones[zoneId].model_forecast])), predicted_revenue: 0 },
    data_source: 'local-preview',
    narrative_source: 'fallback',
  }
}

export default function App() {
  const [gameState, setGameState] = useState<GameState>(() => makeFallbackState())
  const [allocation, setAllocation] = useState<Record<ZoneId, number>>(() => allocationFromState(makeFallbackState()))
  const [activeAdviser, setActiveAdviser] = useState<AdviserId>('twitch')
  const [adviserResponses, setAdviserResponses] = useState<Partial<Record<AdviserId, AdviserResponse>>>({})
  const [selectedZone, setSelectedZone] = useState<ZoneId>('midtown')
  const [secondsLeft, setSecondsLeft] = useState(90)
  const [result, setResult] = useState<RoundResult | null>(null)
  const [gameOverVisible, setGameOverVisible] = useState(false)
  const [isDispatching, setIsDispatching] = useState(false)
  const [apiConnected, setApiConnected] = useState(true)
  const [weatherConfig, setWeatherConfig] = useState<WeatherConfig | null>(null)
  const [error, setError] = useState<string | null>(null)
  const allocationRef = useRef(allocation)
  const dispatchRef = useRef<() => void>(() => undefined)
  const timeoutRef = useRef<() => void>(() => undefined)
  const timeoutTriggeredRef = useRef(false)

  const assigned = Object.values(allocation).reduce((sum, value) => sum + value, 0)
  const remaining = gameState.fleet_available - assigned
  const activeResponse = adviserResponses[activeAdviser] ?? fallbackAdviser(activeAdviser, gameState)
  const historicLeader = useMemo(() => ZONE_IDS.reduce((best, zoneId) => gameState.zones[zoneId].historic_mean > gameState.zones[best].historic_mean ? zoneId : best, ZONE_IDS[0]), [gameState])
  const weatherEvent = getActiveWeatherEvent(weatherConfig, selectedZone)
  const baseWeather = gameState.zones[selectedZone].weather ?? gameState.weather
  const eventDelta = weatherEvent?.ml_delta ?? {}
  const selectedWeather = {
    ...baseWeather,
    label: weatherEvent ? `${weatherEvent.emoji} ${weatherEvent.label}` : baseWeather.label,
    temperature_c: baseWeather.temperature_c + (eventDelta.temp_c ?? 0),
    rain_mm: Math.max(0, baseWeather.rain_mm + (eventDelta.precip_mm ?? 0)),
    wind_mps: Math.max(0, baseWeather.wind_mps + (eventDelta.wind_ms ?? 0)),
    visibility_km: Math.max(0.1, baseWeather.visibility_km + (eventDelta.vis_km ?? 0)),
  }

  useEffect(() => {
    allocationRef.current = allocation
  }, [allocation])

  const loadState = useCallback(async () => {
    try {
      const response = await fetch('/api/game/state')
      if (!response.ok) throw new Error('state request failed')
      const nextState = await response.json() as GameState
      setGameState(nextState)
      setAllocation(allocationFromState(nextState))
      setApiConnected(true)
    } catch {
      setApiConnected(false)
      setGameState(makeFallbackState())
    }
  }, [])

  useEffect(() => {
    void loadState()
  }, [loadState])

  useEffect(() => {
    fetch('/data/weather_events.json')
      .then((response) => response.ok ? response.json() as Promise<WeatherConfig> : null)
      .then((config) => { if (config) setWeatherConfig(config) })
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    setSecondsLeft(90)
    setError(null)
    timeoutTriggeredRef.current = false
  }, [gameState.timestamp])

  useEffect(() => {
    if (gameState.completed) setGameOverVisible(true)
  }, [gameState.completed])

  useEffect(() => {
    let cancelled = false
    const adviserWeather = numericSignals(gameState.weather as unknown as Record<string, unknown>)
    adviserWeather.visibility_m = gameState.weather.visibility_km * 1000
    const request = {
      timestamp: gameState.timestamp,
      demand_by_zone: Object.fromEntries(ZONE_IDS.map((zoneId) => [zoneId, gameState.zones[zoneId].recent_demand])),
      weather: adviserWeather,
      events: gameState.events,
      idle_taxis_by_zone: Object.fromEntries(ZONE_IDS.map((zoneId) => [zoneId, gameState.zones[zoneId].idle_taxis])),
    }
    const loadAdvisers = async () => {
      const entries = await Promise.all(ADVISER_IDS.map(async (id) => {
        try {
          const response = await fetch(`/api/advisers/${id}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(request),
          })
          if (!response.ok) throw new Error('adviser unavailable')
          return [id, await response.json() as AdviserResponse] as const
        } catch {
          return [id, fallbackAdviser(id, gameState)] as const
        }
      }))
      if (!cancelled) setAdviserResponses(Object.fromEntries(entries) as Partial<Record<AdviserId, AdviserResponse>>)
    }
    void loadAdvisers()
    return () => { cancelled = true }
  }, [gameState])

  const dispatch = useCallback(async () => {
    if (isDispatching || gameState.completed) return
    let nextAllocation = { ...allocationRef.current }
    const unassigned = gameState.fleet_available - Object.values(nextAllocation).reduce((sum, value) => sum + value, 0)
    if (unassigned > 0) {
      setError(`Assign all ${gameState.fleet_available} taxis before dispatch.`)
      return
    }
    setIsDispatching(true)
    setError(null)
    try {
      const response = await fetch('/api/game/advance', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ day: gameState.day, round: gameState.round, allocation: nextAllocation }),
      })
      if (!response.ok) {
        const payload = await response.json().catch(() => ({})) as { detail?: string }
        throw new Error(payload.detail ?? 'Dispatch failed')
      }
      const payload = await response.json() as { result: RoundResult; state: GameState }
      setResult(payload.result)
      setGameState(payload.state)
      if (payload.state.completed) setGameOverVisible(true)
      setApiConnected(true)
    } catch (dispatchError) {
      setApiConnected(false)
      setError(dispatchError instanceof Error ? dispatchError.message : 'Dispatch failed')
    } finally {
      setIsDispatching(false)
    }
  }, [gameState, isDispatching])

  const endOnTimeout = useCallback(async () => {
    if (isDispatching || gameState.completed) return
    setIsDispatching(true)
    setSecondsLeft(0)
    try {
      const response = await fetch('/api/game/timeout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ day: gameState.day, round: gameState.round }),
      })
      if (!response.ok) {
        const payload = await response.json().catch(() => ({})) as { detail?: string }
        throw new Error(payload.detail ?? 'Timeout update failed')
      }
      const payload = await response.json() as { state: GameState }
      setGameState(payload.state)
      setGameOverVisible(true)
      setApiConnected(true)
    } catch (timeoutError) {
      const previewState = { ...gameState, completed: true, game_over_reason: 'time_expired' as const }
      setGameState(previewState)
      setGameOverVisible(true)
      setApiConnected(false)
      setError(timeoutError instanceof Error ? timeoutError.message : 'Decision time expired')
    } finally {
      setIsDispatching(false)
    }
  }, [gameState, isDispatching])

  useEffect(() => {
    dispatchRef.current = dispatch
  }, [dispatch])

  useEffect(() => {
    timeoutRef.current = endOnTimeout
  }, [endOnTimeout])

  useEffect(() => {
    if (result || gameState.completed || isDispatching) return
    const timer = window.setInterval(() => {
      setSecondsLeft((current) => {
        if (current <= 1) {
          if (!timeoutTriggeredRef.current) {
            timeoutTriggeredRef.current = true
            window.setTimeout(() => timeoutRef.current(), 0)
          }
          return 0
        }
        return current - 1
      })
    }, 1000)
    return () => window.clearInterval(timer)
  }, [gameState.completed, gameState.timestamp, isDispatching, result])

  const changeAllocation = (zoneId: ZoneId, delta: number) => {
    setAllocation((current) => {
      const next = { ...current }
      if (delta > 0 && remaining <= 0) return current
      next[zoneId] = Math.max(0, next[zoneId] + delta)
      return next
    })
    setSelectedZone(zoneId)
  }

  const resetGame = async () => {
    try {
      const response = await fetch('/api/game/reset', { method: 'POST' })
      if (!response.ok) throw new Error('reset failed')
      const nextState = await response.json() as GameState
      setGameState(nextState)
      setAllocation(allocationFromState(nextState))
      setResult(null)
      setGameOverVisible(false)
      setSecondsLeft(90)
      setError(null)
      timeoutTriggeredRef.current = false
      setApiConnected(true)
    } catch {
      const nextState = makeFallbackState()
      setGameState(nextState)
      setAllocation(allocationFromState(nextState))
      setResult(null)
      setGameOverVisible(false)
      setSecondsLeft(90)
      setError(null)
      timeoutTriggeredRef.current = false
      setApiConnected(false)
    }
  }

  const activeProfile = ADVISER_PROFILES[activeAdviser]
  const isLastRound = gameState.completed
  const timerUrgent = !gameState.completed && secondsLeft <= 20
  const timedOut = gameState.game_over_reason === 'time_expired'
  const finalVerdict = gameState.average_model_match >= 82
    ? 'CITY READER'
    : gameState.average_model_match >= 62
      ? 'SOLID DISPATCHER'
      : 'NIGHT SHIFT ROOKIE'
  const runNet = gameState.total_net_revenue || (gameState.currency - 2450)

  return (
    <main className="game-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark"><span /> <span /> <span /></div>
          <div>
            <div className="brand-title">RAT CAB CO.</div>
            <div className="brand-subtitle">NYC · DISPATCH OPERATIONS</div>
          </div>
        </div>
        <div className="header-stat"><span className="stat-label">DAY</span><strong>{gameState.day} / 3</strong></div>
        <div className="header-stat"><span className="stat-label">ROUND</span><strong>{gameState.round} / 4</strong></div>
        <div className="header-stat wide"><span className="stat-label">WINDOW</span><strong>{gameState.time_start} — {gameState.time_end}</strong></div>
        <div className="header-stat cash"><span className="stat-label">BANK</span><strong>{currency(gameState.currency)}</strong></div>
        <div className={`countdown ${timerUrgent ? 'urgent' : ''}`}>
          <span className="stat-label">DECISION IN</span>
          <strong>{gameState.completed ? '— —' : `${String(Math.floor(secondsLeft / 60)).padStart(2, '0')}:${String(secondsLeft % 60).padStart(2, '0')}`}</strong>
        </div>
        <button className="reset-button" type="button" onClick={() => void resetGame()} aria-label="Reset game">↺</button>
      </header>

      <div className="game-grid">
        <aside className="panel fleet-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">LIVE ALLOCATION</span><h2>Fleet</h2></div>
            <div className="fleet-total"><strong>{shortNumber(gameState.fleet_available)}</strong><span>CABS</span></div>
          </div>
          <div className="allocation-strip"><span style={{ width: `${Math.min(100, (assigned / gameState.fleet_available) * 100)}%` }} /></div>
          <div className="allocation-summary"><span>{assigned} / {gameState.fleet_available} ASSIGNED</span><span className={remaining === 0 ? 'ready' : ''}>{remaining === 0 ? 'READY' : `${remaining} TO PLACE`}</span></div>
          <div className="zone-head"><span>REGION</span><span>AVAILABLE</span></div>
          <div className="zone-list">
            {REGION_IDS.map((regionId) => {
              const zoneId = regionId
              const zone = gameState.zones[zoneId]
              const assignedHere = allocation[zoneId]
              const share = Math.min(100, (assignedHere / Math.max(gameState.fleet_available, 1)) * 260)
              return (
                <button key={regionId} type="button" className={`zone-row ${selectedZone === zoneId ? 'selected' : ''}`} onClick={() => setSelectedZone(zoneId)}>
                  <span className="zone-color" />
                  <span className="zone-name">{REGION_LABELS[regionId]}</span>
                  <span className="zone-demand"><i style={{ width: `${share}%` }} /><small>{zone.model_forecast}</small></span>
                  <span className="zone-controls">
                    <span className="stepper" onClick={(event) => { event.stopPropagation(); changeAllocation(zoneId, -1) }}>−</span>
                    <strong>{allocation[zoneId]}</strong>
                    <span className="stepper" onClick={(event) => { event.stopPropagation(); changeAllocation(zoneId, 1) }}>+</span>
                  </span>
                </button>
              )
            })}
          </div>
          <div className="fleet-footer"><span className="pulse-dot" /> All vehicles online <span className="footer-source">{apiConnected ? 'API LINKED' : 'LOCAL PREVIEW'}</span></div>
        </aside>

        <section className="center-column">
          <section className="panel map-panel">
            <div className="map-chrome map-chrome-top">
              <div><span className="eyebrow">NEW YORK CITY</span><h2>Taxi dispatch operations</h2></div>
              <div className="map-status"><span className="live-dot" /> SIMULATED SCENARIO <small>· {gameState.data_source}</small></div>
            </div>
             <NycMap allocationByZone={allocation} selectedZone={selectedZone} onZoneSelect={(zoneId) => { if ((ZONE_IDS as readonly string[]).includes(zoneId)) setSelectedZone(zoneId as ZoneId) }} />
            <div className="map-chrome map-chrome-bottom">
               <div className="map-key"><span className="key-swatch demand" /> Weekday average <span className="key-swatch fleet" /> Your fleet</div>
              <div className="map-coords">40° 44′ N&nbsp;&nbsp; 73° 59′ W</div>
            </div>
            {result && !gameOverVisible && (
              <div className="round-result">
                <div className="result-kicker">ROUND RESOLVED · {result.time_start} — {result.time_end}</div>
                <div className="result-main"><strong>{result.net_revenue >= 0 ? '+' : ''}{currency(result.net_revenue)}</strong><span>{result.verdict}</span></div>
                <div className="result-metrics"><span><b>{result.model_match_percentage}%</b> model match</span><span><b>{result.trips_captured}</b> trips captured</span><span><b>+{result.score_gain}</b> points</span></div>
                <button type="button" className="result-button" onClick={() => gameState.completed ? setGameOverVisible(false) : setResult(null)}>{gameState.completed ? 'Review final board' : 'Next round'} <span>→</span></button>
              </div>
            )}
          </section>

          <section className="panel adviser-panel">
            <div className="adviser-heading"><div><span className="eyebrow">OPTIONAL SIGNALS</span><h2>Rat advisors</h2></div><span className="adviser-note">Same input · different instincts</span></div>
            <div className="adviser-tabs">
              {ADVISER_IDS.map((id) => {
                const profile = ADVISER_PROFILES[id]
                const response = adviserResponses[id]
                return (
                  <button type="button" key={id} className={`adviser-tab ${activeAdviser === id ? 'active' : ''}`} onClick={() => setActiveAdviser(id)} style={{ '--advisor-accent': profile.accent } as CSSProperties}>
                    <span className="advisor-avatar"><img src="/assets/rat-advisor.png" alt="" style={{ filter: profile.filter }} /></span>
                    <span className="advisor-meta"><b>{response?.adviser_name ?? (id === 'grandpa' ? 'Grandpa' : id[0].toUpperCase() + id.slice(1))}</b><small>{profile.role}</small></span>
                    <span className="advisor-signal">{response ? `${Math.round(response.confidence * 100)}%` : '—'}</span>
                  </button>
                )
              })}
            </div>
            <div className="adviser-message">
              <div className="message-top"><span>{activeResponse.adviser_name.toUpperCase()} / {activeProfile.specialty}</span><span>{gameState.time_start}</span></div>
              <p>{activeResponse.recommendation}</p>
              <div className="evidence-row">
                {(activeResponse.evidence.length ? activeResponse.evidence.slice(0, 2) : [{ label: 'SIGNAL', detail: 'Reading the current city state.' }]).map((item) => <span key={`${item.label}-${item.detail}`}><b>{item.label}</b> {item.detail}</span>)}
              </div>
            </div>
          </section>
        </section>

        <aside className="right-column">
          <section className="panel signal-panel weather-panel">
             <div className="panel-heading compact"><div><span className="eyebrow">ATMOSPHERE</span><h2>Weather station</h2></div><span className="station-tag">{LABELS[selectedZone].toUpperCase()} · STN 724</span></div>
             <div className="weather-hero"><span className="weather-icon">{selectedWeather.rain_mm ? '☂' : '◒'}</span><div><strong>{selectedWeather.label}</strong><small>{selectedWeather.note}</small></div></div>
            <div className="weather-grid">
               <span><b>{selectedWeather.temperature_c.toFixed(1)}°</b><small>TEMP</small></span>
               <span><b>{selectedWeather.rain_mm.toFixed(1)} mm</b><small>RAIN / 1H</small></span>
               <span><b>{selectedWeather.wind_mps.toFixed(1)} m/s</b><small>WIND</small></span>
               <span><b>{selectedWeather.visibility_km.toFixed(1)} km</b><small>VISIBILITY</small></span>
            </div>
            <div className="signal-foot"><span /> {gameState.timestamp.replace('T', ' ')} local observation</div>
          </section>

          <section className="panel signal-panel demand-panel">
             <div className="panel-heading compact"><div><span className="eyebrow">HISTORIC DEMAND</span><h2>Weekday average</h2></div><span className="confidence">{gameState.weekday_label.slice(0, 3)}</span></div>
             <div className="demand-comparison"><div><span>{LABELS[selectedZone]} recent</span><strong>{shortNumber(gameState.zones[selectedZone].recent_demand)}</strong></div><div className="compare-arrow">→</div><div><span>Weekday avg</span><strong>{shortNumber(gameState.zones[selectedZone].historic_mean)}</strong></div></div>
             <div className="model-line"><span>{gameState.weekday_label} MEAN</span><b>{LABELS[historicLeader]}</b><em>{Math.round((gameState.zones[historicLeader].historic_mean / Math.max(gameState.zones[selectedZone].historic_mean, 1) - 1) * 100)}%</em></div>
             <div className="mini-bars">{ZONE_IDS.map((zoneId) => <span key={zoneId} title={LABELS[zoneId]} style={{ height: `${Math.max(14, (gameState.zones[zoneId].historic_mean / Math.max(gameState.zones[historicLeader].historic_mean, 1)) * 100)}%`, background: zoneId === selectedZone ? '#f2bf24' : 'rgba(237,231,215,.36)' }} />)}</div>
             <div className="signal-foot"><span /> Store-derived weekday mean</div>
          </section>

          <section className="panel signal-panel news-panel">
             <div className="panel-heading compact"><div><span className="eyebrow">REGION FEED</span><h2>Field notes</h2></div><span className="news-count">{gameState.news.filter((item) => item.zone_id === selectedZone).length} NEW</span></div>
             {gameState.news.filter((item) => item.zone_id === selectedZone).map((item) => <button type="button" key={item.headline} className="news-item" onClick={() => setSelectedZone(item.zone_id)}><span className={`news-intensity ${item.intensity}`} /><span><small>{item.age} · {item.zone}</small><b>{item.headline}</b><em>Selected region →</em></span></button>)}
             {!gameState.news.some((item) => item.zone_id === selectedZone) && <div className="news-more">No field notes for {LABELS[selectedZone]}.</div>}
             <div className="news-more">Regional scenario feed · live at decision time</div>
          </section>
        </aside>
      </div>

      <footer className="dispatch-bar">
        <div className="dispatch-state"><span className={`checkmark ${remaining === 0 ? 'complete' : ''}`}>{remaining === 0 ? '✓' : '·'}</span><div><strong>{remaining === 0 ? 'All taxis assigned' : `${remaining} taxis still in depot`}</strong><small>{error ?? 'Target allocation · next block is hidden'}</small></div></div>
        <div className="dispatch-model"><span>MODEL CONFIDENCE</span><b>{Math.round(gameState.model_confidence * 100)}%</b><i style={{ width: `${gameState.model_confidence * 100}%` }} /></div>
        <button type="button" className="dispatch-button" onClick={() => void dispatch()} disabled={isDispatching || gameState.completed}>{isDispatching ? 'RUNNING…' : isLastRound ? 'RUN COMPLETE' : 'DISPATCH'} <span>→</span></button>
        <span className="advance-label">Advance 3 hours</span>
      </footer>

      {gameOverVisible && gameState.completed && (
        <div className="game-over-screen" role="dialog" aria-modal="true" aria-labelledby="game-over-title">
          <div className="game-over-card">
            <div className="game-over-topline"><span><i /> RAT CAB CO.</span><span>{timedOut ? 'DECISION TIME EXPIRED' : `RUN COMPLETE · ${gameState.rounds_completed} / 12 ROUNDS`}</span></div>
            <div className="game-over-title-wrap">
              <span className="eyebrow">{timedOut ? 'THE CLOCK RAN OUT' : 'THE CITY HAS STOPPED MOVING'}</span>
              <h2 id="game-over-title"><span>GAME</span> OVER</h2>
              <p>{timedOut ? `${gameState.rounds_completed} rounds completed` : finalVerdict} <b>·</b> {timedOut ? 'No dispatch was made' : 'Day 3 closeout at 20:00'}</p>
            </div>
            <div className="final-score-block">
              <span className="eyebrow">FINAL SCORE</span>
              <strong>{shortNumber(gameState.score)}</strong>
              <span className="score-caption">POINTS EARNED</span>
            </div>
            <div className="game-over-stats">
              <div><span>FINAL BANK</span><b>{currency(gameState.currency)}</b><small>{runNet >= 0 ? '+' : ''}{currency(runNet)} net run</small></div>
              <div><span>MODEL MATCH</span><b>{Math.round(gameState.average_model_match)}%</b><small>best round {gameState.best_model_match}%</small></div>
              <div><span>TRIPS CAPTURED</span><b>{shortNumber(gameState.total_trips_captured)}</b><small>across {gameState.rounds_completed} turns</small></div>
            </div>
            <div className="game-over-bottomline"><span><i /> {timedOut ? 'Run ended before this turn was dispatched' : 'Historical demand replay complete'}</span><span>{apiConnected ? 'API LINKED' : 'LOCAL PREVIEW'}</span></div>
            <div className="game-over-actions">
              <button type="button" className="review-button" onClick={() => setGameOverVisible(false)}>Review final board</button>
              <button type="button" className="new-run-button" onClick={() => void resetGame()}>Run it back <span>→</span></button>
            </div>
          </div>
        </div>
      )}
    </main>
  )
}
