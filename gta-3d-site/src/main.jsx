import React from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'

const chapters = [
  ['A', 'The city is a signal', 'A field guide to the places that only appear after the last ferry leaves. Follow the light, not the map.'],
  ['B', 'Take the long way', 'Three routes, nine stops, zero reason to hurry. Each detour has its own weather, rhythm, and view.'],
  ['C', 'Leave with a story', 'The best souvenir is a strange hour you cannot quite explain. Keep your windows down.'],
]
const routes = [
  ['The Long Cut', 'harbour / 02:14', '17.8 km', 'mint'],
  ['Afterimage Loop', 'midtown / 03:41', '09.2 km', 'coral'],
  ['Low Tide Radio', 'south pier / 04:08', '22.4 km', 'amber'],
]

function Scene({ mode }) {
  return <div className={`scene scene-${mode}`} role="img" aria-label="Interactive 3D night drive map. Use the view controls to change the camera.">
    <div className="scene-grid" />
    <div className="scene-orbit orbit-a" /><div className="scene-orbit orbit-b" />
    <div className="scene-tower tower-a" /><div className="scene-tower tower-b" /><div className="scene-tower tower-c" />
    <div className="scene-tower tower-d" /><div className="scene-tower tower-e" />
    <div className="scene-sun" /><div className="scene-route route-a" /><div className="scene-route route-b" />
    <span className="scene-label label-one">NIGHT / 04:08</span><span className="scene-label label-two">SOUTH PIER</span>
  </div>
}

function App() {
  const [mode, setMode] = React.useState('orbit')
  const [activeRoute, setActiveRoute] = React.useState(0)
  return <div className="app">
    <header className="topbar"><a className="wordmark" href="#top" aria-label="Night Drive Atlas home"><span className="mark">✦</span> NIGHT DRIVE <span className="muted">/ ATLAS</span></a><nav aria-label="Primary navigation"><a href="#field-notes">Field notes</a><a href="#routes">Routes</a><a href="#about">About</a></nav><button className="menu-button" aria-label="Open navigation">Menu <span>↗</span></button></header>
    <main id="top">
      <section className="hero section-shell" aria-labelledby="hero-title"><div className="hero-copy"><p className="eyebrow"><span className="signal-dot" /> Coastal city field notes / 001</p><h1 id="hero-title">Stay out<br /><em>late.</em></h1><p className="hero-intro">A living atlas for the hours between the last train and the first light.</p><a className="text-link" href="#field-notes">Enter the atlas <span>↓</span></a></div><div className="hero-visual"><Scene mode={mode} /><div className="scene-controls" aria-label="Scene view controls"><span className="control-label">VIEW</span>{['orbit','street','aerial'].map((item) => <button key={item} className={mode === item ? 'active' : ''} onClick={() => setMode(item)} aria-pressed={mode === item}>{item}</button>)}</div></div><div className="hero-meta"><span>36° 07′ N / 115° 10′ W</span><span>Updated tonight</span></div></section>
      <section className="manifesto section-shell" id="field-notes"><div className="section-kicker"><span>Field notes</span><span>What the night knows</span></div><div className="manifesto-grid"><p className="display-copy">The map is not<br />the territory.<br /><em>It is the feeling.</em></p><div className="manifesto-side"><p>Some cities go quiet when the sun goes down. This one changes frequency. We collect the overlooked corners, the sodium-lit shortcuts, and the places that are better without a reservation.</p><span className="tiny-note">No rankings. No checklists.<br />Just a better reason to wander.</span></div></div></section>
      <section className="chapters section-shell"><div className="chapter-list">{chapters.map(([letter, title, copy], index) => <article className="chapter" key={letter}><span className="chapter-index">{letter}</span><div><h2>{title}</h2><p>{copy}</p></div><span className="chapter-arrow">↗</span></article>)}</div></section>
      <section className="routes section-shell" id="routes"><div className="section-kicker"><span>Tonight's routes</span><span>Choose your frequency</span></div><div className="routes-header"><h2>Where to<br /><em>next?</em></h2><p>Curated loops for when a straight line feels like a wasted opportunity.</p></div><div className="route-table">{routes.map(([name, detail, distance, color], index) => <button className={`route-row ${activeRoute === index ? 'selected' : ''}`} key={name} onClick={() => setActiveRoute(index)} aria-pressed={activeRoute === index}><span className={`route-swatch ${color}`} /><span className="route-name">{name}<small>{detail}</small></span><span className="route-distance">{distance}</span><span className="route-arrow">{activeRoute === index ? '✦' : '↗'}</span></button>)}</div></section>
      <section className="closing section-shell" id="about"><div className="closing-mark">✦</div><h2>The night<br /><em>is yours.</em></h2><a className="button-link" href="#top">Start wandering <span>↗</span></a></section>
    </main>
    <footer className="footer section-shell"><span>© 2024 Night Drive Atlas</span><span>Made for the insomniacs</span><a href="#top">Back to top ↑</a></footer>
  </div>
}

createRoot(document.getElementById('root')).render(<App />)
