import { Link } from 'react-router-dom'

export default function Schedule() {
  return (
    <div className="page-shell">
      <section className="page-hero">
        <div className="page-hero-copy">
          <p className="page-eyebrow">Your exam schedule</p>
          <h1 className="page-title">Let’s start with your PDF.</h1>
          <p className="page-lead">Upload your university’s exam schedule, then enter your class code to build your personal timetable.</p>
          <Link className="text-link" to="/">Upload a schedule <span aria-hidden="true">↗</span></Link>
        </div>
      </section>
    </div>
  )
}
