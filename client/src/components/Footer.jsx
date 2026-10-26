import { Link } from 'react-router-dom'

export default function Footer() {
  return (
    <footer className="site-footer">
      <div className="layout-width footer-inner">
        <p>© {new Date().getFullYear()} SchedKo</p>
        <div className="footer-links">
          <Link to="/about">About</Link>
          <Link to="/support">Support</Link>
          <a href="https://github.com/johnnywvu" target="_blank" rel="noopener noreferrer">GitHub</a>
        </div>
      </div>
    </footer>
  )
}
