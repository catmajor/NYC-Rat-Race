import NycMap from './components/NycMap.tsx'

export default function App() {
  return (
    <main className="app">
      <header className="topbar">
        <span className="topbar-dot" />
        <h1>RAT RACE</h1>
        <span className="topbar-sub">NYC · 2019 · real roads</span>
      </header>
      <NycMap />
    </main>
  )
}