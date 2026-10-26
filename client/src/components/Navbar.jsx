import { useState } from 'react'
import { Link, NavLink } from 'react-router-dom'

export default function Navbar() {
  const [isOpen, setIsOpen] = useState(false)

  return (
    <header className="site-header">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <nav className="site-nav layout-width" aria-label="Main navigation">
        <Link className="brand" to="/" onClick={() => setIsOpen(false)} aria-label="SchedKo home">
          <img src="/logo_symbol_only.png" alt="" width="42" height="42" />
          <span>SchedKo</span>
        </Link>
        <span className="brand-caption">Exam schedule planner</span>
        <button className="menu-toggle" aria-expanded={isOpen} aria-controls="nav-links" onClick={() => setIsOpen(!isOpen)}>
          {isOpen ? 'Close' : 'Menu'} <span aria-hidden="true">{isOpen ? '×' : '☰'}</span>
        </button>
        <div id="nav-links" className={`nav-links ${isOpen ? 'is-open' : ''}`}>
          {[['/', 'Home'], ['/about', 'About'], ['/support', 'Support']].map(([to, label]) => (
            <NavLink key={to} to={to} end onClick={() => setIsOpen(false)}>{label}</NavLink>
          ))}
        </div>
      </nav>
    </header>
  )
}
