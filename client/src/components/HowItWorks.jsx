const steps = [
  ['Upload the PDF', 'Start with the official exam schedule from your university. We’ll take care of reading it.'],
  ['Find your class', 'Enter your class code to pick out the exams that belong on your schedule.'],
  ['Make room to prepare', 'Review your timetable, check the details, and save an image to keep close by.'],
]

export default function HowItWorks() {
  return (
    <section className="how-section" id="how-it-works" aria-labelledby="how-title">
      <div className="layout-width">
        <div className="how-heading"><p className="eyebrow">From PDF to peace of mind</p><h2 id="how-title">Your exam week, in three steps.</h2></div>
        <ol className="steps-grid">
          {steps.map(([title, description], index) => (
            <li key={title}><span className="step-number">0{index + 1}</span><h3>{title}</h3><p>{description}</p></li>
          ))}
        </ol>
      </div>
    </section>
  )
}
