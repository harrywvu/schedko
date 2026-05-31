import React from 'react';

const pillars = [
  {
    title: 'Fast schedule extraction',
    text: 'Turn dense university exam PDFs into a readable schedule with less manual searching.',
  },
  {
    title: 'Focused student workflow',
    text: 'Enter your program details once and jump straight to the exams that matter to you.',
  },
  {
    title: 'Clear calendar output',
    text: 'See each exam in a structured timetable that is easier to scan, compare, and plan around.',
  },
];

const About = () => (
  <div className="page-shell">
    <section className="page-hero">
      <div className="page-hero-copy">
        <p className="page-eyebrow">About SchedKo</p>
        <h1 className="page-title">A cleaner way to read exam schedules.</h1>
        <p className="page-lead">
          SchedKo is built to take a crowded exam PDF and turn it into a timetable that is easier to
          understand, easier to trust, and easier to act on during exam season.
        </p>
      </div>

      <div className="page-panel">
        <h2 className="panel-title">What it does</h2>
        <p className="panel-text">
          Upload the official exam file, identify your section, and get a schedule view that keeps
          the important details together instead of scattering them across multiple pages.
        </p>
      </div>
    </section>

    <section className="page-grid">
      {pillars.map((pillar) => (
        <article key={pillar.title} className="info-card">
          <h3>{pillar.title}</h3>
          <p>{pillar.text}</p>
        </article>
      ))}
    </section>
  </div>
);

export default About;
