import { Link } from 'react-router-dom'

export default function Footer() {
  return (
    <footer className="site-footer">
      <div className="layout-width footer-inner">
        <div><Link to="/" className="footer-brand">SchedKo.</Link><p>Less searching. More time to prepare.</p></div>
        <div className="footer-links"><Link to="/about">About</Link><Link to="/support">Need a hand?</Link><a href="https://github.com/johnnywvu" target="_blank" rel="noopener noreferrer">GitHub ↗</a></div>
        <p className="copyright">© {new Date().getFullYear()} SchedKo</p>
      </div>
    </footer>
  )
}
